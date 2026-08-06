# grok-delegate (MCP)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-purple.svg)](https://modelcontextprotocol.io/)
[![Version](https://img.shields.io/badge/version-0.5.3-informational.svg)](pyproject.toml)

**Claude / Cursor orchestrate → Grok CLI codes** (worktrees, receipts, token economy).

> **Unofficial community project** — not xAI, Grok, Anthropic, or OpenAI.  
> Auth = local `grok login` only. Never put OAuth/API keys in MCP config.

---

## Install (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

Windows: `irm https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.ps1 | iex`

Then:

```bash
grok login
~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test
```

Merge `~/.config/grok-mcp/mcp/claude_desktop.snippet.json` into Claude/Cursor → restart → `grok_agent_status`.

**Full easy guide:** [docs/EASY.md](docs/EASY.md)

| Language | Page |
|---|---|
| Easy (canonical) | [docs/EASY.md](docs/EASY.md) |
| EN / RU / 中文 / ES | [docs/install/](docs/install/) (short pointers) |

---

## What it is

| | |
|---|---|
| Host | Claude, Cursor, Codex, … (stdio MCP) |
| Worker | **Grok CLI** on the same machine or VPS |
| Why | Save host tokens — long coding loop runs on Grok |
| Economy | `export GROK_DELEGATE_ECONOMY=1` · tool `grok_agent_economy` |

## Optional

- [VPS](docs/install/vps.md) · [FastMCP](docs/install/fastmcp.md) · [Economy](docs/economy.md)
- [Skills](docs/SKILLS.md) · [Security](SECURITY.md) · [Examples](examples/)

```bash
# day-to-day
grok-mcp          # launcher from installer
# or
python -m grok_delegate.server
```
