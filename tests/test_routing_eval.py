"""Routing eval: casual RU/EN requests -> router line -> first tool / skill / guide.

Each case must match exactly one router line by intent words (4-char stems); the expected tool must be
reachable from that line (on it, in its skill, or in its guide). The suite also fails when any tool,
skill or guide topic is unreachable from the router or its grok://guide/index / tools fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from grok_delegate import agent_layer
from grok_delegate.server import list_tools

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/evals/routing_cases.json").read_text(encoding="utf-8"))
WORD = re.compile(r"[a-zа-яё0-9]+")
ROUTER = agent_layer.ROUTER_INSTRUCTIONS
SKILLS = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))


def stems(text: str) -> set[str]:
    return {w[:4] for w in WORD.findall(text.lower().replace("ё", "е")) if len(w) >= 3}


def route_for(request: str) -> tuple[str, str, str, str, str]:
    words = stems(request)
    scores = {r[0]: len(stems(r[1]) & words) for r in agent_layer.ROUTES}
    best = max(scores.values())
    winners = [rid for rid, score in scores.items() if score == best and score > 0]
    assert len(winners) == 1, (request, winners or "no route")
    return next(r for r in agent_layer.ROUTES if r[0] == winners[0])


def skill_text(name: str) -> str:
    if not name:
        return ""
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_case_count_and_languages():
    assert 15 <= len(CASES) <= 25
    assert any(re.search("[а-я]", c["request"]) for c in CASES)
    assert any(not re.search("[а-я]", c["request"]) for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["request"][:40] for c in CASES])
def test_request_routes_to_tool_skill_and_guide(case):
    rid, intent, tool, skill, topic = route_for(case["request"])
    assert rid == case["route"]
    line = next(ln for ln in ROUTER.splitlines() if ln.startswith(f"- {intent}:"))
    for part in (tool, skill, topic):
        assert not part or part in line
    if skill:
        assert skill in SKILLS
    body = agent_layer.guide_text(topic)
    assert body and not body.startswith("Guide not bundled"), topic
    assert case["tool"] in " ".join((line, skill_text(skill), body)), (case["tool"], rid)


def test_every_tool_skill_and_topic_reachable():
    names = {t["name"] for t in list_tools()}
    for route in agent_layer.ROUTES:
        assert route[2] in names, route[2]
    tool_map = agent_layer.guide_text("tools") or ""
    missing = sorted(n for n in names if not re.search(rf"\b{n}\b", tool_map))
    assert not missing, f"add to grok://guide/tools: {missing}"
    for skill in SKILLS:
        assert skill in ROUTER
    routed = {r[4] for r in agent_layer.ROUTES}
    index = agent_layer.guide_index()
    for topic in agent_layer.GUIDE_TOPICS:
        assert topic in routed or f"grok://guide/{topic}" in index
        assert agent_layer.guide_text(topic)
    assert "grok://guide/index" in ROUTER and "grok://guide/tools" in ROUTER
    assert len(ROUTER) <= 1200
