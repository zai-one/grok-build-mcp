---
name: grok-delegate
description: Operate the local grok-delegate MCP bridge (stdio host, ACP transports, worktree receipts). Use when configuring Claude/Cursor/Codex MCP, running self-test, or diagnosing transport/security issues.
version: 0.4.1
---

# grok-delegate skill

## Safe defaults

- Never put OAuth tokens, API keys, or `GROK_AGENT_SECRET` in MCP config files.
- Auth is the local Grok CLI session (`grok login`).
- Require `GROK_DELEGATE_ALLOWED_ROOTS` (fail-closed).
- Prefer `transport: stdio` (or `auto`) before WebSocket.
- Managed WebSocket generates an ephemeral loopback secret; do not persist it.

## Operator checklist

1. `python -m grok_delegate --self-test`
2. Host tool: `grok_agent_status`
3. Read-only: `grok_agent_consult` on a small question
4. Write: temporary git repo first; inspect `changed_files` + bridge-verifier tests
5. Human merges `grok/*` branches — this MCP never push/merges

## Client pointers

- Install docs: `docs/install/en.md` (+ ru / zh-CN / es)
- Templates: `examples/*.mcp.json`
- Transports: `docs/ACP-TRANSPORTS.md`
- Security: `SECURITY.md`
