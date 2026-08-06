# Install

Only supported path: **one-command installer** + product login.

## macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh \
  | bash -s -- --project "<PROJECT_ROOT>"
grok login
~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test
```

## Windows

```powershell
irm https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.ps1 | iex
grok login
```

## Wire host

Merge `~/.config/grok-mcp/mcp/claude_desktop.snippet.json` (or cursor snippet) into the host MCP config. Restart host. Call `grok_agent_status`.

## Success

- Self-test: binary PASS, auth present
- Host shows tools including `grok_agent_economy`
- No secrets in MCP JSON

See `docs/EASY.md`.
