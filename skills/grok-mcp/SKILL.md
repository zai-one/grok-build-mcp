---
name: grok-mcp
description: >
  Router for unofficial grok-mcp. ALWAYS use for install/update/wire/debug/grok MCP,
  delegate coding, verify, brainstorm, or MCP Issues. First tool: grok_agent_session_begin.
  Triggers: grok-mcp, session_begin, grok login, economy, execute/poll, verify, brainstorm.
  Prefer session_* + MCP tools over long docs; never multi-step pip or secrets.
version: 0.8.0
metadata:
  short-description: "Session Protocol v1 router for grok-mcp"
---

# grok-mcp

> Unofficial — not xAI/Grok. Auth = **`grok login`** only. No OAuth/keys in config/Issues.

## Token budget

| Load | When |
|---|---|
| This file | most turns |
| **1** reference | only if `session_begin.skill_ref` says so |
| templates/ | fill, don't narrate |
| scripts/ | **run**, don't cat |

## Protocol (always)

1. **`grok_agent_session_begin`** (intent: auto|brainstorm|execute|verify|install|update|triage|feedback)
2. Use **only** `recommended_tools` from the response
3. **`grok_agent_session_tick`** while jobs run (compact; `verbose` default false)
4. **`grok_agent_session_end`** → short receipt (± suggest_issue draft)

Do **not** open `references/*` unless begin returns `skill_ref` and you need that one file.

## Gate

`grok --version` · `grok login` · EASY install · self-test. Fail → intent `install`/`triage`.

## Economy

session_begin enables compact mode. light ≪ execute · tight briefs · one job · no dumps.

Env: `GROK_DELEGATE_ECONOMY=1` `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1`

## Never

multi-step pip · secrets · execute for Q&A · claim OK without gate/session_begin
