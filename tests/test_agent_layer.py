"""ZAI.ONE MCP standard: L0 budget, prompts, guide resources, plugin packaging."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from grok_delegate import agent_layer
from grok_delegate.server import handle_jsonrpc, list_tools

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("token_budget", ROOT / "scripts" / "token_budget.py")
token_budget = importlib.util.module_from_spec(_spec)
sys.modules["token_budget"] = token_budget
_spec.loader.exec_module(token_budget)

TOOLS_KB = 32  # 24 tools incl. typed job schemas; measured ~28 KB compact


def _rpc(method: str, params: dict | None = None) -> dict:
    out = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert out is not None
    return out


def test_budget_clean() -> None:
    report = token_budget.Report()
    token_budget.LIMITS["tools_kb"] = TOOLS_KB
    token_budget.check_manifest({"instructions": agent_layer.ROUTER_INSTRUCTIONS, "tools": list_tools()}, report)
    for skill in token_budget.find_skills(ROOT):
        token_budget.check_skill(skill, report)
    assert not report.errors, report.errors
    assert not report.warnings, report.warnings


def test_instructions_are_a_router() -> None:
    text = _rpc("initialize", {"capabilities": {}})["result"]["instructions"]
    assert text.startswith(agent_layer.ROUTER_INSTRUCTIONS)
    assert len(agent_layer.ROUTER_INSTRUCTIONS) <= 1200
    for needle in ("grok_agent_session_begin", "grok_agent_session_next", "grok://guide/index", "delegate_task"):
        assert needle in text


def test_every_tool_has_title() -> None:
    tools = list_tools()
    assert {t["name"] for t in tools} == set(agent_layer.TITLES)
    assert all(t.get("title") and t.get("annotations") for t in tools)


def test_prompts() -> None:
    names = {p["name"] for p in _rpc("prompts/list")["result"]["prompts"]}
    assert names == {"delegate_task", "review_diff"}
    got = _rpc("prompts/get", {"name": "delegate_task", "arguments": {"goal": "fix bug"}})["result"]
    text = got["messages"][0]["content"]["text"]
    assert "fix bug" in text and "grok_agent_session_begin" in text
    assert _rpc("prompts/get", {"name": "nope"})["error"]["code"] == -32602


def test_guide_resources_match_skill_references() -> None:
    assert _rpc("resources/list")["result"]["resources"][0]["uri"] == "grok://guide/index"
    templates = _rpc("resources/templates/list")["result"]["resourceTemplates"]
    assert templates[0]["uriTemplate"] == "grok://guide/{topic}"
    for topic in agent_layer.GUIDE_TOPICS:
        ref = (ROOT / "skills" / "grok-mcp" / "references" / f"{topic}.md").read_text(encoding="utf-8")
        shipped = (ROOT / "grok_delegate" / "guide" / f"{topic}.md").read_text(encoding="utf-8")
        assert ref == shipped, f"run scripts/sync_skills.py ({topic})"
        body = _rpc("resources/read", {"uri": f"grok://guide/{topic}"})["result"]["contents"][0]["text"]
        assert body == ref
    assert _rpc("resources/read", {"uri": "grok://guide/nope"})["error"]["code"] == -32002


def test_plugin_manifests() -> None:
    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    cursor = json.loads((ROOT / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["plugins"][0]["name"] == claude["name"] == cursor["name"] == "grok-mcp"
    for manifest in (claude, cursor):
        assert manifest["mcpServers"]["grok-mcp"]["command"] == "uvx"
        assert (ROOT / manifest["skills"] / "grok-mcp" / "SKILL.md").is_file()
