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

## After install (Session Protocol)

1. Wire host MCP (snippet from installer).
2. In the host agent, call **`grok_agent_session_begin`** with `intent: "auto"`.
3. Follow `recommended_tools` → work → **`grok_agent_session_end`**.
4. Skill: `grok-mcp` (v0.8) — do not paste long docs into chat.

## Session Plan (v1.1)

1. Call **`grok_agent_session_begin`** with `goal` and `host_budget: "small"`.
2. Follow returned **`plan`** tools only (see `host_script`).
3. **`session_tick`** until done/`force_end`, then **`session_end`**.
4. Skill `grok-mcp` v0.9 — do not re-plan in prose.

## Navigator (v1.2)

1. `grok_agent_session_begin` with goal + host_budget=small  
2. Loop **`grok_agent_session_next`** — do only what `card` says  
3. Stop when `done=true`
