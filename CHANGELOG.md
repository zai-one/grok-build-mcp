# Changelog

Every release has a name, because a version number does not tell anyone whether
to upgrade. Entries answer **why** a change exists and what an existing setup
will notice; the diff already says what moved.

**How this file is kept**

- One section per version, newest first: `## X.Y.Z — <what the release is>`.
  Work that has not shipped collects under `## Unreleased` until it does, and
  that heading then becomes the version.
- Anything an operator or a host can observe gets an entry: tool schemas, wire
  answers, defaults, env vars, refusals, install steps. Internal refactors that
  change nothing observable do not.
- Breaking changes are marked **Breaking:** and say what an existing setup will
  see. A moved default is breaking even when nothing raises an error.
- Semver is judged from the MCP surface. A changed tool schema, a changed wire
  answer, or a moved default is at least a minor. Fixes that leave the surface
  alone are patches.
- Written for the person deciding whether to upgrade, so entries name the
  symptom rather than the commit.

**Where the version lives.** Four places must agree: `pyproject.toml`,
`SERVER_VERSION` in `grok_delegate/guard.py` (the package reads it from there —
`__init__.py` re-exports it, and a test forbids any other module from spelling
it out), the badge in `README.md`, and the line in `docs/CODEX-MCP-SETUP.md`.
Release procedure is in [AGENTS.md](AGENTS.md).

---

## Unreleased

### A capture of the protocol was also a capture of the operator's servers

`evidence/live-acp/*.jsonl` is committed on purpose -- `tests/test_live_acp_fixtures.py`
reads it -- and the Grok CLI announces the host's whole MCP inventory during a
session. So four fixtures carried the names and endpoint URLs of whatever else
the person who recorded them had wired up. No token, which is exactly why the
redactor let it through: a bare URL matches no secret pattern.

`scripts/capture_acp_live.py` now replaces any `mcpServers` list with its count,
and the four committed fixtures are scrubbed. Nothing asserts on that field, so
the evidence they exist to provide is unchanged.

`.claude/settings.json` is no longer tracked, and is ignored from now on. It held
one line naming a plugin from the author's own workspace: personal host state
that had nothing to do with this project and no reason to be public.

## 0.14.0 — The host already knows which folder you opened

Installing the bridge used to leave you with a server that started fine and then
refused every job tool with `ALLOWED_ROOTS_EMPTY`. The only cure was editing the
MCP host's own config to add an environment variable and restarting the host —
before the first useful call, in every host, for every project.

MCP has a mechanism for exactly this question, and the reason it was missing is
structural rather than accidental: `roots/list` is a request the **server** sends
to the **client**, and this server's stdio loop only ever answered. It never
asked. Now it does.

- After `notifications/initialized`, a client that declared the `roots`
  capability is asked for its roots, and those directories join the allowlist.
  No environment variable, no restart.
- `notifications/roots/list_changed` re-asks. Closing a folder narrows the
  scope — a withdrawn root stops being granted rather than lingering.
- Declared roots **widen** the explicit allowlist rather than replacing it.
  `GROK_DELEGATE_ALLOWED_ROOTS` and `GROK_DELEGATE_REPO_ROOT` keep their meaning.
- `GROK_DELEGATE_MCP_ROOTS=0` refuses host-declared roots entirely.

**Breaking:** a host that declares roots now grants them by default. This is
deliberately unlike `GROK_DELEGATE_TRUST_HOST_ROOTS`, which stays opt-in: that
one reads an environment variable any process could set, while a root here
arrives because a person opened that directory in their editor, over the
protocol's designated channel. No tool call can invent one — the agent never
gets to name a root.

### `ALLOWED_ROOTS_EMPTY` says which of three situations this is

The message was `configure GROK_DELEGATE_ALLOWED_ROOTS or GROK_DELEGATE_REPO_ROOT`
regardless of cause, and in the most common case — a host that would have
declared its workspace if asked — it was also the wrong advice. It now names
whether host roots are switched off, whether the host never offered the
capability, or whether it simply has not answered yet, and carries `fix_with`
steps for that case. `PROJECT_NOT_ENABLED` gained `restart_required: false`,
because that one really is fixable from inside the running session.

## 0.13.0 — Say what the transport is

Three read-only research passes asked what it would take to be a network MCP
server. The answer that survived was: don't be one. Stdio is the product and
is a normative transport; the 199-line HTTP listener is a private Bearer
JSON-RPC binding an operator opts into, and calling it "Streamable-ish" was
the closest thing to a spec claim it ever had. So this release stops implying
otherwise, and fixes the one thing that was genuinely wrong on the wire — the
handshake.

Reports: `Service/Research/2026-08-18-mcp-transport-conformance.md`,
`-network-threat-model.md`, `-multi-client.md`.

### The handshake stopped answering with the first revision ever published

- `initialize` reads `params.protocolVersion` and echoes a handshake-era
  revision we actually speak (`2024-11-05`, `2025-03-26`, `2025-06-18`).
  Unknown or modern-only requests get `2025-06-18`. Previously every client
  was told `2024-11-05` no matter what it asked for, which is what the spec
  says to do only when nothing else matches.
- `2026-07-28` is not claimed. That era drops `initialize` for
  `server/discover`, and we do not speak it.

**Breaking:** a host that pins `2024-11-05` still gets `2024-11-05`; a host
that requires exactly `2025-11-25` now sees `2025-06-18` and may disconnect
rather than fall back.

### One client per process, but a reconnect is not a second client

An `initialize` from a different `clientInfo.name` is refused with
`ONE_CLIENT_PER_PROCESS`. The same client reconnecting is not — the claim used
to be permanent, so a dropped connection cost a service restart. Nothing was
protected by that: `tools/call` never required `initialize`, so the refusal
only ever stopped the well-behaved client. The check is a warning that two
hosts are sharing one job registry, not authentication; the bearer is that.

### HTTP remote install

- Non-loopback HTTP bind requires `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1` and
  prints a plaintext warning. This process still does not terminate TLS.
- `GET /` is no longer an unauthenticated alias of `/healthz`. `GET` and
  `DELETE` on `/mcp` return 405 with `Allow: POST`.
- POST without `Content-Type: application/json` is 415, including a missing
  header.
- Access logs drop query strings. The HTTP bearer is registered with the
  receipt redactor (CSPRNG hex has no `xai-` prefix).
- `docs/install/vps.md` is the install path for the private JSON-RPC
  transport, not a FastMCP workaround.

## 0.12.0 — Lanes live with the project

A lane holds unmerged work someone is going to review. That is project state,
not a cache, so it now lives inside the project it belongs to:

    <project>/.grok/lanes/<slug>

The leading dot is what makes this safe rather than a mess, and each claim was
checked rather than assumed: pytest's default `norecursedirs` skips `.*`, so the
project's own suite does not collect the tests inside every lane; ripgrep and
most indexers skip hidden directories; and one `.gitignore` line hides it from
git. The bridge appends that line itself on first use, asking `git check-ignore`
rather than parsing the file, so a rule already present anywhere counts.

**Breaking:** the default moved. `GROK_DELEGATE_LANES_PARENT` still overrides it
and existing lanes are untouched, but a host relying on the old default will find
new lanes somewhere else.

### One question, one answer

Three call sites computed three different defaults, so `grok_agent_status`
reported a directory `execute` would never write to -- `<parent>/pcp-lanes`,
a name inherited from an unrelated project, while typed execute used
`<parent>/<repo>-grok-lanes` and the installer wrote a third. They now share one
resolver, and a test asserts they agree.

### The inside-repo guard became a rule instead of a refusal

`WORKTREE_INSIDE_REPO` and `LANES_PARENT_INSIDE_REPO` refused any path inside the
project. They now refuse any path inside the *visible* source tree: reachable
only through a leading dot-directory is allowed, everything else -- including
`src/.grok/x`, whose first segment is an ordinary directory -- still fails closed.

## 0.11.0 — What the audits found

Six independent read-only audits ran in parallel over the 0.10.0 tree: security,
receipt truthfulness, concurrency, host contract, test quality (by mutation), and
platform/docs. Every blocker and major finding below was re-verified against the
code before it was acted on. Triage of all of them, including the five that were
rejected or downgraded, is in `Service/Audits/2026-08-17-triage.md`.

Tests: **710 passed, 1 skipped** (was 605).

Also carried in from after the 0.10.0 tag: `jsonschema` is declared in the `test`
extra, so `tests/test_tool_schemas.py` validates the tool schemas against the real
2020-12 metaschema instead of skipping in silence on a runtime install.

### Receipts could still lie

- **A rejected job could seed the lane it was rejected on.** `no_changes`
  returned before the unexpected-files check ran, so a run that changed nothing
  while sitting on someone else's file reported only that nothing happened -- and
  the bridge committed that file to the branch anyway.
- **And the next job hid it.** `base_ref` defaults to `HEAD` and was resolved
  inside the worktree, where on a reused lane `HEAD` is the previous job's
  commit. Everything an earlier job left dropped out of the next diff, so the
  second receipt came back clean with the refused file still on the branch. The
  base is now resolved in the main repository, which is where `prepare_worktree`
  already resolved it to create the lane.
- **The verifier could certify itself.** Acceptance is read after the tests so a
  test that reverts the artifact fails -- which also meant a test that *creates*
  it looked like delivered work. The tree is snapshotted before the tests too;
  new field `verifier_touched_files`, new reason `ARTIFACT_WRITTEN_BY_VERIFIER`.

### Correct work was being refused

- `git status --porcelain` collapses an untracked tree to `?? src/`, so a worker
  asked for `src/app.py` delivered exactly that and the receipt reported a change
  to `src` -- a path nobody expected. `-uall` lists the files instead, which also
  stops one collapsed entry from hiding a second, unexpected file beside it.
- Paths were compared as strings on a filesystem that folds case, so
  `EXPECTED.TXT` and `expected.txt` arrived as one missing artifact plus one
  foreign change: the same file blocking the job twice.
- `git diff` cannot show an untracked file, so "create this file" -- the most
  ordinary execute there is -- produced an empty `unified_diff` beside a
  non-empty `changed_files`. The rendering is refreshed from the lane commit.

### Security

- **The HTTP transport now requires a token wherever it binds, loopback
  included** (breaking). With no token every request was authorized, and that
  endpoint reaches the job tools. A POST that is not `application/json` is
  refused, so a cross-origin simple request cannot reach the handler.
- **The command allowlist held nothing on its own.** Tested directly it let
  through an interpreter the worker could have written (`argv[0]` was never
  confined, and Windows searches the working directory for a bare name),
  `Get-Content .env`, and `git show HEAD:.env`. `argv[0]` is now the one operand
  whose rule is inverted, and is resolved before exec.
- **Redaction knew two prefixes.** GitHub, GitLab, AWS, Slack, Google, JWT and
  npm credentials, and passwords inside URLs, went through untouched. A
  credential wrapped onto the next line leaked too -- stdio redacted stderr line
  by line, so whether a key escaped depended on the terminal width.

### The navigator could not finish a write job

- The execute plan offered one poll for a job measured at 32 seconds, then said
  done. The poll step repeats while the bound job runs; `max_polls` still bounds
  it, an unknown job releases it, and the turn that sees a terminal job still
  hands back the poll, because that call fetches the finished receipt.
- `session_begin` accepts `job_id`, so verify mode can be aimed at a job.
- `PROJECT_NOT_ENABLED` carries `fix_with`: the tool and arguments that fix it.
- The update route pointed at a shell script from before `grok_agent_update`.

### Tests that could not fail

Four acceptance gates had no test at all -- a mutation audit deleted each in turn
and the suite stayed green every time. The command allowlist had no direct test,
because every existing case reached it through a command the task never declared.
Both are covered now. A missing live-ACP fixture used to skip, which meant the
protection against a CLI upgrade could vanish while the suite reported green.

### Docs the audits proved wrong

`ACP-TRANSPORTS` still promised fail-closed version negotiation, which would send
a reader off to pin the CLI. The documented Windows one-liner could not pass
`-Project` and defaulted to the whole user profile. `install.ps1` wrote its MCP
snippets with a BOM under Windows PowerShell 5.1, which strict JSON parsers
refuse. The tool count said 21 where `list_tools` returns 23. The token-economy
claim is now stated with the measurement behind it -- 61-88% smaller than a full
job record, and *larger* than reading the diff alone until the diff passes 16 KiB.

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
