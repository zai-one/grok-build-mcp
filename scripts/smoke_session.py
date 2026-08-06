#!/usr/bin/env python3
"""Smoke Session Protocol v1 (no network). Exit 0 on success."""
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
    b = handle_tool_call("grok_agent_session_begin", {"intent": "auto"})
    assert b.get("ok"), b
    assert len(json.dumps(b)) < 2048, len(json.dumps(b))
    t = handle_tool_call("grok_agent_session_tick", {"session_id": b.get("session_id")})
    assert t.get("ok"), t
    e = handle_tool_call(
        "grok_agent_session_end",
        {"session_id": b.get("session_id"), "suggest_issue": True, "note": "ok"},
    )
    assert e.get("ok") and e.get("receipt"), e
    blob = json.dumps(b) + json.dumps(t) + json.dumps(e)
    for bad in ("BEGIN RSA", "alexzascherinsky@"):
        assert bad not in blob
    print("SMOKE SESSION PASS")
    print(" mode=", b.get("mode"), " tools=", b.get("recommended_tools"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
