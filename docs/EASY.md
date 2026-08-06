# Easy install (plain English)

> Unofficial community project — **not** xAI / Grok official.

## Can a non-developer do this?

**Mostly yes**, if you can paste one command and finish `grok login` in a browser.

| Step | Who does it | Hard? |
|---|---|---|
| Install Python + this MCP + config files | **1 command** below | Easy |
| Install **Grok CLI** | You (product installer) | Medium — once |
| `grok login` | You (browser/device) | Easy but **required** |
| Paste JSON into Claude / Cursor | You (or agent skill) | Easy |
| Day-to-day use | Ask host agent to call tools | Easy |

There is **no** fully automatic login: Grok account auth is interactive on purpose.

## One command (macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh | bash -s -- --project "$HOME/code/my-project"
```

Replace `$HOME/code/my-project` with a real folder you want the agent to work in.

What the script does:

1. Finds or installs Python 3.10+ (via [uv](https://github.com/astral-sh/uv) if needed)
2. Clones this repo to `~/.local/share/grok-mcp`
3. Creates a venv and `pip install -e .`
4. Writes env + a `grok-mcp` launcher in `~/.local/bin`
5. Writes Claude/Cursor MCP JSON snippets
6. Runs `self-test` and tells you if `grok login` is still needed

## One command (Windows PowerShell)

```powershell
irm https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.ps1 | iex
```

Or with a project path:

```powershell
# download then:
.\install.ps1 -Project "$env:USERPROFILE\code\my-project"
```

## After the script

```bash
grok login                                          # once
source ~/.config/grok-mcp/env && python -m grok_delegate --self-test   # if using repo venv
# or simply:
~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test
```

Wire Claude Desktop: merge `~/.config/grok-mcp/mcp/claude_desktop.snippet.json` into your Claude config, restart Claude.

In chat: **grok_agent_status** → **grok_agent_economy** → small **grok_agent_consult**.

## Honesty meter

| Claim | Reality |
|---|---|
| “Truly one command forever” | **Almost** — CLI login is still a second human step |
| “Works without Grok CLI” | **No** — this is only a bridge |
| “Safe to pipe curl \| bash” | Review the script first if you are careful; it writes only under your home |
| “Official xAI” | **No** — community |

More detail: [START_HERE.md](START_HERE.md) · [install/en.md](install/en.md)
