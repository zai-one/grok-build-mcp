"""MCP tool annotations: hints for host confirmation UIs, never enforcement."""

from __future__ import annotations

from grok_delegate.server import list_tools


def test_every_tool_is_annotated_consistently() -> None:
    tools = {t["name"]: t.get("annotations") for t in list_tools()}
    assert all(tools.values()), [name for name, hints in tools.items() if not hints]
    for name in ("grok_agent_status", "grok_agent_poll", "grok_delegate_poll", "grok_delegate_doctor"):
        assert tools[name]["readOnlyHint"] is True, name
    for name in ("grok_agent_execute", "grok_agent_fix", "grok_delegate", "grok_agent_consult"):
        assert tools[name]["readOnlyHint"] is False, name
    assert tools["grok_agent_update"]["destructiveHint"] is True
    assert tools["grok_agent_cancel"]["destructiveHint"] is True
