---
name: grok-delegate
description: >
  Operate grok-delegate MCP after Grok CLI install+login. Economy sequence,
  consult/execute/poll, VPS HTTP, security. Refuse work if CLI/auth missing.
version: 0.5.1
---

# grok-delegate (runtime)

> **Unofficial.** Auth = local `grok login` only. Not affiliated with xAI/Grok/Anthropic/OpenAI.

## Preflight (every session)

```bash
grok --version
python -m grok_delegate --self-test   # binary + auth present required
```

If self-test fails on binary/auth → send user to skill **install-grok-mcp**. Do not proceed.

Host tools: `grok_agent_status` must succeed first.

## Economy sequence (save host tokens)

| # | Tool | Rule |
|---|---|---|
| 1 | `grok_agent_status` | once |
| 2 | `grok_agent_economy` | once — follow its do/dont |
| 3 | `grok_agent_consult` / `review` | questions |
| 4 | `grok_agent_execute` | tight objective + artifacts + 1–3 tests |
| 5 | `grok_agent_poll` | job_id only; read summary/changed_files/tests |
| 6 | Human | merges `grok/*` — never push from agent |

Env: `GROK_DELEGATE_ECONOMY=1`, `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1`

## Anti-waste

- Paths not paste dumps
- No execute for Q&A
- Low max_turns (8–16)
- One job; cancel stale
- No OAuth / `GROK_AGENT_SECRET` in config

## VPS

Only after local self-test green. See `docs/install/vps.md`. Bearer token ≠ Grok OAuth.

## Docs

`docs/START_HERE.md` · `docs/install/en.md` · `docs/economy.md` · `SECURITY.md`
