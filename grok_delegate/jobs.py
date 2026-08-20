"""Detached lane jobs: start a delegation, poll for its result (R6).

Why this exists: a real lane runs for minutes, while an MCP client gives up on a
synchronous tool call in seconds. The call is then killed mid-flight and the lane
worktree is left prepared but empty — indistinguishable from "the executor did
nothing" unless the caller inspects git. Measured repeatedly on this host.

``start_job`` runs the SAME guarded ``delegate()`` path on a daemon thread and
returns immediately with a job id; ``get_job``/``list_jobs`` report progress. No new
capability is granted: the permission profile, worktree isolation and the no
push/merge guarantee are unchanged, and nothing here executes a command itself.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

try:
    from . import jobs_store  # type: ignore[no-redef]
except ImportError:  # flat import when package dir is on sys.path
    import jobs_store  # type: ignore

logger = logging.getLogger(__name__)

# Job states.
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
# R7-D: a rehydrated record whose owning process is gone comes back in this state
# (jobs_store downgrades it with a STALE_RUNNING reason). It must exist here too, or
# callers reading jobs.STATE_* cannot recognise a state this module hands them.
STATE_UNKNOWN = "unknown"

# Bounded registry: oldest finished jobs are evicted first, so a long-lived server
# process cannot grow without limit.
MAX_JOBS = 64

_JOBS: "dict[str, dict[str, Any]]" = {}
_LOCK = threading.Lock()

# Fields a running delegation may publish into its own record. A whitelist, not a
# merge: progress is reported from inside the runner, and no progress key may
# ever reach ``state``, ``result``, ``error`` or ``job_id`` — those belong to the
# job lifecycle and a stray key there would fabricate an outcome.
_PROGRESS_FIELDS = frozenset(
    {
        "phase",
        "phase_at",
        "worker_pid",
        "last_step",
        "last_step_at",
        "worktree_path",
        "reusing",
        # Round 8 typed-agent progress.  Events are bounded before they reach
        # this module; lifecycle state/result remain protected by the whitelist.
        "events",
        "transport",
        "session_id",
        "agent_pid",
        "correlation_id",
        "cancel_requested",
    }
)

# Phase of a job that has been registered but has not entered the runner yet.
PHASE_QUEUED = "queued"

# R7-D: durable location for job records. In-memory only was a real gap — a server
# restart lost every status while the work itself survived on the lane branch, so the
# pipeline went blind about lanes it had already dispatched.
_JOBS_DIR: "Path | None" = None


def configure_jobs_dir(jobs_dir: str | Path | None) -> None:
    """Point persistence at *jobs_dir* (None disables it)."""
    global _JOBS_DIR
    _JOBS_DIR = Path(jobs_dir) if jobs_dir else None


def _persist(record: "dict[str, Any]") -> None:
    """Best-effort durable write; never let persistence break a delegation."""
    if _JOBS_DIR is None:
        return
    try:
        if jobs_store.save_job(record, _JOBS_DIR):
            jobs_store.evict_on_disk(_JOBS_DIR, max_jobs=MAX_JOBS)
    except Exception:  # noqa: BLE001 — a jobs-dir problem must not fail the lane
        pass


def rehydrate_jobs(jobs_dir: str | Path | None = None) -> int:
    """Load persisted records into the registry; returns how many were restored.

    Stale ``running`` records (owning pid gone) come back as ``unknown`` with an
    explicit STALE_RUNNING reason rather than pretending to still be running.
    """
    target = Path(jobs_dir) if jobs_dir else _JOBS_DIR
    if target is None:
        return 0
    try:
        loaded = jobs_store.load_jobs(target)
    except Exception:  # noqa: BLE001
        return 0
    with _LOCK:
        for jid, rec in loaded.items():
            _JOBS.setdefault(jid, dict(rec))
    return len(loaded)


def new_job_id() -> str:
    """Opaque job id (no host or path material)."""
    return f"job-{uuid.uuid4().hex[:12]}"


def _evict_locked() -> None:
    """Drop oldest finished jobs while over capacity (caller holds the lock)."""
    if len(_JOBS) <= MAX_JOBS:
        return
    finished = [
        (rec.get("finished_at") or 0.0, jid)
        for jid, rec in _JOBS.items()
        if rec.get("state") != STATE_RUNNING
    ]
    finished.sort()
    for _, jid in finished:
        if len(_JOBS) <= MAX_JOBS:
            break
        _JOBS.pop(jid, None)


def update_job(job_id: str, fields: "dict[str, Any]") -> dict[str, Any] | None:
    """Merge whitelisted progress *fields* into a live record; return the copy.

    Why a live record needs updating at all: a dispatch spends its first minutes
    inside git with no process, no branch and no lane directory to look at, and
    ``poll`` used to answer with a pid and a start time. That is
    indistinguishable from a wedged server, and the wrong reading of it —
    "the channel is dead" — cost more debugging than the actual defect.
    """
    allowed = {k: v for k, v in fields.items() if k in _PROGRESS_FIELDS}
    if not allowed:
        return None
    to_persist: dict[str, Any] | None = None
    phase_changed = False
    with _LOCK:
        rec = _JOBS.get(str(job_id))
        if rec is None:
            return None
        # A terminal record is history; late progress must not reanimate it.
        if rec.get("state") != STATE_RUNNING:
            return dict(rec)
        phase_changed = "phase" in allowed and allowed["phase"] != rec.get("phase")
        rec.update(allowed)
        to_persist = dict(rec)
        # Serialize the durable write with lifecycle mutation.  Persisting a
        # copied running record after releasing this lock allowed a concurrent
        # terminal _finish() write to be overwritten by stale progress.
        _persist(to_persist)
    if phase_changed:
        logger.info(
            "job %s lane=%s phase=%s step=%s",
            job_id,
            to_persist.get("lane"),
            to_persist.get("phase"),
            to_persist.get("last_step"),
        )
    return to_persist


def cancel_queued_job(job_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Atomically terminalize a Future that was cancelled before it started."""
    with _LOCK:
        rec = _JOBS.get(str(job_id))
        if rec is None:
            return None
        if rec.get("state") != STATE_RUNNING or rec.get("phase") != PHASE_QUEUED:
            return dict(rec)
        rec["state"] = STATE_DONE
        rec["result"] = dict(result)
        rec["error"] = None
        rec["finished_at"] = time.time()
        rec["cancel_requested"] = True
        snapshot_record = dict(rec)
        _persist(snapshot_record)
        return snapshot_record


def _bind_progress_sink(job_id: str | None) -> None:
    """Route this thread's runner progress into *job_id* (None clears it).

    Imported lazily: jobs.py is deliberately free of runner internals, and a
    persistence-only user of this module must not pull in the spawn path.
    """
    try:
        try:
            from . import runner  # type: ignore[no-redef]
        except ImportError:  # flat import when package dir is on sys.path
            import runner  # type: ignore
    except ImportError:
        return
    if job_id is None:
        runner.set_progress_sink(None)
        return
    runner.set_progress_sink(lambda fields: update_job(job_id, fields))


def start_job(
    work: Callable[[], dict[str, Any]],
    *,
    lane: str = "",
    tool: str = "",
    job_id: str | None = None,
    thread_starter: Callable[[Callable[[], None]], None] | None = None,
) -> dict[str, Any]:
    """Run ``work()`` in the background; return the job record immediately.

    ``thread_starter`` is injectable so tests can run the work synchronously
    instead of spawning a real thread.
    """
    jid = job_id or new_job_id()
    record: dict[str, Any] = {
        "job_id": jid,
        "lane": lane,
        "tool": tool,
        "state": STATE_RUNNING,
        # Two pids, because they answer two different questions and conflating
        # them broke both. ``server_pid`` identifies the incarnation that owns
        # this record — a record from another pid cannot have a live thread here,
        # which is the only sound basis for the stale-running downgrade.
        # ``worker_pid`` is the executor itself, and it is the one an operator
        # may kill; the old single ``pid`` field held the server's, so following
        # that instruction would have taken down every other lane too.
        "server_pid": os.getpid(),
        "worker_pid": None,
        "phase": PHASE_QUEUED,
        "phase_at": time.time(),
        "last_step": None,
        "started_at": time.time(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    with _LOCK:
        _JOBS[jid] = record
        _evict_locked()
    _persist(record)

    def _finish(state: str, *, result: dict[str, Any] | None, error: Any) -> None:
        """Record a terminal state and persist it outside the lock."""
        to_persist: dict[str, Any] | None = None
        with _LOCK:
            rec = _JOBS.get(jid)
            if rec is not None:
                if result is not None:
                    rec["result"] = result
                rec["state"] = state
                rec["error"] = error
                rec["finished_at"] = time.time()
                to_persist = dict(rec)
                # See update_job(): durable ordering must match in-memory
                # lifecycle ordering, especially at the running->terminal edge.
                _persist(to_persist)

    def _run() -> None:
        _bind_progress_sink(jid)
        try:
            result = work()
            _finish(
                # A cancellation receipt is a successfully processed terminal
                # lifecycle outcome, not a transport crash.
                STATE_DONE
                if result.get("ok") or result.get("status") == "cancelled"
                else STATE_ERROR,
                result=result,
                error=result.get("error"),
            )
        except BaseException as exc:  # noqa: BLE001 — never lose a job to a raise
            _finish(STATE_ERROR, result=None, error=f"{type(exc).__name__}: {exc}")
        finally:
            _bind_progress_sink(None)

    # The record is already `running` by the time the work is handed off, so a
    # handoff that fails leaves a job that never finishes: `_evict_locked` drops
    # only terminal records, `LANE_BUSY` keeps seeing it, and cancel answers
    # JOB_NOT_OWNED because no cancel event was ever registered. It survives
    # until the process restarts. Reproduced by handing `start_job` a starter
    # that raises -- the executor refusing after `shutdown_runtime` does exactly
    # this.
    try:
        if thread_starter is not None:
            thread_starter(_run)
        else:
            threading.Thread(target=_run, name=f"grok-delegate-{jid}", daemon=True).start()
    except BaseException as exc:
        _finish(
            STATE_ERROR,
            result=None,
            error=f"{type(exc).__name__}: {exc}"[:400],
        )
        raise

    return snapshot(jid) or dict(record)


def forget_job(job_id: str) -> bool:
    """Drop a record entirely. Only for tombstones nothing can finish.

    A record rehydrated from a dead server incarnation reads as ``unknown``: no
    thread owns it and no path leads to a terminal state, so it sat in the way
    of retrying the same packet until eviction happened to reach it.
    """
    with _LOCK:
        return _JOBS.pop(str(job_id), None) is not None


def _with_elapsed(rec: "dict[str, Any]") -> "dict[str, Any]":
    """Add wall-clock ``elapsed_s`` (and ``phase_elapsed_s`` while running).

    Computed at read time rather than stored: the caller wants to know how long
    this has been going *now*, and a number frozen at write time answers a
    question nobody asked.
    """
    out = dict(rec)
    started = out.get("started_at")
    if isinstance(started, (int, float)):
        end = out.get("finished_at")
        end = end if isinstance(end, (int, float)) else time.time()
        out["elapsed_s"] = round(max(0.0, end - started), 3)
    if out.get("state") == STATE_RUNNING:
        phase_at = out.get("phase_at")
        if isinstance(phase_at, (int, float)):
            out["phase_elapsed_s"] = round(max(0.0, time.time() - phase_at), 3)
    return out


def snapshot(job_id: str) -> dict[str, Any] | None:
    """Copy of one job record, or None when unknown."""
    with _LOCK:
        rec = _JOBS.get(str(job_id))
        return _with_elapsed(rec) if rec is not None else None


def get_job(job_id: str) -> dict[str, Any] | None:
    """Alias of :func:`snapshot` for call-site readability."""
    return snapshot(job_id)


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first job summaries without result payloads."""
    with _LOCK:
        records = sorted(
            _JOBS.values(),
            key=lambda r: r.get("started_at") or 0.0,
            reverse=True,
        )
        out: list[dict[str, Any]] = []
        for rec in records[: max(0, int(limit))]:
            summary = _with_elapsed(rec)
            out.append(
                {
                    "job_id": summary.get("job_id"),
                    "lane": summary.get("lane"),
                    "tool": summary.get("tool"),
                    "state": summary.get("state"),
                    "phase": summary.get("phase"),
                    "started_at": summary.get("started_at"),
                    "finished_at": summary.get("finished_at"),
                    "elapsed_s": summary.get("elapsed_s"),
                    "error": summary.get("error"),
                }
            )
        return out


def reset_jobs_for_tests() -> None:
    """Clear the registry (tests only)."""
    with _LOCK:
        _JOBS.clear()


__all__ = [
    "MAX_JOBS",
    "PHASE_QUEUED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_RUNNING",
    "STATE_UNKNOWN",
    "configure_jobs_dir",
    "get_job",
    "list_jobs",
    "new_job_id",
    "rehydrate_jobs",
    "reset_jobs_for_tests",
    "snapshot",
    "start_job",
    "update_job",
]
