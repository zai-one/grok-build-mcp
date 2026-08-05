# grok_delegate — ROUND3 evidence (R1–R5)

Working directory: `C:\Users\<USER>\Documents\Projects\MCP\Grok CLI`  
Source relocate: `pcp-lanes/grokd-delegate-mcp/tools/grok-delegate/`  
Live CLI verified on this host: `grok --help`, `grok agent --help` (see implementer scratch `grok-help.txt`).

## Two-commit history

1. **Relocate as-is** — byte-identical module bodies into `grok_delegate/` + `tests/`.
2. **Fix R1–R5** — headless argv, interpreter allow removal, permission-mode + Windows denies, behavior tests, client `grok_bin`/root trust.

## R1 — Headless launch

**Live help (host):**

| Interface | Help text | Used? |
|---|---|---|
| `-p, --single <PROMPT>` | “Single-turn prompt. Prints the response to stdout and exits” | **Yes** — production argv |
| `--prompt-file <PATH>` | Single-turn prompt from a file | Supported by `assert_argv_safe` / `argv_has_headless_interface` |
| `--prompt-json <JSON>` | Single-turn prompt as JSON content blocks | Supported by validators |
| bare `[PROMPT]` | “Initial prompt for the **interactive** session” | **Rejected** |
| `grok agent stdio\|headless\|serve` | Alternate transports (stdio/WS); not used for simple subprocess capture | Not selected |

**Ship path:** `guard.build_grok_argv` → `["…", "--single", "<goal>"]` with `--output-format json`, `--cwd`, `--max-turns`, `--permission-mode`.  
**Tests:** `tests/test_grok_delegate.py` → `test_argv_uses_headless_single_not_positional`, `test_vector_headless_required_on_shipped_builder`, `test_assert_argv_safe_requires_headless`.  
**Never:** `--always-approve`.

## R2 — Shell / interpreters

**Before:** `_EXECUTE_ALLOW` included `Bash(python*)`, `Bash(pytest*)`, `Bash(npm*)` — arbitrary code nullifies all shell denies.  
**After:** execute allow is **git-only** (`status/diff/log/add/commit`) plus in-cwd Read/Write/Edit.  
**Enforcement:** `guard._assert_execute_allow_safe` / `profile_allows_interpreters` reject interpreter smuggling at argv build time.  
**Claim level:** shell is **narrow allowlist**, not a closed general shell. Integrator runs full test toolchains outside the delegated profile if needed.

## R3 — Permission mode and cwd-escape

**Live `--permission-mode` values:** `default | acceptEdits | auto | dontAsk | bypassPermissions | plan`.

| Choice | Reason |
|---|---|
| Execute → `dontAsk` | Headless under `capture_output` must not hang on interactive permission prompts; allow/deny still applied |
| Plan → `plan` | Matches plan-only read-only intent |
| **Never** `bypassPermissions` | Hard-rejected in `permission_mode_for_profile` / `assert_argv_safe` |

**Path denies (best-effort permission-engine globs):**

- UNC: `Write(//**)`, `Edit(//**)`, `Read(//**)`
- Windows drives: `Write([A-Za-z]:/**)`, `Edit([A-Za-z]:/**)`, `Read([A-Za-z]:/**)`
- Home secrets: `Read(~/.grok/**)`, `Write(~/.grok/**)`, auth/secret globs

**`--cwd`:** always pinned to the worktree. This sets the process working directory; it is **not** proven OS-level filesystem confinement by itself.

### Honest non-guarantees (R3)

| Vector | Status |
|---|---|
| Relative `../` traversal via `Write(**)` / `Edit(**)` | **Non-guarantee** — profile allow is cwd-glob style; path normalization is engine-dependent |
| True OS sandbox without `--sandbox` | **Non-guarantee** — not enabled in argv (profile selection not documented for this host) |
| Permission engine honoring every glob exactly | **Best-effort** — patterns match documented allow/deny CLI surface |

## R4 — Tests vs theater

Adversarial tests drive **shipped** `build_grok_argv`, `build_permission_profile`, `prepare_worktree`, `assert_argv_safe`, `resolve_server_grok_bin`, runner reject helpers — not mere `"rm -rf" in deny` or `"//**" in blob` as sole proof.

Examples:

- Headless missing → `ARGV_NOT_HEADLESS`
- Interpreter allow smuggled → `ALLOW_INTERPRETER_FORBIDDEN`
- Push/merge → deny rules present **on argv** + `GIT_VERB_FORBIDDEN` on runner
- Client `grok_bin` → `GROK_BIN_CLIENT_FORBIDDEN`

## R5 — `grok_bin` and root trust

| Input | Behavior | Anchor |
|---|---|---|
| Client `grok_bin` | Always rejected | `server.resolve_server_grok_bin` / `guard.validate_grok_bin(..., from_client=True)` |
| Env `GROK_DELEGATE_BIN` | Bare `grok`/`grok.exe` or path basename `grok`/`grok.exe` only | `validate_grok_bin` |
| Schema | `grok_bin` **absent**; `additionalProperties: false` | `server._INPUT_SCHEMA` |
| Client `repo_root` | Must match env pin or server-injected root; else `REPO_ROOT_UNTRUSTED` | `server.resolve_trusted_repo_root` |
| Client `lanes_parent` | Rejected if inside repo; must stay under `GROK_DELEGATE_LANES_PARENT` when set | `server.resolve_trusted_lanes_parent` |

## Bypass attempts table

| Vector | Closed? | Mechanism (`file:symbol`) | Notes |
|---|---|---|---|
| Interactive TUI hang (bare prompt) | **Closed** | `guard.build_grok_argv`, `guard.assert_argv_safe`, `guard.argv_has_headless_interface` | Requires `--single` / `-p` / `--prompt-file` / `--prompt-json` |
| `--always-approve` | **Closed** | `guard._assert_no_always_approve`, `runner._reject_always_approve` | Fail-closed |
| `bypassPermissions` | **Closed** | `guard.permission_mode_for_profile`, `guard.assert_argv_safe` | Never emitted |
| `git push` via runner assembly | **Closed** | `runner._reject_forbidden_git_args` | `GIT_VERB_FORBIDDEN` |
| `git merge` via runner assembly | **Closed** | `runner._reject_forbidden_git_args` | Same |
| Profile push/merge tool surface | **Closed (profile)** | `guard.build_permission_profile`, deny `Bash(git push*)` / `Bash(git merge*)`, disallowed `git_push`/`git_merge` | Engine-enforced best-effort |
| Interpreter allow nullifying denies | **Closed** | Removed from allow; `guard._assert_execute_allow_safe` | R2 |
| Destructive `rm -rf` shell pattern | **Profile deny** | `guard._DENY_DESTRUCTIVE` on deny list / argv `--deny` | Not OS-enforced; no broad shell allow |
| Worktree inside main repo | **Closed** | `runner.prepare_worktree` + `runner.is_path_inside` | `WORKTREE_INSIDE_REPO` |
| Reserved lanes `dev`/`master`/`main` | **Closed** | `guard.normalize_lane` | `LANE_RESERVED` |
| Dirty / unreachable base | **Closed** | `runner.prepare_worktree` | `BASE_DIRTY` / `BASE_UNREACHABLE` |
| Missing git / grok | **Closed** | `prepare_worktree` / `run_delegation` | Fail-closed |
| Over-cap `max_turns` | **Closed** | `guard.enforce_bounds` | `MAX_TURNS_CAP` |
| Auto-merge / push to remote | **Closed** | No code paths; `__all__` export list | Integrator-only merge |
| Audit secret / `~/.grok` / raw goal | **Closed** | `audit.sanitize_event`, `audit.emit`, `goal_fingerprint` | B6 |
| Client arbitrary `grok_bin` | **Closed** | `server.resolve_server_grok_bin` | R5 |
| Blind client `repo_root` | **Closed** | `server.resolve_trusted_repo_root` | R5 |
| `lanes_parent` inside repo | **Closed** | `server.resolve_trusted_lanes_parent` | R5 |
| Windows absolute `C:\…` write via profile | **Best-effort closed** | `Write([A-Za-z]:/**)` / `Edit([A-Za-z]:/**)` + `--permission-mode dontAsk` | Depends on engine glob semantics |
| Relative `../` cwd escape | **Non-guarantee** | — | Documented; not claimed closed |
| Full OS sandbox | **Non-guarantee** | — | `--sandbox` not asserted |

## Invariants retained (B1–B6, B9)

- Worktree isolation + reserved lane reject  
- No `--always-approve`  
- Fail-closed without grok/git; dirty/unreachable base  
- No auto-merge/push  
- Bounded max_turns + wall timeout + output cap  
- Audit without secrets / full diffs / raw goal  
- No secrets in code/argv; never reads `~/.grok/auth.json`

## Integrator files untouched

Not modified by this work: project root `README.md`, `.mcp.json`, `.claude/settings.json`.  
`grok_delegate` is **not** wired into `.mcp.json` (integrator step after acceptance).

## Verification commands

```text
py -3 -m pytest tests -q
py -3 -m py_compile grok_delegate\guard.py grok_delegate\runner.py grok_delegate\audit.py grok_delegate\server.py grok_delegate\__init__.py grok_delegate\__main__.py
git log --oneline
```
