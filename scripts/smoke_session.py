#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grok_delegate.server import handle_tool_call
from grok_delegate.session import reset_sessions_for_tests

def main() -> int:
    reset_sessions_for_tests()
    b = handle_tool_call("grok_agent_session_begin", {"intent": "auto", "goal": "review auth", "host_budget": "small"})
    assert b["ok"] and b["protocol"] == "session/v1.2" and b.get("plan") is not None
    assert len(json.dumps(b)) < 4096
    sid = b["session_id"]
    n = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    assert n.get("card") and "kind" in n["card"]
    e = handle_tool_call("grok_agent_session_end", {"session_id": sid})
    assert e.get("budget_report")
    print("SMOKE v1.2 PASS", b["mode"], n["card"]["kind"])
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
