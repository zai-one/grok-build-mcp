#!/usr/bin/env python3
"""Smoke Session Protocol v1.1."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grok_delegate.server import handle_tool_call  # noqa: E402
from grok_delegate.session import reset_sessions_for_tests  # noqa: E402


def main() -> int:
    reset_sessions_for_tests()
    b = handle_tool_call(
        "grok_agent_session_begin",
        {"intent": "auto", "goal": "review auth module", "host_budget": "small"},
    )
    assert b.get("ok"), b
    assert b.get("protocol") == "session/v1.1"
    assert "budget" in b and "plan" in b and "host_script" in b
    assert 0 <= len(b["plan"]) <= 5
    assert len(json.dumps(b)) < 1536
    t = handle_tool_call("grok_agent_session_tick", {"session_id": b["session_id"]})
    assert "force_end" in t and "budget_remaining" in t
    e = handle_tool_call("grok_agent_session_end", {"session_id": b["session_id"]})
    assert e.get("budget_report") and e.get("receipt")
    print("SMOKE SESSION v1.1 PASS", "mode=", b.get("mode"), "plan_len=", len(b["plan"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
