# ACP transports

## Routing contract

Transport selection is explicit. `auto` is an alias for `stdio`; it never
cascades to WebSocket or legacy after an error.

| Requested value | Adapter | Fallback |
|---|---|---|
| `legacy` | Grok CLI/headless | none |
| `stdio` | ACP v1 newline-delimited JSON-RPC | none |
| `websocket` | ACP v1 JSON-RPC text frames | none |
| `auto` | `stdio` | none |

Both ACP adapters execute `initialize → session/new → session/prompt`. They
handle `session/update`, answer `session/request_permission`, and send the
`session/cancel` notification on cancellation or timeout. Protocol version is
integer `1`. The live contract is **ACP v1**, not a specific Grok CLI build:
the bridge does not pin `agentVersion` by default. Set
`GROK_DELEGATE_EXPECTED_AGENT_VERSION` only if an operator wants an opt-in
comparison; a mismatch is a warning on status/doctor and a non-blocking
`version_mismatch` ACP event — it does not fail the typed path.

Historical fixtures under `evidence/round8/acp-fixtures/` were captured on
Grok `0.2.118`. On that build, `rawInput` arrives in the preceding `tool_call`
update while the permission request carries its `toolCallId`; the bridge still
joins those two frames before applying the deny-by-default policy. Treat that
as observed behaviour, not a version gate. A live ACP rebaseline on the
currently installed CLI is a remaining check — do not invent new fixtures.

On a WebSocket disconnect after session creation the client makes one bounded
reconnect, negotiates ACP again and calls `session/load`. It does not replay an
in-flight prompt because that could duplicate writes; the receipt returns
`ACP_RETRY_REQUIRED`, and an operator may retry with a new correlation ID. The
installed `0.2.118` live daemon accepted the reconnect + `session/load` flow.

## Stdio

The server starts one `grok agent stdio` process per task with explicit default
permission mode, no leader reuse, no subagents, and web search disabled. stdout
is reserved for ACP JSON-RPC; stderr is bounded and redacted. Cancellation and
timeout close the process tree, including a Windows Job Object best-effort gate.

This is the default transport.

## WebSocket

Default WebSocket mode is managed per task. The adapter:

1. reserves a free loopback port;
2. generates a random ephemeral secret in memory;
3. starts `grok agent serve` in the task cwd, passing the secret only through
   `GROK_AGENT_SECRET` in the child environment;
4. performs an authenticated RFC 6455 upgrade at `/ws?server-key=...`;
5. runs the same ACP v1 lifecycle and shuts the daemon down.

Per-task cwd is deliberate. Grok `0.2.118` was observed to stall
`session/new` when a persistent daemon was started in a different cwd.

For an optional existing daemon, set process-local variables before launching
the MCP host:

```powershell
$env:GROK_DELEGATE_WS_ENDPOINT = 'ws://127.0.0.1:2419/ws'
$env:GROK_AGENT_SECRET = Read-Host 'Temporary Grok daemon secret' -MaskInput
grok agent serve --bind 127.0.0.1:2419
```

The endpoint rejects userinfo, query strings, non-`/ws` paths, `wss` and every
non-loopback host. The secret must not be placed in a command argument, config
file, task packet, receipt or evidence file. Start the daemon in the same cwd as
the requested task; managed mode is recommended for write roles because their
cwd is a newly created worktree.

## Legacy compatibility

The old tool names remain available. Read-only legacy work uses `grok --single`
in plan/read-only mode. Legacy execute retains the established isolated
worktree path. The typed `WorkReceipt` evidence gates apply to the new tools;
the old names keep their prior response shape for compatibility.

| Old 0.3.0 tool | Actual Round 8 mapping | Role | Transport | Permission |
|---|---|---|---|---|
| `grok_delegate` | retained compatibility handler | execute | legacy | workspace |
| `grok_delegate_plan` | retained compatibility handler | consult/plan | legacy | read-only |
| `grok_delegate_start` | retained async compatibility handler | execute | legacy | workspace |
| `grok_delegate_poll` | retained job readback | status | none | read-only |
| `grok_delegate_status` | retained status handler | status | all | read-only |
| `grok_delegate_doctor` | retained diagnostic | diagnostic | legacy | read-only |
| `grok_delegate_models` | retained diagnostic | diagnostic | legacy | read-only |
| `grok_delegate_inspect` | retained diagnostic | diagnostic | legacy | read-only |

New typed calls do not silently remap to those aliases. For example,
`grok_agent_execute` defaults to ACP stdio and requires an explicit
`transport: "legacy"` to use the old executor.

All typed write transports, including legacy, run the packet's exact
`test_commands` again in the returned worktree. Agent-emitted test events are
progress only and cannot satisfy the final receipt gate.

## Known compatibility boundary

The wire fixtures in `evidence/round8/acp-fixtures/` are the tested source of
truth. A different Grok agent version fails version negotiation instead of
silently claiming compatibility. Rebaseline fixtures and rerun live acceptance
before changing the expected version.
