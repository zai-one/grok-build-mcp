"""Per-project opt-in for the bridge: ``.grok-mcp.json`` in the project root.

Delegating work to a local CLI is not something a project should inherit from a
machine-wide setting it never agreed to. A project opts in by carrying a config
file, and a project that carries none is simply not a Grok project -- job tools
refuse it rather than quietly running the worker against unfamiliar code.

Presets exist because the interesting choice is "how hard should the worker
think", not the four fields that answer it. ``model`` is deliberately absent from
every preset: naming one here would pin the project to whatever was current when
the preset was written, which is the mistake this bridge already refuses to make
with ``agentVersion``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .guard import GuardError, validate_model, validate_reasoning_effort

CONFIG_FILENAME = ".grok-mcp.json"

#: Preset name -> budget the worker runs under. ``off`` carries no budget
#: because it never reaches the worker.
PRESETS: dict[str, dict[str, Any]] = {
    "off": {},
    "cheap": {"reasoning_effort": "low", "max_turns": 12},
    "standard": {"reasoning_effort": "high", "max_turns": 24},
    "max": {"reasoning_effort": "xhigh", "max_turns": 40},
}

PRESET_DESCRIPTIONS: dict[str, str] = {
    "off": "Grok is not used in this project.",
    "cheap": "Mechanical edits; shallow reasoning, short leash.",
    "standard": "Everyday work; full reasoning, moderate leash.",
    "max": "Hardest work on the worker so the host spends the fewest tokens.",
}

_ALLOWED_KEYS = frozenset({"preset", "model", "reasoning_effort", "max_turns", "note", "$schema"})


def config_path(project_root: Path | str) -> Path:
    return Path(project_root) / CONFIG_FILENAME


def read_project_config(project_root: Path | str) -> dict[str, Any] | None:
    """Parsed config, or None when the project carries none.

    A malformed file raises rather than defaulting: someone wrote it on purpose,
    and silently running a different budget than the one on disk is worse than
    saying the file is broken.
    """
    path = config_path(project_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GuardError("PROJECT_CONFIG_UNREADABLE", f"cannot read {CONFIG_FILENAME}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardError("PROJECT_CONFIG_INVALID", f"{CONFIG_FILENAME} is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise GuardError("PROJECT_CONFIG_INVALID", f"{CONFIG_FILENAME} must be a JSON object")
    return validate_project_config(data)


def validate_project_config(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a config mapping, rejecting anything the bridge would ignore."""
    unknown = sorted(set(value) - _ALLOWED_KEYS)
    if unknown:
        raise GuardError(
            "PROJECT_CONFIG_UNKNOWN_FIELDS",
            f"{CONFIG_FILENAME} has unknown fields: {', '.join(unknown)}",
        )
    preset = str(value.get("preset", "") or "").strip().lower()
    if not preset:
        raise GuardError("PROJECT_CONFIG_INVALID", f"{CONFIG_FILENAME} must name a preset")
    if preset not in PRESETS:
        raise GuardError(
            "PROJECT_CONFIG_PRESET_UNKNOWN",
            f"preset must be one of {sorted(PRESETS)}, got {preset!r}",
        )

    out: dict[str, Any] = {"preset": preset, "enabled": preset != "off"}
    budget = dict(PRESETS[preset])

    # Explicit fields override the preset, so a project can start from a preset
    # and disagree with one number without restating the rest.
    model = validate_model(value.get("model"))
    if model:
        budget["model"] = model
    effort = validate_reasoning_effort(value.get("reasoning_effort"))
    if effort:
        budget["reasoning_effort"] = effort
    if "max_turns" in value and value["max_turns"] is not None:
        turns = value["max_turns"]
        if not isinstance(turns, int) or isinstance(turns, bool):
            raise GuardError("PROJECT_CONFIG_INVALID", "max_turns must be an integer")
        if not 1 <= turns <= 60:
            raise GuardError("PROJECT_CONFIG_INVALID", "max_turns must be between 1 and 60")
        budget["max_turns"] = turns

    if preset == "off" and budget:
        # A budget under `off` is a contradiction the operator should see, not a
        # setting the bridge silently drops.
        raise GuardError(
            "PROJECT_CONFIG_INVALID",
            "preset 'off' cannot carry a budget; remove the fields or pick another preset",
        )
    out["budget"] = budget
    note = value.get("note")
    if note is not None:
        out["note"] = str(note)[:500]
    return out


def project_gate(project_root: Path | str) -> dict[str, Any]:
    """Whether this project has opted in, and why not when it has not."""
    config = read_project_config(project_root)
    if config is None:
        return {
            "enabled": False,
            "reason": "PROJECT_NOT_CONFIGURED",
            "preset": None,
            "budget": {},
            "config_path": str(config_path(project_root)),
            "presets": {name: PRESET_DESCRIPTIONS[name] for name in PRESETS},
        }
    return {
        "enabled": bool(config["enabled"]),
        "reason": None if config["enabled"] else "PROJECT_PRESET_OFF",
        "preset": config["preset"],
        "budget": dict(config["budget"]),
        "config_path": str(config_path(project_root)),
    }


def render_config(preset: str, *, note: str | None = None) -> str:
    """The file contents for a preset, ready to write."""
    key = str(preset or "").strip().lower()
    if key not in PRESETS:
        raise GuardError(
            "PROJECT_CONFIG_PRESET_UNKNOWN",
            f"preset must be one of {sorted(PRESETS)}, got {key!r}",
        )
    body: dict[str, Any] = {"preset": key}
    body["note"] = note if note else PRESET_DESCRIPTIONS[key]
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"
