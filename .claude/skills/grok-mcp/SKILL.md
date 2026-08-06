---
name: grok-mcp
description: >
  Single router for the unofficial grok-mcp (grok-delegate) package. Use whenever
  the user installs, updates, wires, debugs, or runs Grok MCP; delegates coding
  via Grok CLI; verifies results; brainstorms without execute; or wants a GitHub
  Issue after repeated MCP friction. Triggers on: grok-mcp, grok-delegate, install
  MCP, update MCP, MCP broken, tools missing, grok login, economy, worktree,
  consult, execute, poll, verify, review, brainstorm, open issue, improve MCP.
  Prefer this skill over inventing multi-step pip guides or pasting secrets.
version: 0.6.0
metadata:
  short-description: "Router: install/update/operate/execute/verify/brainstorm/feedback for Grok MCP"
---

# grok-mcp (router)

> **Unofficial community project** — not xAI, Grok, Anthropic, or OpenAI.
> Auth = local **`grok login`** only. Never put OAuth, API keys, or `GROK_AGENT_SECRET` in MCP config or Issues.

Progressive disclosure: keep this file short. **Read the matching `references/*` only when that mode applies.**

## HARD GATE (every mode except pure docs)

1. Grok CLI on PATH: `grok --version`
2. Logged in as same OS user: `grok login` completed
3. Package + roots: one-command install or `docs/EASY.md`
4. Self-test: `python -m grok_delegate --self-test` → binary PASS + auth present

If gate fails → **install** mode. Do not claim MCP works.

Optional script: [`scripts/check_ready.sh`](scripts/check_ready.sh)

## Mode router

| User signal | Mode | Load |
|---|---|---|
| install, setup, wire Claude/Cursor | **install** | [`references/install.md`](references/install.md) |
| update, upgrade, pull, reinstall | **update** | [`references/update.md`](references/update.md) + [`scripts/update_mcp.sh`](scripts/update_mcp.sh) |
| MCP broken, tools missing, auth fail | **triage** | install/update + [`references/security.md`](references/security.md); after 2+ failures → **feedback** |
| implement, fix code, build feature | **executor** | [`references/executor.md`](references/executor.md) + [`templates/goal-brief.md`](templates/goal-brief.md) |
| check, verify, tests, review result | **verifier** | [`references/verifier.md`](references/verifier.md) |
| brainstorm, design, how should we… | **brainstorm** | [`references/brainstorm.md`](references/brainstorm.md) — **no execute** |
| MCP sucks, improve product, file bug | **feedback** | [`references/feedback-issues.md`](references/feedback-issues.md) + `templates/issue-*.md` |
| normal coding session | **operate** | [`references/operate.md`](references/operate.md) + [`references/modes.md`](references/modes.md) |

Default interaction model: [`references/modes.md`](references/modes.md)

## Economy (always)

| Host agent pays | Grok CLI / MCP pays |
|---|---|
| Short plan + tool calls | Long coding loop, diffs, tests |

1. Once: `grok_agent_status` then `grok_agent_economy`
2. Prefer **consult/review** over **execute**
3. Execute: tight objective + artifacts + 1–3 tests; low max_turns (8–16)
4. Poll with job_id only; read summary/changed_files — not full dumps
5. Human merges `grok/*` branches — agent does not push/merge product branches

Env: `GROK_DELEGATE_ECONOMY=1`, `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1`

## Never

- Multi-step manual pip tutorials (use install script)
- Secrets in JSON, Issues, or chat paste
- Execute for pure Q&A
- Fake success without self-test / status

## Docs

`docs/EASY.md` · `docs/economy.md` · `SECURITY.md` · `docs/SKILLS.md`
