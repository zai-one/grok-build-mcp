"""Round report: turn a finished unattended round into shareable text (R7-H).

Why this exists: an unattended loop is only useful if a human can read what happened
afterwards without re-deriving it from git and job files. And because the report is meant
to be shared, it must never carry the material that would make sharing unsafe — goal text,
absolute host paths, or unbounded gate output.

Determinism is a feature: the same input produces byte-identical output (lanes sorted by id,
no clock read inside the functions), so two rounds can be diffed against each other.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

# Bound on gate output carried into a report (chars, tail kept).
MAX_GATE_TAIL_CHARS = 500

GATE_OK = "ok"
GATE_FAILED = "failed"
GATE_ABSENT = "absent"

STATUS_READY_FOR_MERGE = "ready_for_merge"


def goal_fingerprint(goal: str | None) -> dict[str, Any]:
    """Length + short hash only — the goal text itself must never be reported."""
    text = goal or ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return {"goal_chars": len(text), "goal_sha256_8": digest}


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _count(value: Any) -> int:
    """Length of a sequence-ish value, 0 for anything else (never raises)."""
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return 0


def safe_relative_path(value: Any, repo_root: str | Path | None) -> str:
    """Relativize a path against *repo_root*, else return its final component.

    Absolute host paths identify the machine and the operator's directory layout, so they
    are never reported verbatim.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    if repo_root:
        try:
            return candidate.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    if candidate.is_absolute() or (len(text) > 1 and text[1] == ":"):
        return candidate.name
    return candidate.as_posix()


def _gate_state(report: Any) -> str:
    """ok / failed / absent for one lane's gate result."""
    if not isinstance(report, Mapping):
        return GATE_ABSENT
    return GATE_OK if bool(report.get("ok")) else GATE_FAILED


def _gate_tail(report: Any) -> str:
    """One bounded output tail per lane, or empty when there is nothing to show."""
    if not isinstance(report, Mapping):
        return ""
    results = report.get("results")
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        return ""
    for entry in results:
        if not isinstance(entry, Mapping):
            continue
        tail = entry.get("output_tail")
        if isinstance(tail, str) and tail.strip():
            if len(tail) > MAX_GATE_TAIL_CHARS:
                return "…(truncated)" + tail[-MAX_GATE_TAIL_CHARS:]
            return tail
    return ""


def _duration_seconds(lane: Mapping[str, Any], jobs: Mapping[str, Any]) -> float | None:
    """Wall clock for a lane when both ends are known and ordered.

    A backwards clock (finished before started) reports None rather than a negative
    duration — a nonsense number in a report is worse than an absent one.
    """
    started = _as_float(lane.get("started_at"))
    finished = _as_float(lane.get("finished_at"))
    if started is None or finished is None:
        job_id = lane.get("job_id")
        record = jobs.get(str(job_id)) if job_id is not None else None
        if isinstance(record, Mapping):
            started = started if started is not None else _as_float(record.get("started_at"))
            finished = finished if finished is not None else _as_float(record.get("finished_at"))
    if started is None or finished is None:
        return None
    delta = finished - started
    if delta < 0:
        return None
    return round(delta, 3)


def build_round_report(
    queue: Mapping[str, Any] | None,
    jobs: Mapping[str, Any] | None,
    gate_reports: Mapping[str, Any] | None,
    *,
    repo_root: str | Path | None = None,
    generated_at: Any = None,
) -> dict[str, Any]:
    """Summarize one round. Malformed input is skipped, never raised over."""
    lanes_in = []
    if isinstance(queue, Mapping):
        raw = queue.get("lanes")
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            lanes_in = [item for item in raw if isinstance(item, Mapping)]

    jobs_map: Mapping[str, Any] = jobs if isinstance(jobs, Mapping) else {}
    gates_map: Mapping[str, Any] = gate_reports if isinstance(gate_reports, Mapping) else {}

    lanes_out: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    ready: list[str] = []

    for lane in lanes_in:
        lane_id = str(lane.get("id") or "")
        lane_name = str(lane.get("lane") or "")
        status = str(lane.get("status") or "")
        # Gate reports may be keyed by lane NAME or by lane ID — both conventions are
        # natural for a queue whose entries carry both, so accept either instead of
        # silently reporting "absent" when the caller picked the other one.
        gate_report = None
        for key in (lane_name, lane_id):
            if key and key in gates_map:
                gate_report = gates_map[key]
                break

        entry: dict[str, Any] = {
            "id": lane_id,
            "lane": lane_name,
            "status": status,
            "attempts": _as_int(lane.get("attempts")),
            "turns": lane.get("turns_used") if lane.get("turns_used") is not None else None,
            "changed_file_count": _count(lane.get("changed_files")),
            "commit_count": _count(lane.get("commits")),
            "gate": _gate_state(gate_report),
            "blocked_reason": lane.get("blocked_reason") or None,
            "duration_s": _duration_seconds(lane, jobs_map),
        }

        tail = _gate_tail(gate_report)
        if tail:
            entry["gate_tail"] = tail

        # A goal is only ever reported as a fingerprint.
        if lane.get("goal") is not None:
            entry.update(goal_fingerprint(str(lane.get("goal"))))

        worktree = lane.get("worktree_path")
        if worktree:
            entry["worktree"] = safe_relative_path(worktree, repo_root)

        verdict = lane.get("verdict")
        if isinstance(verdict, Mapping):
            entry["verdict_status"] = lane.get("verdict_status") or None
            entry["tests_added"] = _as_int(verdict.get("tests_added"))

        lanes_out.append(entry)
        totals[status] = totals.get(status, 0) + 1
        if status == STATUS_READY_FOR_MERGE and lane_name:
            ready.append(lane_name)

    lanes_out.sort(key=lambda item: (item["id"], item["lane"]))

    return {
        "generated_at": generated_at,
        "lanes": lanes_out,
        "totals": dict(sorted(totals.items())),
        "ready_for_merge": sorted(ready),
    }


def render_round_report_markdown(report: Mapping[str, Any] | None) -> str:
    """Compact Markdown view of :func:`build_round_report` output (deterministic)."""
    data: Mapping[str, Any] = report if isinstance(report, Mapping) else {}
    lanes = data.get("lanes")
    lanes_list = [item for item in lanes if isinstance(item, Mapping)] if isinstance(lanes, Sequence) else []

    lines = [
        "| id | lane | status | attempts | turns | files | commits | gate | duration_s |",
        "|---|---|---|---|---:|---:|---:|---|---:|",
    ]
    for lane in lanes_list:
        lines.append(
            "| {id} | {lane} | {status} | {attempts} | {turns} | {files} | {commits} | {gate} | {duration} |".format(
                id=lane.get("id", ""),
                lane=lane.get("lane", ""),
                status=lane.get("status", ""),
                attempts=lane.get("attempts", 0),
                turns="—" if lane.get("turns") is None else lane.get("turns"),
                files=lane.get("changed_file_count", 0),
                commits=lane.get("commit_count", 0),
                gate=lane.get("gate", GATE_ABSENT),
                duration="—" if lane.get("duration_s") is None else lane.get("duration_s"),
            )
        )

    totals = data.get("totals")
    totals_map = totals if isinstance(totals, Mapping) else {}
    totals_text = ", ".join(f"{key}: {totals_map[key]}" for key in sorted(totals_map))
    ready = data.get("ready_for_merge")
    ready_list = [str(item) for item in ready] if isinstance(ready, Sequence) and not isinstance(ready, (str, bytes)) else []

    lines.append("")
    lines.append(f"Totals: {totals_text or 'none'}")
    lines.append(f"Ready for merge: {', '.join(sorted(ready_list)) or 'none'}")
    return "\n".join(lines)


__all__ = [
    "GATE_ABSENT",
    "GATE_FAILED",
    "GATE_OK",
    "MAX_GATE_TAIL_CHARS",
    "build_round_report",
    "goal_fingerprint",
    "render_round_report_markdown",
    "safe_relative_path",
]
