"""Durable on-disk job record persistence for grok_delegate (R7-D).

Why: jobs.py keeps state in memory only, so a server restart loses every
in-flight/finished job status even though the lane work itself survives on
the branch. This module is the pure persistence layer — atomic JSON writes,
tolerant rehydration, disk eviction, and stale-running detection via a
recorded pid. Claude wires it into jobs.py; this file must not depend on
the in-memory registry.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from .contracts import redact_value
except ImportError:
    from contracts import redact_value  # type: ignore

logger = logging.getLogger(__name__)

# Mirror jobs.MAX_JOBS so disk and memory stay aligned when wired.
DEFAULT_MAX_JOBS = 64

# Job state tokens (kept local so this module stays freestanding).
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_UNKNOWN = "unknown"

# Only these characters survive in a filename derived from job_id.
_SAFE_JOB_ID = re.compile(r"[^A-Za-z0-9._-]+")

# Bound a single job JSON payload so a huge result cannot blow memory on load.
MAX_JOB_FILE_BYTES = 1_000_000
JOB_RECORD_SCHEMA = "grok-job-record.v2"

AliveCheck = Callable[[int], bool]


def job_filename(job_id: str) -> str:
    """Stable, filesystem-safe filename for a job id (no path separators)."""
    raw = str(job_id or "").strip() or "unknown"
    safe = _SAFE_JOB_ID.sub("_", raw)
    # Bound length so a pathological id cannot create an unusable path.
    if len(safe) > 120:
        safe = safe[:120]
    return f"{safe}.json"


def job_path(jobs_dir: str | Path, job_id: str) -> Path:
    """Absolute path of the on-disk record for *job_id* under *jobs_dir*."""
    return Path(jobs_dir) / job_filename(job_id)


def ensure_jobs_dir(jobs_dir: str | Path) -> Path | None:
    """Create *jobs_dir* when missing. Return the path, or None when unusable.

    Why: missing is normal (first use); unwritable must not crash the server —
    callers degrade to memory-only after a warning.
    """
    path = Path(jobs_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("jobs_dir unwritable or uncreatable (%s): %s", path, exc)
        return None
    if not path.is_dir():
        logger.warning("jobs_dir is not a directory: %s", path)
        return None
    # Probe writability with a unique file.  A fixed .write_probe races when a
    # progress event and a terminal receipt persist concurrently on Windows.
    probe: Path | None = None
    try:
        fd, raw_probe = tempfile.mkstemp(prefix=".write_probe.", dir=str(path))
        os.close(fd)
        probe = Path(raw_probe)
        probe.unlink(missing_ok=True)
    except OSError as exc:
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
        logger.warning("jobs_dir unwritable (%s): %s", path, exc)
        return None
    return path


def is_process_alive(pid: int) -> bool:
    """Return True when *pid* appears to be a live process (stdlib only).

    POSIX uses ``os.kill(pid, 0)`` (existence probe, no signal delivered).
    Windows must **not** call ``os.kill`` for this — on Win32 that API
    terminates the target — so we use OpenProcess instead. Never raises.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it.
        return True
    except OSError:
        return False


def _windows_pid_alive(pid: int) -> bool:
    """Best-effort Windows liveness without psutil."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # ERROR_ACCESS_DENIED (5) still means the process exists.
        err = kernel32.GetLastError()
        return err == 5
    except Exception:  # noqa: BLE001 — liveness must never raise
        return False


def is_stale_running(
    record: Mapping[str, Any],
    *,
    alive_check: AliveCheck | None = None,
    self_pid: int | None = None,
) -> bool:
    """True when a ``running`` record's owning process is gone (or never set).

    Why: after a server restart every in-memory thread is dead, but the on-disk
    record still says ``running``. Lying about liveness is worse than reporting
    ``unknown`` — callers should re-label stale rows rather than claim work is
    still in flight. Injectable ``alive_check`` keeps unit tests free of real
    process signals.

    The decisive field is ``server_pid``: the incarnation that owns the thread.
    Records used to store the executor-facing ``pid``, which on the write path
    was the server's own — so the liveness probe asked "is this very process
    alive?", answered yes forever, and the downgrade above never once fired.
    A record stamped by a *different* pid is stale by construction: whatever that
    process is doing, this one holds no thread for it and cannot report progress,
    so ``unknown`` is the honest answer. Legacy records (``pid``, no
    ``server_pid``) keep the old liveness-probe behaviour so a rehydrate across
    the upgrade still reads them.
    """
    if str(record.get("state") or "") != STATE_RUNNING:
        return False

    raw_server_pid = record.get("server_pid")
    if raw_server_pid is None:
        # Legacy record written before the pid split.
        raw_pid = record.get("pid")
        if raw_pid is None:
            # No pid recorded → cannot prove the worker is alive.
            return True
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            return True
        checker = alive_check if alive_check is not None else is_process_alive
        try:
            return not checker(pid)
        except Exception:  # noqa: BLE001 — check failure means stale, never raise
            return True

    try:
        server_pid = int(raw_server_pid)
    except (TypeError, ValueError):
        return True
    if server_pid != (os.getpid() if self_pid is None else int(self_pid)):
        return True
    checker = alive_check if alive_check is not None else is_process_alive
    try:
        return not checker(server_pid)
    except Exception:  # noqa: BLE001 — treat check failure as stale, never raise
        return True


def apply_stale_running(
    record: Mapping[str, Any],
    *,
    alive_check: AliveCheck | None = None,
    self_pid: int | None = None,
) -> dict[str, Any]:
    """Copy *record*; rewrite stale ``running`` → ``unknown`` when the pid is dead."""
    out = dict(record)
    if is_stale_running(out, alive_check=alive_check, self_pid=self_pid):
        out["state"] = STATE_UNKNOWN
        # setdefault is wrong here: a live record normally carries error=None, so the
        # key EXISTS and the reason would be silently dropped — leaving an "unknown"
        # state with no explanation for the operator or the driver.
        if not out.get("error"):
            out["error"] = "STALE_RUNNING"
        out["recovery_state"] = "orphaned"
        out["recoverable"] = bool(out.get("worktree_path"))
    return out


def save_job(record: Mapping[str, Any], jobs_dir: str | Path) -> bool:
    """Persist *record* as JSON under *jobs_dir* via temp file + ``os.replace``.

    Atomic replace guarantees readers never observe a half-written file.
    Missing *jobs_dir* is created; unwritable dir degrades to a no-op with a
    warning and returns False — never raises.
    """
    try:
        job_id = str(record.get("job_id") or "").strip()
        if not job_id:
            logger.warning("save_job skipped: record has no job_id")
            return False

        root = ensure_jobs_dir(jobs_dir)
        if root is None:
            return False

        target = job_path(root, job_id)
        payload = _serialize_record(record)
        # Write beside the target so replace stays on the same filesystem.
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{job_filename(job_id)}.",
            suffix=".tmp",
            dir=str(root),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # fsync is best-effort; replace still gives atomic rename.
                    pass
            os.replace(str(tmp_path), str(target))
        except Exception:
            # Clean up the temp file on any failure path.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return True
    except OSError as exc:
        logger.warning("save_job failed for %s: %s", record.get("job_id"), exc)
        return False
    except Exception as exc:  # noqa: BLE001 — never crash the server on persist
        logger.warning("save_job unexpected error for %s: %s", record.get("job_id"), exc)
        return False


def load_jobs(
    jobs_dir: str | Path,
    *,
    alive_check: AliveCheck | None = None,
    self_pid: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Rehydrate all job records from *jobs_dir*.

    Corrupt or unreadable files are skipped with a warning — one bad file must
    never prevent the rest of the registry from loading. Stale ``running``
    records (dead or missing pid) are rewritten to ``unknown`` so callers do
    not lie about in-flight work after a restart.
    """
    root = Path(jobs_dir)
    if not root.exists():
        return {}
    if not root.is_dir():
        logger.warning("load_jobs: jobs_dir is not a directory: %s", root)
        return {}

    loaded: dict[str, dict[str, Any]] = {}
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        logger.warning("load_jobs: cannot list %s: %s", root, exc)
        return {}

    for entry in entries:
        if not entry.is_file():
            continue
        if entry.name.startswith("."):
            # Temp files (atomic write) and probes — never load mid-write debris.
            continue
        if not entry.name.endswith(".json"):
            continue
        record = _read_job_file(entry)
        if record is None:
            continue
        record = apply_stale_running(
            record, alive_check=alive_check, self_pid=self_pid
        )
        job_id = str(record.get("job_id") or entry.stem)
        record["job_id"] = job_id
        loaded[job_id] = record
    return loaded


def evict_on_disk(
    jobs_dir: str | Path,
    max_jobs: int = DEFAULT_MAX_JOBS,
) -> int:
    """Delete oldest finished job files until at most *max_jobs* remain.

    Why: the in-memory registry already caps at MAX_JOBS; disk must not grow
    without bound either. Newest records (by ``started_at``, then mtime) are
    kept. Running/unknown records are preferred for retention so an in-flight
    lane is not deleted under the server. Returns the number of files removed.
    Never raises.
    """
    root = Path(jobs_dir)
    if not root.is_dir():
        return 0
    try:
        cap = max(0, int(max_jobs))
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_JOBS

    records: list[tuple[float, bool, Path]] = []
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        logger.warning("evict_on_disk: cannot list %s: %s", root, exc)
        return 0

    for entry in entries:
        if not entry.is_file() or entry.name.startswith(".") or not entry.name.endswith(".json"):
            continue
        rec = _read_job_file(entry)
        if rec is None:
            # Unreadable / corrupt — treat as oldest disposable debris.
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0.0
            records.append((mtime, False, entry))
            continue
        started = _as_float(rec.get("started_at"), default=0.0)
        is_active = str(rec.get("state") or "") in {STATE_RUNNING, STATE_UNKNOWN}
        records.append((started, is_active, entry))

    if len(records) <= cap:
        return 0

    # Sort: active first (True > False when reverse), then newest started_at.
    records.sort(key=lambda t: (t[1], t[0]), reverse=True)
    keep = {p for _, _, p in records[:cap]}
    removed = 0
    for _, _, path in records[cap:]:
        if path in keep:
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning("evict_on_disk: failed to remove %s: %s", path, exc)
    return removed


def _serialize_record(record: Mapping[str, Any]) -> str:
    """JSON-encode a job record for disk (UTF-8 safe, sorted keys)."""
    data = dict(redact_value(dict(record)))
    data["job_record_schema"] = JOB_RECORD_SCHEMA
    data["events"] = list(data.get("events") or [])[-64:]
    result = data.get("result")
    if isinstance(result, dict):
        result = dict(result)
        result["events"] = list(result.get("events") or [])[-64:]
        result["summary"] = str(result.get("summary") or "")[:16_000]
        result["tests"] = [
            {
                **dict(test),
                "command": str(test.get("command") or "")[:1_000],
                "output_preview": str(test.get("output_preview") or "")[:2_000],
            }
            for test in list(result.get("tests") or [])[:64]
            if isinstance(test, Mapping)
        ]
        result["findings"] = list(result.get("findings") or [])[:64]
        for key in ("changed_files", "full_changed_files", "commits", "artifacts"):
            result[key] = [str(value)[:1_024] for value in list(result.get(key) or [])[:256]]
        data["result"] = result

    def encode() -> str:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str) + "\n"

    payload = encode()
    if len(payload.encode("utf-8")) <= MAX_JOB_FILE_BYTES:
        return payload
    # Events are progress convenience; the terminal receipt and state are the
    # durable contract and must survive restart.
    data["events"] = []
    if isinstance(data.get("result"), dict):
        data["result"]["events"] = []
        data["result"]["summary"] = str(data["result"].get("summary") or "")[:4_000]
    payload = encode()
    if len(payload.encode("utf-8")) <= MAX_JOB_FILE_BYTES:
        return payload
    if isinstance(data.get("result"), dict):
        keep = {
            "schema_version", "status", "ok", "job_id", "session_id", "transport",
            "objective_hash", "branch", "worktree_path", "changed_files", "full_changed_files", "commits",
            "diffstat", "unified_diff", "tests", "artifacts", "findings", "blocked_reason",
            "started_at", "finished_at", "stop_reason", "agent_version",
            "worker_alive_after_shutdown",
        }
        data["result"] = {key: value for key, value in data["result"].items() if key in keep}
    payload = encode()
    if len(payload.encode("utf-8")) > MAX_JOB_FILE_BYTES:
        raise ValueError("bounded job receipt still exceeds reload cap")
    return payload


def _read_job_file(path: Path) -> dict[str, Any] | None:
    """Load one job JSON file; return None on corrupt/unreadable (with warning)."""
    try:
        size = path.stat().st_size
        if size > MAX_JOB_FILE_BYTES:
            logger.warning("load_jobs: skipping oversized job file %s (%s bytes)", path, size)
            return None
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("load_jobs: unreadable job file %s: %s", path, exc)
        return None
    except UnicodeDecodeError as exc:
        logger.warning("load_jobs: undecodable job file %s: %s", path, exc)
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("load_jobs: corrupt job file %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("load_jobs: non-object job file %s", path)
        return None
    version = data.get("job_record_schema")
    if version is None:
        data["migrated_from"] = "grok-job-record.v1"
        data["job_record_schema"] = JOB_RECORD_SCHEMA
    elif version != JOB_RECORD_SCHEMA:
        logger.warning("load_jobs: unsupported job schema %r in %s", version, path)
        return None
    return dict(redact_value(data))


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "DEFAULT_MAX_JOBS",
    "MAX_JOB_FILE_BYTES",
    "JOB_RECORD_SCHEMA",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_RUNNING",
    "STATE_UNKNOWN",
    "apply_stale_running",
    "ensure_jobs_dir",
    "evict_on_disk",
    "is_process_alive",
    "is_stale_running",
    "job_filename",
    "job_path",
    "load_jobs",
    "save_job",
]
