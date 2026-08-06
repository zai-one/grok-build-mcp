from __future__ import annotations

import json

from grok_delegate.server import handle_tool_call, list_tools
from grok_delegate.session import reset_sessions_for_tests, scrub_secrets


def setup_function() -> None:
    reset_sessions_for_tests()


def test_session_tools_listed() -> None:
    names = {t["name"] for t in list_tools()}
    assert "grok_agent_session_begin" in names
    assert "grok_agent_session_tick" in names
    assert "grok_agent_session_end" in names


def test_session_begin_auto_compact() -> None:
    r = handle_tool_call("grok_agent_session_begin", {"intent": "auto"})
    assert r["ok"] is True
    assert r["protocol"] == "session/v1"
    assert r["mode"] in {
        "install",
        "triage",
        "operate",
        "brainstorm",
        "execute",
        "verify",
        "update",
        "feedback",
    }
    assert "gate_status" in r
    assert "recommended_tools" in r
    assert r.get("skill_ref", "").startswith("references/")
    assert "next_step" in r
    assert len(json.dumps(r)) < 2048
    assert "sk-" not in json.dumps(r).lower() or "skill_ref" in r


def test_session_begin_invalid_intent() -> None:
    r = handle_tool_call("grok_agent_session_begin", {"intent": "dance"})
    assert r["ok"] is False
    assert r.get("error_code") == "INTENT_INVALID"


def test_session_tick_and_end() -> None:
    b = handle_tool_call("grok_agent_session_begin", {"intent": "brainstorm"})
    sid = b["session_id"]
    t = handle_tool_call(
        "grok_agent_session_tick",
        {"session_id": sid, "verbose": False},
    )
    assert t["ok"] is True
    assert "host_message" in t
    assert len(t["host_message"]) <= 500
    e = handle_tool_call(
        "grok_agent_session_end",
        {"session_id": sid, "suggest_issue": True, "note": "api_key=sk-testplanted"},
    )
    assert e["ok"] is True
    assert "receipt" in e
    assert e.get("suggest_issue") is True
    draft = e.get("issue_draft") or ""
    assert "sk-testplanted" not in draft
    assert "[REDACTED]" in draft or "sk-" not in draft.lower()


def test_scrub_secrets() -> None:
    assert "REDACTED" in scrub_secrets("bearer " + "a" * 24)
