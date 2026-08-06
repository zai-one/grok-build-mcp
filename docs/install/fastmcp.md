# FastMCP guide (English)

> ## ⚠️ Unofficial product disclaimer
>
> **Not** an official product of xAI, Grok, Anthropic, OpenAI, or Codex.
> Community software only. No OAuth in MCP config.

## When to use FastMCP

| Path | Use case |
|---|---|
| **Local stdio** | FastMCP (or any host) spawns `python -m grok_delegate.server` on this machine |
| **Remote proxy** | Grok + MCP run on a VPS; local FastMCP uses `create_proxy` (or equivalent) to a TLS URL |

## Local stdio (shortest path)

```bash
cd <REPO_PATH>
pip install -e .
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_ECONOMY=1
# FastMCP / host command:
python -m grok_delegate.server
```

Point FastMCP at that command with the same non-secret env vars as
[en.md](en.md).

## Remote proxy

1. On VPS: install Grok CLI, `grok login`, run HTTP MCP with bearer
   ([vps.md](vps.md)).
2. Expose **HTTPS** via reverse proxy (Caddy/nginx).
3. Locally: proxy with bearer header only — see
   [examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py).

```python
# Conceptual — replace placeholders; library APIs may vary by FastMCP version
from fastmcp import FastMCP  # if available in your install

# Prefer create_proxy / Client against https://mcp.example.invalid/mcp
# headers={"Authorization": "Bearer <OPERATOR_BEARER>"}
# NEVER put Grok OAuth into Authorization.
```

## Security rules

- Bearer = operator CSPRNG secret (`GROK_DELEGATE_HTTP_TOKEN` / `_FILE`)
- **Not** Grok OAuth, **not** API keys
- Prefer `TOKEN_FILE` over env for services
- Keep allowlisted roots tight on the VPS

## Related

- [vps.md](vps.md) · [economy.md](../economy.md) · [en.md](en.md)
