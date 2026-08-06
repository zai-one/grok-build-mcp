"""Token-economy helpers for host agents (Claude, Cursor, etc.).

Design goal: the *host* model should send short instructions and receive
compact receipts, while the heavy coding work runs on the local/VPS Grok CLI.
No OAuth or API keys are stored here.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

# Defaults applied when GROK_DELEGATE_ECONOMY is on and the client omits fields.
ECONOMY_DEFAULT_MAX_TURNS = 12
ECONOMY_DEFAULT_TIMEOUT_SECONDS = 600
ECONOMY_DEFAULT_REASONING = "low"
ECONOMY_MAX_SUMMARY = 1_500
ECONOMY_MAX_EVENTS = 4
ECONOMY_MAX_CHANGED_FILES = 24

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


def apply_task_economy_defaults(task: dict[str, Any]) -> dict[str, Any]:
    """Fill missing budget fields with economy defaults (never override client)."""
    if not economy_enabled():
        return task
    out = dict(task)
    if "max_turns" not in out:
        out["max_turns"] = ECONOMY_DEFAULT_MAX_TURNS
    if "timeout_seconds" not in out:
        out["timeout_seconds"] = ECONOMY_DEFAULT_TIMEOUT_SECONDS
    if "reasoning_effort" not in out:
        out["reasoning_effort"] = ECONOMY_DEFAULT_REASONING
    return out


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    return value


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
        "diffstat",
        "started_at",
        "finished_at",
        "events",
    )
    out: dict[str, Any] = {}
    for key in keep_keys:
        if key not in record:
            continue
        value = record[key]
        if key == "summary":
            out[key] = _clip(value, ECONOMY_MAX_SUMMARY)
        elif key in {"changed_files", "full_changed_files", "artifacts"} and isinstance(value, list):
            out[key] = value[:ECONOMY_MAX_CHANGED_FILES]
        elif key == "events" and isinstance(value, list):
            out[key] = value[-ECONOMY_MAX_EVENTS:]
        elif key == "tests" and isinstance(value, list):
            slim = []
            for item in value[:16]:
                if not isinstance(item, Mapping):
                    continue
                slim.append(
                    {
                        "command": _clip(item.get("command"), 200),
                        "passed": item.get("passed"),
                        "returncode": item.get("returncode"),
                        "source": item.get("source"),
                    }
                )
            out[key] = slim
        elif key == "diffstat":
            out[key] = _clip(value, 800)
        else:
            out[key] = value
    out["economy_compact"] = True
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
            "Read only summary + changed_files + tests + blocked_reason.",
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
            "stdio tunnel or bearer HTTP. Host tokens buy orchestration only; "
            "Grok does the long coding loop."
        ),
        "disclaimer": (
            "Unofficial community project. Not affiliated with, endorsed by, or "
            "supported by xAI, Grok, Anthropic, OpenAI, or Codex."
        ),
    }
