"""Unattended lane driver: queue → delegate → retry empty → gates → ready_for_merge.

Why (R7-E): nothing in the project advances work by itself. Every lane currently
needs a human/Claude turn to dispatch, notice an empty result, retry, and run
gates. This module is the unattended loop that closes that gap — strictly one
lane in flight, file-locked queue cursor, escalating write-first nudge on empty
results, and a hard ceiling at ``ready_for_merge`` (never merge, never push).

Runnable as ``python -m grok_delegate.driver``. Delegate and gate runners are
injectable so unit tests never spawn a real executor or gate.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence, TextIO

try:
    from . import audit
    from .gates import run_gates as default_run_gates
    from .jobs_store import is_process_alive
except ImportError:  # flat import when package dir is on sys.path
    import audit  # type: ignore
    from gates import run_gates as default_run_gates  # type: ignore
    from jobs_store import is_process_alive  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lane status tokens (driver-owned; furthest state is ready_for_merge)
# ---------------------------------------------------------------------------
STATUS_QUEUED = "queued"
STATUS_IN_FLIGHT = "in_flight"
STATUS_READY_FOR_MERGE = "ready_for_merge"
STATUS_GATES_FAILED = "gates_failed"
STATUS_BLOCKED = "blocked"
STATUS_INTERRUPTED = "interrupted"

# Exit codes
EXIT_OK = 0
EXIT_NOTHING = 0
EXIT_ERROR = 1
EXIT_QUEUE_LOCKED = 2
EXIT_INTERRUPTED = 130
EXIT_CORRUPT_QUEUE = 3

# Defaults
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_COOLDOWN_SECONDS = 0.0
DEFAULT_GATE_PROFILE = "python"

# Escalating write-first nudges appended to the goal on empty-result retries.
# Index 0 is unused (first attempt has no nudge); later indices grow firmer.
_WRITE_FIRST_NUDGES: tuple[str, ...] = (
    "",
    (
        "\n\n[driver retry 1] EMPTY RESULT. Write-first: create the target file "
        "with one real assertion or implementation before any broad exploration. "
        "git add + git commit immediately. Do not end the turn without a file change."
    ),
    (
        "\n\n[driver retry 2] STILL EMPTY. Stronger write-first: open the primary "
        "target path NOW, write a minimal real implementation, commit it, then "
        "iterate. An empty worktree is a failed lane. Commit early."
    ),
    (
        "\n\n[driver retry 3] FINAL ATTEMPT. Produce at least one committed file "
        "change before any other work. Do not explore; write the deliverable first."
    ),
)

# Bound queue / verdict payloads so a pathological file cannot blow memory.
MAX_QUEUE_BYTES = 2_000_000
MAX_VERDICT_FILE_BYTES = 500_000
MAX_GOAL_CHARS = 1_000_000

# Env keys
ENV_QUEUE_PATH = "GROK_DELEGATE_QUEUE"
ENV_REPO_ROOT = "GROK_DELEGATE_REPO_ROOT"
ENV_LANES_PARENT = "GROK_DELEGATE_LANES_PARENT"
ENV_VERDICTS_DIR = "GROK_DELEGATE_VERDICTS_DIR"

AliveCheck = Callable[[int], bool]
DelegateFn = Callable[..., dict[str, Any]]
RunGatesFn = Callable[..., dict[str, Any]]
SleepFn = Callable[[float], None]
AuditEmitFn = Callable[..., dict[str, Any]]


class DriverError(Exception):
    """Structured driver failure with a stable exit code."""

    def __init__(self, code: str, message: str, *, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class InterruptRequested(Exception):
    """Raised when SIGINT/SIGTERM arrives mid-loop so bookkeeping can finish."""


# ---------------------------------------------------------------------------
# Queue load / save / selection
# ---------------------------------------------------------------------------


def load_queue(path: str | Path) -> dict[str, Any]:
    """Read a queue JSON file into a normalized ``{lanes: [...], ...}`` dict.

    Why: the driver owns the cursor; a corrupt file must fail closed without
    mutating any lane. Unknown top-level fields are preserved (tolerated).
    """
    queue_path = Path(path)
    if not queue_path.exists():
        raise DriverError("QUEUE_MISSING", f"queue file not found: {queue_path}")
    if queue_path.is_dir():
        raise DriverError(
            "QUEUE_NOT_FILE",
            f"queue path is a directory, not a file: {queue_path}",
        )
    try:
        raw = queue_path.read_bytes()
    except OSError as exc:
        raise DriverError("QUEUE_UNREADABLE", f"cannot read queue: {exc}") from exc
    if len(raw) > MAX_QUEUE_BYTES:
        raise DriverError(
            "QUEUE_TOO_LARGE",
            f"queue exceeds {MAX_QUEUE_BYTES} bytes",
        )
    try:
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DriverError(
            "QUEUE_CORRUPT",
            f"queue JSON is corrupt: {exc}",
            exit_code=EXIT_CORRUPT_QUEUE,
        ) from exc

    if isinstance(data, list):
        return {"lanes": list(data)}
    if not isinstance(data, dict):
        raise DriverError(
            "QUEUE_CORRUPT",
            "queue root must be a JSON object or array",
            exit_code=EXIT_CORRUPT_QUEUE,
        )
    # Normalize lanes key; tolerate missing → empty list.
    lanes = data.get("lanes")
    if lanes is None:
        data = dict(data)
        data["lanes"] = []
    elif not isinstance(lanes, list):
        raise DriverError(
            "QUEUE_CORRUPT",
            "queue.lanes must be a JSON array",
            exit_code=EXIT_CORRUPT_QUEUE,
        )
    else:
        data = dict(data)
        data["lanes"] = list(lanes)
    return data


def save_queue(path: str | Path, queue: Mapping[str, Any]) -> None:
    """Atomically persist the queue (temp file + ``os.replace``).

    Why: a crash mid-write must never leave a half-written cursor that the next
    driver cannot parse.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(queue), ensure_ascii=False, indent=2, sort_keys=True)
    payload_bytes = (payload + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload_bytes)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def select_next_lane(queue: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the first lane record with ``status == queued``, else None.

    Why: strictly serial — one cursor, first-queued wins. Lanes already
    ``ready_for_merge`` / ``blocked`` / ``gates_failed`` are skipped.
    Unknown fields on a lane record are tolerated and left untouched.
    """
    lanes = queue.get("lanes") or []
    if not isinstance(lanes, list):
        return None
    for item in lanes:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or STATUS_QUEUED).strip().lower()
        if status == STATUS_QUEUED:
            return item
    return None


def iter_queued_lanes(queue: Mapping[str, Any]) -> list[dict[str, Any]]:
    """All currently-queued lane records (for dry-run planning)."""
    lanes = queue.get("lanes") or []
    out: list[dict[str, Any]] = []
    if not isinstance(lanes, list):
        return out
    for item in lanes:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or STATUS_QUEUED).strip().lower()
        if status == STATUS_QUEUED:
            out.append(item)
    return out


def find_lane_index(queue: Mapping[str, Any], lane_id: str) -> int:
    """Index of the lane with matching ``id`` (or ``lane``), or -1."""
    lanes = queue.get("lanes") or []
    if not isinstance(lanes, list):
        return -1
    for i, item in enumerate(lanes):
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") == lane_id:
            return i
        # Fallback: match on lane name when id is absent.
        if not item.get("id") and str(item.get("lane") or "") == lane_id:
            return i
    return -1


def lane_identity(lane: Mapping[str, Any]) -> str:
    """Stable identity for a lane record (id preferred, else lane name)."""
    lid = str(lane.get("id") or "").strip()
    if lid:
        return lid
    return str(lane.get("lane") or "unknown").strip() or "unknown"


# ---------------------------------------------------------------------------
# Queue lock (pid-bearing, atomic create, dead-pid takeover)
# ---------------------------------------------------------------------------


def lock_path_for(queue_path: str | Path) -> Path:
    """Sidecar lock file path for *queue_path*."""
    p = Path(queue_path)
    return p.with_suffix(p.suffix + ".lock") if p.suffix else Path(str(p) + ".lock")


def acquire_queue_lock(
    queue_path: str | Path,
    *,
    pid: int | None = None,
    alive_check: AliveCheck | None = None,
    force: bool = False,
) -> Path:
    """Acquire an exclusive lock file carrying *pid*.

    Atomic create (``O_CREAT|O_EXCL``). If a lock exists:
    - live pid → raise ``DriverError(QUEUE_LOCKED)``
    - dead pid → take over (unlink + recreate)
    - unreadable / no pid → treat as stale and take over

    Returns the lock path on success.
    """
    qpath = Path(queue_path)
    lpath = lock_path_for(qpath)
    my_pid = int(pid if pid is not None else os.getpid())
    check = alive_check or is_process_alive

    if lpath.exists() and not force:
        holder = _read_lock_pid(lpath)
        if holder is not None and check(holder) and holder != my_pid:
            raise DriverError(
                "QUEUE_LOCKED",
                f"queue locked by live pid {holder}",
                exit_code=EXIT_QUEUE_LOCKED,
            )
        # Dead or unreadable lock — take over.
        try:
            lpath.unlink(missing_ok=True)
        except OSError as exc:
            raise DriverError(
                "QUEUE_LOCKED",
                f"cannot clear stale lock: {exc}",
                exit_code=EXIT_QUEUE_LOCKED,
            ) from exc

    # Atomic create.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(lpath), flags, 0o644)
    except FileExistsError as exc:
        # Race: another driver won between unlink and create.
        holder = _read_lock_pid(lpath)
        raise DriverError(
            "QUEUE_LOCKED",
            f"queue locked by pid {holder if holder is not None else 'unknown'}",
            exit_code=EXIT_QUEUE_LOCKED,
        ) from exc
    except OSError as exc:
        raise DriverError(
            "QUEUE_LOCK_FAILED",
            f"cannot create lock file: {exc}",
        ) from exc

    try:
        payload = json.dumps(
            {"pid": my_pid, "ts": time.time(), "queue": str(qpath)},
            ensure_ascii=False,
            sort_keys=True,
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload + "\n")
            fh.flush()
    except Exception:
        try:
            lpath.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return lpath


def release_queue_lock(
    queue_path: str | Path,
    *,
    pid: int | None = None,
    force: bool = False,
) -> bool:
    """Release the queue lock if we own it (or *force*). Return True if removed."""
    lpath = lock_path_for(Path(queue_path))
    if not lpath.exists():
        return False
    my_pid = int(pid if pid is not None else os.getpid())
    if not force:
        holder = _read_lock_pid(lpath)
        if holder is not None and holder != my_pid:
            logger.warning(
                "release_queue_lock: lock held by pid %s, not us (%s); leaving it",
                holder,
                my_pid,
            )
            return False
    try:
        lpath.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.warning("release_queue_lock failed: %s", exc)
        return False


def _read_lock_pid(lpath: Path) -> int | None:
    """Best-effort parse of the pid stored in a lock file. Never raises."""
    try:
        text = lpath.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    # Prefer JSON {"pid": N}; fall back to bare integer.
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "pid" in data:
            return int(data["pid"])
        if isinstance(data, int):
            return int(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        return int(text.split()[0])
    except (TypeError, ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Empty-result detection + write-first nudge
# ---------------------------------------------------------------------------


def is_empty_result(result: Mapping[str, Any] | None) -> bool:
    """True when the delegate produced no ``changed_files`` and no ``commits``.

    Why: empty worktrees are the dominant real failure (measured). The driver
    must retry rather than treat exit-0-with-nothing as success. Trust git
    fields on the result dict — not prose or a self-reported verdict alone.
    """
    if not result or not isinstance(result, Mapping):
        return True
    # A hard structured error (spawn never happened) is NOT an "empty result"
    # retry case — it is a block. Callers should check result.ok / error first.
    changed = result.get("changed_files") or []
    commits = result.get("commits") or []
    try:
        n_changed = len(changed) if not isinstance(changed, (str, bytes)) else 0
    except TypeError:
        n_changed = 0
    try:
        n_commits = len(commits) if not isinstance(commits, (str, bytes)) else 0
    except TypeError:
        n_commits = 0
    return n_changed == 0 and n_commits == 0


def write_first_nudge(attempt: int) -> str:
    """Escalating write-first prompt fragment for retry *attempt* (1-based).

    attempt 0 / first dispatch → empty string (no nudge).
    attempt 1+ → stronger nudge from the table (capped at the last entry).
    """
    if attempt <= 0:
        return ""
    idx = min(attempt, len(_WRITE_FIRST_NUDGES) - 1)
    return _WRITE_FIRST_NUDGES[idx]


def build_goal_with_nudge(goal: str, attempt: int) -> str:
    """Compose goal text with the write-first nudge for *attempt* (0-based tries so far)."""
    base = goal or ""
    # attempt here = number of prior empty results (0 on first try).
    nudge = write_first_nudge(attempt)
    if not nudge:
        return base
    # Bound so a huge goal + nudge cannot blow argv later.
    if len(base) > MAX_GOAL_CHARS:
        base = base[:MAX_GOAL_CHARS]
    return base + nudge


# ---------------------------------------------------------------------------
# Goal file loading
# ---------------------------------------------------------------------------


def load_goal_file(goal_file: str | Path, *, base_dir: Path | None = None) -> str:
    """Read a lane's goal file as UTF-8 text.

    Raises DriverError with ``GOAL_FILE_MISSING`` when absent so the lane can
    be marked blocked without spawning.
    """
    path = Path(goal_file)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        raise DriverError(
            "GOAL_FILE_MISSING",
            f"goal_file not found: {path}",
        )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DriverError(
            "GOAL_FILE_UNREADABLE",
            f"cannot read goal_file {path}: {exc}",
        ) from exc
    if len(text) > MAX_GOAL_CHARS:
        text = text[:MAX_GOAL_CHARS]
    return text


# ---------------------------------------------------------------------------
# Per-lane verdict file + audit helpers
# ---------------------------------------------------------------------------


def verdict_path_for(
    verdicts_dir: str | Path,
    lane_id: str,
) -> Path:
    """Filesystem-safe path for a per-lane verdict JSON file."""
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in lane_id) or "unknown"
    if len(safe) > 120:
        safe = safe[:120]
    return Path(verdicts_dir) / f"{safe}.verdict.json"


def write_lane_verdict(
    verdicts_dir: str | Path,
    lane_id: str,
    payload: Mapping[str, Any],
) -> Path:
    """Atomically write a per-lane verdict file. Returns the path written."""
    root = Path(verdicts_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = verdict_path_for(root, lane_id)
    blob = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
    data = (blob + "\n").encode("utf-8")
    if len(data) > MAX_VERDICT_FILE_BYTES:
        data = data[:MAX_VERDICT_FILE_BYTES]
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(root),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def emit_state_change(
    *,
    lane: str,
    status: str,
    outcome: str,
    error: str | None = None,
    base_ref: str | None = None,
    changed_file_count: int | None = None,
    worktree_path: str | None = None,
    elapsed_seconds: float | None = None,
    audit_emit: AuditEmitFn | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any] | None:
    """Emit one audit record for a lane state change. Never raises."""
    emitter = audit_emit or audit.emit
    event: dict[str, Any] = {
        "tool": "driver",
        "lane": lane,
        "status": status,
        "outcome": outcome,
    }
    if error:
        event["error"] = error
    if base_ref:
        event["base_ref"] = base_ref
    if changed_file_count is not None:
        event["changed_file_count"] = int(changed_file_count)
    if worktree_path:
        event["worktree_path"] = worktree_path
    if elapsed_seconds is not None:
        event["elapsed_seconds"] = float(elapsed_seconds)
    try:
        if stream is not None:
            return emitter(event, stream=stream)
        return emitter(event)
    except Exception as exc:  # noqa: BLE001 — audit must not kill the driver
        logger.warning("audit emit failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Single-lane processing
# ---------------------------------------------------------------------------


def _set_lane_fields(lane: MutableMapping[str, Any], **fields: Any) -> None:
    """Update lane record fields in place."""
    for key, value in fields.items():
        lane[key] = value


def process_lane(
    lane: MutableMapping[str, Any],
    *,
    repo_root: Path | str,
    lanes_parent: Path | str | None = None,
    goal_base_dir: Path | str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    delegate_fn: DelegateFn | None = None,
    run_gates_fn: RunGatesFn | None = None,
    audit_emit: AuditEmitFn | None = None,
    verdicts_dir: Path | str | None = None,
    dry_run: bool = False,
    interrupt_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Dispatch one lane through delegate → empty-retry → gates.

    Returns a summary dict with final ``status``, ``attempts``, and optional
    ``blocked_reason`` / ``gate_report`` / ``result``. Mutates *lane* in place.
    Never merges, never pushes.
    """
    if delegate_fn is None:
        # Lazy import so unit tests that only exercise queue helpers need not
        # pull in the full runner (and so the module stays freestanding).
        from .runner import delegate as _delegate  # type: ignore

        delegate_fn = _delegate
    gates_fn = run_gates_fn or default_run_gates

    lane_id = lane_identity(lane)
    lane_name = str(lane.get("lane") or lane_id)
    base_ref = str(lane.get("base_ref") or "origin/dev")
    goal_file = lane.get("goal_file")
    profile = str(lane.get("profile") or lane.get("kind") or DEFAULT_GATE_PROFILE)
    max_attempts = max(1, int(max_attempts if max_attempts is not None else DEFAULT_MAX_ATTEMPTS))
    # Allow per-lane override.
    if lane.get("max_attempts") is not None:
        try:
            max_attempts = max(1, int(lane["max_attempts"]))
        except (TypeError, ValueError):
            pass

    started = time.monotonic()
    _set_lane_fields(lane, status=STATUS_IN_FLIGHT, attempts=int(lane.get("attempts") or 0))
    emit_state_change(
        lane=lane_name,
        status=STATUS_IN_FLIGHT,
        outcome="started",
        base_ref=base_ref,
        audit_emit=audit_emit,
    )

    if dry_run:
        _set_lane_fields(lane, status=STATUS_QUEUED)
        return {
            "ok": True,
            "dry_run": True,
            "lane_id": lane_id,
            "lane": lane_name,
            "status": STATUS_QUEUED,
            "plan": f"would dispatch {lane_name} goal_file={goal_file!r} profile={profile!r}",
        }

    # Goal file is required; missing → block without spawn.
    if not goal_file:
        return _block_lane(
            lane,
            reason="GOAL_FILE_MISSING",
            message="lane has no goal_file",
            base_ref=base_ref,
            audit_emit=audit_emit,
            started=started,
            verdicts_dir=verdicts_dir,
        )

    base = Path(goal_base_dir) if goal_base_dir else Path(repo_root)
    try:
        goal_text = load_goal_file(str(goal_file), base_dir=base)
    except DriverError as exc:
        return _block_lane(
            lane,
            reason=exc.code,
            message=exc.message,
            base_ref=base_ref,
            audit_emit=audit_emit,
            started=started,
            verdicts_dir=verdicts_dir,
        )

    last_result: dict[str, Any] | None = None
    attempts_used = 0

    for attempt_idx in range(max_attempts):
        if interrupt_check is not None and interrupt_check():
            raise InterruptRequested()

        attempts_used = attempt_idx + 1
        nudged_goal = build_goal_with_nudge(goal_text, attempt_idx)
        _set_lane_fields(lane, attempts=attempts_used)

        try:
            result = delegate_fn(
                goal=nudged_goal,
                lane=lane_name,
                repo_root=str(repo_root),
                base_ref=base_ref,
                lanes_parent=str(lanes_parent) if lanes_parent else None,
            )
        except InterruptRequested:
            raise
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            last_result = {
                "ok": False,
                "error": "DELEGATE_EXCEPTION",
                "message": f"{type(exc).__name__}: {exc}",
                "changed_files": [],
                "commits": [],
            }
            # Treat unexpected exception as a hard block (not empty-retry).
            return _block_lane(
                lane,
                reason="DELEGATE_EXCEPTION",
                message=str(exc)[:500],
                base_ref=base_ref,
                audit_emit=audit_emit,
                started=started,
                verdicts_dir=verdicts_dir,
                result=last_result,
            )

        # SIGINT/SIGTERM during a long delegate: re-queue rather than gating.
        if interrupt_check is not None and interrupt_check():
            raise InterruptRequested()

        if not isinstance(result, dict):
            result = {
                "ok": False,
                "error": "DELEGATE_BAD_RESULT",
                "message": f"delegate returned {type(result).__name__}",
                "changed_files": [],
                "commits": [],
            }
        last_result = result

        # Hard prepare/spawn failures → block immediately (no empty-retry).
        err = result.get("error")
        if err and not result.get("ok"):
            # Empty-ish failures that still mean "executor ran but wrote nothing"
            # should fall through to empty detection; known hard codes block.
            hard_codes = {
                "GOAL_FILE_MISSING",
                "BASE_UNREACHABLE",
                "WORKTREE_EXISTS_CONFLICT",
                "WORKTREE_CREATE_FAILED",
                "WORKTREE_INSIDE_REPO",
                "GIT_MISSING",
                "LANE_RESERVED",
                "LANE_INVALID",
                "LANE_EMPTY",
                "GROK_MISSING",
                "ANCHOR_MISSING",
                "BASE_DIRTY",
                "BASE_STATUS_FAILED",
            }
            if str(err) in hard_codes:
                return _block_lane(
                    lane,
                    reason=str(err),
                    message=str(result.get("message") or err)[:500],
                    base_ref=base_ref,
                    audit_emit=audit_emit,
                    started=started,
                    verdicts_dir=verdicts_dir,
                    result=result,
                )

        if is_empty_result(result):
            emit_state_change(
                lane=lane_name,
                status=STATUS_IN_FLIGHT,
                outcome="empty_result",
                error="EMPTY_RESULT",
                base_ref=base_ref,
                changed_file_count=0,
                audit_emit=audit_emit,
            )
            # Retry if attempts remain.
            if attempt_idx + 1 < max_attempts:
                continue
            # Exhausted.
            return _block_lane(
                lane,
                reason="EMPTY_RESULT",
                message=(
                    f"executor returned empty result after {attempts_used} attempt(s) "
                    f"(no changed_files, no commits)"
                ),
                base_ref=base_ref,
                audit_emit=audit_emit,
                started=started,
                verdicts_dir=verdicts_dir,
                result=result,
            )

        # Non-empty — stop retrying this lane.
        break
    else:
        # Loop completed without break (all attempts empty) — handled above.
        pass

    assert last_result is not None
    result = last_result
    changed = list(result.get("changed_files") or [])
    worktree = result.get("worktree_path")

    if interrupt_check is not None and interrupt_check():
        raise InterruptRequested()

    # Run gates for the lane profile.
    gate_report: dict[str, Any]
    if worktree:
        try:
            gate_report = gates_fn(worktree, profile)
        except Exception as exc:  # noqa: BLE001
            gate_report = {
                "ok": False,
                "error": "GATE_EXCEPTION",
                "message": f"{type(exc).__name__}: {exc}",
                "results": [],
                "summary": {"total": 0, "passed": 0, "failed": 0},
            }
    else:
        # No worktree path in result — cannot gate; treat as gates_failed.
        gate_report = {
            "ok": False,
            "error": "GATE_NO_WORKTREE",
            "message": "delegate result had no worktree_path; cannot run gates",
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0},
        }

    gates_ok = bool(gate_report.get("ok"))
    elapsed = time.monotonic() - started

    if gates_ok:
        final_status = STATUS_READY_FOR_MERGE
        outcome = "ready_for_merge"
        _set_lane_fields(
            lane,
            status=final_status,
            attempts=attempts_used,
            blocked_reason=None,
            gate_ok=True,
            changed_file_count=len(changed),
        )
    else:
        final_status = STATUS_GATES_FAILED
        outcome = "gates_failed"
        reason = str(gate_report.get("error") or "GATES_FAILED")
        _set_lane_fields(
            lane,
            status=final_status,
            attempts=attempts_used,
            blocked_reason=reason,
            gate_ok=False,
            changed_file_count=len(changed),
        )

    emit_state_change(
        lane=lane_name,
        status=final_status,
        outcome=outcome,
        error=None if gates_ok else str(gate_report.get("error") or "GATES_FAILED"),
        base_ref=base_ref,
        changed_file_count=len(changed),
        worktree_path=str(worktree) if worktree else None,
        elapsed_seconds=elapsed,
        audit_emit=audit_emit,
    )

    summary = {
        "ok": gates_ok,
        "lane_id": lane_id,
        "lane": lane_name,
        "status": final_status,
        "attempts": attempts_used,
        "changed_files": changed,
        "commits": list(result.get("commits") or []),
        "gate_report": gate_report,
        "result": result,
        "elapsed_seconds": elapsed,
        "blocked_reason": None if gates_ok else str(gate_report.get("error") or "GATES_FAILED"),
    }
    if verdicts_dir is not None:
        try:
            write_lane_verdict(verdicts_dir, lane_id, summary)
        except OSError as exc:
            logger.warning("write_lane_verdict failed: %s", exc)
    return summary


def _block_lane(
    lane: MutableMapping[str, Any],
    *,
    reason: str,
    message: str,
    base_ref: str,
    audit_emit: AuditEmitFn | None,
    started: float,
    verdicts_dir: Path | str | None,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark *lane* blocked, audit, optionally write verdict, return summary."""
    attempts = int(lane.get("attempts") or 0)
    lane_id = lane_identity(lane)
    lane_name = str(lane.get("lane") or lane_id)
    elapsed = time.monotonic() - started
    _set_lane_fields(
        lane,
        status=STATUS_BLOCKED,
        blocked_reason=reason,
        blocked_message=message[:500],
        attempts=attempts,
    )
    emit_state_change(
        lane=lane_name,
        status=STATUS_BLOCKED,
        outcome="blocked",
        error=reason,
        base_ref=base_ref,
        elapsed_seconds=elapsed,
        audit_emit=audit_emit,
    )
    summary: dict[str, Any] = {
        "ok": False,
        "lane_id": lane_id,
        "lane": lane_name,
        "status": STATUS_BLOCKED,
        "attempts": attempts,
        "blocked_reason": reason,
        "message": message[:500],
        "result": dict(result) if result else None,
        "elapsed_seconds": elapsed,
    }
    if verdicts_dir is not None:
        try:
            write_lane_verdict(verdicts_dir, lane_id, summary)
        except OSError as exc:
            logger.warning("write_lane_verdict failed: %s", exc)
    return summary


# ---------------------------------------------------------------------------
# Main driver loop
# ---------------------------------------------------------------------------


def run_driver(
    *,
    queue_path: str | Path,
    repo_root: Path | str,
    lanes_parent: Path | str | None = None,
    goal_base_dir: Path | str | None = None,
    verdicts_dir: Path | str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_lanes: int | None = None,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    dry_run: bool = False,
    delegate_fn: DelegateFn | None = None,
    run_gates_fn: RunGatesFn | None = None,
    audit_emit: AuditEmitFn | None = None,
    sleep_fn: SleepFn | None = None,
    alive_check: AliveCheck | None = None,
    pid: int | None = None,
    install_signals: bool = True,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    """Run the unattended loop until the queue is drained or limits hit.

    Serializes on a pid-bearing queue lock. Exactly one lane in flight.
    On SIGINT/SIGTERM the current lane is returned to ``queued`` and the lock
    is released (exit code ``EXIT_INTERRUPTED``).

    Never merges, never pushes — furthest status is ``ready_for_merge``.
    """
    qpath = Path(queue_path)
    sleep = sleep_fn or time.sleep
    out_stream = stream if stream is not None else sys.stdout

    # Interrupt flag shared with signal handlers and process_lane.
    state: dict[str, Any] = {
        "interrupted": False,
        "current_lane_id": None,
        "lock_held": False,
    }

    def _interrupt_check() -> bool:
        return bool(state["interrupted"])

    def _handle_signal(signum: int, _frame: Any) -> None:
        # Defer bookkeeping to the main loop — only flip the flag here.
        state["interrupted"] = True
        logger.info("driver received signal %s; will release lock after bookkeeping", signum)

    prev_handlers: dict[int, Any] = {}
    if install_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                prev_handlers[sig] = signal.signal(sig, _handle_signal)
            except (ValueError, OSError):
                # Not in main thread or signal not supported — skip.
                pass

    summary: dict[str, Any] = {
        "ok": True,
        "processed": [],
        "skipped": 0,
        "ready_for_merge": 0,
        "gates_failed": 0,
        "blocked": 0,
        "errors": [],
        "message": "",
        "exit_code": EXIT_OK,
    }

    try:
        # Load queue first so corrupt JSON fails before taking the lock? Spec:
        # corrupt queue → exit non-zero without touching lanes. Lock after load
        # is fine; also lock first so two drivers don't both parse. We lock first
        # so QUEUE_LOCKED is observed even on a corrupt file race.
        try:
            acquire_queue_lock(qpath, pid=pid, alive_check=alive_check)
            state["lock_held"] = True
        except DriverError as exc:
            summary["ok"] = False
            summary["errors"].append({"code": exc.code, "message": exc.message})
            summary["message"] = exc.message
            summary["exit_code"] = exc.exit_code
            summary["error"] = exc.code
            return summary

        try:
            queue = load_queue(qpath)
        except DriverError as exc:
            summary["ok"] = False
            summary["errors"].append({"code": exc.code, "message": exc.message})
            summary["message"] = exc.message
            summary["exit_code"] = exc.exit_code
            summary["error"] = exc.code
            return summary

        if dry_run:
            plan = []
            for item in iter_queued_lanes(queue):
                plan.append(
                    {
                        "id": lane_identity(item),
                        "lane": item.get("lane"),
                        "goal_file": item.get("goal_file"),
                        "base_ref": item.get("base_ref") or "origin/dev",
                        "profile": item.get("profile") or item.get("kind") or DEFAULT_GATE_PROFILE,
                    }
                )
            summary["dry_run"] = True
            summary["plan"] = plan
            summary["message"] = (
                f"dry-run: {len(plan)} lane(s) queued; nothing spawned"
                if plan
                else "nothing to do"
            )
            _write_line(out_stream, json.dumps(summary, ensure_ascii=False, indent=2))
            return summary

        processed_count = 0
        vdir = Path(verdicts_dir) if verdicts_dir else (qpath.parent / "verdicts")
        gbase = Path(goal_base_dir) if goal_base_dir else Path(repo_root)

        while True:
            if state["interrupted"]:
                raise InterruptRequested()

            if max_lanes is not None and processed_count >= max_lanes:
                summary["message"] = f"max_lanes={max_lanes} reached"
                break

            nxt = select_next_lane(queue)
            if nxt is None:
                if processed_count == 0:
                    summary["message"] = "nothing to do"
                else:
                    summary["message"] = summary.get("message") or "queue drained"
                break

            lane_id = lane_identity(nxt)
            state["current_lane_id"] = lane_id
            idx = find_lane_index(queue, lane_id)
            # Work on the live record inside the queue list.
            live: MutableMapping[str, Any]
            if idx >= 0:
                live = queue["lanes"][idx]
            else:
                live = nxt

            try:
                lane_summary = process_lane(
                    live,
                    repo_root=repo_root,
                    lanes_parent=lanes_parent,
                    goal_base_dir=gbase,
                    max_attempts=max_attempts,
                    delegate_fn=delegate_fn,
                    run_gates_fn=run_gates_fn,
                    audit_emit=audit_emit,
                    verdicts_dir=vdir,
                    dry_run=False,
                    interrupt_check=_interrupt_check,
                )
            except InterruptRequested:
                # Return current lane to queued and re-raise for outer handler.
                _set_lane_fields(
                    live,
                    status=STATUS_QUEUED,
                    blocked_reason=None,
                )
                # Keep attempts so a restart continues the counter.
                save_queue(qpath, queue)
                raise

            # Persist after every lane so a crash mid-loop loses at most one.
            save_queue(qpath, queue)
            summary["processed"].append(
                {
                    "lane_id": lane_summary.get("lane_id"),
                    "lane": lane_summary.get("lane"),
                    "status": lane_summary.get("status"),
                    "attempts": lane_summary.get("attempts"),
                    "blocked_reason": lane_summary.get("blocked_reason"),
                }
            )
            st = lane_summary.get("status")
            if st == STATUS_READY_FOR_MERGE:
                summary["ready_for_merge"] += 1
            elif st == STATUS_GATES_FAILED:
                summary["gates_failed"] += 1
            elif st == STATUS_BLOCKED:
                summary["blocked"] += 1

            processed_count += 1
            state["current_lane_id"] = None

            # Cooldown between lanes (not after the last one if queue empty —
            # cheap check: if nothing queued, skip sleep).
            if cooldown_seconds > 0 and select_next_lane(queue) is not None:
                if state["interrupted"]:
                    raise InterruptRequested()
                sleep(float(cooldown_seconds))

        if not summary["message"]:
            summary["message"] = (
                f"processed={processed_count} "
                f"ready_for_merge={summary['ready_for_merge']} "
                f"gates_failed={summary['gates_failed']} "
                f"blocked={summary['blocked']}"
            )
        return summary

    except InterruptRequested:
        summary["ok"] = False
        summary["error"] = "INTERRUPTED"
        summary["message"] = "interrupted; current lane returned to queued; lock released"
        summary["exit_code"] = EXIT_INTERRUPTED
        return summary
    finally:
        if state.get("lock_held"):
            release_queue_lock(qpath, pid=pid)
            state["lock_held"] = False
        if install_signals:
            for sig, handler in prev_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass


def _write_line(stream: TextIO, text: str) -> None:
    stream.write(text + "\n")
    stream.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI for ``python -m grok_delegate.driver``."""
    p = argparse.ArgumentParser(
        prog="grok_delegate.driver",
        description=(
            "Unattended lane driver: read a JSON queue, dispatch one lane at a "
            "time through runner.delegate, retry empty results, run gates, mark "
            "ready_for_merge. Never merges, never pushes."
        ),
    )
    p.add_argument(
        "--queue",
        default=os.environ.get(ENV_QUEUE_PATH),
        help=f"path to queue JSON (env {ENV_QUEUE_PATH})",
    )
    p.add_argument(
        "--repo-root",
        default=os.environ.get(ENV_REPO_ROOT) or os.getcwd(),
        help=f"repository root (env {ENV_REPO_ROOT})",
    )
    p.add_argument(
        "--lanes-parent",
        default=os.environ.get(ENV_LANES_PARENT),
        help=f"external lanes parent directory (env {ENV_LANES_PARENT})",
    )
    p.add_argument(
        "--verdicts-dir",
        default=os.environ.get(ENV_VERDICTS_DIR),
        help=f"directory for per-lane verdict files (env {ENV_VERDICTS_DIR})",
    )
    p.add_argument(
        "--goal-base-dir",
        default=None,
        help="base directory for resolving relative goal_file paths (default: repo-root)",
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"empty-result retries per lane (default {DEFAULT_MAX_ATTEMPTS})",
    )
    p.add_argument(
        "--max-lanes",
        type=int,
        default=None,
        help="stop after processing this many lanes (default: no limit)",
    )
    p.add_argument(
        "--cooldown",
        type=float,
        default=DEFAULT_COOLDOWN_SECONDS,
        help="seconds to sleep between lanes (default 0)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and spawn nothing",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m grok_delegate.driver``."""
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.queue:
        _write_line(sys.stderr, "error: --queue is required (or set GROK_DELEGATE_QUEUE)")
        return EXIT_ERROR

    result = run_driver(
        queue_path=args.queue,
        repo_root=args.repo_root,
        lanes_parent=args.lanes_parent,
        goal_base_dir=args.goal_base_dir,
        verdicts_dir=args.verdicts_dir,
        max_attempts=args.max_attempts,
        max_lanes=args.max_lanes,
        cooldown_seconds=args.cooldown,
        dry_run=args.dry_run,
    )
    # Human-readable one-liner + JSON summary on stdout.
    _write_line(sys.stdout, result.get("message") or "")
    _write_line(
        sys.stdout,
        json.dumps(
            {
                k: result[k]
                for k in (
                    "ok",
                    "error",
                    "processed",
                    "ready_for_merge",
                    "gates_failed",
                    "blocked",
                    "dry_run",
                    "plan",
                    "message",
                )
                if k in result
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return int(result.get("exit_code", EXIT_OK if result.get("ok") else EXIT_ERROR))


if __name__ == "__main__":
    raise SystemExit(main())
