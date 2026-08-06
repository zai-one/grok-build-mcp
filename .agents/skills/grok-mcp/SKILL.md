---
name: grok-mcp
description: >
  Router for unofficial grok-mcp. ALWAYS use for install/update/wire/debug/grok MCP, delegate coding, verify, brainstorm-without-heavy-work, or MCP Issues. Triggers: grok-mcp, grok login, MCP broken, economy, execute/goal/poll, verify, brainstorm. Prefer MCP tools over long docs; never multi-step pip or secrets.
version: 0.7.0
metadata:
  short-description: "Token-cheap router for grok-mcp"
---

# grok-mcp

> Unofficial — not xAI/Grok/Anthropic/OpenAI. Auth = **`grok login`** only. No OAuth/keys in MCP config/Issues.

## Token budget (host)

| Load | When |
|---|---|
| This file only | most turns |
| **1** `references/*` | need mode detail |
| templates/ | fill, don't narrate |
| scripts/ | **run**, don't cat |
| `docs/*` | last resort |

Prefer MCP `grok_agent_economy` over `docs/economy.md`.

## Gate

`grok --version` · `grok login` · install (`docs/EASY.md`) · `python -m grok_delegate --self-test` → binary+auth PASS

Fail → **install**. `scripts/check_ready.sh`

## Route

| Signal | Mode | Open |
|---|---|---|
| install/setup/wire | install | `references/install.md` |
| update/pull | update | `references/update.md` |
| broken/auth/tools | triage | install + `references/security.md` → feedback if 2+ |
| implement/fix/build | execute | `references/execute.md` |
| check/tests/review done | verify | `references/verify.md` |
| design/options | brainstorm | `references/brainstorm.md` |
| MCP product gap | feedback | `references/feedback.md` |
| default | operate | `references/operate.md` |

Also: `references/tools.md` · `references/hosts.md`

## Economy

Env: `GROK_DELEGATE_ECONOMY=1 GROK_DELEGATE_ECONOMY_COMPACT_POLL=1`  
Once/session: `grok_agent_status` → `grok_agent_economy`  
light ≪ execute · tight briefs · compact polls · one job · no dumps

## Never

multi-step pip · secrets in JSON/Issues · execute for Q&A · OK without gate
