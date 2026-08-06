# Executor mode

**Goal:** change code via Grok MCP with minimal host tokens.

## When

User wants implementation, fixes, refactors, features — path is clear enough.

## When not

Pure Q&A, architecture debate, or "what should we do?" → brainstorm first.

## Procedure

1. HARD GATE ok (`scripts/check_ready.sh` or self-test).
2. Fill [`templates/goal-brief.md`](../templates/goal-brief.md) mentally (objective, artifacts, tests).
3. `grok_agent_execute` (or plan→execute if required by tools) with **tight** objective.
4. Low max_turns (8–16). Economy env on.
5. `grok_agent_poll` with job_id until terminal; read summary + changed_files + tests.
6. Hand human a short receipt; they merge `grok/*` if needed.

## Anti-patterns

- Re-running execute with the entire chat history as objective
- Host rewriting all files while MCP job is still running
- Multiple parallel jobs without cancel
