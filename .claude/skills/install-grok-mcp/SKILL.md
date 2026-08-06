---
name: install-grok-mcp
description: >
  Install grok-delegate with the one-command script only. Require Grok CLI +
  grok login. Abort if user wants multi-step manual pip setup — point to EASY.md.
version: 0.5.3
---

# install-grok-mcp

> Unofficial. Not xAI/Grok. No OAuth in MCP config.

## Only supported install

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh \
  | bash -s -- --project "<PROJECT_ROOT>"
grok login
~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test
```

Windows: `scripts/install.ps1` / docs in `docs/EASY.md`.

## HARD GATE

Without **Grok CLI** + **`grok login`** the MCP does nothing useful. Stop and tell the user.

## Wire host

Merge `~/.config/grok-mcp/mcp/claude_desktop.snippet.json` (or cursor snippet). Restart app.

## Do not

- Invent multi-step pip/venv manual guides
- Put secrets in JSON
- Claim success without self-test auth present
