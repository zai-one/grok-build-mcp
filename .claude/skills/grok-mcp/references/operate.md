# Operate (day-to-day)

## Session start

```text
grok_agent_status → grok_agent_economy → (then pick mode)
```

## Typical loop

1. Status once if env/roots unclear
2. Economy playbook once per session
3. Small questions → consult/review
4. Implementation → execute with brief from `templates/goal-brief.md`
5. Poll until done; summarize with `templates/receipt-short.md`

## Env

```bash
export GROK_DELEGATE_ECONOMY=1
export GROK_DELEGATE_ECONOMY_COMPACT_POLL=1
# roots set by installer env file
source ~/.config/grok-mcp/env
```

## VPS / HTTP

Only after local self-test green. See `docs/install/vps.md`. Bearer token is **not** Grok OAuth.
