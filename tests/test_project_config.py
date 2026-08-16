"""A project opts into the bridge by carrying `.grok-mcp.json`; none means no.

Delegating work to a local CLI should not be inherited from a machine-wide
setting the project never agreed to, so job tools fail closed on a project that
carries no config rather than running the worker against unfamiliar code.
"""

from __future__ import annotations

import json

from grok_delegate.guard import GuardError
from grok_delegate.project_config import (
    CONFIG_FILENAME,
    PRESETS,
    project_gate,
    read_project_config,
    render_config,
    validate_project_config,
)
from grok_delegate.server import _apply_project_gate, handle_tool_call, list_tools


def _write(tmp_path, body: str):
    (tmp_path / CONFIG_FILENAME).write_text(body, encoding="utf-8")


# --- the gate ---------------------------------------------------------------


def test_a_project_without_a_config_is_not_enabled(tmp_path) -> None:
    gate = project_gate(tmp_path)
    assert gate["enabled"] is False
    assert gate["reason"] == "PROJECT_NOT_CONFIGURED"
    # The refusal has to be actionable, so it carries the menu and the path.
    assert set(gate["presets"]) == set(PRESETS)
    assert gate["config_path"].endswith(CONFIG_FILENAME)


def test_preset_off_is_a_deliberate_no(tmp_path) -> None:
    _write(tmp_path, render_config("off"))
    gate = project_gate(tmp_path)
    assert gate["enabled"] is False
    assert gate["reason"] == "PROJECT_PRESET_OFF"


def test_each_preset_carries_its_budget(tmp_path) -> None:
    for preset, expected in (
        ("cheap", {"reasoning_effort": "low", "max_turns": 12}),
        ("standard", {"reasoning_effort": "high", "max_turns": 24}),
        ("max", {"reasoning_effort": "xhigh", "max_turns": 40}),
    ):
        _write(tmp_path, render_config(preset))
        gate = project_gate(tmp_path)
        assert gate["enabled"] is True
        assert gate["budget"] == expected


def test_no_preset_names_a_model() -> None:
    """Pinning a model in a preset ages the same way pinning agentVersion did."""
    for budget in PRESETS.values():
        assert "model" not in budget


# --- malformed configs are said out loud, not defaulted away ----------------


def test_broken_json_is_reported_rather_than_ignored(tmp_path) -> None:
    _write(tmp_path, "{ not json")
    try:
        read_project_config(tmp_path)
    except GuardError as exc:
        assert exc.code == "PROJECT_CONFIG_INVALID"
    else:  # pragma: no cover - a broken config must not read as "no config"
        raise AssertionError("a malformed config must raise, not fall back to disabled")


def test_unknown_preset_is_rejected() -> None:
    try:
        validate_project_config({"preset": "turbo"})
    except GuardError as exc:
        assert exc.code == "PROJECT_CONFIG_PRESET_UNKNOWN"
    else:  # pragma: no cover
        raise AssertionError("an unknown preset must be rejected")


def test_unknown_fields_are_rejected() -> None:
    try:
        validate_project_config({"preset": "max", "effort": "xhigh"})
    except GuardError as exc:
        assert exc.code == "PROJECT_CONFIG_UNKNOWN_FIELDS"
    else:  # pragma: no cover - a typo'd key must not be silently ignored
        raise AssertionError("a misspelled key must be rejected, not ignored")


def test_a_budget_under_off_is_a_contradiction() -> None:
    try:
        validate_project_config({"preset": "off", "max_turns": 40})
    except GuardError as exc:
        assert exc.code == "PROJECT_CONFIG_INVALID"
    else:  # pragma: no cover
        raise AssertionError("preset off must not silently carry a budget")


def test_out_of_range_turns_are_rejected() -> None:
    for bad in (0, 61, -1):
        try:
            validate_project_config({"preset": "max", "max_turns": bad})
        except GuardError as exc:
            assert exc.code == "PROJECT_CONFIG_INVALID"
        else:  # pragma: no cover
            raise AssertionError(f"max_turns={bad} must be rejected")


def test_explicit_fields_override_the_preset(tmp_path) -> None:
    _write(tmp_path, json.dumps({"preset": "cheap", "reasoning_effort": "max"}))
    gate = project_gate(tmp_path)
    assert gate["budget"]["reasoning_effort"] == "max"
    assert gate["budget"]["max_turns"] == 12  # the rest of the preset still stands


# --- job tools refuse an unconfigured project -------------------------------


def test_job_tools_refuse_a_project_that_never_opted_in(tmp_path) -> None:
    error, _task = _apply_project_gate({"project_root": str(tmp_path), "objective": "x"})
    assert error is not None
    assert error["error"] == "PROJECT_NOT_ENABLED"
    assert set(error["presets"]) == set(PRESETS)


def test_the_gate_fills_the_budget_for_an_enabled_project(tmp_path) -> None:
    _write(tmp_path, render_config("max"))
    error, task = _apply_project_gate({"project_root": str(tmp_path), "objective": "x"})
    assert error is None
    assert task["reasoning_effort"] == "xhigh"
    assert task["max_turns"] == 40


def test_an_explicit_task_value_still_beats_the_preset(tmp_path) -> None:
    _write(tmp_path, render_config("max"))
    _error, task = _apply_project_gate(
        {"project_root": str(tmp_path), "objective": "x", "reasoning_effort": "low"}
    )
    assert task["reasoning_effort"] == "low"


def test_a_task_without_a_root_is_left_to_the_contract(tmp_path) -> None:
    """The task contract reports a missing root better than the gate could."""
    error, _task = _apply_project_gate({"objective": "x"})
    assert error is None


# --- the setup tool ---------------------------------------------------------


def test_project_tool_is_listed() -> None:
    names = {tool["name"] for tool in list_tools()}
    assert "grok_agent_project" in names


def test_project_tool_reports_before_it_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(tmp_path))
    result = handle_tool_call("grok_agent_project", {"project_root": str(tmp_path)})
    assert result["enabled"] is False
    assert result["written"] is False
    assert not (tmp_path / CONFIG_FILENAME).exists()


def test_project_tool_writes_the_chosen_preset(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(tmp_path))
    result = handle_tool_call("grok_agent_project", {"project_root": str(tmp_path), "preset": "max"})
    assert result["written"] is True
    assert result["enabled"] is True
    written = json.loads((tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert written["preset"] == "max"


def test_project_tool_refuses_a_directory_outside_the_allowlist(tmp_path, monkeypatch) -> None:
    """Opting a project in must not become a way to opt in arbitrary directories."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(allowed))
    result = handle_tool_call("grok_agent_project", {"project_root": str(outside), "preset": "max"})
    assert result["error"] == "PROJECT_ROOT_NOT_ALLOWED"
    assert not (outside / CONFIG_FILENAME).exists()


def test_project_tool_rejects_an_unknown_preset(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(tmp_path))
    result = handle_tool_call("grok_agent_project", {"project_root": str(tmp_path), "preset": "turbo"})
    assert result["ok"] is False
