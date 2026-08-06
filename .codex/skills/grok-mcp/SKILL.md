---
name: grok-mcp
description: >
  Router for unofficial grok-mcp. ALWAYS use for MCP install/update/debug/delegate.
  First: grok_agent_session_begin(goal, host_budget). Follow plan tools only. Triggers: grok-mcp,
  session_begin, plan, budget, grok login, economy. Never multi-step pip or secrets.
version: 0.9.0
metadata:
  short-description: "Session v1.1 plan compiler + budget guard for grok-mcp"
---

# grok-mcp

> Unofficial — not xAI/Grok. Auth = **`grok login`** only.

## Protocol

1. **`grok_agent_session_begin`** `intent` + optional `goal` + `host_budget` (tiny|small|normal)
2. Execute **only** `plan[]` / `recommended_tools` (never invent tools; honor `deny_tools`)
3. **`grok_agent_session_tick`** with `tool_used`/`step_done`; stop if `force_end`
4. **`grok_agent_session_end`** → receipt + `budget_report` + `lesson`

Open **one** `references/*` only if `skill_ref` and plan insufficient.

## Budget (token budget)

Host executes plan; does **not** re-plan in prose. Caps from `budget`. Economy on begin.

## Never

Secrets · tools outside plan · long docs when plan exists · OK without begin
