# START HERE (plain English)

> **Unofficial community project** — not made by xAI / Grok / Anthropic / OpenAI.

## This MCP does nothing useful until ALL of these are true

| # | Requirement | How you know it worked |
|---|---|---|
| 1 | **Grok CLI installed** on the same machine (or VPS) that runs this MCP | `grok --version` prints a version |
| 2 | **You logged in** with that CLI | `grok login` completed; `python -m grok_delegate --self-test` shows **auth: present** |
| 3 | **This package installed** | `pip install -e .` then `grok-delegate --help` works |
| 4 | **Allowlisted project roots** set | `GROK_DELEGATE_ALLOWED_ROOTS` points at a real folder |
| 5 | **Your host agent is wired** (Claude / Cursor / Codex / …) **or** you follow a **skill** | MCP tools like `grok_agent_status` appear |

If step 1 or 2 fails, **stop**. Installing only this GitHub repo is not enough.

## Fastest way (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

Then: `grok login` → self-test → paste MCP snippet into Claude/Cursor.  
Details: [EASY.md](EASY.md)

## Two ways to set up (manual / agent)


### A) You do it yourself (commands)

```bash
# 1) Install + login Grok CLI (upstream installer — not this repo)
grok --version
grok login

# 2) Install this MCP
cd <REPO_PATH>
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[test]"
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_ECONOMY=1

# 3) Verify
python -m grok_delegate --self-test
# binary + auth must PASS. If auth fails → run grok login again.

# 4) Wire Claude Desktop / Cursor using examples/*.mcp.json
```

### B) An agent does it via a **skill**

Copy project skills into the host’s skills folder (or use them in-repo):

| Host | Skills path |
|---|---|
| Claude Code | `.claude/skills/` (already in this repo) or `~/.claude/skills/` |
| Codex CLI | `.codex/skills/` or `~/.codex/skills/` or `~/.agents/skills/` |
| Cursor / portable | `.agents/skills/` |
| Any Agent Skills host | same `SKILL.md` format |

Then tell the agent:

> Use skill **install-grok-mcp**. Install only after Grok CLI is on PATH and `grok login` succeeded.

Runtime usage skill: **grok-delegate**.  
Skill authoring skill: **create-agent-skill**.

## What “success” looks like

1. `python -m grok_delegate --self-test` → binary PASS, auth present  
2. Host shows tools: `grok_agent_status`, `grok_agent_economy`, …  
3. `grok_agent_status` returns ok without secrets  
4. Optional: `grok_agent_consult` answers a tiny question  

## Still stuck?

| Symptom | Fix |
|---|---|
| `GROK_MISSING` | Install Grok CLI or set `GROK_DELEGATE_BIN` |
| auth absent | `grok login` as the **same OS user** that runs the MCP |
| ALLOWED_ROOTS empty | export real absolute project path |
| Tools missing in Claude | restart host; check MCP config JSON paths |
| Want VPS | read `docs/install/vps.md` after local works |

More: [docs/install/en.md](docs/install/en.md) · [economy](docs/economy.md) · [SECURITY](SECURITY.md)
