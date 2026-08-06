# grok-delegate (MCP)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-purple.svg)](https://modelcontextprotocol.io/)
[![Version](https://img.shields.io/badge/version-0.5.1-informational.svg)](pyproject.toml)

**One-line pitch:** Local MCP bridge that lets Claude / Cursor orchestrate while **Grok CLI** does the heavy coding on your machine or VPS — isolated worktrees, typed receipts, token economy.

> ## ⚠️ Unofficial product disclaimer
>
> **This is a community project.** It is **not** an official product of **xAI**, **Grok**, Anthropic, OpenAI, or Codex. It is not affiliated with, endorsed by, or supported by those companies. Use at your own risk. Auth stays on your machine via the local Grok CLI session (`grok login`) — never put OAuth tokens or API keys in MCP config.

---


## ⛔ Works only if Grok CLI is installed + logged in

This repository is an **MCP bridge**, not a replacement for the Grok product.

| You must already have… | Check |
|---|---|
| **Grok CLI** on the machine that runs the MCP | `grok --version` |
| A completed **`grok login`** for that OS user | `python -m grok_delegate --self-test` → auth **present** |
| This package installed + roots set | see Quickstart below |

**New here?** Read **[docs/START_HERE.md](docs/START_HERE.md)** first (plain English).

Or ask an agent: *use skill `install-grok-mcp`* (requires CLI+login already).  
Skills for Claude / Codex / Cursor: `.claude/skills/`, `.codex/skills/`, `.agents/skills/`.

---

## Why this exists (token economy)

| Role | Who pays tokens | What they do |
|---|---|---|
| **Host agent** (Claude, Cursor, …) | Short orchestration prompts | Plan, call tools, read compact receipts |
| **Grok CLI** (local / VPS) | Long coding loop | Edit code, run tests, produce worktree diffs |
| **Human** | Review time | Merge `grok/*` branches — this MCP never push/merges |

Turn on economy defaults:

```bash
export GROK_DELEGATE_ECONOMY=1
export GROK_DELEGATE_ECONOMY_COMPACT_POLL=1
```

Then call **`grok_agent_economy`** once per session for the host-agent playbook.

Deep dive → [docs/economy.md](docs/economy.md)

---

## Quickstart

```bash
# 1) Install
git clone <REPO_URL> <REPO_PATH>
cd <REPO_PATH>
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"

# 2) Auth (local Grok CLI — not MCP OAuth)
grok login

# 3) Fail-closed roots
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"

# 4) Run stdio MCP (default for desktop hosts)
grok-delegate
# or: python -m grok_delegate.server
```

Self-check:

```bash
python -m grok_delegate --self-test
```

---

## Install guides (languages)

| Language | Guide |
|---|---|
| English | [docs/install/en.md](docs/install/en.md) |
| Русский | [docs/install/ru.md](docs/install/ru.md) |
| 简体中文 | [docs/install/zh-CN.md](docs/install/zh-CN.md) |
| Español | [docs/install/es.md](docs/install/es.md) |
| FastMCP | [docs/install/fastmcp.md](docs/install/fastmcp.md) |
| VPS | [docs/install/vps.md](docs/install/vps.md) |
| **START HERE** | [docs/START_HERE.md](docs/START_HERE.md) |
| Skills | [docs/SKILLS.md](docs/SKILLS.md) |
| Economy | [docs/economy.md](docs/economy.md) |

---

## Client matrix

| Client | Mode | Notes |
|---|---|---|
| **Claude Desktop** | stdio | [examples/claude_desktop.mcp.json](examples/claude_desktop.mcp.json) |
| **Claude Code** | stdio | [examples/claude-code.mcp.json](examples/claude-code.mcp.json) |
| **Cursor** | stdio / remote URL | [examples/cursor.mcp.json](examples/cursor.mcp.json) |
| **Codex CLI** | stdio | [examples/codex.cli.example.sh](examples/codex.cli.example.sh) |
| **VS Code / Continue** | stdio | Same command / args / env pattern |
| **Remote (Claude remote MCP)** | HTTPS + bearer | TLS reverse proxy → HTTP MCP; **no OAuth in MCP config** |
| **FastMCP** | stdio local or `create_proxy` | [docs/install/fastmcp.md](docs/install/fastmcp.md) · [examples/fastmcp_proxy.py](examples/fastmcp_proxy.py) |

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
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_ECONOMY": "1"
      }
    }
  }
}
```

> **Never** put `GROK_AGENT_SECRET`, OAuth tokens, or API keys in these files.

---

## VPS one-liner (bearer HTTP, not OAuth)

```bash
# On VPS (after grok login + pip install -e .):
openssl rand -hex 32 > <TOKEN_FILE>
export GROK_DELEGATE_HTTP_TOKEN_FILE="<TOKEN_FILE>"
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
# Put Caddy/nginx TLS reverse proxy in front; connect remote MCP with Bearer only.
```

Full guide → [docs/install/vps.md](docs/install/vps.md) · systemd unit → [examples/vps.systemd.service](examples/vps.systemd.service)

---

## FastMCP (short path)

**Local stdio:** point FastMCP / your host at `python -m grok_delegate.server`.

**Remote proxy:** use FastMCP `create_proxy` (or equivalent) against your TLS URL with a **local bearer** — see [docs/install/fastmcp.md](docs/install/fastmcp.md) and [examples/fastmcp_proxy.py](examples/fastmcp_proxy.py).

---

## What it does

| Capability | Detail |
|---|---|
| MCP surface | `grok_agent_*` tools + legacy `grok_delegate*` aliases + **`grok_agent_economy`** |
| Host transport | **stdio** (desktop) or **bearer HTTP** (VPS) |
| Agent backends | `legacy` · ACP `stdio` · ACP loopback WebSocket |
| Isolation | External git worktree on `grok/*` — **no push / no merge** |
| Evidence | Diffstat, artifacts, bridge-verifier tests |
| Roots | `GROK_DELEGATE_ALLOWED_ROOTS` required; empty allowlist rejects work |

---

## Security (no OAuth in MCP config)

- Model auth = **local Grok CLI** session only
- HTTP bearer = **operator-generated** secret (`GROK_DELEGATE_HTTP_TOKEN` / `_FILE`) — **not** Grok OAuth
- No credentials in git, examples, or receipts
- Fail-closed project roots; human merges after review

See [SECURITY.md](SECURITY.md).

---

## Links

| Doc | Path |
|---|---|
| **START HERE** | [docs/START_HERE.md](docs/START_HERE.md) |
| Economy | [docs/economy.md](docs/economy.md) |
| ACP transports | [docs/ACP-TRANSPORTS.md](docs/ACP-TRANSPORTS.md) |
| Security | [SECURITY.md](SECURITY.md) |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Examples | [examples/](examples/) |

## License

MIT — see [LICENSE](LICENSE).
