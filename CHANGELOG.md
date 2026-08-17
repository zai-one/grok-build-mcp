# Changelog

## 0.10.0 — Live protocol evidence, and a receipt you can accept work from

The bridge's ACP frame handling had been written by watching **Grok 0.2.118** and
carried forward on comments citing that build, while **1.0.4** was what shipped.
Comments do not fail when the agent changes. This release replaces them with
captured traffic, and fixes the four defects that capture exposed.

### Live ACP capture — the protection against CLI upgrades

- `scripts/capture_acp_live.py` drives the same sequence the bridge drives
  (`initialize` → `session/new` → `session/prompt` → `session/request_permission`
  → `session/cancel`) against **whatever CLI is installed**, and writes bounded
  redacted fixtures to `evidence/live-acp/`. Four scenarios:
  `permission-cancel`, `consult`, `command`, `websocket`
- `tests/test_live_acp_fixtures.py` (33 tests) replays those fixtures through the
  real parsers, so a CLI upgrade fails a test instead of failing a job
- The capture denies every permission the scenario did not ask for, so producing
  a fixture can never become a way to run arbitrary tools
- Replaces `scripts/capture_acp_initialize.py`, which only captured the handshake
- **Still no version pin.** `DEFAULT_EXPECTED_AGENT_VERSION` stays `None`; the
  compatibility contract remains ACP protocol integer `1`. Fixtures record the
  observed build as evidence, and a test asserts that recording it did not turn
  into requiring it

### Fixed — found by the capture, not by reading the code

- **A legitimate file read was denied.** The 1.0.4 read tool names its path
  `target_file`; path confinement did not know that key, and an unrecognised key
  reads as "no path at all", which fails closed
- **An agent could report a green test that had failed.** A shell chain's exit
  code belongs to its last statement: live, `pytest -q; echo EXIT_CODE=$LASTEXITCODE`
  returned `exit_code: 0` while pytest failed. Agent-harvested results are now
  labelled `source: "agent-reported"` and yield `passed: null` on a chained
  command. Only `bridge-verifier` entries count as acceptance evidence
- **Test commands must reach the permission gate verbatim.** The gate matches the
  declared string exactly; a decorated command is denied and the worker's turns
  are wasted on it. `build_prompt` now tells the worker so. The gate itself was
  not loosened
- The two-frame `rawInput` join is documented as version-defensive rather than
  required — 1.0.4 fills `rawInput` in the permission request itself
- Over WebSocket the agent also emits `_x.ai/session/update`, one character from
  the spec'd `session/update`. Dispatch compares exactly; a test pins that

### Receipt — answers the two questions the host used to open the repo for

- **`tests` is populated whenever there is something to verify.** The verifier
  was gated on the agent reaching `completed`, so a worker that exhausted
  `max_turns` returned an empty `tests` list next to real edits —
  indistinguishable from tests that were never written. Whether the agent
  finished its turn and whether its code passes are different questions
- **New field `tests_skipped_reason`**: `NOT_A_WRITE_ROLE` / `CANCELLED` /
  `NO_CHANGES` / `NO_TEST_COMMANDS`, or `null` when the verifier ran. An empty
  list no longer has to be guessed at
- **New field `lane_commit`**: the bridge commits whatever the worker left
  uncommitted, on its own `grok/*` branch, after the verifier has run — so a test
  that reverts the artifact still fails acceptance. Refuses on any branch that is
  not a lane, verifies the branch with git rather than trusting the caller, and
  never retries with `--no-verify`. Asking the worker to commit only worked while
  it had turns left, which is not a mechanism
- **`base_ref` is resolved to a commit before the worker starts.** It defaults to
  `HEAD`, and `HEAD` moves on the first commit in the lane — after which every
  diff "against the base" is a diff against the work itself, and a finished job
  reports `no_changes`
- The receipt schema now states that `changed_files` and `unified_diff` are
  **different slices** and do not correspond 1:1

### Navigator

- `intent=auto` matched the bare substring `test`, so `update tests/conftest.py`
  routed to `verify` and a directory name decided the mode. Path-ish tokens are
  stripped, words match whole, and the goal's leading verb is asked first
- The install card offered a `curl | bash` pipeline to hosts that have neither;
  it now names the installer for the platform. A card is executed verbatim, so an
  unrunnable one reads as a working answer
- **Breaking for card consumers:** the `update` card is now
  `{"kind": "tool", "tool": "grok_agent_update"}` instead of
  `{"kind": "host_cmd", "cmd": "..."}`, now that the tool exists

### Also

- `test_cancel_has_independent_grace_deadline` was flaky roughly every other run:
  it cancelled on a wall-clock sleep, so under load the fake agent had not
  answered `session/new` yet and the run took the "cancelled before a session
  existed" branch. It now waits for the `session_created` event — the condition
  the code actually branches on
- `--no-subagents` stays hard-coded, by decision rather than oversight: no
  capture shows a subagent's tool calls arriving on `session/request_permission`,
  and until one does, enabling them would put work outside the only gate the
  bridge has. The reason is recorded beside the argv
- Tests: **605 passed, 1 skipped** (was 543)
- Evidence: `Service/Handoffs/grok-mcp-production-ready-evidence.md`

## 0.9.0 — Unpin, typed session_next, evidence pack
- Default Grok CLI `agentVersion` check is **off** (`any`). Opt-in pin:
  `GROK_DELEGATE_EXPECTED_AGENT_VERSION`. Mismatch is a warning event plus
  status/doctor `compatibility.warning`; the typed path is not blocked
- `session_next` execute cards compile a full `task` packet; poll cards are
  `{job_id}` only. The server binds `job_id` from execute. Contract tests
  validate cards against the real tool schemas
- Compact poll/receipt evidence: listed files, diffstat, optional **bounded**
  unified diff (16KiB cap), bridge-verifier tests, `worktree_path`
- `grok_agent_status` / doctor report `bridge_version`, `grok_delegate_version`,
  detected CLI, ACP v1, unpin status, skill protocol, `update_hint`
  (`git pull` / editable reinstall / restart MCP — no updater daemon)
- AGENTS.md, Cursor rule, GitHub issue templates, skill v1.1 on
  `.cursor` / `.claude` / `.codex` / `.agents`
- Live ACP **initialize** captured on the installed CLI (observed `1.0.4`);
  that string is not a pin. Permission/cancel/WebSocket frames remain to capture
- `GROK_DELEGATE_TRUST_HOST_ROOTS=1` lets the host's project directory
  (`CLAUDE_PROJECT_DIR`) join the allowlist, so the session's own project no
  longer has to be listed by hand. Opt-in; widens the explicit list instead of
  replacing it; exact-equality membership unchanged. `grok_agent_status` reports
  `roots.host_root_trusted` and `roots.host_root`
- `.mcp.json` wired a third-party `grok-cli-mcp` server instead of this one; its
  draft-04 schemas made hosts fail every request after loading a tool
- Project-scoped entry now resolves the package via `CLAUDE_PROJECT_DIR` (no
  working-directory assumption) and takes the interpreter from `${GROK_MCP_PYTHON:-py}`
- `tests/test_tool_schemas.py` guards draft 2020-12 conformance of every tool schema
- `install.ps1` writes Claude/Cursor snippets, matching `install.sh`, and carries
  the environment inside them. `install.sh` points its snippets at a wrapper that
  sources the env file first; Windows has no wrapper, so a snippet with `"env": {}`
  produced a server with an empty allowlist and every repo-touching tool failed
  closed
- Corrected stale server version and tool counts in README and Codex setup readback
- `docs/EASY.md` stacked three contradictory session protocols (v0.8, v1.1, v1.2);
  collapsed to the current navigator loop
- README documents the allowlist the project-scoped path needs, since that path
  never runs the installer that would have written it

## 0.8.0 — Session Protocol v1.2 Navigator
- `*_session_next` returns one action card (host_cmd|mcp_tool|end)
- Host loop: begin → next* → end (minimal tokens)
- Skill v1.0.0 enforces navigator-only protocol
- Install/update/feedback cards without empty plans

## 0.7.0 — Session Protocol v1.1
- Plan compiler + budget guard
