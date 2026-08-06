---
name: grok-mcp
description: >
  Router for unofficial grok-mcp. ALWAYS: grok_agent_session_begin then loop grok_agent_session_next until done.
  Triggers: grok-mcp, session_next, plan, budget, grok login, install, execute.
  Never multi-step pip, secrets, or invent tools outside the card.
version: 1.0.0
metadata:
  short-description: "Session v1.2 navigator — one card at a time"
---

# grok-mcp

> Unofficial — not xAI/Grok. Auth = **`grok login`** only.

## Token budget protocol

1. **`grok_agent_session_begin`** once (`goal`, `host_budget=small`)
2. Loop **`grok_agent_session_next`** only — execute the returned `card` (host_cmd | mcp_tool | end)
3. When `done=true` → **`grok_agent_session_end`** if card says so, then stop

Do **not** open `references/*` unless card/skill_ref and you are blocked.
Do **not** re-plan in prose. Do **not** call tools not on the card.

## Never

OAuth in config · dumps · parallel jobs · claim OK without begin/next
