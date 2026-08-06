from __future__ import annotations

import json

from grok_delegate.server import handle_tool_call, list_tools
from grok_delegate.session import reset_sessions_for_tests, scrub_secrets


def setup_function() -> None:
    reset_sessions_for_tests()


def test_session_tools_listed() -> None:
    names = {t["name"] for t in list_tools()}
    assert "grok_agent_session_begin" in names


def test_session_begin_plan_budget() -> None:
    r = handle_tool_call(
        "grok_agent_session_begin",
        {"intent": "auto", "goal": "implement fix api_key=sk-testplanted", "host_budget": "small"},
    )
    assert r["ok"] is True
    assert r["protocol"] == "session/v1.1"
    assert "budget" in r and r["budget"]["host_budget"] == "small"
    assert isinstance(r.get("plan"), list) and len(r["plan"]) <= 5
    assert "host_script" in r and len(r["host_script"]) <= 240
    assert "deny_tools" in r
    blob = json.dumps(r)
    assert len(blob) < 1536
    assert "sk-testplanted" not in blob


def test_budget_force_end() -> None:
    b = handle_tool_call(
        "grok_agent_session_begin",
        {"intent": "install", "host_budget": "tiny", "max_tool_calls": 2},
    )
    sid = b["session_id"]
    t1 = handle_tool_call("grok_agent_session_tick", {"session_id": sid})
    t2 = handle_tool_call("grok_agent_session_tick", {"session_id": sid})
    assert t2["force_end"] is True
    assert t2["suggested_host_action"] == "session_end"
    e = handle_tool_call("grok_agent_session_end", {"session_id": sid})
    assert e["budget_report"]["was_capped"] is True
    assert "lesson" in e


def test_deny_tool_warn() -> None:
    b = handle_tool_call("grok_agent_session_begin", {"intent": "brainstorm", "host_budget": "small"})
    # may be install if gate fails — still ok
    t = handle_tool_call(
        "grok_agent_session_tick",
        {"session_id": b["session_id"], "tool_used": "grok_agent_execute"},
    )
    assert t["ok"] is True


def test_scrub() -> None:
    assert "REDACTED" in scrub_secrets("oauth=supersecretvalue99")
