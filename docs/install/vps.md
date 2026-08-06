# VPS guide (English)

> ## ⚠️ Unofficial product disclaimer
>
> **Not** an official product of xAI, Grok, Anthropic, OpenAI, or Codex.
> Community software only. **No OAuth in MCP config.**

## Goal

Run Grok CLI + `grok-delegate` on a VPS. Connect Claude remote MCP, Cursor URL
MCP, or a local FastMCP proxy over **HTTPS + bearer** — not OAuth.

## Steps

### 1. On the VPS — CLI auth

```bash
# Install Grok CLI per upstream docs, then:
grok login
grok models   # confirm session; never paste tokens into chat
```

### 2. Install this package

```bash
git clone <REPO_URL> <REPO_PATH>
cd <REPO_PATH>
python -m venv .venv && source .venv/bin/activate
pip install -e .
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_ECONOMY=1
```

### 3. Bearer token (operator secret, not OAuth)

```bash
openssl rand -hex 32 > <TOKEN_FILE>
chmod 600 <TOKEN_FILE>
export GROK_DELEGATE_HTTP_TOKEN_FILE="<TOKEN_FILE>"
# Do NOT also set GROK_DELEGATE_HTTP_TOKEN
```

### 4. HTTP MCP on loopback

```bash
python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
```

Or systemd: [examples/vps.systemd.service](../../examples/vps.systemd.service)  
Env template: [examples/http.env.example](../../examples/http.env.example)

| Method | Path | Auth |
|---|---|---|
| `GET` | `/healthz` | none |
| `GET` | `/readyz` | bearer when configured |
| `POST` | `/mcp` | bearer |

### 5. TLS reverse proxy

Terminate TLS in Caddy/nginx/Traefik → `http://127.0.0.1:8765`.  
Public URL shape: `https://mcp.example.invalid/mcp`

### 6. Connect clients

| Client | How |
|---|---|
| Claude remote MCP | HTTPS URL + `Authorization: Bearer <OPERATOR_BEARER>` |
| FastMCP | `create_proxy` — [fastmcp.md](fastmcp.md) |
| Cursor (URL MCP) | Same URL + bearer header |

## Checklist

- [ ] `grok login` on VPS user that runs the service
- [ ] Roots allowlist set; economy on if desired
- [ ] Bearer from `openssl rand` in a file outside git
- [ ] Process binds `127.0.0.1`; only proxy is public
- [ ] **No** Grok OAuth / API keys in MCP JSON or bearer field
- [ ] Firewall blocks direct `:8765` from the internet

## Related

- [en.md](en.md) · [economy.md](../economy.md) · [../SECURITY.md](../SECURITY.md) if present · root [SECURITY.md](../../SECURITY.md)
