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
        ("cheap", {"reasoning_effort": "low", "max_turns": 12, "timeout_seconds": 1800}),
        ("standard", {"reasoning_effort": "high", "max_turns": 24, "timeout_seconds": 2400}),
        ("max", {"reasoning_effort": "xhigh", "max_turns": 40, "timeout_seconds": 3600}),
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


# --- navigator cards must not override the project's own preset -------------


def test_navigator_defaults_follow_the_project_preset(tmp_path) -> None:
    """A card carries an explicit budget, so it outranks the preset applied later.

    Without this the navigator would hand the host max_turns=12 for a project
    that chose `max`, quietly downgrading the preset the project just picked.
    """
    from grok_delegate.session import _session_budget

    _write(tmp_path, render_config("max"))
    budget = _session_budget({"project_root": str(tmp_path)})
    assert budget == {"max_turns": 40, "reasoning_effort": "xhigh", "timeout_seconds": 3600}


def test_navigator_falls_back_when_the_project_has_no_config(tmp_path) -> None:
    from grok_delegate.economy import ECONOMY_DEFAULT_MAX_TURNS, ECONOMY_DEFAULT_REASONING
    from grok_delegate.session import _session_budget

    budget = _session_budget({"project_root": str(tmp_path)})
    assert budget["max_turns"] == ECONOMY_DEFAULT_MAX_TURNS
    assert budget["reasoning_effort"] == ECONOMY_DEFAULT_REASONING


def test_a_broken_config_does_not_break_card_compilation(tmp_path) -> None:
    """The job gate reports a broken config with a usable message; a card cannot."""
    from grok_delegate.session import _session_budget

    _write(tmp_path, "{ broken")
    budget = _session_budget({"project_root": str(tmp_path)})
    assert budget["max_turns"] > 0


def test_the_plan_hint_matches_the_budget_the_card_will_carry() -> None:
    from grok_delegate.session import compile_plan

    hint = compile_plan("execute", "ship it", True, max_turns=40)[0]["args_hint"]
    assert hint["max_turns"] == 40


def test_the_opt_in_tool_honours_an_injected_allowlist(tmp_path) -> None:
    """`handle_tool_call(..., allowed_roots=[x])` has to mean the same for every tool.

    Job tools resolved the injected allowlist and this one read module state, so
    an embedder that passes its own roots -- `scripts/routines.py` does -- got
    ALLOWED_ROOTS_EMPTY from the single tool whose job is to clear
    PROJECT_NOT_ENABLED. Nothing else in the process had to be misconfigured for
    that to happen; it was the parameter being quietly ignored.
    """
    out = handle_tool_call(
        "grok_agent_project",
        {"project_root": str(tmp_path), "preset": "cheap"},
        allowed_roots=[tmp_path],
    )
    assert out["ok"] is True, out
    assert (tmp_path / CONFIG_FILENAME).exists()


def test_an_injected_allowlist_still_excludes_everything_else(tmp_path) -> None:
    """Honouring the injection must not widen it: a sibling is not an allowed root."""
    granted = tmp_path / "granted"
    stranger = tmp_path / "stranger"
    granted.mkdir()
    stranger.mkdir()
    out = handle_tool_call(
        "grok_agent_project",
        {"project_root": str(stranger), "preset": "cheap"},
        allowed_roots=[granted],
    )
    assert out["ok"] is False
    assert out["error"] == "PROJECT_ROOT_NOT_ALLOWED"
    assert not (stranger / CONFIG_FILENAME).exists()


# --- the opt-in the compat tools were exempt from --------------------------------


def _bare_repo(tmp_path):
    import subprocess

    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "s"]):
        subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True)
    return tmp_path.resolve()


def test_the_compat_start_tool_honours_the_project_opt_in(tmp_path) -> None:
    """`grok_delegate_start` started a real worker where `grok_agent_execute` refused.

    The opt-in is described as the whole security model -- a project has to say
    yes before the bridge runs anything in it -- and one advertised tool walked
    straight past it. Found by the audit routine, reproduced before it was
    believed: the same directory answered PROJECT_NOT_ENABLED to the typed tool
    and ok=True with a live job_id to this one.
    """
    root = _bare_repo(tmp_path)
    out = handle_tool_call(
        "grok_delegate_start",
        {"goal": "anything", "lane": "gate-probe", "cwd": str(root)},
        allowed_roots=[root],
    )
    assert out["ok"] is False
    assert out["error"] == "PROJECT_NOT_ENABLED"
    assert out.get("job_id") is None


def test_the_compat_delegate_tool_honours_it_too(tmp_path) -> None:
    root = _bare_repo(tmp_path)
    out = handle_tool_call(
        "grok_delegate",
        {"goal": "anything", "lane": "gate-probe", "cwd": str(root)},
        allowed_roots=[root],
    )
    assert out["ok"] is False
    assert out["error"] == "PROJECT_NOT_ENABLED"


def test_planning_is_still_allowed_without_a_config(tmp_path) -> None:
    """A plan reads and reports. Refusing it would leave a caller unable to see
    what the gate is objecting to, which is the opposite of actionable."""
    root = _bare_repo(tmp_path)
    out = handle_tool_call(
        "grok_delegate_plan",
        {"goal": "anything", "lane": "gate-probe", "cwd": str(root)},
        allowed_roots=[root],
    )
    assert out.get("error") != "PROJECT_NOT_ENABLED", out


def test_a_preset_that_buys_more_work_also_buys_more_time(tmp_path) -> None:
    """The clock is part of the budget, and leaving it out made `max` incoherent.

    `max` granted xhigh reasoning and forty turns and then ran them on the same
    1800 s packet default `cheap` gets for twelve low-effort ones. Measured on a
    nightly routine over a `max` project: consult jobs dispatched at 02:06 UTC
    hit the wall near 02:37 and came back `ACP_TIMEOUT` with the answer cut off
    mid-sentence, on almost every run.
    """
    clocks = {}
    for preset in ("cheap", "standard", "max"):
        _write(tmp_path, render_config(preset))
        clocks[preset] = project_gate(tmp_path)["budget"]["timeout_seconds"]

    assert clocks["cheap"] <= clocks["standard"] < clocks["max"], clocks
    assert clocks["max"] == 3600, "the hardest preset must reach the packet ceiling"
    assert clocks["cheap"] >= 1800, (
        "lowering an existing allowance fixes nothing and cuts short a job that used to fit"
    )


def test_the_preset_clock_reaches_the_packet_and_the_task_still_wins(tmp_path) -> None:
    """A budget nobody applies is decoration; a budget nobody can override is a cage."""
    from grok_delegate.contracts import validate_task_packet

    _write(tmp_path, render_config("max"))
    base = {
        "objective": "find defects, read only",
        "role": "consult",
        "project_root": str(tmp_path),
        "correlation_id": "nightly-cycle",
    }

    _, resolved = _apply_project_gate(base)
    task = validate_task_packet(resolved, allowed_roots=[tmp_path])
    assert task["timeout_seconds"] == 3600, "the preset clock never reached the packet"

    _, resolved = _apply_project_gate({**base, "timeout_seconds": 300})
    task = validate_task_packet(resolved, allowed_roots=[tmp_path])
    assert task["timeout_seconds"] == 300, "an explicit task value must still win"


def test_a_project_may_state_its_own_clock(tmp_path) -> None:
    _write(tmp_path, json.dumps({"preset": "cheap", "timeout_seconds": 600}))
    assert project_gate(tmp_path)["budget"]["timeout_seconds"] == 600

    _write(tmp_path, json.dumps({"preset": "cheap", "timeout_seconds": 99_999}))
    try:
        project_gate(tmp_path)
    except GuardError as exc:
        assert exc.code == "PROJECT_CONFIG_INVALID"
    else:
        raise AssertionError("a clock past the packet ceiling was accepted")


def test_the_execute_card_does_not_override_the_clock_it_just_read(tmp_path) -> None:
    """`_session_budget` exists so a card cannot quietly downgrade the preset.

    It read the preset and then the card hardcoded `timeout_seconds` on the line
    below the spread, so a `max` project resolving to 3600 s handed the host a
    card carrying 600 -- a third of what the same job would have got by saying
    nothing at all. On the navigator path, which is the cycle the skill
    prescribes.

    The packet builder is called directly rather than through
    `session_begin`/`session_next`: which card the navigator offers first depends
    on the gate, and on a machine with no Grok CLI and no login -- CI, for one --
    the first card is not the execute one. The first version of this test drove
    the navigator and passed only where a worker happened to be installed.
    """
    from grok_delegate.session import _write_task_packet

    for preset, expected in (("max", 3600), ("standard", 2400), ("cheap", 1800)):
        root = tmp_path / preset
        root.mkdir()
        _write(root, render_config(preset))
        packet = _write_task_packet({
            "goal": "write a new parser",
            "project_root": str(root),
            "session_id": f"s-{preset}",
        })
        assert packet["timeout_seconds"] == expected, (
            f"preset {preset} resolves to {expected}s but its card carries "
            f"{packet['timeout_seconds']}s"
        )
        assert packet["max_turns"] == {"max": 40, "standard": 24, "cheap": 12}[preset]
