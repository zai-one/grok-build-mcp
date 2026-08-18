# Security boundary

Round 8 is a local engineering bridge, not an OS sandbox. Windows sandbox flags
are not treated as isolation proof.

## Enforced controls

- `project_root` must resolve exactly to an allowlisted root. Descendants are
  not implicitly accepted.
- Execute/fix work happens in an external git worktree. Diff, commits, tests and
  artifacts are read back after the agent stops.
- Consult/skeptic use `read-only`; execute/fix use `workspace`. Role/profile
  mismatches fail closed.
- Permission requests are deny-by-default. Writes must resolve inside cwd.
  Reads/searches and writes must resolve inside cwd. Execute permission accepts
  only an exact packet `test_commands` entry after argv/path validation.
- `git push`, merge, pull, rebase, reset, clean and checkout are rejected, as
  are destructive filesystem, network and credential-related commands.
- Generated ACP argv cannot contain `--always-approve` or
  `bypassPermissions`.
- WebSocket binds/connects only to loopback. Its secret is process-local and
  redacted from both `Secret:` and `server-key=` log formats.
- Events, stderr previews and output are bounded. Managed daemon stderr is
  continuously drained into aggregate-capped process memory and redacted as one
  joined stream before a bounded preview is returned, including markers split
  across reader chunks. Secret-looking event keys are dropped, and a shared
  recursive redactor covers summaries, errors, events, audit and durable records.
- Default concurrency is 1 with a bounded queue of 8. Overflow fails with
  `QUEUE_FULL`; jobs never share an ACP session.
- Durable records preserve terminal receipts. A running record owned by a dead
  server incarnation is downgraded to `unknown`/stale rather than reported as
  live success. The v2 record marks it `orphaned` and whether its worktree makes
  it recoverable; serialized records are compacted below the reload cap.
- HTTP `--transport http`, if started, is private Bearer JSON-RPC: token
  required including loopback, loopback bind by default, one client per
  process (a differently-named `clientInfo` is refused; the same client
  reconnecting is not — the check is a warning, not authentication, because
  `tools/call` never required `initialize`). It is not MCP Streamable HTTP. Spec tool-bans from the 2026-08-18
  network threat model stay documentation-level unless that listener is on;
  turning it on still exposes the same tools as stdio to the bearer holder.

## False-success gates

`execute` and `fix` cannot remain `completed` without changed files. Expected
artifacts must exist inside the worktree after resolution (an outside symlink is
not evidence), and every post-run changed file must be an explicitly expected
artifact. The bridge snapshots a reused worktree before execution, so a stale
untracked artifact or old lane diff cannot certify a new run. Every write task
requires explicit tests; only a successful independent
bridge-verifier run of each exact command satisfies the receipt.

Agent prose is never the acceptance source. `changed_files`, `diffstat`, test
records, artifact readback and an external verifier are.

## Out of scope / residual risk

- An allowed local command executes with the user's OS identity. The command
  allowlist, worktree and post-readback are the primary controls.
- An already-running external WebSocket daemon is outside the MCP server's
  process tree. The operator owns its lifecycle and must keep its secret out of
  persistent configuration.
- Windows junction/reparse-point behavior is checked through resolved paths,
  but a malicious concurrent filesystem actor can still create TOCTOU races.
- The bridge does not push, merge, install global packages or modify global
  MCP/Grok configuration.

## Operator checklist

Use exact roots, keep lane/job directories outside the source repository,
inspect receipts before merge, repeat tests independently in the returned
worktree, and treat `blocked`, `failed`, `cancelled`, `no_changes` and stale
`unknown` records as non-success.
