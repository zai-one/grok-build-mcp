---
name: install-grok-mcp
description: >
  Install and wire grok-delegate MCP only after Grok CLI is installed and
  `grok login` succeeded. Use for Claude/Cursor/Codex/VPS/FastMCP setup.
  Abort if CLI or login is missing. Token-cheap; no coding jobs.
version: 0.5.1
---

# install-grok-mcp

> **Unofficial.** Not an official xAI/Grok product. Never put OAuth/API keys in MCP config.

## HARD GATE (read first)

This MCP **cannot work** without:

1. **Grok CLI** on PATH (`grok --version`)
2. **`grok login`** completed on the **same OS user** that will run the MCP process
3. Then: install this package + set roots + wire host

If (1) or (2) fail → **stop and tell the user**. Do not invent API keys. Do not skip login.

```bash
grok --version          # must work
grok login              # interactive; user completes browser/device flow
python -m grok_delegate --self-test   # auth must be "present"
```

## Install package (only after CLI gate)

```bash
cd <REPO_PATH>
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"   # absolute path
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"    # outside repo
export GROK_DELEGATE_ECONOMY=1
python -m grok_delegate --self-test
```

## Wire a host

| Host | Action |
|---|---|
| Claude Desktop | merge `examples/claude_desktop.mcp.json` (placeholders only) |
| Claude Code | `examples/claude-code.mcp.json` → project `.mcp.json` |
| Cursor | `examples/cursor.mcp.json` |
| Codex CLI | `examples/codex.cli.example.sh` |
| FastMCP | `docs/install/fastmcp.md` |
| VPS HTTP | `docs/install/vps.md` after local self-test passes |

## Skills for other agents

Same content lives under:

- `.claude/skills/install-grok-mcp/` (Claude Code)
- `.codex/skills/install-grok-mcp/` (Codex CLI)
- `.agents/skills/install-grok-mcp/` (portable Agent Skills)

## After install — hand off

Tell user to use skill **grok-delegate** for runtime. Call `grok_agent_economy` once.

## Never

- Put secrets in git or MCP JSON
- Run `execute` during install skill
- Claim success if self-test auth is absent
