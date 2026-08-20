"""A parameter in a tool schema is a promise. This makes the promise checkable.

`grok_agent_poll` advertised `limit` for two releases. It survived the
unknown-argument check and was then read only in the branch that lists every
job -- never on the path that takes a `job_id`, which is the only path a host
uses. `limit: 1` returned exactly as much as passing nothing, and no test
noticed, because no test called poll with a limit at all.

The gap was not "one missing test". It was that nothing connected the advertised
surface to the tested surface, so a knob could be added, documented and shipped
without anyone being asked whether it does anything.

The registry below closes that. Every parameter of every tool must appear in it,
and every entry must still correspond to a parameter that exists. Adding a
parameter fails this file until its author writes down what proves it works;
deleting one fails until the stale entry goes.
"""

from __future__ import annotations

import pytest

from grok_delegate import server

#: parameter -> what makes it more than decoration.
#: `task.*` fields are one entry: they are validated as a packet by
#: contracts.validate_task_packet and exercised through it, not one by one.
PROMISES: dict[str, str] = {
    "task": "validate_task_packet; tests/test_grok_delegate.py packet tests",
    "task.*": "contracts.validate_task_packet rejects bad packets; role/profile pairing tested",
    # --- job control -----------------------------------------------------------
    "job_id": "poll/cancel resolve it; JOB_UNKNOWN tested",
    "limit": "tests/test_poll_bounds_and_skeptic_commands.py bounds the events it returns",
    "lane": "runner.prepare_worktree names the worktree; reserved lanes refused",
    "lanes_parent": "tests/test_lane_home.py; LANES_PARENT_INSIDE_REPO",
    "transport": "validate_transport; stdio/websocket/legacy routed in agent_runtime",
    "repo_root": "resolve_trusted_repo_root; allowlist tests",
    "project_root": "project_gate; PROJECT_NOT_ENABLED carries the path",
    "base_ref": "pinned to a SHA before the worker starts; tests/test_base_ref_and_receipt_reason.py",
    # --- worker budget ---------------------------------------------------------
    "model": "argv omits --model when empty; tests/test_model_and_effort_defaults.py",
    "max_turns": "HARD_CAP_MAX_TURNS; over-cap refused",
    "reasoning_effort": "validate_reasoning_effort; tests/test_model_and_effort_defaults.py",
    "sandbox": "DEFAULT_EXECUTE_SANDBOX / DEFAULT_PLAN_SANDBOX in argv",
    "no_subagents": "argv flag; tests/test_round8_bridge.py argv assertions",
    "disable_web_search": "argv flag; tests/test_round8_bridge.py argv assertions",
    "max_tool_calls": "bounded in argv construction",
    "test_commands": "verifier runs them; permission gate matches them literally",
    "expected_artifacts": "acceptance gate: EXPECTED_ARTIFACT_MISSING / NOT_CHANGED",
    "correlation_id": "echoed into the receipt and the audit line",
    # --- session navigator -----------------------------------------------------
    "goal": "session_begin stores it; GOAL_EMPTY refused",
    "host_budget": "card size; tests/test_navigator_cycle.py",
    "session_id": "bind_session_job; navigator cycle tests",
    "intent": "_auto_mode routing; tests/test_navigator_cards_and_intent.py",
    "step_done": "advances the navigator; cycle tests",
    "advance": "advances the navigator; cycle tests",
    "tool_used": "recorded so the next card knows what happened",
    "lane_verdict": "verdict.py parses and validates it",
    "note": "carried into the session record",
    "continue_session": "resume flags in argv",
    "fork_session": "resume flags in argv",
    "resume": "resume flags in argv",
    # --- misc ------------------------------------------------------------------
    "preset": "project_config writes it; presets tested",
    "confirm": "grok_agent_update refuses to act without it",
    "plan_only": "plan path returns argv without spawning",
    "json_schema": "validate_json_schema",
    "rules": "passed through to argv",
    "suggest_issue": "doctor output shape",
    "verbose": "doctor output shape",
    "wait_seconds": "test_wait_blocks_until_terminal / test_wait_emits_progress",
}


def _advertised() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for tool in server.list_tools():
        schema = tool.get("inputSchema") or {}
        for name, spec in (schema.get("properties") or {}).items():
            out.setdefault(name, set()).add(tool["name"])
            if isinstance(spec, dict) and spec.get("type") == "object":
                for sub in (spec.get("properties") or {}):
                    out.setdefault(f"{name}.{sub}", set()).add(tool["name"])
    return out


def _key(parameter: str) -> str:
    return "task.*" if parameter.startswith("task.") else parameter


def test_every_advertised_parameter_is_accounted_for() -> None:
    """A new knob cannot ship until someone says what proves it does anything."""
    missing = sorted({_key(p) for p in _advertised()} - set(PROMISES))
    assert not missing, (
        "these parameters are advertised in a tool schema and nothing here says "
        f"what makes them real: {missing}"
    )


def test_no_promise_outlives_its_parameter() -> None:
    """Rot in the other direction: an entry for a knob that no longer exists."""
    live = {_key(p) for p in _advertised()}
    stale = sorted(set(PROMISES) - live - {"task.*"})
    assert not stale, f"promises for parameters that no longer exist: {stale}"


def test_every_promise_says_something() -> None:
    for parameter, proof in PROMISES.items():
        assert len(proof.strip()) > 10, f"{parameter} has a placeholder, not a reason"


@pytest.mark.parametrize("tool", [t["name"] for t in server.list_tools()])
def test_every_tool_refuses_arguments_it_never_advertised(tool: str) -> None:
    """The other half of the contract: the schema is also a limit, not just a menu.

    A tool that silently swallows an unknown argument lets a caller believe it
    asked for something.
    """
    schema = next(t["inputSchema"] for t in server.list_tools() if t["name"] == tool)
    assert schema.get("additionalProperties") is False, (
        f"{tool} accepts undeclared arguments, so a typo reads as a request"
    )
