# Brainstorm / communication mode

**Goal:** think with the user (and optional Grok consult) **without** burning an execute loop.

## Tools

- Host reasoning first for product/UX choices
- `grok_agent_consult` / `grok_agent_review` for second opinions or codebase questions
- **No** `grok_agent_execute` in this mode

## Output

- Options table (2–4)
- Recommendation + risks
- Clear "ready to execute?" ask before switching to executor

## Economy

Brainstorm is the default when the user is exploring. Switching to execute requires an explicit build intent.
