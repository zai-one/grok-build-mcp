# Hosts

Install skill `grok-mcp` under:

- `.cursor/skills/grok-mcp/` (Cursor)
- `.claude/skills/grok-mcp/`
- `.codex/skills/grok-mcp/`
- `.agents/skills/grok-mcp/`

Canonical source: `skills/grok-mcp`. Sync: `python scripts/sync_skills.py`.

**Rule:** host executes navigator cards; does not re-plan in prose.
If a card fails, fall back to typed tools — same for every host.
