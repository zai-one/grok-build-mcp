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
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.ps1))) -Project "$env:USERPROFILE\code\my-project"
```

`irm ... | iex` cannot pass `-Project`, and the default is your entire user
profile — which is what the bridge would then be allowed to touch. Name the
project.

This installs the package, env, and Claude/Cursor snippets. On macOS/Linux it
also installs Python if it is missing and writes a `grok-mcp` launcher; the
Windows script does neither — it tells you to install Python and wires the
snippets to the venv directly.

## Then do these 3 things

```bash
# 1) Grok product login (required — interactive)
grok login

# 2) Check
# macOS / Linux
~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test
# Windows (install.ps1 clones to %LOCALAPPDATA%\grok-mcp)
%LOCALAPPDATA%\grok-mcp\.venv\Scripts\python.exe -m grok_delegate --self-test
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

- VPS: `docs/install/vps.md` (stdio over SSH; HTTP is private JSON-RPC, not Streamable HTTP)
- FastMCP proxy: `docs/install/fastmcp.md`
- Token economy tips: `docs/economy.md`

No multi-step pip/venv manual install is supported in docs anymore — use the script.

## Agent skill

Use router skill **`grok-mcp`** ([SKILLS.md](SKILLS.md)).

## After install — Navigator (Session Protocol v1.2)

1. Wire host MCP (snippet from installer).
2. **`grok_agent_session_begin`** once, with `goal` and `host_budget: "small"`.
3. Loop **`grok_agent_session_next`** — do only what the returned `card` says
   (`host_cmd` | `mcp_tool` | `end`). Do not re-plan in prose.
4. Stop when `done=true`, calling **`grok_agent_session_end`** if the card says so.

Skill `grok-mcp` v1.1.0 enforces this — do not paste long docs into chat.

`session_tick` still exists for compact progress and budget, but the v1.1 loop
built on it is superseded by `session_next` — `session_begin` returns a `plan`,
a `budget` and a `host_script` that says to loop `session_next` until
`done=true`. Check `gate_status` in that first reply: `ready` stays false while
`roots_ok` is false, which means the allowlist is still empty.
