"""Optional lazy tool loading: start with a small core, enable toolsets at runtime.

GROK_DELEGATE_TOOL_LOADING=eager (default) lists every tool, exactly as before. With
GROK_DELEGATE_TOOL_LOADING=lazy, tools/list shows only the core (status, update check,
economy, project preset, the session loop and grok_agent_enable_toolsets); enabling a
toolset adds its tools for this server process and sends notifications/tools/list_changed.
Enabled tools keep every gate they had (allowed roots, project opt-in, confirm, sandbox).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

ENV = "GROK_DELEGATE_TOOL_LOADING"
MODES = ("eager", "lazy")
ENABLE_TOOL = "grok_agent_enable_toolsets"

TOOLSETS: dict[str, dict[str, Any]] = {
    "meta": {
        "description": "Status, update check, token economy, project preset (always listed).",
        "tools": ("grok_agent_status", "grok_agent_check_update", "grok_agent_economy", "grok_agent_project"),
    },
    "session": {
        "description": "Session Protocol: begin -> next card -> tick -> end (always listed).",
        "tools": ("grok_agent_session_begin", "grok_agent_session_next", "grok_agent_session_tick",
                  "grok_agent_session_end"),
    },
    "jobs": {
        "description": "Background jobs: consult, execute, fix, review, start, poll, cancel.",
        "tools": ("grok_agent_consult", "grok_agent_execute", "grok_agent_fix", "grok_agent_review",
                  "grok_agent_start", "grok_agent_poll", "grok_agent_cancel"),
    },
    "maintenance": {
        "description": "Update the bridge checkout (preview, confirm=true to pull and reinstall).",
        "tools": ("grok_agent_update",),
    },
    "compat": {
        "description": "Legacy grok_delegate* tools: sync/background delegation, plan, doctor, models.",
        "tools": ("grok_delegate", "grok_delegate_plan", "grok_delegate_start", "grok_delegate_poll",
                  "grok_delegate_status", "grok_delegate_doctor", "grok_delegate_models",
                  "grok_delegate_inspect"),
    },
}  # fmt: skip
CORE_TOOLSETS = ("meta", "session")
ROUTER_LINE = f"Lazy tools: {ENABLE_TOOL}() lists toolsets; {ENABLE_TOOL}(toolsets=[...]) adds them."
HINT = (
    "If the new tools do not appear, this client ignores tools/list_changed: re-list tools or reconnect "
    f"the MCP server, or set {ENV}=eager."
)
ENABLE_SPEC: dict[str, Any] = {
    "name": ENABLE_TOOL,
    "title": "Enable toolsets",
    "description": (
        "List toolsets that are not loaded yet (description, tools, token cost), or enable them by name "
        "('all' enables every one). Enabled tools appear via tools/list_changed. Local, no Grok call."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "toolsets": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 10,
                "description": "Toolset names, or ['all']. Omit to list.",
            }
        },
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}

_ENABLED: set[str] = set()  # one stdio session per process: process-wide state is the session state


def mode(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = (env.get(ENV) or "eager").strip().lower()
    if value not in MODES:
        raise ValueError(f"{ENV} must be eager or lazy")
    return value


def is_lazy(environ: Mapping[str, str] | None = None) -> bool:
    return mode(environ) == "lazy"


def reset() -> None:
    _ENABLED.clear()


def estimate_tokens(payload: Any) -> int:
    """Rough token estimate (~4 characters per token) of a JSON-serialised MCP object."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return (len(text) + 3) // 4


def core_names() -> set[str]:
    return {name for toolset in CORE_TOOLSETS for name in TOOLSETS[toolset]["tools"]}


def _grouped() -> set[str]:
    return {name for spec in TOOLSETS.values() for name in spec["tools"]}


def visible(tools: list[dict[str, Any]], environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """tools/list payload for the current mode (eager: unchanged)."""
    if not is_lazy(environ):
        return tools
    grouped = _grouped()
    shown = [
        tool for tool in tools if tool["name"] in core_names() | _ENABLED or tool["name"] not in grouped
    ]
    return [*shown, dict(ENABLE_SPEC)]


def hidden(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """True when a lazy session calls a tool whose toolset is not enabled yet."""
    return is_lazy(environ) and name in _grouped() - core_names() - _ENABLED


def enable(
    arguments: Mapping[str, Any] | None, tools: list[dict[str, Any]], notify: Any = None
) -> dict[str, Any]:
    by_name = {tool["name"]: tool for tool in tools}
    groups = {
        toolset: [name for name in spec["tools"] if name in by_name]
        for toolset, spec in TOOLSETS.items()
        if toolset not in CORE_TOOLSETS
    }
    raw = (arguments or {}).get("toolsets") or []
    requested = [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []
    if not requested:
        return {
            "ok": True,
            "mode": "lazy",
            "toolsets": [
                {
                    "toolset": toolset,
                    "description": TOOLSETS[toolset]["description"],
                    "tools": names,
                    "token_cost": sum(estimate_tokens(by_name[name]) for name in names),
                    "enabled": all(name in _ENABLED for name in names),
                }
                for toolset, names in groups.items()
            ],
            "hint": f"Call {ENABLE_TOOL}(toolsets=[...]).",
        }
    if "all" in requested:
        requested = list(groups)
    unknown = sorted(set(requested) - set(groups))
    if unknown:
        return {"ok": False, "error": "unknown_toolset", "unknown": unknown, "available": sorted(groups)}
    added = [name for toolset in requested for name in groups[toolset] if name not in _ENABLED]
    added = list(dict.fromkeys(added))
    _ENABLED.update(added)
    if added and notify is not None:
        try:
            notify({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        except Exception:  # noqa: BLE001 -- the hint covers clients we could not tell
            pass
    return {
        "ok": True,
        "mode": "lazy",
        "enabled": requested,
        "added_tools": added,
        "already_enabled": not added,
        "hint": HINT,
    }


def not_enabled(name: str) -> dict[str, Any]:
    toolset = next((ts for ts, spec in TOOLSETS.items() if name in spec["tools"]), "?")
    return {
        "ok": False,
        "error": "TOOL_NOT_ENABLED",
        "retryable": True,
        "hint": f"Lazy tool loading: call {ENABLE_TOOL}(toolsets=['{toolset}']) first, then retry.",
    }
