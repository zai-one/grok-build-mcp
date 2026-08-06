# Install (the only path)

> **Unofficial** community project — **not** xAI / Grok.

## One command

**macOS / Linux** — change the project folder:

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.ps1 | iex
```

This installs Python if needed, the package, env, launcher, and Claude/Cursor snippets.

## Then do these 3 things

```bash
# 1) Grok product login (required — interactive)
grok login

# 2) Check
~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test
# want: binary PASS + auth present

# 3) Wire Claude Desktop / Cursor
# merge: ~/.config/grok-mcp/mcp/claude_desktop.snippet.json
# restart the app → call grok_agent_status
```

## If something fails

| Message | Fix |
|---|---|
| Grok CLI not found | Install Grok CLI, then `grok login` |
| auth absent | Same OS user: `grok login` again |
| Tools missing in Claude | Restart app; check JSON path to `grok-mcp` launcher |

## Optional later (not required)

- VPS / HTTP: `docs/install/vps.md`
- FastMCP proxy: `docs/install/fastmcp.md`
- Token economy tips: `docs/economy.md`

No multi-step pip/venv manual install is supported in docs anymore — use the script.

## Agent skill

Use router skill **`grok-mcp`** ([SKILLS.md](SKILLS.md)).
