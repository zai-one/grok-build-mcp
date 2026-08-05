# Codex MCP setup (Windows)

Round 8 server remains an MCP stdio process. The configuration below contains
exact roots and no credentials. Run it only after the branch is merged into
`D:\ZAI\MCP\Grok CLI`.

## Codex CLI

Copy into PowerShell:

```powershell
codex mcp add grok-delegate `
  --env 'PYTHONPATH=D:\ZAI\MCP\Grok CLI' `
  --env 'GROK_DELEGATE_ALLOWED_ROOTS=D:\ZAI\MCP\Grok CLI' `
  --env 'GROK_DELEGATE_REPO_ROOT=D:\ZAI\MCP\Grok CLI' `
  --env 'GROK_DELEGATE_LANES_PARENT=D:\ZAI\MCP\grok-lanes' `
  --env 'GROK_DELEGATE_JOBS_DIR=C:\Users\codex\AppData\Local\grok-delegate\jobs' `
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
        "PYTHONPATH": "D:\\ZAI\\MCP\\Grok CLI",
        "GROK_DELEGATE_ALLOWED_ROOTS": "D:\\ZAI\\MCP\\Grok CLI",
        "GROK_DELEGATE_REPO_ROOT": "D:\\ZAI\\MCP\\Grok CLI",
        "GROK_DELEGATE_LANES_PARENT": "D:\\ZAI\\MCP\\grok-lanes",
        "GROK_DELEGATE_JOBS_DIR": "C:\\Users\\codex\\AppData\\Local\\grok-delegate\\jobs"
      }
    }
  }
}
```

Multiple exact roots use `;` in `GROK_DELEGATE_ALLOWED_ROOTS`. A child of an
allowlisted root is not implicitly trusted.

## First readback

1. MCP `initialize` must return protocol `2024-11-05` and server `0.4.0`.
2. `tools/list` must include all 8 `grok_agent_*` tools and all 8 compatibility
   tools.
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
