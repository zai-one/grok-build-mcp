from __future__ import annotations
import json
from grok_delegate.server import handle_tool_call, list_tools
from grok_delegate.session import reset_sessions_for_tests, scrub_secrets

def setup_function():
    reset_sessions_for_tests()

def test_session_next_listed():
    assert "grok_agent_session_next" in {t["name"] for t in list_tools()}

def test_navigator_install_loop():
    b = handle_tool_call("grok_agent_session_begin", {"intent": "auto", "goal": "fix x", "host_budget": "small"})
    assert b["protocol"] == "session/v1.2"
    assert b["plan"]
    sid = b["session_id"]
    cards = []
    for _ in range(6):
        n = handle_tool_call("grok_agent_session_next", {"session_id": sid})
        assert len(json.dumps(n)) < 1536
        cards.append(n.get("card", {}).get("kind"))
        if n.get("done"):
            break
    assert "host_cmd" in cards or "end" in cards
    assert cards[-1] in {"end", "mcp_tool", "host_cmd"}
    e = handle_tool_call("grok_agent_session_end", {"session_id": sid})
    assert e.get("budget_report")

def test_begin_scrubs_goal():
    b = handle_tool_call("grok_agent_session_begin", {"goal": "x api_key=sk-leaktest99", "host_budget": "tiny"})
    assert "sk-leaktest99" not in json.dumps(b)

def test_scrub():
    assert "REDACTED" in scrub_secrets("oauth=abcsecretvalue")
