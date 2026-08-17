# Update

`grok_agent_status` compares the checkout against its remote and returns an
`update` block. When `update.available` is true, use the bridge's own tool
rather than a shell recipe — it knows where the checkout is, and it refuses on
a dirty one instead of overwriting uncommitted work:

1. `grok_agent_update` with no arguments — returns the exact plan, changes nothing.
2. `grok_agent_update` with `confirm: true` — pulls `--ff-only` and reinstalls.
3. Restart the MCP host (Claude / Cursor / Codex). The server cannot restart
   itself, so a landed update does not reach the running process until you do.

Three copies of the code exist at once — the remote, the checkout, and the
process already in memory. A fix that reached the first two can still look
unfixed until step 3.

Manual equivalent, if the tool is unavailable:

```bash
git pull --ff-only
pip install -e .
```

```powershell
git pull --ff-only
py -3 -m pip install -e .
```

Keeps `~/.config/grok-mcp/env`. Re-login only if auth fails.
There is no background updater: checking is automatic, applying is confirmed.
Do not pin a Grok CLI version — the contract is ACP protocol integer `1`.
