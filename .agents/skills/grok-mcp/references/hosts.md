# Hosts

| Host | Skill path | MCP |
|---|---|---|
| Claude Code | `.claude/skills/grok-mcp/` | `.mcp.json` / desktop |
| Claude Desktop | open repo / copy skill | installer snippet |
| Cursor | `.agents/skills/grok-mcp/` | Cursor MCP + snippet |
| Codex CLI | `.codex/skills/grok-mcp/` | stdio launcher |
| Other | `.agents/skills/grok-mcp/` | stdio |

**All hosts:** call `grok_agent_session_begin` at the start of any MCP work. Do not paste skill references into user chat; run scripts; keep tool results compact.
