# Every environment variable this bridge reads

Twenty-one of these were readable in the code and written down nowhere, which is
how an operator ends up believing a knob does not exist. A test
(`tests/test_environment_reference.py`) fails if the code grows a
`GROK_DELEGATE_*` name that is missing from this table, so the two cannot drift
apart again.

Nothing here is required. The bridge runs with none of them set, and the
defaults are the product.

## Where the bridge may work

| Variable | Default | What it does |
|---|---|---|
| `GROK_DELEGATE_ALLOWED_ROOTS` | unset | Explicit project allowlist, `;`-separated. Widened by, never replaced by, the roots the host declares. |
| `GROK_DELEGATE_REPO_ROOT` | unset | Pins a single project instead of a list. |
| `GROK_DELEGATE_MCP_ROOTS` | on | Ask the host for its roots over MCP `roots/list`. `0` turns the asking off. |
| `GROK_DELEGATE_TRUST_HOST_ROOTS` | off | Also trust `CLAUDE_PROJECT_DIR`. Off because any process can set an environment variable. |
| `GROK_DELEGATE_LANES_PARENT` | `<project>/.grok/lanes` | Where lane worktrees are created. |
| `GROK_DELEGATE_LANE_CLEANUP` | on | Remove a lane that produced nothing when its job ends. `0` keeps every lane. |

## The worker's budget

| Variable | Default | What it does |
|---|---|---|
| `GROK_DELEGATE_MODEL` | unset | Model id. Empty means the CLI's own default, and `--model` never reaches argv. |
| `GROK_DELEGATE_REASONING_EFFORT` | unset | `low` / `medium` / `high` / `xhigh` / `max`. An unreadable value is refused with `REASONING_EFFORT_INVALID`. |
| `GROK_DELEGATE_MAX_TURNS` | unset | Default turn budget; clamped to the hard cap of 60. |
| `GROK_DELEGATE_SANDBOX` | per profile | Overrides the CLI sandbox profile the bridge asks for. |
| `GROK_DELEGATE_BIN` | `grok` | Which Grok CLI to run. A client can never choose this. |
| `GROK_DELEGATE_EXPECTED_AGENT_VERSION` | unset (unpinned) | Opt-in CLI version check. A mismatch is a warning, never a refusal. |
| `GROK_DELEGATE_CONCURRENCY` | `1` | Jobs in flight at once, capped at 2. |
| `GROK_DELEGATE_MAX_QUEUED` | `8` | Jobs allowed to wait, capped at 32. |

## What the host pays

| Variable | Default | What it does |
|---|---|---|
| `GROK_DELEGATE_ECONOMY` | off | Smaller default turn/timeout/effort, and compact polls unless told otherwise. |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL` | follows economy | Force compact poll payloads on or off. |
| `GROK_DELEGATE_PREWARM` | on | Start the session probe (`grok models`, ~12.7 s) in the background at `initialize` instead of inside the first call. |
| `GROK_DELEGATE_SPAWN_PRIORITY` | on | Give a subprocess spawn a fair share of the GIL for the moment it takes. Measured: 1280 ms without, 23 ms with, under sixteen busy threads. |

## Timeouts and transports

| Variable | Default | What it does |
|---|---|---|
| `GROK_DELEGATE_GIT_TIMEOUT_SECONDS` | `60` | Budget for one git probe. Raise it on a machine where spawns are starved. |
| `GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS` | `600` | Budget for `worktree add`, which legitimately runs for minutes on a large repo. |
| `GROK_DELEGATE_WS_ENDPOINT` | unset | WebSocket ACP endpoint, for the websocket transport. |
| `GROK_DELEGATE_HTTP_TOKEN` | unset | Bearer token for the private HTTP transport. Required — the server will not start without it, loopback included. |
| `GROK_DELEGATE_HTTP_TOKEN_FILE` | unset | Read that token from a file instead of the environment. |
| `GROK_DELEGATE_HTTP_HOST` | `127.0.0.1` | Bind address. |
| `GROK_DELEGATE_HTTP_PORT` | `8765` | Bind port. |
| `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK` | off | Required to bind anywhere but loopback. There is no TLS in this process; put a reverse proxy in front. |
| `GROK_DELEGATE_HTTP_MAX_INFLIGHT` | bounded | Concurrent HTTP requests accepted. |

## Where things are written

| Variable | Default | What it does |
|---|---|---|
| `GROK_DELEGATE_JOBS_DIR` | per-user state dir | Where job records are persisted so a poll after a restart returns the result instead of `JOB_UNKNOWN`. Default `%LOCALAPPDATA%\grok-delegate\jobs` on Windows, `$XDG_STATE_HOME/grok-delegate/jobs` (else `~/.local/state/...`) elsewhere. Set a path to move it, or `off` to keep jobs in memory only. |
| `GROK_DELEGATE_VERDICTS_DIR` | unset | Where lane verdicts are written. |
| `GROK_DELEGATE_QUEUE` | unset | Queue file for the unattended lane driver (`python -m grok_delegate.driver`). |
| `GROK_DELEGATE_LOG_LEVEL` | `INFO` | Server log level. |
| `GROK_DELEGATE_LOG_FILE` | unset | Server log destination. |
| `GROK_DELEGATE_CLI_LOG` | off | Keep the Grok CLI's own debug log. |
| `GROK_DELEGATE_CLI_LOG_FILE` | unset | Where that log goes. |

## Not this bridge's

`GROK_AGENT_SECRET` and `GROK_SANDBOX` are read by the Grok CLI; `XAI_API_KEY`
is not read here at all. Authentication is `grok login`, and the bridge never
opens `auth.json`.
