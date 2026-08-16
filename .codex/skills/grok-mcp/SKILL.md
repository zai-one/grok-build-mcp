---
name: grok-mcp
description: >
  Router for unofficial grok-mcp. ALWAYS: grok_agent_session_begin then loop
  grok_agent_session_next until done. If a card fails schema, fall back to
  typed consult → execute → poll → review. Triggers: grok-mcp, session_next,
  grok login, execute. Never OAuth in config. Unpin is default.
version: 1.1.0
metadata:
  short-description: "Session v1.2 navigator — typed cards, unpin default"
---

# grok-mcp

> Unofficial — not xAI/Grok. Auth = **`grok login`** only.

## Token budget protocol

1. **`grok_agent_session_begin`** once (`goal`, `host_budget=small`)
2. Loop **`grok_agent_session_next`** — execute only the `card` (`host_cmd` | `mcp_tool` | `end`)
3. Execute cards carry a full `task`; poll cards are `{job_id}` only
4. Schema / unknown-tool failure → typed consult → execute → poll → review (`references/execute.md`)
5. When `done=true` → **`grok_agent_session_end`**, then stop

Do **not** open `references/*` unless blocked. Do **not** re-plan. Do **not** pin a Grok CLI version.

## Never

OAuth in config · dumps · parallel jobs · push/merge from the bridge
