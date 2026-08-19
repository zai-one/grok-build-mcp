# grok-delegate (dev-only local MCP)

Local **stdio MCP** wrapper that delegates a coding goal to the host's `grok` /
`grok.exe` **headless** executor (`--single` / `-p`) inside an **isolated git
worktree** on a `grok/*` branch, then returns **branch + diffstat** for the
integrator's normal review → merge flow.

This is a **developer tooling** channel. It is **not** a product surface and
**not** a tenant admin MCP bridge.

## Policy note (non-negotiable)

| This package | Product admin-bridge |
|---|---|
| Claude → local `grok.exe` (dev machine) | Grok → tenant-scoped product HTTP API |
| Worktree isolation on `grok/*` | API key principal, dual-gate writes |
| No push / no merge / no `--always-approve` | No unrestricted platform admin |
| **Does not** import product `src/**` | Product control plane |

Do **not** wire this server into production cabinet, tenant HTTP, or an admin
bridge process until acceptance and an explicit integrator step.

## How to verify MCP is alive (no Claude restart)

```bash
# Operator self-check: binary, version, auth presence (no auth.json read),
# in-process initialize → tools/list → each read-only status tool.
# Prints PASS/FAIL table; exit non-zero on failure. No real delegate.
py -3 -m grok_delegate --self-test

# Live bounded plan-only headless smoke in a temp git repo (not mocked).
py -3 -m grok_delegate --smoke-delegate
```

Unit tests (mocked git/subprocess only):

```bash
py -3 -m pytest tests -q
```

## Tools

| MCP tool | Purpose |
|---|---|
| `grok_delegate` | Prepare external worktree on `grok/<slug>` from `base_ref` (default `origin/dev`), run headless grok with a guarded permission profile + sandbox, return `{lane, branch, worktree_path, turns_used, status, summary, changed_files, diffstat}`. |
| `grok_delegate_plan` | Same entry with `plan_only=true` — **read-only** profile (deny write/edit/shell mutation). |
| `grok_delegate_status` | Structured health JSON: binary/version, auth presence (no `auth.json`), git, allowed roots/lanes, permission + sandbox info. |
| `grok_delegate_doctor` | `grok doctor --json` only (**never** `doctor fix`). |
| `grok_delegate_models` | `grok models` (read-only). |
| `grok_delegate_inspect` | `grok inspect --json` for an **allowlisted** project root. |

### Input (`grok_delegate`)

```json
{
  "goal": "Implement X with tests…",
  "lane": "my-slice",
  "base_ref": "origin/dev",
  "max_turns": 30,
  "model": "optional-model-id",
  "plan_only": false,
  "repo_root": "C:/absolute/allowlisted/repo",
  "sandbox": "workspace",
  "reasoning_effort": "high",
  "rules": "optional extra rules",
  "json_schema": {"type": "object"},
  "no_subagents": false,
  "disable_web_search": false,
  "resume": false,
  "continue_session": false,
  "fork_session": false,
  "session_id": null
}
```

`grok_bin` is **not** a client argument (set `GROK_DELEGATE_BIN` if needed).
Client `repo_root` is accepted **only** if it `resolve()`s to an entry on the
server allowlist (`GROK_DELEGATE_ALLOWED_ROOTS` or single `GROK_DELEGATE_REPO_ROOT`).

### Output (success)

```json
{
  "ok": true,
  "lane": "grok/my-slice",
  "branch": "grok/my-slice",
  "worktree_path": "…/pcp-lanes/my-slice",
  "turns_used": 12,
  "status": "ok",
  "summary": "…",
  "changed_files": ["path/a.ts"],
  "diffstat": "…"
}
```

Fail-closed errors return `{ "ok": false, "error": "<CODE>", "message": "…" }`.

## Hard bounds

1. **Worktree isolation** — target must resolve **outside** the main repo tree.
   Reserved lanes `dev` / `master` / `main` are rejected. Own `prepare_worktree`
   (not CLI `-w/--worktree`).
2. **Never `--always-approve`** — only guarded `--allow` / `--deny` /
   `--disallowed-tools` / `--tools` plus `--permission-mode` (`dontAsk` or `plan`;
   never `bypassPermissions`).
3. **Headless only** — argv uses `--single` (documented headless interface), not
   a bare interactive positional prompt.
4. **Deny push & merge** — profile + runner refuse `git push` / `git merge`
   assembly. No auto-merge, no push.
5. **Shell is git-only** — no `python*` / `pytest*` / `npm*` allows.
6. **Sandbox** — default `--sandbox workspace` (execute) / `read-only` (plan).
   Known profiles only: `off|workspace|devbox|read-only|strict`. OS enforcement
   is platform-dependent (see `Service/Archive/EVIDENCE-ROUND4.md`).
7. **Server path normalization** — `confine_path_to_root` rejects `..` escape for
   server-controlled paths before spawn.
8. **Bounded** — server-side `max_turns` hard cap + wall-clock timeout + output
   size cap; free-text params length-capped.
9. **Audit without secrets** — stderr JSON with principal/tool/lane/base_ref/cwd/
   turns/outcome; goal is length+hash only; process never reads `~/.grok/auth.json`.
10. **No destructive CLI tools** — status path allowlists `version|doctor|models|inspect`
    only; rejects `fix`, `logout`, `update`, `plugin`, `mcp`, `sessions`, etc.

## Guarded permission profile

Built by `guard.build_permission_profile(plan_only)`:

**Execute (`plan_only=false`)**

- Allow: in-cwd Read/Write/Edit; git shell only (`status/diff/log/add/commit`).
- Tools allowlist (`--tools`): Read,Write,Edit,Bash,Glob,Grep.
- Deny: `git push*`, `git merge*`, UNC + Windows absolute write/edit/read globs,
  `~/.grok/**`, secret/auth paths, live/device/prod/root patterns, `rm -rf*`.
- Disallowed tools: `git_push`, `git_merge`, `mcp`.
- Permission mode: `dontAsk`. Sandbox: `workspace`.

**Plan (`plan_only=true`)**

- Allow: Read/Glob/Grep only. Tools: Read,Glob,Grep.
- Deny: all Write/Edit/Bash plus the base deny list.
- Permission mode: `plan`. Sandbox: `read-only`.

## Layout

```text
grok_delegate/
  guard.py      # pure policy / argv / bounds / path confine (no I/O)
  runner.py     # injectable git + subprocess; prepare / run / diffstat
  status.py     # read-only status probes (version/doctor/models/inspect)
  audit.py      # redaction-safe stderr JSON audit
  server.py     # thin stdio JSON-RPC MCP adapter + multi-root allowlist
  __main__.py   # --self-test / --smoke-delegate / default stdio
  README.md
tests/
  test_grok_delegate.py
Service/Archive/     # round evidence and goal specs (history)
```

## Env

| Env | Meaning |
|---|---|
| `GROK_DELEGATE_ALLOWED_ROOTS` | Allowlist of absolute repo roots (`;` separated or JSON array). **Required** for multi-project (or use single pin below). |
| `GROK_DELEGATE_REPO_ROOT` | Single-root pin (becomes one-entry allowlist if ALLOWED_ROOTS unset) |
| `GROK_DELEGATE_BIN` | Path or name of `grok` / `grok.exe` only |
| `GROK_DELEGATE_LANES_PARENT` | Optional pin for worktree parent directory |
| `GROK_DELEGATE_SANDBOX` / `GROK_SANDBOX` | Override default sandbox (`off` disables) |

Empty allowlist → fail-closed with setup error (`ALLOWED_ROOTS_EMPTY`).

## Integrator flow (after a successful tool call)

1. Review `branch` + `diffstat` + `changed_files` from the tool result.
2. Open the worktree path; run focused tests / preflight.
3. Merge only after review — **this MCP never merges or pushes**.
