# Round 8 handoff

## VERDICT: GO

The worktree contains one MCP stdio frontend over three explicit transports:
`legacy`, ACP `stdio`, and ACP `websocket`. `auto` means only `stdio`; no
transport silently falls back to legacy. The current code snapshot produced a
nonempty, independently tested change through every write transport.

| Phase / transport | Verdict | Current evidence |
|---|---|---|
| P0 ACP 0.2.118 fixtures | GO | real redacted initialize/session/permission/cancel/WebSocket frames |
| P1 legacy consult | GO | live non-git read-only consult |
| P1 legacy execute | GO | typed MCP job `job-01e2e3567df61d6e`, nonempty artifact, bridge test + independent test |
| P2 ACP stdio | GO | typed MCP job `job-f70ba766f627f6bc`, ACP session, nonempty artifact, both tests, worker stopped |
| P3 ACP WebSocket | GO | typed MCP job `job-c32b279a02292b8c`, authenticated loopback handshake, nonempty artifact, both tests, worker stopped |
| P4 hardening | GO | root pre-merge recheck: 0 BLOCK / 0 HIGH; 413-test full suite green |

## What was implemented

- Eight `grok_agent_*` typed tools plus all eight existing compatibility tools.
- Versioned closed task, receipt and event contracts.
- Explicit `TransportRouter` with legacy, stdio and WebSocket adapters.
- ACP v1 initialize, session, streaming updates, two-frame permission joining,
  completion, bounded cancel and one fail-closed reconnect/session-load attempt.
- Exact-root/read-path/credential-path confinement, exact test-command policy,
  no shell execution by the bridge verifier and no auto-approve/bypass mode.
- Isolated write worktrees; pre-run and post-verifier git snapshots prevent stale
  or test-reverted artifacts from certifying completion. Unexpected changed
  files block the receipt. Any failed, timed-out or missing mandatory git probe
  fails with `DIFF_SNAPSHOT_FAILED` before acceptance can run or be promoted.
- Versioned bounded durable jobs, idempotent admission, concurrency 1, bounded
  queue, immediate queued cancellation and terminal persistence ordering.
- Byte-correct MCP Content-Length, bounded ACP/legacy/daemon output, recursive
  redaction (including bare credentials, PEM markers, mapping keys, containers,
  unknown scalar objects and the live receipt) and cross-reader-boundary stderr
  redaction. A surviving worker forces `WORKER_STILL_ALIVE_AFTER_SHUTDOWN`.

## Reproduction

```powershell
Set-Location '<LANES_PARENT>\safe-consult-acp-bridge'
py -3 -m pytest tests/test_round8_bridge.py -q
py -3 -m pytest tests -q
py -3 -m grok_delegate --self-test
```

Expected final readback:

- Root pre-merge focused: `6 passed, 46 deselected`.
- Round 8 targeted: `51 passed, 1 skipped`.
- Full: `413 passed, 1 skipped, 43 subtests passed`.
- Self-test: `RESULT: PASS`, 16 tools advertised, Grok `0.2.118` detected.

The final current-hash stdio job is `job-af10545752371eea`: MCP and ACP
handshakes completed, `current-hash.txt` was the only diff, the bridge and
independent tests passed, and the worker stopped. Current-hash before/after
reproductions are in `evidence/round8/root-premerge-fixes.json`. Earlier
failed/cancelled attempts in `live-smoke.json` were not promoted to success.

## Branch and changed files

- Branch: `grok/safe-consult-acp-bridge` from `b99b23931917a17ec433db332bc0067189af0eb5`.
- Commits created: none.
- Uncommitted worktree diff: 32 files, 6,274 insertions, 41 deletions.
- No push or merge was performed.

Changed files:

```text
README.md
ROUND8-HANDOFF.md
docs/ACP-TRANSPORTS.md
docs/CODEX-MCP-SETUP.md
docs/SECURITY.md
evidence/round8/acp-fixtures/README.md
evidence/round8/acp-fixtures/cancel.jsonl
evidence/round8/acp-fixtures/initialize.jsonl
evidence/round8/acp-fixtures/permission-deny.jsonl
evidence/round8/acp-fixtures/session-consult.jsonl
evidence/round8/acp-fixtures/websocket.jsonl
evidence/round8/baseline.json
evidence/round8/final-verification.json
evidence/round8/live-smoke.json
evidence/round8/root-premerge-fixes.json
evidence/round8/skeptic-receipt.json
evidence/round8/test-results.txt
grok_delegate/acp.py
grok_delegate/agent_runtime.py
grok_delegate/audit.py
grok_delegate/contracts.py
grok_delegate/guard.py
grok_delegate/jobs.py
grok_delegate/jobs_store.py
grok_delegate/runner.py
grok_delegate/server.py
schemas/grok-events.v1.schema.json
schemas/grok-task-packet.v1.schema.json
schemas/grok-work-receipt.v1.schema.json
tests/fake_acp_agent.py
tests/fake_acp_ws_server.py
tests/test_round8_bridge.py
```

## Copy-ready Codex connection (not executed by Round 8)

```powershell
codex mcp add grok-delegate `
  --env 'PYTHONPATH=<REPO_PATH>' `
  --env 'GROK_DELEGATE_ALLOWED_ROOTS=<REPO_PATH>' `
  --env 'GROK_DELEGATE_REPO_ROOT=<REPO_PATH>' `
  --env 'GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>' `
  --env 'GROK_DELEGATE_JOBS_DIR=C:\Users\<USER>\AppData\Local\grok-delegate\jobs' `
  -- py -3 -m grok_delegate.server
```

This command changes the user's Codex configuration and was deliberately not
run. No secret belongs in the MCP configuration; managed WebSocket mode creates
an ephemeral child-process secret.

## Rollback

Before merge, rollback is simply abandoning the isolated branch/worktree. After
merge, callers can explicitly use `transport: "legacy"` or the preserved old
`grok_delegate*` tools. To remove the MCP registration, the operator may run
`codex mcp remove grok-delegate`; Round 8 did not run that global mutation.

## Residual risks

- This is not an OS sandbox. Windows isolation claims are limited to exact
  roots, worktrees, permission policy, process trees and post-readback.
- Grok `0.2.118` persistent WebSocket sessions can stall when daemon cwd and
  task cwd differ. Managed per-task WebSocket is the supported write mode.
- Disconnect recovery loads the session but does not replay a write prompt;
  the caller receives `ACP_RETRY_REQUIRED` and must approve a new correlation.
- One host-dependent directory-symlink test is skipped because this Windows
  account lacks symlink privilege; deterministic exact-root and resolved-path
  escape tests pass.

## Independent pre-merge check

Codex should inspect the complete diff, parse all JSON evidence/schemas, rerun
the three commands above, start a fresh temporary git repository, and repeat
MCP initialize → tools/list → typed legacy/stdio/WebSocket execute → poll. It
must confirm an expected-only diff, `source=bridge-verifier`, an independent
test pass, and no surviving worker. Merge/push remains a separate human action.
