"""Replay real ACP traffic from the installed CLI through the bridge's parsers.

The frame handling was written by watching Grok 0.2.118 and then carried forward
on comments alone. Comments do not fail when the agent changes. These fixtures
were captured live by ``scripts/capture_acp_live.py`` against the CLI on this
machine, so every assumption the parsing makes is checked against traffic that
actually happened -- and the check keeps working when the CLI is upgraded and
the fixtures are re-captured.

Nothing here pins a version. The fixtures record which build they came from as
evidence; the bridge still negotiates on ACP protocol integer 1 alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grok_delegate.acp import (
    _agent_reported_pass,
    _consume_update,
    _paths_confined,
    _permission_params_with_tool_state,
    permission_decision,
)

FIXTURES = Path(__file__).resolve().parent.parent / "evidence" / "live-acp"
#: `websocket` drives the same protocol over a different socket, so the
#: transport-agnostic checks take all four; the frame-shape checks name the
#: stdio scenario that exercises the tool they are about.
SCENARIOS = ("permission-cancel", "consult", "command", "websocket")


def _require(path: Path) -> Path:
    """Fail, do not skip. These fixtures ARE the protection.

    Skipping on a missing capture meant the guard against a CLI upgrade could
    disappear without turning anything red -- the same silent-pass shape this
    file exists to remove from the bridge. The captures are committed, so their
    absence is a broken checkout or a deletion, and both deserve a failure.
    """
    if not path.exists():
        raise AssertionError(
            f"{path.name} is missing. It is committed evidence, not an optional "
            f"artifact; re-capture with scripts/capture_acp_live.py --scenario "
            f"{path.stem.removeprefix('session-').removesuffix('.observed')}"
        )
    return path


def load(scenario: str) -> list[dict[str, Any]]:
    path = _require(FIXTURES / f"session-{scenario}.jsonl")
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def observed(scenario: str) -> dict[str, Any]:
    path = _require(FIXTURES / f"session-{scenario}.observed.json")
    return json.loads(path.read_text(encoding="utf-8"))


def updates(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        (frame.get("params") or {}).get("update") or {}
        for frame in frames
        if frame.get("method") == "session/update"
    ]


def permission_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [frame for frame in frames if frame.get("method") == "session/request_permission"]


def response(frames: list[dict[str, Any]], wanted: int) -> dict[str, Any]:
    for frame in frames:
        if frame.get("dir") == "<-" and frame.get("id") == wanted and "result" in frame:
            return frame
    raise AssertionError(f"no response with id={wanted} in fixture")


# --- the handshake ------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_capture_negotiated_protocol_one(scenario: str) -> None:
    """The compatibility contract is the protocol integer, and only that."""
    assert observed(scenario)["protocol_version"] == 1


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_agent_version_is_recorded_but_never_required(scenario: str) -> None:
    from grok_delegate.acp import DEFAULT_EXPECTED_AGENT_VERSION

    assert observed(scenario)["agent_version"], "capture should record what it talked to"
    assert DEFAULT_EXPECTED_AGENT_VERSION is None, "recording a version must not become pinning it"


def test_session_new_returns_a_session_id() -> None:
    frames = load("consult")
    assert (response(frames, 1).get("result") or {}).get("sessionId")


# --- permission: the decision runs on frames the agent really sent -------------


def test_a_write_request_carries_kind_and_rawinput() -> None:
    """Both are load-bearing: kind picks the branch, rawInput confines the path."""
    tool = (permission_frames(load("permission-cancel"))[0]["params"] or {})["toolCall"]
    assert tool["kind"] == "edit"
    assert tool["rawInput"]["file_path"]


def test_the_bridge_allows_the_confined_write_the_agent_asked_for(tmp_path) -> None:
    params = permission_frames(load("permission-cancel"))[0]["params"]
    task = {"permission_profile": "workspace", "test_commands": []}
    decision = permission_decision(params, task, tmp_path)
    assert decision["outcome"] == "selected"
    allow = next(o for o in params["options"] if o["kind"] == "allow_once")
    assert decision["optionId"] == allow["optionId"]


def test_a_write_outside_the_worktree_is_still_denied(tmp_path) -> None:
    """Same live frame, path swapped: confinement is doing the work, not luck."""
    params = json.loads(json.dumps(permission_frames(load("permission-cancel"))[0]["params"]))
    params["toolCall"]["rawInput"]["file_path"] = "../escaped.md"
    decision = permission_decision(params, {"permission_profile": "workspace"}, tmp_path)
    reject = next(o for o in params["options"] if o["kind"] == "reject_once")
    assert decision["optionId"] == reject["optionId"]


def test_read_only_profile_rejects_the_same_write(tmp_path) -> None:
    params = permission_frames(load("permission-cancel"))[0]["params"]
    decision = permission_decision(params, {"permission_profile": "read-only"}, tmp_path)
    reject = next(o for o in params["options"] if o["kind"] == "reject_once")
    assert decision["optionId"] == reject["optionId"]


def test_the_read_tool_names_its_path_target_file(tmp_path) -> None:
    """Found by this capture: `target_file` was not a key confinement knew.

    A path key the check does not recognise reads as "no path at all", which
    fails closed -- so a legitimate read was denied for the wrong reason.
    """
    raw = next(
        update["rawInput"]
        for update in updates(load("permission-cancel"))
        if (update.get("rawInput") or {}).get("target_file")
    )
    assert "file_path" not in raw and "path" not in raw
    assert _paths_confined(raw, tmp_path) is True


def test_the_two_frame_join_is_a_no_op_when_the_request_is_complete() -> None:
    """1.0.4 fills rawInput in the request; the join must not disturb that."""
    params = permission_frames(load("permission-cancel"))[0]["params"]
    merged = _permission_params_with_tool_state(params, {})
    assert merged["toolCall"]["rawInput"] == params["toolCall"]["rawInput"]


def test_the_join_still_rescues_a_request_that_omits_rawinput() -> None:
    """The older shape, rebuilt from live frames: decisions must survive both."""
    frames = load("permission-cancel")
    params = json.loads(json.dumps(permission_frames(frames)[0]["params"]))
    raw = params["toolCall"].pop("rawInput")
    tool_id = params["toolCall"]["toolCallId"]
    earlier = next(
        update
        for update in updates(frames)
        if update.get("toolCallId") == tool_id and update.get("rawInput")
    )
    merged = _permission_params_with_tool_state(params, {tool_id: earlier})
    assert merged["toolCall"]["rawInput"] == earlier["rawInput"]
    assert raw["file_path"] == earlier["rawInput"]["file_path"]


def test_an_unknown_tool_call_is_denied_not_guessed(tmp_path) -> None:
    params = json.loads(json.dumps(permission_frames(load("permission-cancel"))[0]["params"]))
    params["toolCall"].pop("rawInput")
    decision = permission_decision(
        _permission_params_with_tool_state(params, {}), {"permission_profile": "workspace"}, tmp_path
    )
    reject = next(o for o in params["options"] if o["kind"] == "reject_once")
    assert decision["optionId"] == reject["optionId"]


# --- execute: the command the bridge must match against ------------------------


def test_the_execute_request_carries_the_command_verbatim() -> None:
    """Permission is an exact match against test_commands, so this must be clean.

    Captured with a prompt that asked for nothing extra. When the prompt invited
    the agent to report an exit code it sent
    `python -m pytest -q; echo EXIT_CODE=$LASTEXITCODE` instead, which the
    bridge denies -- the worker's instructions have to ask for the command
    verbatim, and build_prompt says so.
    """
    tool = (permission_frames(load("command"))[0]["params"] or {})["toolCall"]
    assert tool["kind"] == "execute"
    assert tool["rawInput"]["command"] == "python -m pytest -q"


def test_the_declared_test_command_is_allowed_and_others_are_not(tmp_path) -> None:
    params = permission_frames(load("command"))[0]["params"]
    allowed = permission_decision(
        params, {"permission_profile": "workspace", "test_commands": ["python -m pytest -q"]}, tmp_path
    )
    assert allowed["optionId"] == next(
        o for o in params["options"] if o["kind"] == "allow_once"
    )["optionId"]

    undeclared = permission_decision(
        params, {"permission_profile": "workspace", "test_commands": []}, tmp_path
    )
    assert undeclared["optionId"] == next(
        o for o in params["options"] if o["kind"] == "reject_once"
    )["optionId"]


# --- what the agent reports about its own tests --------------------------------


def test_a_finished_turn_streams_the_text_the_summary_is_built_from() -> None:
    """No agent_message_chunk would mean every receipt summary silently empties."""
    chunks: list[str] = []
    for update in updates(load("consult")):
        _consume_update(update, text_chunks=chunks, tests=[], tool_state={})
    assert "".join(chunks).strip()


def test_the_agents_own_exit_code_is_recorded_as_a_claim_not_a_verdict() -> None:
    tests: list[dict[str, Any]] = []
    state: dict[str, dict[str, Any]] = {}
    for update in updates(load("command")):
        _consume_update(update, text_chunks=[], tests=tests, tool_state=state)
    assert tests, "the fixture ran a test command"
    assert {test["source"] for test in tests} == {"agent-reported"}


def test_a_chained_command_yields_no_verdict_at_all() -> None:
    """Live: pytest failed, the trailing echo succeeded, exit_code came back 0.

    Reporting that as passed would be worse than reporting nothing, so the
    chained case has to answer "unknown" rather than "green".
    """
    assert _agent_reported_pass("python -m pytest -q; echo EXIT_CODE=$LASTEXITCODE", {"exit_code": 0}) is None
    assert _agent_reported_pass("python -m pytest -q", {"exit_code": 0}) is True
    assert _agent_reported_pass("python -m pytest -q", {"exit_code": 1}) is False


# --- cancellation and the private notification stream --------------------------


def test_cancel_is_acknowledged_with_a_cancelled_stop_reason() -> None:
    frames = load("permission-cancel")
    assert any(frame.get("method") == "session/cancel" for frame in frames if frame.get("dir") == "->")
    assert observed("permission-cancel")["stop_reason"] == "cancelled"
    assert observed("consult")["stop_reason"] == "end_turn"


# --- the WebSocket transport ---------------------------------------------------


def test_the_managed_daemon_speaks_the_same_protocol() -> None:
    """A second transport is a second thing that can drift; it is checked too."""
    seen = observed("websocket")
    assert seen["transport"] == "websocket"
    assert seen["protocol_version"] == 1
    assert seen["stop_reason"] == "end_turn"
    assert seen["notes"] == [], f"capture reported problems: {seen['notes']}"


def test_reconnect_can_resume_the_session_the_bridge_left_behind() -> None:
    """The reconnect path assumes `loadSession` means session/load will work.

    It reconnects, re-initializes and loads, and then refuses to replay the
    prompt -- so if load did not actually work, the bridge would be failing
    closed on a capability it never verified.
    """
    seen = observed("websocket")
    assert seen["load_session_advertised"] is True
    assert seen["reconnected"] is True
    assert seen["session_load_ok"] is True


def test_the_capture_leaves_no_daemon_running() -> None:
    assert observed("websocket")["daemon_alive_after_kill"] is False


def test_session_load_is_answered_with_a_result_not_an_error() -> None:
    frames = load("websocket")
    assert any(
        frame.get("dir") == "->" and frame.get("method") == "session/load" for frame in frames
    )
    loaded = response(frames, 10_001)
    assert "error" not in loaded


def test_a_private_method_one_character_from_the_real_one_is_not_the_real_one() -> None:
    """Over WS the agent also sends `_x.ai/session/update`.

    The dispatch compares the method exactly, so the private near-miss lands in
    the notification bucket. A prefix or substring match here would feed
    arbitrary private payloads to the session-update parser.
    """
    assert "_x.ai/session/update" in observed("websocket")["unknown_methods"]
    assert "_x.ai/session/update" != "session/update"
    private = [
        frame for frame in load("websocket") if frame.get("method") == "_x.ai/session/update"
    ]
    for frame in private:
        assert frame.get("method") != "session/update"


def test_private_notifications_are_present_and_are_not_answers() -> None:
    """`_x.ai/session/prompt_complete` looks like a turn result and is not one.

    The bridge routes every unknown method to a bounded notification event, so
    a private frame can never be mistaken for the session/prompt response. If
    that ever changed, a turn would be declared finished by a notification the
    agent is free to redefine between builds.
    """
    methods = observed("consult")["unknown_methods"]
    assert methods, "1.0.4 does emit private notifications"
    assert all(method.startswith("_x.ai/") for method in methods)
    assert "_x.ai/session/prompt_complete" in methods
    assert (response(load("consult"), 2).get("result") or {}).get("stopReason") == "end_turn"


# --- the fixtures themselves ---------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_fixtures_carry_no_absolute_paths_or_secrets(scenario: str) -> None:
    body = (FIXTURES / f"session-{scenario}.jsonl").read_text(encoding="utf-8")
    assert "C:\\\\Users" not in body and "C:/Users" not in body
    for marker in ("xai-", "sk-", "Bearer ", "GROK_AGENT_SECRET", "capture-1", "capture-2"):
        assert marker not in body, f"{marker} must not reach a committed fixture"


def test_a_missing_fixture_is_a_failure_and_not_a_skip() -> None:
    """The guard has to be louder than the thing it guards against.

    Skipping on a missing capture would let the protection against a CLI upgrade
    vanish while the suite still reported green -- which is the silent-pass shape
    this whole file exists to remove.
    """
    with pytest.raises(AssertionError) as missing:
        _require(FIXTURES / "session-does-not-exist.jsonl")
    assert "capture_acp_live.py" in str(missing.value)


def test_every_scenario_this_module_names_is_actually_committed() -> None:
    for scenario in SCENARIOS:
        assert (FIXTURES / f"session-{scenario}.jsonl").exists()
        assert (FIXTURES / f"session-{scenario}.observed.json").exists()
