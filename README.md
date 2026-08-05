# grok-delegate (MCP)

**Version 0.4.1** — Local **stdio MCP** bridge that delegates coding work to the
host [Grok CLI](https://grok.x.ai/) agent with isolated git worktrees, typed
receipts, and three explicit backend transports.

> **Security one-liner:** this repository must never contain OAuth tokens, API
> keys, or WebSocket secrets. Model auth is the **local Grok CLI session**
> (`grok login` → machine-local auth store). MCP config must only list paths and
> non-secret policy env vars.

## Install guides

| Language | Guide |
|---|---|
| English | [docs/install/en.md](docs/install/en.md) |
| Русский | [docs/install/ru.md](docs/install/ru.md) |
| 简体中文 | [docs/install/zh-CN.md](docs/install/zh-CN.md) |
| Español | [docs/install/es.md](docs/install/es.md) |

Also: [SECURITY](SECURITY.md) · [CONTRIBUTING](CONTRIBUTING.md) ·
[ACP transports](docs/ACP-TRANSPORTS.md) · [Client setup examples](examples/)

## What it does

| Capability | Detail |
|---|---|
| MCP surface | 8 typed `grok_agent_*` tools + 8 legacy `grok_delegate*` aliases |
| Host transport | **stdio JSON-RPC MCP** (what Claude / Cursor / Codex spawn) |
| Agent backends | `legacy` headless · ACP `stdio` · ACP **loopback WebSocket** |
| Isolation | External git worktree on `grok/*` branch — **no push / no merge** |
| Evidence | Diffstat, expected artifacts, independent `bridge-verifier` tests |
| Fail-closed roots | `GROK_DELEGATE_ALLOWED_ROOTS` required; empty allowlist rejects work |

**WebSocket note:** MCP clients still connect over **stdio**. WebSocket is the
optional ACP channel between this bridge and a local `grok agent serve` daemon
(loopback + ephemeral secret only).

## Prerequisites

- Python **3.10+**
- **Grok CLI** installed and authenticated (`grok login`)
- **git** available on `PATH`

## Quick install

```bash
cd <REPO_PATH>
python -m pip install -e ".[test]"
```

Configure allowlisted project roots (fail-closed):

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"   # outside the repo
export GROK_DELEGATE_JOBS_DIR="<JOBS_DIR>"           # optional durable jobs
```

## Quick run

```bash
# stdio MCP server (default entry for desktop hosts)
grok-delegate
# or:
python -m grok_delegate.server

# operator checks (no host restart needed)
python -m grok_delegate --self-test
python -m pytest -q
```

## Connect clients

Copy a template from [`examples/`](examples/) and replace placeholders only
(`<REPO_PATH>`, `<PROJECT_ROOT>`, …). **Never** put `GROK_AGENT_SECRET`, OAuth
tokens, or API keys in these files.

| Client | How |
|---|---|
| **Claude Desktop** | Merge [`examples/claude_desktop.mcp.json`](examples/claude_desktop.mcp.json) into `claude_desktop_config.json` |
| **Claude Code** | Project or user [`.mcp.json`](examples/claude-code.mcp.json) |
| **Cursor** | [`.cursor/mcp.json`](examples/cursor.mcp.json) or Cursor MCP settings |
| **Codex CLI** | [`examples/codex.cli.example.sh`](examples/codex.cli.example.sh) → `codex mcp add` |
| **VS Code / Continue** | Same stdio command/args/env pattern as Claude |
| **ChatGPT** | Web ChatGPT expects a **remote HTTPS MCP** connector. This package is **local stdio**. Use Claude/Cursor/Codex locally, or put a **trusted** TLS reverse-proxy in front of a remote MCP bridge **without** shipping Grok OAuth or agent secrets to the public internet. |

Full steps: [docs/install/en.md](docs/install/en.md).

### Minimal Claude Desktop fragment

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "cwd": "<REPO_PATH>",
      "env": {
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>"
      }
    }
  }
}
```

## First verification

1. `python -m grok_delegate --self-test` → PASS table  
2. From the host: call `grok_agent_status`  
3. Prefer `grok_agent_consult` before any `execute` on a real repo  
4. For write roles, use a temporary git repository until receipts look correct  

## Transports (backend)

| Value | Meaning | Fallback |
|---|---|---|
| `stdio` (default / `auto`) | ACP v1 over `grok agent stdio` | none |
| `websocket` | ACP v1 over managed loopback `grok agent serve` | none |
| `legacy` | Headless `grok --single` compatibility path | none |

Details: [docs/ACP-TRANSPORTS.md](docs/ACP-TRANSPORTS.md).

## Security boundary

- No credentials in git, examples, or receipts  
- Local Grok CLI session only — bridge does **not** read `auth.json` into logs  
- WebSocket: loopback only, process-local secret, redacted from logs  
- Deny-by-default tool permissions; no `--always-approve` / `bypassPermissions`  
- Write work in external worktrees; operator merges after review  

See [SECURITY.md](SECURITY.md).

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest -q
python -m compileall -q grok_delegate tests
```

## License

MIT — see [LICENSE](LICENSE).
