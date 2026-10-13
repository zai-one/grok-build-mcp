"""Agent-facing layer: router instructions, tool titles, short descriptions,
MCP prompts and guide resources (ZAI.ONE MCP standard, progressive disclosure).

L0 (always loaded): ROUTER_INSTRUCTIONS (<=1200 chars) and tool descriptions
(<=300 chars each). L2 (on demand): ``grok://guide/{topic}`` serves the files in
``skills/grok-mcp/references/`` -- the same text the skill loads, one source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "grok-mcp"
PACKAGE_GUIDE = Path(__file__).resolve().parent / "guide"
GUIDE_URL = "https://github.com/zai-one/grok-build-mcp/blob/main/skills/grok-mcp/references/{topic}.md"

# Intent router: (id, intent words, first tool, skill, guide topic). tests/test_routing_eval.py replays
# casual RU/EN requests against these lines and fails if any tool, skill or guide is unreachable.
ROUTES: tuple[tuple[str, str, str, str, str], ...] = (
    ("delegate", "сделай задачу, реализуй, напиши код, implement, build", "grok_agent_session_begin",
     "grok-mcp", "operate"),
    ("review", "ревью, проверь дифф, review, diff", "grok_agent_review", "grok-mcp", "verify"),
    ("consult", "обсудить, идеи, план, brainstorm, ask, plan", "grok_agent_consult", "grok-mcp",
     "brainstorm"),
    ("fix", "починить, падает, тесты, fix, failing, tests", "grok_agent_fix", "grok-mcp", "execute"),
    ("jobs", "фоновая, статус задачи, отменить, job, poll, cancel", "grok_agent_poll", "", "tools"),
    ("health", "здоровье, не работает, логин, модели, doctor, health, models", "grok_agent_status", "",
     "install"),
    ("update", "обновить мост, версия, update, version", "grok_agent_check_update", "", "update"),
    ("project", "настройки проекта, пресет, токены, project, preset, economy", "grok_agent_project", "",
     "hosts"),
)  # fmt: skip


def _router() -> str:
    head = (
        "grok-mcp (unofficial): local Grok CLI codes in an isolated git worktree, returns a verified "
        "receipt. Loop: grok_agent_session_begin -> grok_agent_session_next (do only the "
        "card) -> grok_agent_session_end. Prompts: delegate_task, review_diff."
    )
    lines = [f"- {intent}: " + "; ".join(p for p in (tool, skill, topic) if p) for _, intent, tool, skill, topic in ROUTES]
    tail = (
        "Never push or merge from the bridge; auth is `grok login`, never tokens in config. "
        "Retry only if the reason says retryable. "
        "Unsure? skill grok-mcp, grok://guide/index (topics) or grok://guide/tools (every tool)."
    )
    return "\n".join([head, "Intent -> first tool; skill; grok://guide/<topic>:", *lines, tail])


ROUTER_INSTRUCTIONS = _router()

TITLES: dict[str, str] = {
    "grok_agent_economy": "Token-economy playbook",
    "grok_agent_update": "Update bridge",
    "grok_agent_check_update": "Check for update",
    "grok_agent_project": "Project preset",
    "grok_agent_session_begin": "Begin session",
    "grok_agent_session_tick": "Session tick",
    "grok_agent_session_next": "Next session card",
    "grok_agent_session_end": "End session",
    "grok_agent_status": "Agent status",
    "grok_agent_start": "Start agent job",
    "grok_agent_poll": "Poll agent job",
    "grok_agent_cancel": "Cancel agent job",
    "grok_agent_consult": "Consult (read-only)",
    "grok_agent_review": "Review diff",
    "grok_agent_execute": "Execute in worktree",
    "grok_agent_fix": "Fix in worktree",
    "grok_delegate": "Delegate (sync)",
    "grok_delegate_plan": "Plan only",
    "grok_delegate_start": "Delegate (background)",
    "grok_delegate_poll": "Poll delegation",
    "grok_delegate_status": "Bridge status",
    "grok_delegate_doctor": "grok doctor",
    "grok_delegate_models": "List models",
    "grok_delegate_inspect": "grok inspect",
}

# Template: <Verb + object>. Use when ...; for ... use `<other>`. [Writes ...] Returns ...
DESCRIPTIONS: dict[str, str] = {
    "grok_agent_update": (
        "Update the bridge checkout from its git remote. Use after grok_agent_check_update reports a "
        "newer version. Without confirm it only reports steps; confirm=true pulls and reinstalls, then "
        "restart the host. Refuses a dirty checkout."
    ),
    "grok_agent_check_update": (
        "Compare the installed bridge with the latest GitHub release. Use when the user asks about updates "
        "or instructions show an update line. Read-only, cached 24 h. Returns current, latest, notes link, "
        "update command."
    ),
    "grok_agent_project": (
        "Read or set this project's Grok preset (.grok-mcp.json): off, cheap, standard, max. Use first "
        "when a job tool returns PROJECT_NOT_ENABLED. Without preset it reports; with preset it writes "
        "the file."
    ),
    "grok_delegate_status": (
        "Report bridge health. Use before the first job or when a job fails to start: grok binary/version, "
        "auth presence (auth.json never read), git, allowed roots/lanes_parent, permission and sandbox "
        "profile. Read-only."
    ),
    "grok_delegate_poll": (
        "Poll a job from grok_delegate_start. With job_id: state, result (branch, changed_files, commits, "
        "diffstat) and live phase/elapsed_s; early minutes in git with no worker are normal. Without "
        "job_id: newest jobs. Read-only."
    ),
}

GUIDE_TOPICS: dict[str, str] = {
    "operate": "Session loop and cards",
    "execute": "Typed fallback: consult, execute, poll, review",
    "verify": "Reading and verifying receipts",
    "brainstorm": "Consult/brainstorm before code",
    "install": "Install and host config",
    "hosts": "Claude Code, Cursor, Codex specifics",
    "update": "Updating the bridge",
    "security": "Security rules",
    "tools": "Tool reference",
    "feedback": "Filing feedback and issues",
}

PROMPTS: list[dict[str, Any]] = [
    {
        "name": "delegate_task",
        "title": "Delegate a coding task to Grok",
        "description": "Run one coding goal through the session loop and report the verified receipt.",
        "arguments": [
            {"name": "goal", "description": "What to build or fix, with acceptance checks.", "required": True},
        ],
    },
    {
        "name": "review_diff",
        "title": "Review a Grok branch",
        "description": "Review a finished job's branch/diff read-only and list blocking findings.",
        "arguments": [
            {"name": "job_id", "description": "Job id from a finished execute/delegate job.", "required": False},
        ],
    },
]


def apply_tool_metadata(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for tool in tools:
        name = tool["name"]
        if name in TITLES:
            tool["title"] = TITLES[name]
        if name in DESCRIPTIONS:
            tool["description"] = DESCRIPTIONS[name]
    return tools


def render_prompt(name: str, arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    args = arguments or {}
    if name == "delegate_task":
        goal = str(args.get("goal") or "(ask the user for the goal and its acceptance checks)")
        text = (
            f"Goal: {goal}\n"
            "1. grok_agent_session_begin(goal, host_budget='small') once.\n"
            "2. Loop grok_agent_session_next; do only the returned card (host_cmd | mcp_tool | end).\n"
            "3. If a card fails schema: grok_agent_consult -> grok_agent_execute -> grok_agent_poll -> "
            "grok_agent_review (grok://guide/execute).\n"
            "4. On done=true call grok_agent_session_end. Report branch, diffstat, checks, open risks.\n"
            "Never push or merge. One job at a time."
        )
        description = PROMPTS[0]["description"]
    elif name == "review_diff":
        job = str(args.get("job_id") or "")
        target = f"job {job}" if job else "the newest finished job (grok_agent_poll without job_id)"
        text = (
            f"Review {target}.\n"
            "1. grok_agent_poll(job_id) for branch, changed_files, diffstat.\n"
            "2. grok_agent_review(job_id) -- read-only review lane.\n"
            "3. Report blocking findings first, then nits; say whether checks ran and passed "
            "(grok://guide/verify). Do not merge or push."
        )
        description = PROMPTS[1]["description"]
    else:
        return None
    return {"description": description, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


def guide_index() -> str:
    rows = [f"- `grok://guide/{name}` — {label}" for name, label in GUIDE_TOPICS.items()]
    return "# grok-mcp guides\n\nRead only the topic you need.\n\n" + "\n".join(rows) + "\n"


def guide_text(topic: str) -> str | None:
    if topic == "index":
        return guide_index()
    if topic not in GUIDE_TOPICS:
        return None
    for path in (SKILL_DIR / "references" / f"{topic}.md", PACKAGE_GUIDE / f"{topic}.md"):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return f"Guide not bundled with this install. Read {GUIDE_URL.format(topic=topic)}\n"


def list_resources() -> list[dict[str, Any]]:
    return [
        {
            "uri": "grok://guide/index",
            "name": "guide-index",
            "title": "grok-mcp guides",
            "description": "Index of on-demand guides (session loop, fallback, verify, hosts, security).",
            "mimeType": "text/markdown",
        }
    ]


def list_resource_templates() -> list[dict[str, Any]]:
    return [
        {
            "uriTemplate": "grok://guide/{topic}",
            "name": "guide",
            "title": "grok-mcp guide topic",
            "description": "One guide: " + ", ".join(GUIDE_TOPICS) + ".",
            "mimeType": "text/markdown",
        }
    ]


def read_resource(uri: str) -> dict[str, Any] | None:
    prefix = "grok://guide/"
    if not uri.startswith(prefix):
        return None
    text = guide_text(uri[len(prefix):])
    if text is None:
        return None
    return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}
