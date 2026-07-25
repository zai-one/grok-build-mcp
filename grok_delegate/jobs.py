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

import threading
import time
import uuid
from typing import Any, Callable

# Job states.
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

# Bounded registry: oldest finished jobs are evicted first, so a long-lived server
# process cannot grow without limit.
MAX_JOBS = 64

_JOBS: "dict[str, dict[str, Any]]" = {}
_LOCK = threading.Lock()


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
        "started_at": time.time(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    with _LOCK:
        _JOBS[jid] = record
        _evict_locked()

    def _run() -> None:
        try:
            result = work()
            with _LOCK:
                rec = _JOBS.get(jid)
                if rec is not None:
                    rec["result"] = result
                    rec["state"] = STATE_DONE if result.get("ok") else STATE_ERROR
                    rec["error"] = result.get("error")
                    rec["finished_at"] = time.time()
        except BaseException as exc:  # noqa: BLE001 — never lose a job to a raise
            with _LOCK:
                rec = _JOBS.get(jid)
                if rec is not None:
                    rec["state"] = STATE_ERROR
                    rec["error"] = f"{type(exc).__name__}: {exc}"
                    rec["finished_at"] = time.time()

    if thread_starter is not None:
        thread_starter(_run)
    else:
        threading.Thread(target=_run, name=f"grok-delegate-{jid}", daemon=True).start()

    return snapshot(jid) or dict(record)


def snapshot(job_id: str) -> dict[str, Any] | None:
    """Copy of one job record, or None when unknown."""
    with _LOCK:
        rec = _JOBS.get(str(job_id))
        return dict(rec) if rec is not None else None


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
            out.append(
                {
                    "job_id": rec.get("job_id"),
                    "lane": rec.get("lane"),
                    "tool": rec.get("tool"),
                    "state": rec.get("state"),
                    "started_at": rec.get("started_at"),
                    "finished_at": rec.get("finished_at"),
                    "error": rec.get("error"),
                }
            )
        return out


def reset_jobs_for_tests() -> None:
    """Clear the registry (tests only)."""
    with _LOCK:
        _JOBS.clear()


__all__ = [
    "MAX_JOBS",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_RUNNING",
    "get_job",
    "list_jobs",
    "new_job_id",
    "reset_jobs_for_tests",
    "snapshot",
    "start_job",
]
