---
name: grok-delegate
description: Operate the local grok-delegate MCP bridge (stdio/HTTP, ACP transports, worktree receipts, token economy). Use when configuring Claude/Cursor MCP, VPS bearer HTTP, FastMCP proxy, economy playbook, or diagnosing transport/security issues.
version: 0.5.0
---

# grok-delegate skill

> **Unofficial.** Not an official xAI/Grok product. Not affiliated with Anthropic,
> OpenAI, or Codex. Auth = local `grok login` only.

## Install (token-cheap)

```bash
cd <REPO_PATH>
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_ECONOMY=1
python -m grok_delegate --self-test
```

Docs: `docs/install/en.md` · FastMCP: `docs/install/fastmcp.md` · VPS: `docs/install/vps.md`  
For install-only agent help: skill `install-grok-mcp`.

## Economy call sequence

| Order | Action | Notes |
|---|---|---|
| 1 | `grok_agent_status` | Once per session |
| 2 | `grok_agent_economy` | Load playbook; no args |
| 3 | `grok_agent_consult` / `review` | Q&A without worktrees |
| 4 | `grok_agent_execute` | Tight objective, artifacts, 1–3 tests |
| 5 | `grok_agent_poll` | `job_id` only; compact fields |
| 6 | Human merge | Never push/merge from the agent |

Env:

- `GROK_DELEGATE_ECONOMY=1` → lower default max_turns / timeout / reasoning
- `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1` → compact poll payloads

See `docs/economy.md`.

## Anti-waste rules (host agents)

- Don't paste large source into objectives — use paths.
- Don't use `execute` for questions.
- Don't set max_turns near 60 for routine work.
- Don't re-send full goals on poll.
- Don't request full event dumps unless debugging.
- One job at a time; cancel stale jobs.
- **Never** put OAuth, API keys, or `GROK_AGENT_SECRET` in MCP config.

## VPS

1. `grok login` on VPS user  
2. `openssl rand -hex 32 > <TOKEN_FILE>` → `GROK_DELEGATE_HTTP_TOKEN_FILE`  
3. `python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765`  
4. TLS reverse proxy; remote MCP with **Bearer only** (not OAuth)  
5. Templates: `examples/vps.systemd.service`, `examples/http.env.example`, `examples/fastmcp_proxy.py`

## Safe defaults

- Fail-closed roots required.
- Prefer backend transport `stdio` / `auto` before WebSocket.
- Managed WebSocket: ephemeral loopback secret — do not persist.
- HTTP bearer ≠ Grok OAuth.

## Operator checklist

1. `python -m grok_delegate --self-test`
2. Host: `grok_agent_status` → `grok_agent_economy`
3. Consult first; execute only on allowlisted temp/prod roots after review of receipts
4. Inspect `changed_files` + tests; human merges `grok/*`

## Client pointers

- Install i18n: `docs/install/{en,ru,zh-CN,es}.md`
- Templates: `examples/*.mcp.json`
- Transports: `docs/ACP-TRANSPORTS.md`
- Security: `SECURITY.md`
