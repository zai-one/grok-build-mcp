"""Token-economy helpers for host agents (Claude, Cursor, etc.).

Design goal: the *host* model should send short instructions and receive
compact receipts, while the heavy coding work runs on the local/VPS Grok CLI.
No OAuth or API keys are stored here.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

# Defaults applied when GROK_DELEGATE_ECONOMY is on and the client omits fields.
ECONOMY_DEFAULT_MAX_TURNS = 12
ECONOMY_DEFAULT_TIMEOUT_SECONDS = 600
ECONOMY_DEFAULT_REASONING = "low"
ECONOMY_MAX_SUMMARY = 1_500
ECONOMY_MAX_EVENTS = 4
ECONOMY_MAX_CHANGED_FILES = 24
ECONOMY_MAX_UNIFIED_DIFF = 16_384
#: What one compact poll may cost the host, whole. The per-field caps above bound
#: each field on its own and sum to well over this; the record is fitted to it
#: after assembly so the promise is about the thing the host actually pays for.
ECONOMY_MAX_RECORD = 16_384

_TRUE = frozenset({"1", "true", "yes", "on"})


def economy_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("GROK_DELEGATE_ECONOMY", "")).strip().lower() in _TRUE


def compact_poll_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    raw = str(source.get("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "")).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in _TRUE:
        return True
    try:
        from .session import session_compact_active

        if session_compact_active():
            return True
    except Exception:
        pass
    return economy_enabled(source)


def default_reasoning_effort(env: Mapping[str, str] | None = None) -> str:
    """Effort the bridge picks when the caller named none.

    ``low`` suits the cheap-worker loop this module was written for, but it is a
    default, not a policy: an operator paying for a strong model wants that model
    thinking, and had no way to say so before.
    """
    from .guard import configured_reasoning_effort

    source = env if env is not None else os.environ
    return configured_reasoning_effort(source) or ECONOMY_DEFAULT_REASONING


def default_max_turns(env: Mapping[str, str] | None = None) -> int:
    """Turn budget the bridge picks when the caller named none.

    Raising effort without raising this trades one ceiling for another: the model
    reasons harder and still runs out of turns mid-task.
    """
    from .guard import configured_max_turns

    source = env if env is not None else os.environ
    return configured_max_turns(source) or ECONOMY_DEFAULT_MAX_TURNS


def apply_task_economy_defaults(task: dict[str, Any]) -> dict[str, Any]:
    """Fill missing budget fields with economy defaults (never override client)."""
    if not economy_enabled():
        return task
    out = dict(task)
    if "max_turns" not in out:
        out["max_turns"] = default_max_turns()
    if "timeout_seconds" not in out:
        out["timeout_seconds"] = ECONOMY_DEFAULT_TIMEOUT_SECONDS
    if "reasoning_effort" not in out:
        out["reasoning_effort"] = default_reasoning_effort()
    return out


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def _clip_bytes(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    clipped = encoded[: max(0, limit)].decode("utf-8", errors="replace")
    return clipped + "\n…(truncated)"


def compact_job_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Shrink a job/receipt for host-agent context windows."""
    if not compact_poll_enabled():
        return dict(record)
    keep_keys = (
        "ok",
        "job_id",
        "state",
        "status",
        "transport",
        "lane",
        "correlation_id",
        "blocked_reason",
        "error",
        "error_code",
        "worktree_path",
        "branch",
        "summary",
        "changed_files",
        "full_changed_files",
        "artifacts",
        "tests",
        "tests_skipped_reason",
        "denied_tool_calls",
        "lane_commit",
        "diffstat",
        "unified_diff",
        "started_at",
        "finished_at",
        "events",
    )
    # A finished job keeps its verdict inside `result`, while the envelope only
    # carries state. Compacting the envelope alone produced the worst possible
    # receipt -- state "error" next to error null -- so lift the fields that say
    # *why* before shrinking, without letting them overwrite an envelope value.
    record = dict(record)
    nested = record.get("result")
    if isinstance(nested, Mapping):
        # `full_changed_files` was in keep_keys and not in this list, so a
        # finished job's compact poll dropped it entirely -- and it is the field
        # that separates "what this run did" from "what is sitting in the lane".
        for key in ("status", "blocked_reason", "summary", "worktree_path", "branch",
                    "changed_files", "full_changed_files", "artifacts", "tests",
                    "tests_skipped_reason", "denied_tool_calls", "lane_commit",
                    "diffstat", "unified_diff"):
            if record.get(key) in (None, "", [], {}) and nested.get(key) not in (None, "", [], {}):
                record[key] = nested[key]

    out: dict[str, Any] = {}
    for key in keep_keys:
        if key not in record:
            continue
        value = record[key]
        if key == "summary":
            out[key] = _clip(value, ECONOMY_MAX_SUMMARY)
        elif key in {"changed_files", "full_changed_files", "artifacts"} and isinstance(value, list):
            # Say what was left out. Eighty changed files arriving as twenty-four
            # with no count reads as "that is all this job touched", which is the
            # one thing a reviewer must not be wrong about.
            out[key] = value[:ECONOMY_MAX_CHANGED_FILES]
            if len(value) > ECONOMY_MAX_CHANGED_FILES:
                out[key + "_omitted"] = len(value) - ECONOMY_MAX_CHANGED_FILES
                out[key + "_total"] = len(value)
        elif key == "events" and isinstance(value, list):
            out[key] = value[-ECONOMY_MAX_EVENTS:]
        elif key == "tests" and isinstance(value, list):
            slim = []
            for item in value[:16]:
                if not isinstance(item, Mapping):
                    continue
                # `outcome` travels or the distinction it exists for dies here:
                # a compact poll that kept only `passed` would hand the host
                # `passed: None` for a run that never happened, which is the
                # same ambiguity in a new place. `passed` is copied only when
                # the row has one.
                row = {
                    "command": _clip(item.get("command"), 200),
                    "returncode": item.get("returncode"),
                    "source": item.get("source"),
                }
                for field in ("outcome", "not_run_reason"):
                    if item.get(field) is not None:
                        row[field] = item[field]
                if "passed" in item:
                    row["passed"] = item["passed"]
                slim.append(row)
            out[key] = slim
        elif key == "diffstat":
            out[key] = _clip(value, 800)
        elif key == "unified_diff":
            out[key] = _clip_bytes(value, ECONOMY_MAX_UNIFIED_DIFF)
        else:
            out[key] = value
    out["economy_compact"] = True
    return _fit_record_budget(out, ECONOMY_MAX_RECORD)


#: field -> the smallest length worth keeping, in the order a reader can most
#: afford to lose them. Events are already bounded and purely informational, a
#: summary is the agent's prose about work the diff shows anyway, and the diff
#: is the last thing to give because it is what the host came for.
_TRIM_ORDER: tuple[tuple[str, int], ...] = (
    ("events", 1), ("summary", 300), ("diffstat", 200),
    ("unified_diff", 512), ("full_changed_files", 4), ("changed_files", 4),
)


def wire_size(value: Any) -> int:
    """What this payload actually costs on the wire, in bytes.

    Every stdio and HTTP write in `server.py` serialises with
    `ensure_ascii=False` and encodes UTF-8, so that is the only measurement the
    budget may use. Counting characters of the escaped form inflates Cyrillic
    roughly threefold and fails a poll that was inside the promise; counting
    characters of the unescaped form undercounts it, because Cyrillic is two
    bytes each. A live audit run tripped over both directions of that in one
    afternoon.
    """
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def fit_poll_budget(record: dict[str, Any], cap: int = 0) -> dict[str, Any]:
    """Hold one poll under the budget whether or not compaction is on.

    `compact_job_record` only runs for hosts that asked for it, so the typed
    path had no ceiling at all: a live audit came back at 17,725 characters in a
    single poll against a promise of 16 KiB. The fat fields sit under `result`
    there and at the top level in compact mode, so both are fitted.
    """
    cap = cap or ECONOMY_MAX_RECORD
    if wire_size(record) <= cap:
        return record
    nested = record.get("result")
    if isinstance(nested, dict):
        # Budget the nested receipt against what the envelope leaves it.
        envelope = wire_size({k: v for k, v in record.items() if k != "result"})
        _fit_record_budget(nested, max(1_024, cap - envelope))
    return _fit_record_budget(record, cap)


def _fit_record_budget(out: dict[str, Any], cap: int) -> dict[str, Any]:
    """Hold the whole record under one budget, not each field under its own.

    Per-field caps do not add up to a promise. ``ECONOMY_MAX_UNIFIED_DIFF`` alone
    is the entire poll budget, so a job with a large diff produced a "compact"
    record of 22 KB while every individual cap was respected -- and the host paid
    for it on the poll that mattered most, the last one.

    Trimming is ordered by what a reader can most afford to lose, and it is never
    silent: ``economy_trimmed`` names each field that gave way, because a receipt
    that quietly drops half a diff is worse than one that admits it.
    """
    def size() -> int:
        return wire_size(out)

    if size() <= cap:
        return out

    trimmed: list[str] = []
    for key, floor in _TRIM_ORDER:
        for _ in range(8):  # halving converges; the bound stops a pathological value
            over = size() - cap
            if over <= 0:
                break
            value = out.get(key)
            if isinstance(value, str) and len(value) > floor:
                # Same marker the per-field clip uses: one truncation convention
                # on the wire, and `economy_trimmed` to say the budget caused it.
                keep = max(floor, len(value) - over - 48)
                out[key] = value[:keep].removesuffix("\n…(truncated)") + "\n…(truncated)"
            elif isinstance(value, list) and len(value) > floor:
                out[key] = value[: max(floor, len(value) // 2)]
            else:
                break
            if key not in trimmed:
                trimmed.append(key)
        if size() <= cap:
            break

    if trimmed:
        out["economy_trimmed"] = trimmed
        out["economy_budget_chars"] = cap
    return out


def economy_playbook() -> dict[str, Any]:
    """Short host-agent playbook: save Claude/Cursor tokens by offloading work."""
    return {
        "ok": True,
        "economy": True,
        "prerequisite": (
            "Grok CLI must be installed and `grok login` completed on this host "
            "before any tool can do useful work. This MCP is only a bridge."
        ),
        "goal": (
            "Keep the host model thin: plan briefly, delegate coding to Grok on "
            "this machine/VPS, poll compact receipts, merge as a human."
        ),
        "do": [
            "Confirm grok CLI + login (self-test auth present) before other tools.",
            "Call grok_agent_status once per session (not every turn).",
            "Prefer grok_agent_consult / grok_agent_review for Q&A and critique.",
            "For writes: one focused grok_agent_execute with a tight objective, "
            "expected_artifacts, and 1–3 cheap test_commands.",
            "Poll with grok_agent_poll using job_id; do not re-send the full goal.",
            "Read summary + changed_files + diffstat + bounded unified_diff + tests + worktree_path.",
            "Never request full event transcripts or raw tool dumps unless debugging.",
            "Set max_turns low (8–16) and reasoning_effort low|medium unless stuck.",
            "One job at a time; cancel stale jobs instead of stacking.",
        ],
        "dont": [
            "Don't paste large source trees into the objective — point at paths.",
            "Don't use execute for questions (use consult).",
            "Don't set max_turns near the hard cap (60) for routine work.",
            "Don't put OAuth/API keys or GROK_AGENT_SECRET in MCP config.",
            "Don't push/merge from the agent — human reviews the grok/* branch.",
        ],
        "host_prompts": {
            "consult": "Answer in ≤12 bullets. No code dumps unless asked.",
            "execute": (
                "Implement only the listed artifacts. Stop when tests pass. "
                "Do not refactor unrelated files."
            ),
            "poll": "Return status, blocked_reason, changed_files, test pass flags only.",
        },
        "env": {
            "GROK_DELEGATE_ECONOMY": "1 enables lower default max_turns/timeout/reasoning",
            "GROK_DELEGATE_ECONOMY_COMPACT_POLL": "1 forces compact poll payloads",
        },
        "vps": (
            "Run this MCP on a VPS with Grok CLI logged in; connect Claude via "
            "stdio over SSH or bearer HTTP JSON-RPC behind TLS (not Streamable HTTP). "
            "Host tokens buy orchestration only; "
            "Grok does the long coding loop."
        ),
        "disclaimer": (
            "Unofficial community project. Not affiliated with, endorsed by, or "
            "supported by xAI, Grok, Anthropic, OpenAI, or Codex."
        ),
    }
