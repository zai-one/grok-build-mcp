---
name: install-grok-mcp
description: Token-cheap install and setup only for grok-delegate MCP (local stdio, FastMCP, VPS bearer HTTP). Use when the user needs wiring, env, or client config — not deep coding delegation.
version: 0.5.0
---

# install-grok-mcp (setup only)

> **Unofficial.** Not an official xAI/Grok product. No OAuth in MCP config.

Keep replies short. Point at docs; avoid pasting large file contents.

## Minimum local path

```bash
cd <REPO_PATH>
python -m venv .venv && source .venv/bin/activate
pip install -e .
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_ECONOMY=1
grok login   # local CLI session
python -m grok_delegate --self-test
```

## Client config

Use placeholders from `examples/claude_desktop.mcp.json`, `examples/claude-code.mcp.json`,
`examples/cursor.mcp.json`. Env keys only — **no secrets**.

## FastMCP

- Local: command `python -m grok_delegate.server` + env above  
- Remote: `docs/install/fastmcp.md` + `examples/fastmcp_proxy.py`

## VPS HTTP

```bash
openssl rand -hex 32 > <TOKEN_FILE>
export GROK_DELEGATE_HTTP_TOKEN_FILE=<TOKEN_FILE>
python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
```

TLS reverse proxy in front. Bearer = operator secret, **not** Grok OAuth.  
Unit: `examples/vps.systemd.service` · env: `examples/http.env.example`

## Doc map

| Need | File |
|---|---|
| Full install EN | `docs/install/en.md` |
| FastMCP | `docs/install/fastmcp.md` |
| VPS | `docs/install/vps.md` |
| Economy | `docs/economy.md` |
| Runtime skill | `grok-delegate` skill |

## Do not

- Invent ChatGPT click-paths
- Put tokens in repo or MCP JSON
- Run live execute jobs in this skill — hand off to `grok-delegate` skill
