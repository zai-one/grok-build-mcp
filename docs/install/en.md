# Install guide (English)

`grok-delegate` is a **local stdio MCP server**. It talks to MCP hosts over
stdin/stdout and reuses your **already logged-in Grok CLI** session. It does
not implement OAuth inside MCP config and must not receive API keys or
`GROK_AGENT_SECRET` in host JSON files.

Package version: **0.5.0**

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | `python3 --version` or `py -3 --version` |
| **Grok CLI** | Installed and on `PATH` (or set `GROK_DELEGATE_BIN`) |
| **Logged-in Grok CLI** | Complete the CLI’s normal login once on this machine |
| **git** | Required for worktrees and readback |
| **git clone of this repo** | Source install path below |

Verify CLI presence and session (never paste tokens into chat or config):

```bash
grok --version
grok models    # should succeed when the local CLI session is valid
```

---

## Install from source

```bash
git clone <REPO_URL> <REPO_PATH>
cd <REPO_PATH>
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
# optional: test deps
pip install -e ".[test]"
```

Editable install exposes the console script `grok-delegate` (entry:
`grok_delegate.server:main`).

---

## How to run the server

The server is a long-lived **stdio** process. MCP hosts spawn it; you can also
start it manually for debugging (it will wait on stdin).

```bash
# After pip install -e .
grok-delegate

# Module forms (equivalent)
python -m grok_delegate.server
python -m grok_delegate
```

Operator checks (not the MCP host):

```bash
python -m grok_delegate --self-test
python -m grok_delegate --smoke-delegate   # optional live plan-only smoke
python -m grok_delegate --help
```

Minimum useful environment (paths are placeholders — replace with yours):

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_REPO_ROOT="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_JOBS_DIR="<JOBS_DIR>"
# optional
# export GROK_DELEGATE_BIN="grok"
# export PYTHONPATH="<REPO_PATH>"   # only if not installed editable
```

Empty allowlist → fail-closed (`ALLOWED_ROOTS_EMPTY`).

---

## Connect Claude Desktop

Edit the Claude Desktop MCP config (location depends on OS; typical names
include `claude_desktop_config.json`). Merge the `mcpServers` entry — **no
secrets**:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "grok-delegate",
      "args": [],
      "env": {
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

If the console script is not on the PATH Claude uses, call the interpreter
explicitly:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Restart Claude Desktop after saving. See also
`examples/claude_desktop.mcp.json`.

---

## Connect Claude Code

Project-scoped `.mcp.json` at the project root (or your documented Claude Code
MCP location):

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Template: `examples/claude-code.mcp.json`.

---

## Connect Codex CLI

```bash
codex mcp add grok-delegate \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- grok-delegate
```

Or module form:

```bash
codex mcp add grok-delegate \
  --env "PYTHONPATH=<REPO_PATH>" \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- python -m grok_delegate.server
```

Verify: `codex mcp list`. Template: `examples/codex.cli.example.sh`.

**Never** pass `--env GROK_AGENT_SECRET=...` into `codex mcp add`.

---

## Connect Cursor

Cursor MCP config (user or project `mcp.json` — follow current Cursor docs for
the exact path). Example shape:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Template: `examples/cursor.mcp.json`.

---

## Connect VS Code / Continue

### VS Code (MCP-capable builds / Copilot MCP)

Where the product documents an MCP servers JSON (user or workspace settings),
add a stdio server entry with the same non-secret `env` keys as above:

```json
{
  "mcp": {
    "servers": {
      "grok-delegate": {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "grok_delegate.server"],
        "env": {
          "PYTHONPATH": "<REPO_PATH>",
          "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
          "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
          "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
          "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
        }
      }
    }
  }
}
```

Field names may vary by VS Code release — keep **command / args / env** and
stdio transport; do not add remote URL or OAuth fields for this server.

### Continue

In Continue’s MCP / server configuration (YAML or JSON per your Continue
version), register a **stdio** MCP server pointing at `grok-delegate` or
`python -m grok_delegate.server` with the same environment variables. Do not
configure HTTP/SSE endpoints for this package.

---

## ChatGPT / OpenAI

ChatGPT custom MCP connectors are designed for **remote HTTP** (or similar
hosted) endpoints. **This package is a local stdio process** and is not a
hosted OpenAI MCP remote server.

Options:

1. **Preferred:** Use `grok-delegate` only with **local** hosts that spawn
   stdio MCP servers (Claude Desktop, Claude Code, Codex CLI, Cursor, local
   VS Code / Continue).
2. **If** you expose it through a **trusted MCP bridge** you operate yourself
   (stdio on the machine ↔ remote front-end), treat that bridge as high-risk
   infrastructure: never put OAuth secrets, API keys, or `GROK_AGENT_SECRET`
   in remote or shared config; keep allowlisted roots minimal; prefer managed
   WebSocket secrets only in process memory on the machine running Grok.

This guide does **not** invent ChatGPT UI click-paths that are not part of
this repository’s surface. Follow OpenAI’s current docs for any remote MCP
product features; they are separate from this local server.

---

## Generic MCP host JSON

Any host that can spawn a local stdio MCP server:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Multiple exact roots: separate with `;` in `GROK_DELEGATE_ALLOWED_ROOTS`.
Descendants of an allowlisted root are **not** implicitly trusted — each
`project_root` must match an allowlist entry exactly.

---

## First verification

### 1. Operator self-test

```bash
cd <REPO_PATH>
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
python -m grok_delegate --self-test
```

Expect a PASS/FAIL table covering binary, version, auth presence, git, and
status-tool JSON-RPC paths. Full green requires a valid local Grok CLI session.

### 2. From the MCP host

After the host lists tools:

1. Confirm server info version **0.5.0** (initialize / status).
2. Call **`grok_agent_status`** (or compatibility `grok_delegate_status`).
3. Confirm default transport behavior: **stdio** (auto → stdio only, no
   silent cascade to WebSocket/legacy).
4. Prefer a first write test only on a temporary git repository, not a
   production monorepo.

### 3. Unit tests (contributors)

```bash
pip install -e ".[test]"
pytest tests -q
```

---

## Transports explained

Two different layers are easy to confuse:

| Layer | What it is | Who connects |
|---|---|---|
| **MCP ↔ host** | Always **stdio** JSON-RPC for this package | Claude / Codex / Cursor / etc. spawn the process |
| **Bridge ↔ Grok agent** | Chosen **backend transport** inside the server | `legacy`, `stdio` (ACP), or `websocket` (ACP) |

### Backend transports (task packet / tool argument)

| Value | Role |
|---|---|
| `legacy` | Headless Grok CLI path (`grok --single` / legacy delegate) |
| `stdio` | ACP v1 over a per-task `grok agent stdio` process (**default**; `auto` aliases here) |
| `websocket` | ACP v1 over **loopback** WebSocket to managed or operator-run `grok agent serve` |
| `auto` | Alias for `stdio` only — **no** fallback cascade |

MCP is **not** WebSocket to the host. WebSocket is only the optional ACP path
to a **local** Grok agent on loopback. See `docs/ACP-TRANSPORTS.md`.

---

## Environment variables

Use placeholders in docs and examples. Prefer absolute paths.

| Variable | Required | Description |
|---|---|---|
| `GROK_DELEGATE_ALLOWED_ROOTS` | Yes* | Exact project roots allowlist (`;`-separated or JSON array) |
| `GROK_DELEGATE_REPO_ROOT` | Yes* | Single-root pin if `ALLOWED_ROOTS` unset |
| `GROK_DELEGATE_LANES_PARENT` | Recommended | Parent directory for external git worktrees |
| `GROK_DELEGATE_JOBS_DIR` | Recommended | Durable job records + optional log colocated |
| `GROK_DELEGATE_BIN` | No | Path or name of `grok` / `grok.exe` only |
| `GROK_DELEGATE_SANDBOX` / `GROK_SANDBOX` | No | Sandbox profile override (`off` disables) |
| `GROK_DELEGATE_CONCURRENCY` | No | 1–2 (default 1) |
| `GROK_DELEGATE_MAX_QUEUED` | No | 1–32 (default 8) |
| `GROK_DELEGATE_GIT_TIMEOUT_SECONDS` | No | Git probe timeout (default 60) |
| `GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS` | No | `worktree add` budget (default 600) |
| `GROK_DELEGATE_LOG_FILE` | No | Log path (never write MCP JSON-RPC to stdout) |
| `GROK_DELEGATE_LOG_LEVEL` | No | e.g. `INFO` |
| `GROK_DELEGATE_WS_ENDPOINT` | Optional advanced | Loopback WS URL only, e.g. `ws://127.0.0.1:<PORT>/ws` |
| `PYTHONPATH` | If not installed | Set to `<REPO_PATH>` when using `python -m` without editable install |

\* At least one of `GROK_DELEGATE_ALLOWED_ROOTS` or `GROK_DELEGATE_REPO_ROOT`
must yield a non-empty allowlist.

### Secrets — process-only, never config files

| Variable | Rule |
|---|---|
| `GROK_AGENT_SECRET` | **Never** in MCP JSON, git, or examples. Only process env for an optional operator-run WS daemon; managed mode generates an ephemeral secret in memory. |
| OAuth tokens / API keys | **Never** set for this server. Use the Grok CLI login on the machine. |

---

## Security rules for config

1. **Never** put `GROK_AGENT_SECRET`, API keys, or OAuth tokens in config files.
2. **Never** commit real home paths or private roots; use placeholders in
   shared templates.
3. Keep lane and job directories **outside** the source repository when possible.
4. Review worktree diffs before merge; this server never pushes or merges.
5. Report vulnerabilities via GitHub Security Advisories — see root
   `SECURITY.md`.

---

## Troubleshooting

| Symptom | What to check |
|---|---|
| `ALLOWED_ROOTS_EMPTY` / setup error | Set `GROK_DELEGATE_ALLOWED_ROOTS` or `GROK_DELEGATE_REPO_ROOT` |
| Client root rejected | Root must match allowlist **exactly** (not a child path) |
| Auth absent in self-test | Run Grok CLI login on this OS user; do not add tokens to MCP config |
| `GROK_MISSING` | Install CLI or set `GROK_DELEGATE_BIN` to a safe `grok` path |
| Tools missing in host | Restart host after config change; confirm command is on host PATH |
| Server “hangs” when run manually | Expected: stdio waits for JSON-RPC from a host |
| WebSocket failures | Use loopback only; do not put secrets in config; prefer managed mode |
| `QUEUE_FULL` | Lower concurrency load or raise `GROK_DELEGATE_MAX_QUEUED` within caps |
| Stale / `unknown` jobs after restart | Durable records may mark orphaned runs; inspect worktree, do not assume success |
| ChatGPT remote connector | This server is local stdio — see ChatGPT section above |

Logs: set `GROK_DELEGATE_LOG_FILE` or rely on `<JOBS_DIR>/grok-delegate.log`
when jobs dir is configured. Do not log secrets.

---

## Related docs

- `docs/ACP-TRANSPORTS.md` — ACP stdio/WebSocket details  
- `docs/SECURITY.md` — enforcement and residual risk  
- Root `SECURITY.md` — reporting and credential policy  
- `examples/` — JSON and shell templates with placeholders only


---

## Disclaimer (unofficial product)

> **This is a community project.** It is **not** an official product of **xAI**,
> **Grok**, Anthropic, OpenAI, or Codex. Not affiliated with or endorsed by
> those companies. Auth stays on the machine via **local Grok CLI** (`grok login`).
> **Never** put OAuth tokens, API keys, or `GROK_AGENT_SECRET` in MCP config.

---

## Token economy

Host agents (Claude / Cursor) should orchestrate with short prompts; **Grok CLI**
does the long coding loop on this machine or a VPS.

| Env | Purpose |
|---|---|
| `GROK_DELEGATE_ECONOMY=1` | Lower default `max_turns` / timeout / reasoning when omitted |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1` | Compact poll/job payloads for the host context window |

Session tool (no args): **`grok_agent_economy`**.

Sequence: `status` → `economy` → `consult`/`review` → focused `execute` → `poll` by `job_id`.

Full guide: [../economy.md](../economy.md).

---

## FastMCP

| Path | How |
|---|---|
| Local stdio | FastMCP / host spawns `python -m grok_delegate.server` with non-secret env |
| Remote proxy | VPS HTTP + TLS; local FastMCP `create_proxy` with operator bearer |

Short guide: [fastmcp.md](fastmcp.md) · example: [../../examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py).

---

## VPS (bearer HTTP, not OAuth)

```bash
# placeholders only
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_HTTP_TOKEN_FILE="<TOKEN_FILE>"   # openssl rand -hex 32
python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
# TLS reverse proxy → https://mcp.example.invalid/mcp
```

- Bearer = **operator CSPRNG secret**, never Grok OAuth  
- systemd: [../../examples/vps.systemd.service](../../examples/vps.systemd.service)  
- env template: [../../examples/http.env.example](../../examples/http.env.example)  
- guide: [vps.md](vps.md)

---

## Economy environment variables

| Variable | Description |
|---|---|
| `GROK_DELEGATE_ECONOMY` | `1` / `true` / `on` enables task defaults |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL` | Force compact poll; defaults on when economy is on |
| `GROK_DELEGATE_HTTP_TOKEN` | HTTP bearer (env); exclusive with token file |
| `GROK_DELEGATE_HTTP_TOKEN_FILE` | Preferred path to bearer file (`<TOKEN_FILE>`) |
| `GROK_DELEGATE_HTTP_HOST` | Default `127.0.0.1` |
| `GROK_DELEGATE_HTTP_PORT` | Default `8765` |
