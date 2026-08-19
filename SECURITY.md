# Security Policy

## Reporting a vulnerability

Please report security issues through **GitHub Security Advisories** on this
repository (Security → Advisories → New draft advisory). Do not open a public
issue for unfixed vulnerabilities, and do not attach secrets, tokens, or full
MCP host configs that may contain environment values.

If you cannot use advisories, write to
[contact@zai.one](mailto:contact@zai.one) — still without credentials, and
without a full MCP config. The bridge is maintained by
[ZAI.ONE](https://zai.one).

We aim to acknowledge reports promptly and coordinate a fix before public
disclosure when possible.

## Credentials and secrets — repository policy

**Never commit:**

- OAuth tokens, refresh tokens, or session cookies
- API keys, bearer tokens, or webhook secrets
- `GROK_AGENT_SECRET` or any WebSocket server key
- Real absolute home paths, machine names, or private repo roots in examples
- Screenshots or logs that include the above

This repository must stay free of live credentials. Use placeholders only
(`<REPO_PATH>`, `<PROJECT_ROOT>`, `<LANES_PARENT>`, `<JOBS_DIR>`).

## How authentication works

`grok-delegate` does **not** implement its own OAuth flow and does **not**
accept Grok API keys in MCP configuration.

1. Install and log in with the **local Grok CLI** (`grok login` or the CLI’s
   documented auth flow) on the same machine that will run the MCP server.
2. The MCP server reuses that **existing local CLI session** (presence is
   probed with read-only commands such as `grok models`). It never opens
   credential files to extract tokens for MCP config.
3. MCP host configs (`claude_desktop_config.json`, `.mcp.json`, Cursor
   `mcp.json`, Codex MCP entries, etc.) should only pass **non-secret**
   environment variables (allowlisted roots, lanes parent, jobs dir, binary
   path, sandbox profile, log level).

### Never put secrets in MCP config

Do **not** set any of the following in host JSON, shell history snippets
committed to git, or shared docs:

| Name | Why |
|---|---|
| `GROK_AGENT_SECRET` | Loopback WebSocket auth; process-local only |
| OAuth access / refresh tokens | Belong to the Grok CLI session store |
| API keys / bearer tokens | Not used by this server’s public surface |
| `server-key` query parameters | Must not appear in durable config |

Managed WebSocket mode generates an **ephemeral** secret in memory for the
child `grok agent serve` process. Optional persistent daemons require the
operator to inject `GROK_AGENT_SECRET` only in the **process environment** of
the running shell — never in committed config files.

## What is stored (and what is not)

### Not stored by this server

- OAuth tokens or API keys
- Password or cookie material
- Full unredacted daemon secrets in receipts, audit events, or status JSON
- Implicit trust for paths outside the configured allowlist

### May be stored (operator-controlled, non-secret)

- Durable job records under `GROK_DELEGATE_JOBS_DIR` (state, receipts, bounded
  redacted previews)
- Optional log file (`GROK_DELEGATE_LOG_FILE` or under the jobs dir)
- Git worktrees under `GROK_DELEGATE_LANES_PARENT` (code and diffs only)
- In-memory job registry for the lifetime of the MCP process

Secret-looking event keys are dropped; stderr and nested structures are
recursively redacted before being returned to the MCP client.

## Threat model (summary)

This is a **local engineering bridge**, not an OS sandbox. Allowed commands
run as the user’s OS identity inside policy gates (exact project roots,
worktree isolation for execute/fix, deny-by-default permissions, no push/
merge, loopback-only WebSocket). See `docs/SECURITY.md` for enforcement
details and residual risk.

## Supported versions

Security fixes target the latest published release line on `main`. Older
dev-only snapshots may not receive backports.

## Token economy & remote (VPS)

- Economy mode shrinks host-agent payloads (compact polls / playbooks). It does
  **not** relax auth or root policy.
- HTTP bearer tokens are **operator-generated secrets**, never OAuth/session
  material from Grok or Codex CLI. `--transport http` is private JSON-RPC,
  not MCP Streamable HTTP; prefer stdio (including over SSH).
- Bind loopback. A plaintext non-loopback bind requires
  `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1`. Treat the bearer as single-tenant
  (one process per client) and rotate it if leaked.
- This project is **unofficial** and not affiliated with xAI, Grok, OpenAI,
  Codex, or Anthropic.

