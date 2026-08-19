# grok-delegate

**Hand the coding loop to Grok CLI. Your host reads a receipt, not a repository.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple.svg)](https://modelcontextprotocol.io/)
[![Version](https://img.shields.io/badge/version-0.13.0-informational.svg)](pyproject.toml)

Claude Code, Cursor and Codex spend most of their context on the cheapest part
of the work: the edit → run tests → read output → fix loop. `grok-delegate` is
an MCP server that hands that loop to **Grok CLI** on your own machine, in a git
worktree of its own, and gives your host back a bounded receipt — changed files,
diffstat, a capped diff, and test results.

Against the full job record it replaces, that receipt is **61–88% smaller**.
Worth stating precisely, because the honest version sells better than the
brochure one: against *reading the diff yourself* it only wins once the diff
passes the 16 KiB cap. On a one-file change the saving is in not pulling the
record at all.

## Why the receipt is worth trusting

Delegation is only cheaper if you can believe the result without re-reading
everything. Four things make that true here:

- **The bridge runs your tests — the worker does not get to grade itself.**
  Anything the agent says about its own tests is labelled `agent-reported` and
  is not evidence. A live capture once caught an agent reporting exit code 0
  while pytest was failing: in a shell, `a; b` returns *b*'s exit code.
- **A job that changed nothing — or touched files you never asked for — comes
  back `blocked`,** with the reason, instead of `ok` and a cheerful summary.
  An artifact written by the test run rather than by the worker is caught too.
- **It never pushes and never merges.** Work lands on a `grok/*` branch, which
  the bridge commits for you even if the worker ran out of turns. You review it.
- **It fails closed.** The allowlist is empty until you grant an exact root, and
  every project stays off until it carries a `.grok-mcp.json` of its own.

Nothing is pinned, deliberately: no hardcoded model, no pinned Grok CLI build.
An upstream upgrade reaches you without waiting for a release here.

## Requirements

Python 3.10+, git, and **Grok CLI installed and logged in** (`grok login`) as the
same OS user that runs the bridge. Auth stays with the CLI — this server never
reads your credentials, and no OAuth or API key ever belongs in an MCP config.
Zero runtime dependencies otherwise.

> **Unofficial community project** — not xAI, Grok, Anthropic, or OpenAI.

---

## Install (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

Windows (name the project — the default is your whole user profile):

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.ps1))) -Project "$env:USERPROFILE\code\my-project"
```

Then, on macOS/Linux:

```bash
grok login
~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test
```

On Windows the installer puts the checkout in `%LOCALAPPDATA%\grok-mcp`:

```powershell
grok login
& "$env:LOCALAPPDATA\grok-mcp\.venv\Scripts\python.exe" -m grok_delegate --self-test
```

Merge `~/.config/grok-mcp/mcp/claude_desktop.snippet.json` into Claude/Cursor → restart → `grok_agent_status`.

## Host loop

Once it is wired, the whole protocol is three steps:

1. `grok_agent_session_begin({"goal":"…","host_budget":"small"})`
2. Loop `grok_agent_session_next` → execute only the `card` it hands you
   (`host_cmd` | `mcp_tool` | `end`)
3. Stop when `done=true`

The **`grok-mcp`** skill enforces this shape, so a host that loads it does not
have to be told twice. Execute cards carry a full `task`; a poll card is
`{job_id}` and nothing else. If a card ever fails schema validation, the typed
tools — consult → execute → poll → review — take the same packet.


### Claude Code, on this repository

A project-scoped [`.mcp.json`](.mcp.json) ships in the repo, so opening it in Claude
Code wires `grok-delegate` with no install step — the package has no runtime
dependencies. The entry resolves the package from `CLAUDE_PROJECT_DIR`, which Claude
Code sets in the server's environment, so it does not depend on the working directory
the host happens to use.

The command defaults to the Windows `py` launcher. Elsewhere, point it at your
interpreter:

```bash
export GROK_MCP_PYTHON=python3
```

This path skips the installer, so nothing writes the env file for you. Read-only
tools such as `grok_agent_status` work immediately; anything that touches a
repository fails closed until you grant an exact root, because the allowlist is
empty by design:

```bash
export GROK_DELEGATE_ALLOWED_ROOTS=/path/to/project   # ';' separates several
export GROK_DELEGATE_LANES_PARENT=/path/to/.grok-mcp-lanes
```

Unset, lanes go to **`<project>/.grok/lanes/<slug>`** — inside the project they
belong to, under a dot-directory the bridge adds to `.gitignore` on first use.
A lane holds unmerged work someone will review, so it lives with the work rather
than in a sibling directory nobody asked for. The dot is what keeps it out of the
way: pytest skips `.*`, ripgrep and indexers skip hidden, git is told once.

`GROK_DELEGATE_LANES_PARENT` still overrides it, and a path in the *visible*
source tree is still refused — tools walk that. The receipt's `worktree_path` is
always the honest answer.

Set them where the host will inherit them, then restart it. `grok_agent_status`
reports what was actually granted under `roots.allowed`. A child of an
allowlisted root is not implicitly trusted.

### Letting the host grant the current project

Maintaining that list by hand gets old once you work across several projects.
The host already knows which directory you opened — Claude Code exports it to
the server as `CLAUDE_PROJECT_DIR` — so the server can take the allowlist from
there:

```bash
export GROK_DELEGATE_TRUST_HOST_ROOTS=1
```

With it set, the directory the session was launched in joins the allowlist and
no longer needs to be listed. It **widens** the list rather than replacing it:
anything in `GROK_DELEGATE_ALLOWED_ROOTS` stays granted, and exact-equality
membership is unchanged — a sibling or a child of the session directory is still
refused.

Off by default, and deliberately. Granting a root because the host named it
means the operator's explicit list is no longer the whole answer; that is a fair
trade when the host is your own editor, but it is yours to make. `grok_agent_status`
shows `roots.host_root_trusted` and `roots.host_root` so a root you never typed
is traceable.

Hosts that do not set `CLAUDE_PROJECT_DIR` are unaffected — the flag then grants
nothing. Reading the host's roots over MCP `roots/list`, which would also cover
`--add-dir` directories and non-Claude hosts, needs a bidirectional stdio loop
this server does not have yet.

### Keeping the running server current

The server runs from an editable install of a checkout, so three copies of the
code exist at once: GitHub, that checkout, and the process already in memory.
Nothing used to reconcile them, and the failure was silent -- a landed fix looked
unfixed because it never reached the process.

`grok_agent_status` now carries an `update` block comparing the checkout against
`origin/main`. It uses `ls-remote`, never `fetch`, so checking cannot mutate your
checkout, and an unreachable network reports `REMOTE_UNREACHABLE` rather than
"up to date".

When one is available, `grok_agent_update` previews the exact steps; called with
`confirm: true` it pulls, reinstalls, and asks you to restart the host. It
refuses on a dirty checkout -- staying a version behind beats overwriting
uncommitted work. The server cannot restart itself, so that last step is yours.

### Turning the bridge on for a project

The bridge is off in every project until that project says otherwise. A project
opts in by carrying `.grok-mcp.json` in its root; job tools refuse one that does
not, and say so with the path and the menu rather than failing vaguely:

```json
{ "preset": "max" }
```

| Preset | Worker budget | For |
|---|---|---|
| `off` | — | Grok is not used here |
| `cheap` | `low`, 12 turns | mechanical edits |
| `standard` | `high`, 24 turns | everyday work |
| `max` | `xhigh`, 40 turns | hardest work on the worker, fewest host tokens |

Ask the `grok_agent_project` tool to read or write it — `{project_root}` reports
whether the project opted in, `{project_root, preset}` writes the file. It only
writes inside an allowlisted root, so opting a project in cannot become a way to
opt in arbitrary directories.

No preset names a model, deliberately: that would pin the project to whatever was
current when the preset was written. Individual fields may still override a
preset (`reasoning_effort`, `max_turns`, `model`), and a value passed in the task
itself beats both. A malformed config raises instead of quietly reading as "off".

### Choosing the model and the worker's budget

The bridge names no model of its own. With nothing configured it omits `--model`
entirely and the Grok CLI uses whatever it defaults to, so a CLI upgrade that
ships a better model reaches you without a bridge release. Name one only when you
want to override that:

```bash
export GROK_DELEGATE_MODEL=grok-4.6
export GROK_DELEGATE_REASONING_EFFORT=xhigh   # low|medium|high|xhigh|max
export GROK_DELEGATE_MAX_TURNS=40             # 1..60
```

These set the budget the bridge picks when a caller names none; a `model`,
`reasoning_effort` or `max_turns` passed in the task always wins. An unparsable
or out-of-range value reads as "no preference" rather than failing every job.

They are independent of `GROK_DELEGATE_ECONOMY`. Economy keeps the *host's*
context small — compact receipts, bounded diffs — which is a different question
from how hard the worker should think. Turning economy on to save your own
context no longer forces the worker down to `low`.

**Skill (router):** `grok-mcp` — see [docs/SKILLS.md](docs/SKILLS.md)

**Full easy guide:** [docs/EASY.md](docs/EASY.md)

| Language | Page |
|---|---|
| Easy (canonical) | [docs/EASY.md](docs/EASY.md) |
| EN / RU / 中文 / ES | [docs/install/](docs/install/) (short pointers) |

---

## What it is

| | |
|---|---|
| Host | Claude, Cursor, Codex, … (stdio MCP) |
| Worker | **Grok CLI** on the same machine or VPS |
| Why | Save host tokens — long coding loop runs on Grok |
| Economy | `export GROK_DELEGATE_ECONOMY=1` · tool `grok_agent_economy` |

## Optional

- [VPS](docs/install/vps.md) · [FastMCP](docs/install/fastmcp.md) · [Economy](docs/economy.md)
- [Skills](docs/SKILLS.md) · [Security](SECURITY.md) · [Examples](examples/)

```bash
# day-to-day
grok-mcp          # launcher, macOS/Linux only — install.ps1 writes no wrapper
# or, anywhere
python -m grok_delegate.server
```

---

Built and maintained by **[ZAI](https://zai.one)**, an internet agency.
Issues and pull requests are welcome.
