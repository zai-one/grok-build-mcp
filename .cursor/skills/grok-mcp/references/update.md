# Update

Consuming agents: if `grok_agent_status` → `compatibility.bridge_version`
(or `grok_delegate_version`) is older than this checkout:

```bash
git pull
pip install -e .
# restart the MCP host (Claude / Cursor / Codex)
```

Optional skill copy:

```bash
bash ~/.local/share/grok-mcp/skills/grok-mcp/scripts/update_mcp.sh
```

Keeps `~/.config/grok-mcp/env`. Re-login only if auth fails.
There is no background updater. Do not pin a Grok CLI version.
