# Codex MCP setup (Windows)

Round 8 server remains an MCP stdio process. The configuration below contains
exact roots and no credentials. Run it only after the branch is merged into
`<REPO_PATH>`.

## Codex CLI

Copy into PowerShell:

```powershell
codex mcp add grok-delegate `
  --env 'PYTHONPATH=<REPO_PATH>' `
  --env 'GROK_DELEGATE_ALLOWED_ROOTS=<REPO_PATH>' `
  --env 'GROK_DELEGATE_REPO_ROOT=<REPO_PATH>' `
  --env 'GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>' `
  --env 'GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>' `
  -- py -3 -m grok_delegate.server
```

This command mutates the user's Codex MCP configuration, so Round 8 did **not**
run it automatically. Verify with `codex mcp list`, then start a new Codex task.

## Generic MCP host

Merge this server entry into the host's MCP JSON:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "py",
      "args": ["-3", "-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<REPO_PATH>",
        "GROK_DELEGATE_REPO_ROOT": "<REPO_PATH>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Multiple exact roots use `;` in `GROK_DELEGATE_ALLOWED_ROOTS`. A child of an
allowlisted root is not implicitly trusted.

## First readback

1. MCP `initialize` echoes a handshake-era protocol version the server
   speaks. A client that sends `2024-11-05` gets `2024-11-05` back; a
   client that sends an unknown or later revision (including
   `2026-07-28`) gets `2025-06-18`. `serverInfo.version` is the package
   version (`0.26.0` on this line).
2. `tools/list` must include all 15 `grok_agent_*` tools and all 8
   `grok_delegate*` compatibility tools — 23 in total. (`grok_agent_project`
   and `grok_agent_update` arrived after this count was last written; verify it
   against the server rather than this line, which has now drifted twice.)
3. Call `grok_agent_status`; default transport must be `stdio`, and auto
   behavior must be `stdio-only-no-fallback`.
4. Start with `grok_agent_consult`, then use a temporary git repository for an
   `execute` acceptance check before authorizing a production root.

No MCP configuration should contain `GROK_AGENT_SECRET`. Managed WebSocket mode
generates an ephemeral process-only secret. See `ACP-TRANSPORTS.md` for the
optional persistent daemon.

## Rollback

Use the old `grok_delegate*` tools or explicitly set `transport: "legacy"`.
To remove the new server from Codex, run `codex mcp remove grok-delegate`; this
does not remove worktrees or job evidence. No Round 8 step changed a global
configuration.
