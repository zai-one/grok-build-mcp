# Remote HTTP install (English)

> ## Unofficial product disclaimer
>
> **Not** an official product of xAI, Grok, Anthropic, OpenAI, or Codex.
> Community software only. **No OAuth in MCP config.**

This page is the **one install path** for running the bridge on another
machine than the host editor. Local day-to-day use is still stdio:
[en.md](en.md).

## What this transport is

`python -m grok_delegate.server --transport http` is **private JSON-RPC over
HTTP with a static Bearer token**. It is **not** MCP Streamable HTTP: there is
no SSE, no OAuth, no `MCP-Session-Id`. `GET /mcp` returns **405**.

| Piece | Fact |
|---|---|
| Bind default | `127.0.0.1:8765` |
| JSON-RPC | `POST /mcp` only (`POST /` is 404) |
| Health | `GET /healthz` — no auth, no jobs, no roots, no token |
| Ready | `GET /readyz` — Bearer required; means “token accepted”, **not** “Grok CLI is logged in” |
| TLS | **Not in this process.** Put Caddy/nginx in front. |
| Non-loopback | Refused unless `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1` |

Hosts that only speak Streamable HTTP (SSE GET, OAuth discovery) will not
speak this dialect. For those hosts use stdio on the VPS over SSH, not this
URL. FastMCP is **not** the server install path; see [fastmcp.md](fastmcp.md).

One HTTP process is one operator. The bearer identifies the process, not a
person. A second `initialize` on the same process returns JSON-RPC error
`ONE_CLIENT_PER_PROCESS`. Remote tools spend the **VPS user’s** `grok login`.

## Linux VPS path

Commands below are **Linux**. They were **not** executed on the machine that
wrote this page (Windows, no Docker, no WSL). See
[Not verified and why](#not-verified-and-why). The same flags and paths are
what the code actually reads.

Replace `<REPO_PATH>`, `<PROJECT_ROOT>`, `<SERVICE_USER>`. Do not paste a live
token into chat, git, or this file.

### 1. Install Grok CLI and log in (Linux)

```bash
# Install Grok CLI from upstream docs, then:
grok login
grok models
```

`grok models` must succeed for the **same OS user** that will run systemd.
The bridge never reads `auth.json`.

### 2. Install the bridge (Linux)

```bash
git clone https://github.com/zai-one/grok-mcp.git <REPO_PATH>
cd <REPO_PATH>
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Enable the project the jobs will touch (`<PROJECT_ROOT>` must be an exact
allowlisted git root):

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
```

Lanes default to `<PROJECT_ROOT>/.grok/lanes`. Do **not** set
`GROK_DELEGATE_LANES_PARENT` unless you are overriding that default.

Jobs refuse until `<PROJECT_ROOT>/.grok-mcp.json` exists (opt-in). Create it
from a stdio session with `grok_agent_project`, or write the JSON yourself
on the VPS.

### 3. Issue a token (Linux)

```bash
sudo mkdir -p /etc/grok-delegate
python3 -c "import secrets; print(secrets.token_hex(32))" | sudo tee /etc/grok-delegate/token >/dev/null
sudo chmod 600 /etc/grok-delegate/token
sudo chown <SERVICE_USER>:<SERVICE_USER> /etc/grok-delegate/token
```

`openssl rand -hex 32` is equivalent if `openssl` is installed. Set **one** of:

- `GROK_DELEGATE_HTTP_TOKEN_FILE=/etc/grok-delegate/token` (preferred for systemd)
- `GROK_DELEGATE_HTTP_TOKEN` (process env; `systemctl show` can print it — do not use `Environment=` for the raw token)

Setting both is a start error. An empty token file is a start error. Missing
token is a start error, **including on loopback**.

#### Rotate

1. Write a new value to `/etc/grok-delegate/token` (same path, new hex).
2. `sudo systemctl restart grok-delegate`.
3. Put the new value in the host’s Bearer header.
4. The old value dies with the old process. There is no overlap window and no
   MCP tool that rotates the token.

### 4. systemd (Linux)

Unit template: [examples/vps.systemd.service](../../examples/vps.systemd.service).
Env template: [examples/http.env.example](../../examples/http.env.example).

This unit starts **this** HTTP JSON-RPC process. It is not a FastMCP proxy.

```bash
sudo cp <REPO_PATH>/examples/vps.systemd.service /etc/systemd/system/grok-delegate.service
# edit User, Group, WorkingDirectory, ExecStart, ALLOWED_ROOTS, TOKEN_FILE
sudo systemctl daemon-reload
sudo systemctl enable --now grok-delegate
sudo systemctl status grok-delegate
```

| Need | Command |
|---|---|
| Logs | `journalctl -u grok-delegate -f` |
| Last hour | `journalctl -u grok-delegate --since "1 hour ago"` |
| Restart after crash | `Restart=on-failure` in the unit (already set) |
| Rotation | systemd-journald (`SystemMaxUse=` in `journald.conf`). This unit does not ship logrotate. |

Access logs write method + path **without** the query string. The bearer is
registered with the receipt redactor so a worker that dumps env cannot echo it.

### 5. Confirm loopback before TLS (Linux)

```bash
curl -sS http://127.0.0.1:8765/healthz
# -> {"ok": true, "service": "grok-delegate", "transport": "http", "mcp_binding": "private-jsonrpc"}

curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/mcp
# -> 405

curl -sS -D - -o /dev/null http://127.0.0.1:8765/readyz
# -> HTTP/1.0 401, WWW-Authenticate: Bearer, body {"error":"unauthorized"}
```

Firewall: do not publish `:8765` on the internet. Only the TLS proxy is public.

### 6. TLS reverse proxy (Linux)

This process **never** terminates TLS. If you bind it on a public address
without a proxy, clients speak plaintext and the server will not start unless
you set `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1` — which still does **not**
add TLS; it only admits you meant to bind plaintext off loopback (for example
a container whose proxy is another container). Prefer `127.0.0.1` plus a
proxy on the same host.

Minimal Caddy (Linux VPS, hostname you control):
[examples/vps.Caddyfile](../../examples/vps.Caddyfile).

```
mcp.example.invalid {
    reverse_proxy 127.0.0.1:8765
}
```

Caddy issues certificates when DNS for that hostname points at the VPS.
Replace `mcp.example.invalid`. Do not commit live hostnames that identify
your machine if that is a secret in your environment.

nginx equivalent (also not executed here): `proxy_pass http://127.0.0.1:8765;`
inside a `server { listen 443 ssl; ... }` that you already trust.

Public URL shape: `https://mcp.example.invalid/mcp` for JSON-RPC POST.

## Connect a host

The server accepts:

```http
POST /mcp HTTP/1.1
Authorization: Bearer <OPERATOR_BEARER>
Content-Type: application/json
```

`initialize` echoes a handshake-era `protocolVersion`: `2024-11-05` if the
client asked for it, otherwise `2025-06-18` when the request is unknown or
newer (including `2026-07-28`). A host that requires Streamable HTTP GET/SSE
will fail; use SSH stdio instead:

```bash
ssh <SERVICE_USER>@<VPS> 'cd <REPO_PATH> && .venv/bin/python -m grok_delegate.server'
```

That is ordinary stdio MCP over SSH, not this HTTP dialect.

Do **not** put Grok OAuth or API keys in `Authorization`.

## Health

| Request | Auth | Meaning |
|---|---|---|
| `GET /healthz` → 200 `{"ok":true,"service":"grok-delegate","transport":"http","mcp_binding":"private-jsonrpc"}` | none | Process is accepting HTTP. **No** Grok session check. **No** token check. Body has no tools, roots, or token. |
| `GET /readyz` → 401 `{"error":"unauthorized"}` | missing/wrong Bearer | Process is up; bearer rejected. |
| `GET /readyz` → 200 `{"ok":true,"ready":true}` | valid Bearer | Bearer matches. **Not** “Grok CLI is logged in”. |
| `GET /mcp` → 405 `{"error":"method_not_allowed"}` + `Allow: POST` | none | You found the JSON-RPC path; use POST. |
| `POST /mcp` without Bearer → 401 | — | Same as readyz 401. |

Monitor `/healthz` from the VPS loopback (or through the proxy). Alert on
non-200 or connection refused. Do **not** treat `/healthz` as proof that jobs
can run.

## Three connection errors

Signals below assume `curl` against the URL you configured. Replace the URL.
Never put the live token on the command line in a shared log; use a header
file.

### 1. Nothing is listening

**Signal:** `Failed to connect` / `Connection refused` to `127.0.0.1:8765`.

On Linux: `systemctl is-active grok-delegate` is not `active`, or `ss -ltn |
grep 8765` is empty. `journalctl -u grok-delegate -n 50` shows the Python
traceback (missing token, both TOKEN and TOKEN_FILE set, empty file, or
non-loopback without the flag).

This is **not** a TLS problem and **not** a wrong bearer: the TCP handshake
never completed.

### 2. Process up, bearer wrong

**Signal:** `GET /healthz` is **200** with the health JSON (`mcp_binding` is
`private-jsonrpc`), and `GET /readyz` or `POST /mcp` is **401**
`{"error":"unauthorized"}` with `WWW-Authenticate: Bearer`.

Wrong file, stale token after rotate, extra whitespace if you used TOKEN env
and wrapped it in quotes, or the host is not sending `Authorization: Bearer`.
A 401 with `/healthz` 200 means you reached **this** process.

### 3. TLS proxy, not the bridge

**Signal:** `http://127.0.0.1:8765/healthz` is 200, and
`https://mcp.example.invalid/healthz` fails with a certificate error, DNS
error, connection timeout, or HTTP 502.

| Public error | Meaning |
|---|---|
| certificate verify failed / unknown CA | Proxy cert or hostname mismatch. The bridge is not involved. |
| timeout on `:443`, loopback `:8765` works | Firewall / DNS / proxy not listening. |
| 502 Bad Gateway | Proxy is up; it cannot reach `127.0.0.1:8765`. |
| `GET https://…/mcp` → 405 with `Allow: POST` | Proxy **is** hitting this bridge. The host wanted Streamable HTTP GET/SSE. |

## Windows loopback (this OS)

systemd, Caddy, and a public hostname are Linux-VPS concerns. On Windows the
same Python server binds loopback. Token generation:

```powershell
# Windows PowerShell — loopback only. Do not use /etc paths here.
$tok = Join-Path $env:TEMP "grok-delegate.http.token"
py -3 -c "import secrets, pathlib, os; p=pathlib.Path(os.environ['TEMP'])/'grok-delegate.http.token'; p.write_text(secrets.token_hex(32), encoding='utf-8')"
$env:GROK_DELEGATE_HTTP_TOKEN_FILE = $tok
# Do not also set GROK_DELEGATE_HTTP_TOKEN
py -3 -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
```

In another PowerShell:

```powershell
curl.exe -sS http://127.0.0.1:8765/healthz
curl.exe -sS -o NUL -w "%{http_code}" http://127.0.0.1:8765/mcp
```

Start without a token (expect the process to exit):

```powershell
Remove-Item Env:GROK_DELEGATE_HTTP_TOKEN_FILE -ErrorAction SilentlyContinue
Remove-Item Env:GROK_DELEGATE_HTTP_TOKEN -ErrorAction SilentlyContinue
py -3 -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
# ValueError: HTTP transport requires GROK_DELEGATE_HTTP_TOKEN, including on loopback
```

## Not verified and why

| Claim | Why it was not run here |
|---|---|
| Linux `python3 -m venv`, systemd enable, `journalctl` | Authoring OS is Windows. No WSL, no Docker. |
| Caddy / nginx TLS, Let’s Encrypt, a real DNS name | No domain and no Linux proxy on this machine. |
| `grok login` + live host (Claude/Cursor/Codex) against this HTTP URL | Would spend a Grok session and needs a host that accepts private JSON-RPC. |
| `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1` bind of `0.0.0.0` | Intentionally not bound. The refusal **without** the flag is tested. |
| FastMCP `create_proxy` against this URL | Library stub; see [fastmcp.md](fastmcp.md). |
| logrotate | Logs go to journald; unit does not ship a logrotate file. |

What **was** run on this Windows machine: `py -3 -m pytest tests -q` —
**753 passed, 1 skipped, 79 subtests**, exit 0. HTTP auth, `/healthz` body,
401/405/415, token-not-in-logs, redaction of the bearer, and
refuse-without-token are in that suite. A loopback
`python -m grok_delegate.server --transport http` smoke is in
`Service/Handoffs/http-remote-install-verification.md`.

## Related

- [en.md](en.md) · [fastmcp.md](fastmcp.md) · [economy.md](../economy.md)
- Root [SECURITY.md](../../SECURITY.md)
