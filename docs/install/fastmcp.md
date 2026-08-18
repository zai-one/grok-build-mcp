# FastMCP guide (English)

> ## Unofficial product disclaimer
>
> **Not** an official product of xAI, Grok, Anthropic, OpenAI, or Codex.
> Community software only. No OAuth in MCP config.

FastMCP is **not** how you install the bridge on a VPS. The server install
path is [vps.md](vps.md): `python -m grok_delegate.server --transport http`
behind a TLS reverse proxy.

Use this page only if your **local host** already runs FastMCP (or any MCP
host that spawns a command).

## Local stdio (the working FastMCP path)

On the **same machine** as Grok CLI (Windows or POSIX). This is ordinary
stdio MCP. FastMCP, Claude, Cursor, and Codex all spawn a command this way.

**POSIX**

```bash
cd <REPO_PATH>
pip install -e .
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
python -m grok_delegate.server
```

**Windows PowerShell**

```powershell
cd <REPO_PATH>
py -3 -m pip install -e .
$env:GROK_DELEGATE_ALLOWED_ROOTS = "<PROJECT_ROOT>"
py -3 -m grok_delegate.server
```

Point the host at that command. Non-secret env vars: [en.md](en.md).
Do not put tokens in the host JSON for stdio.

## Remote

The remote server is the HTTP JSON-RPC described in [vps.md](vps.md), not a
FastMCP process. Local FastMCP cannot make that dialect into Streamable HTTP
by itself.

[examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py) is a **stub**: it
loads a bearer and prints a redacted URL. It does not call `FastMCP.as_proxy`.
Wiring a real Streamable HTTP adapter is out of tree.

If the host can send `POST` + `Authorization: Bearer` to
`https://<host>/mcp`, point it at the VPS URL. If the host requires
Streamable HTTP GET/SSE, use stdio over SSH instead of FastMCP:

```bash
# POSIX local host, Linux VPS
ssh <SERVICE_USER>@<VPS> "cd <REPO_PATH> && .venv/bin/python -m grok_delegate.server"
```

Windows OpenSSH equivalent: the remote command is still POSIX on the VPS;
the local `ssh.exe` invocation is Windows.

## Security rules

- Bearer = operator CSPRNG secret (`GROK_DELEGATE_HTTP_TOKEN` / `_FILE`)
- **Not** Grok OAuth, **not** API keys
- Prefer `TOKEN_FILE` over env for services
- Keep allowlisted roots tight on the VPS
- One HTTP process = one operator

## Related

- [vps.md](vps.md) · [economy.md](../economy.md) · [en.md](en.md)
