# GOAL ROUND 7 — unattended autonomy for the delegate channel

**Target repo:** this one (`Projects/MCP/Grok CLI`, local-only, no remote).
**Division of labour:** Claude sets goals, keeps the executor loaded, verifies, makes final
fixes, commits. **Grok does the implementation work.** Grok never pushes or merges — that
boundary is the safety model, not a limitation to route around.

## Why round 7 exists

Round 6 repaired the channel: the permission profile no longer blocks the executor's own
job, background `start`/`poll` survives the client timeout, and lane results report
committed work. Measured after R6: a lane that had produced three empty worktrees ran 9
turns and delivered a passing test.

What is still missing is **autonomy**. Every lane currently needs a human/Claude turn to
dispatch, to notice an empty result, to retry, and to run gates. Round 7 closes exactly
that gap, in six slices.

## Executor contract (applies to every slice — read before starting)

1. **One slice per lane.** A multi-target goal makes the executor acknowledge and end its
   turn without writing (measured 3× on an unsplit goal). Do only your slice.
2. **Write-first.** Create the target file with one real assertion/implementation before
   broad exploration, then iterate. Commit early, commit often (`git add` + `git commit`).
3. **Relative paths only** for Read/Write/Edit/Grep/Glob. Absolute paths are denied by the
   permission profile; if a call is denied, retry it relatively instead of stopping.
4. **Verify every anchor you cite** (`grep` it) before relying on it. Fabricated anchors
   are the top failure mode — a cited file that does not exist has burned whole sessions.
5. **You cannot run interpreters** (`pytest`, `python`, `npx`) — the sandbox denies them and
   that is deliberate: an interpreter in the allow list nullifies every shell deny. Write
   the code and its tests; **Claude runs `py -3 -m pytest tests -q` and fixes what fails.**
6. **Never weaken a safety invariant to make something pass.** The invariants in §R7-F are
   the acceptance floor for every slice.
7. Python 3.11+, stdlib only (no new dependencies). Type hints, `from __future__ import
   annotations`, docstrings that say *why*. Match the existing style of
   `grok_delegate/*.py`.
8. **Deliver NEW modules, not edits to large existing files.** Measured on this repo: a lane
   asked to add functions to `guard.py` (~900 lines) and wire them into `runner.py`
   (~930 lines) came back with nothing written, twice, while lanes that created a new module
   (`gates.py`, 418 lines) plus a new test file (538 lines) succeeded. Editing a large file
   requires reading it first, and that is where the lane gives up. So: put new logic in its
   own module with a narrow public API; **Claude does the few-line wiring into the existing
   large files** as part of final integration.
9. **New tests go in a NEW file per slice** (`tests/test_gates.py`, `tests/test_anchors.py`,
   `tests/test_verdict.py`, `tests/test_jobs_durable.py`, `tests/test_driver.py`). Do not
   append to `tests/test_grok_delegate.py` — it is ~1900 lines, and appending to it means
   reading it first, which measurably makes the lane give up with nothing written. Import
   what you need (`from grok_delegate import gates`) and keep each new file focused.

## Verified anchors (all exist as of 2026-07-25)

| Anchor | Line |
|---|---|
| `grok_delegate/guard.py:build_permission_profile` | 346 |
| `grok_delegate/guard.py:build_grok_argv` | 532 |
| `grok_delegate/guard.py:_FORBIDDEN_EXECUTE_ALLOW_PREFIXES` | 246 |
| `grok_delegate/runner.py:prepare_worktree` | 277 |
| `grok_delegate/runner.py:collect_diff` | 415 |
| `grok_delegate/runner.py:run_delegation` | 547 |
| `grok_delegate/runner.py:delegate` | 687 |
| `grok_delegate/jobs.py:start_job` | 55 |
| `grok_delegate/server.py:handle_tool_call` | 554 |
| `grok_delegate/audit.py:goal_fingerprint` | 69 |
| `tests/test_grok_delegate.py` | — |

---

## R7-A — `gates.py`: fixed-command gate runner (the keystone)

**Problem.** The executor cannot verify its own work: interpreters are denied, so no lane
can run `pytest`/`tsc`/`vitest` or produce a receipt. Every lane therefore costs an
integrator turn just to learn whether it compiles.

**Design constraint that must not be violated:** the MODEL must never choose what runs.
Commands are **hardcoded constants** in trusted Python; the tool input selects only a
*named profile* plus optional *paths*, and paths are validated to resolve inside the
worktree. The model's own shell allow list stays git-only.

Deliver `grok_delegate/gates.py`:

- `GATE_PROFILES: dict[str, tuple[tuple[str, ...], ...]]` — named, hardcoded command
  tuples. At minimum: `"python"` → `(("py","-3","-m","pytest","tests","-q"),)`;
  `"node"` → `(("npx","tsc","--noEmit"), ("npx","vitest","run","--reporter=basic"),
  ("npx","eslint"))`. Never build a command from caller text.
- `run_gates(worktree, profile, *, paths=None, timeout_seconds=..., subprocess_runner=None)
  -> dict` returning per-command `{command, returncode, ok, duration_s, output_tail}` plus
  an aggregate `ok`, and `summary` counts. Output is **truncated** (tail, bounded chars).
- Path handling: `paths` are appended only to commands that accept them (declare that in
  the profile table); each path must be **relative** and must resolve inside the worktree
  (reuse `confine_path_to_root` from `guard.py`) — otherwise `GATE_PATH_ESCAPE`.
- Redaction: pass gate output through the same discipline as `audit.py` — never emit
  anything matching secret patterns; truncate long lines.

**Scenarios to cover with tests (all of them):** unknown profile → `GATE_PROFILE_UNKNOWN`;
caller-supplied command text is impossible by construction (assert the API has no
command parameter); absolute path in `paths` → `GATE_PATH_ESCAPE`; `..` escape → same;
missing `node_modules` → clear `GATE_ENV_MISSING` rather than a raw stack trace; command
binary absent (`FileNotFoundError`) → `ok:false` with a diagnosable reason, never a crash;
non-zero exit → aggregate `ok:false`; timeout → `ok:false`, marked `timed_out`, and the
remaining commands still reported; huge output → truncated with a marker; multi-command
profile where the first fails → later commands still run (report all, do not short-circuit)
and aggregate is false; empty `paths` list behaves like `None`; Windows quoting of paths with
spaces; concurrent `run_gates` on two worktrees do not interfere; secrets planted in gate
output are redacted.

**Done:** `grok_delegate/gates.py` + tests in `tests/test_grok_delegate.py` (new
`GateRunnerTests` class, mocked subprocess only — never spawn a real gate in unit tests).

---

## R7-B — anchor pre-validation (cheapest win)

**Problem.** A goal citing `src/services/does-not-exist.ts` silently kills the session:
the executor searches, fails, ends its turn. Measured: three dead dispatches from one
fabricated anchor.

Deliver in `grok_delegate/guard.py` (pure, testable):

- `extract_goal_anchors(goal: str) -> list[str]` — pull path-like tokens: `a/b/c.ext`,
  optional `:123` line suffix, backticked paths, and `path/**` globs. Bound the count
  (e.g. 64) and the token length.
- `validate_goal_anchors(goal, worktree) -> dict` — returns
  `{ok, missing: [...], checked: [...]}`. Missing anchors are **not** an automatic failure
  decision — the runner decides (see below), because prose can look like a path.

Wire into `runner.delegate`: after `prepare_worktree`, before spawn — when **every**
extracted anchor is missing, fail fast with `ANCHOR_MISSING` and do not spawn (a goal whose
anchors are all fictional cannot succeed). When *some* are missing, spawn anyway but return
the missing list in the result so the driver and Claude can see it.

**Scenarios:** plain relative path present; absent; `file.ts:123` suffix; backticked
path; path inside a markdown link `[x](Service/Goals/Y.md)`; a URL
(`https://example.com/a/b.ts`) must NOT be treated as an anchor; prose containing a dot
(`e.g. version 1.2`) must not; Windows backslash path; absolute path → reported, never
treated as in-worktree; glob `src/**/*.ts`; a path that exists only in another branch;
case-difference on Windows; more than the bound → truncated deterministically; empty goal;
goal with no paths at all → `ok:true, checked: []` and spawn proceeds; **all** anchors
missing → `ANCHOR_MISSING`, `sp.calls == []` (never spawned); mixed → spawns, result
carries `missing_anchors`.

---

## R7-C — structured lane verdict (make "done vs gave up" machine-readable)

**Problem.** `run_delegation` already plumbs `json_schema` into the CLI (`runner.py:547`,
`:687`) but **nothing uses it**, so the outcome is free-form prose. A driver cannot decide
whether a lane finished, and a lane can claim success it did not achieve.

Deliver:

- `LANE_VERDICT_SCHEMA` in `grok_delegate/gates.py` or a new `verdict.py`:
  `{files_written: [str], committed: bool, tests_added: int, gates_run: bool,
  self_skeptic_findings: [str], blocked_reason: str|null, summary: str}`.
- `parse_lane_verdict(stdout_json) -> dict` — tolerant: invalid/absent JSON → `{ok:false,
  reason:"VERDICT_MISSING"}`, never a raise.
- **Cross-check (the important part):** `reconcile_verdict(verdict, diff)` — compare the
  claim against git reality from `collect_diff`. A verdict claiming `files_written` while
  `changed_files` and `commits` are both empty is `VERDICT_UNSUPPORTED`; claiming
  `committed: true` with no commits is the same. The driver must trust git, not prose.
- `delegate()` passes the schema by default (allow opt-out) and returns
  `verdict` + `verdict_status`.

**Scenarios:** valid verdict matching git → `ok`; valid verdict but empty git →
`VERDICT_UNSUPPORTED`; `committed:true` with zero commits → unsupported; malformed JSON →
`VERDICT_MISSING` (no raise); JSON with extra fields → tolerated; missing required field →
`VERDICT_INVALID` listing the field; non-dict JSON (array/string) → `VERDICT_MISSING`;
verdict with `blocked_reason` set → surfaced as `blocked` even when files changed;
enormous verdict → bounded; non-ASCII in verdict preserved (UTF-8, no cp1252 crash);
schema opt-out path still works.

---

## R7-D — durable jobs (survive a server restart)

**Problem.** `jobs.py:start_job` keeps state in memory only: restart the server and every
in-flight/finished job status is lost, even though the work itself survives on the lane
branch.

Deliver: persist each job record as JSON under a jobs dir (default beside `lanes_parent`,
overridable), written atomically on every state change; `load_jobs(dir)` rehydrates the
registry at import/first use; a **stale-running detector** (a record still `running` whose
owning process is gone — record the pid and check liveness) reports `state:"unknown"`
rather than lying that it is running.

**Scenarios:** write on start/done/error; rehydrate after simulated restart; corrupt job
file (truncated JSON) → skipped with a warning, other jobs still load; unreadable file
(permission) → skipped, no crash; atomic write (temp + replace, no half-written file
observed); eviction keeps the newest `MAX_JOBS` on disk too; stale `running` with a dead
pid → `unknown`; two writers on the same job id → last-writer-wins without corruption;
jobs dir missing → created; jobs dir unwritable → degrade to memory-only with a warning,
never crash the server; non-ASCII lane names round-trip.

---

## R7-E — `driver.py`: the unattended loop

**Problem.** Nothing in the project advances work by itself. One lane at a time must be
picked, dispatched, watched, retried on an empty result, gated, and recorded.

Deliver `grok_delegate/driver.py`, runnable as `python -m grok_delegate.driver`:

- Reads a **queue file** (JSON, path from CLI/env) with lane records
  `{id, lane, goal_file, base_ref, kind, status, attempts, ...}`.
- Loop: pick the first `queued` lane → dispatch via `runner.delegate` → on **empty
  result** (no `changed_files`, no `commits`) retry with an escalating write-first nudge,
  up to `max_attempts` (default 2) → then mark `blocked` with the reason. On non-empty:
  run `gates.run_gates` for the lane's profile, attach the verdict + gate report, mark
  `ready_for_merge` (never merged by the driver).
- **Serialize strictly:** exactly one lane in flight (parallel headless sessions get
  cancelled by the backend — measured). Add a configurable cooldown between lanes.
- **File locking** on the queue (a lock file with pid + atomic create) so two drivers
  cannot both own the cursor; second driver exits with `QUEUE_LOCKED`.
- Graceful shutdown: on SIGINT/SIGTERM, finish bookkeeping for the current lane, mark it
  back to `queued` (or `interrupted`), release the lock, exit non-zero.
- Emit an audit record per state change (`audit.py`), and write a per-lane verdict file.

**Scenarios:** empty queue → clean exit 0 with "nothing to do"; all lanes done → same;
first lane blocked → driver continues to the next, not stops; executor returns empty →
retried with a stronger prompt; retries exhausted → `blocked` with `attempts` recorded;
gates fail → lane marked `gates_failed` (not `ready_for_merge`), driver continues; lane
whose `goal_file` is missing → `blocked:GOAL_FILE_MISSING`, never spawn; corrupt queue
JSON → exit non-zero without touching lanes; queue with unknown fields → tolerated; queue
locked by a live pid → `QUEUE_LOCKED` exit; lock held by a **dead** pid → taken over;
worktree already exists on the right branch → reused; exists on the wrong branch →
`blocked`, no destructive fix; `base_ref` unreachable → `blocked`; SIGINT mid-lane →
lane back to `queued`, lock released; `max_lanes` reached → stop with a summary;
`--dry-run` prints the plan and spawns nothing; two consecutive empty results on
*different* lanes must not be conflated; a lane already `ready_for_merge` is skipped.

---

## R7-F — safety invariants (acceptance floor for the whole round)

These must hold after every slice; add explicit tests where missing:

1. No code path ever assembles `git push`, `git merge`, or `--always-approve`.
2. The model's execute allow list never gains an interpreter (`_FORBIDDEN_EXECUTE_ALLOW_PREFIXES`
   at `guard.py:246` still rejects them) — the gate runner is trusted Python, not a model
   capability, and `run_gates` takes **no** command parameter.
3. The driver never merges, never pushes, never rewrites history; the furthest state it can
   set is `ready_for_merge`.
4. Secrets never reach a verdict, gate report, job file, or audit record; goal text is still
   recorded only as length + short hash (`audit.py:goal_fingerprint`).
5. Absolute Write/Edit denies and the UNC read deny stay in the profile; worktrees stay
   outside the main repo tree.
6. Every new subprocess call decodes as UTF-8 (a cp1252 decode crash already cost a whole
   result once).
7. `py -3 -m pytest tests -q` stays green and the suite only grows.

---

## R7-G — chaos & failure-injection sweep (test-only slice)

Everything above is happy-path-plus-known-edges. This slice attacks the pipeline the way
reality does. **Test-only**: no production edits; a failure here is a ranked finding in
`EVIDENCE-ROUND7.md`, and the fix goes to a follow-up slice.

New file `tests/test_chaos.py`. Inject faults through the existing injection points
(`git_runner`, `subprocess_runner`, `thread_starter`, fake `delegate`/`run_gates`):

**Executor misbehaviour** (measured classes, all must be survivable):
- returns exit 0 with an empty worktree (the dominant real failure) → driver retries, then
  `blocked`; never reported as success;
- returns exit 0 having written files but **no commit** → work is still reported
  (`changed_files` non-empty) and the lane is not lost;
- writes a file **outside** the worktree path in its claim → reconciliation ignores it;
- claims a commit that does not exist → `VERDICT_UNSUPPORTED`;
- emits gigabytes of stdout → bounded, no memory blow-up;
- emits invalid UTF-8 bytes → decoded with replacement, never a crash (this class already
  cost one whole result);
- hangs → wall-clock timeout fires, `status:"timeout"`, worktree preserved for inspection;
- dies from a signal (negative return code) → `ok:false` with a diagnosable status;
- spawns nothing because the binary vanished mid-run → `GROK_MISSING`, no partial state.

**Git-layer faults:**
- `worktree add` fails (disk full / permission) → `WORKTREE_CREATE_FAILED`, lane `blocked`;
- worktree directory deleted underneath a running lane → detected, not a crash;
- `base_ref` deleted/moved mid-run → diff falls back gracefully, no traceback;
- git binary missing → `GIT_MISSING` before any spawn;
- branch exists but is checked out elsewhere → `WORKTREE_EXISTS_CONFLICT`, no destructive fix;
- detached HEAD in the lane → commit collection still works or degrades cleanly.

**Filesystem / environment faults:**
- jobs dir read-only → memory-only degradation with a warning;
- queue file replaced with a directory → clean error;
- lock file left by a dead pid → taken over; by a live pid → `QUEUE_LOCKED`;
- clock jumps backwards (timestamps must never make a record "negative duration");
- extremely long lane names / non-ASCII lane names → sanitized, round-trip safe;
- two drivers, two queues, one lanes_parent → no cross-talk.

**Adversarial input** (a goal file is untrusted text):
- goal containing shell metacharacters, backticks, `$(...)`, newlines → never becomes argv
  or a shell payload (assert argv shape);
- goal attempting `--always-approve` / `--allow Bash(*)` injection through free text → still
  rejected by `assert_argv_safe` / `_assert_execute_allow_safe`;
- goal citing `~/.grok/auth.json` or a `*secret*` path → anchor extraction may see it, but
  the profile deny keeps it unreadable, and it must never be echoed into audit/verdict;
- goal 1 MB long → bounded before spawn;
- queue lane with `lane: "master"` / `"dev"` / `"../escape"` → rejected by `normalize_lane`.

## R7-H — observability & operator report

A loop that runs unattended must be readable afterwards without re-deriving what happened.

Deliver `grok_delegate/report.py` + `tests/test_report.py`:
- `build_round_report(queue, jobs, gate_reports) -> dict` and a Markdown renderer: per lane
  the status, attempts, turns, changed-file count, commits, gate verdict, blocked reason,
  and the wall-clock; plus totals and the list of lanes awaiting merge.
- Redaction first: no goal text (length + hash only, per `audit.py:goal_fingerprint`), no
  gate output beyond a bounded tail, no absolute host paths (relativize to the repo root).
- Scenarios: empty round; all-blocked round; mixed; a lane with 3 attempts; missing gate
  report; non-ASCII lane names; enormous gate output; a job in state `unknown`; report is
  deterministic (same input → byte-identical output) so it can be diffed between rounds.

## R7-I — end-to-end acceptance (the round is done when this passes)

New file `tests/test_e2e_autonomy.py`: drive the **whole** pipeline with fakes only —
queue file → driver → delegate (fake executor with scripted behaviours) → verdict →
reconciliation → gates (fake) → report — and assert:
1. a good lane ends `ready_for_merge` with a verdict git can confirm and a gate pass;
2. an empty-result lane is retried then `blocked`, and nothing is ever marked merged;
3. a gate-failing lane ends `gates_failed`, and the driver continues to the next lane;
4. a lane whose verdict lies ends `blocked:VERDICT_UNSUPPORTED`;
5. the round report lists exactly the lanes above with their real outcomes;
6. across the entire run: no `git push`, no `git merge`, no `--always-approve` in any argv
   the fakes recorded (assert over every captured call), and no secret string planted in
   goal/gate output appears anywhere in report/audit/job files.

## Dispatch order

`R7-A` (gate runner) → `R7-B` (anchors) → `R7-C` (verdict) → `R7-D` (durable jobs) →
`R7-E` (driver, depends on A/C/D) → `R7-G` (chaos sweep) → `R7-H` (report) →
`R7-F` (invariant audit) → `R7-I` (end-to-end acceptance, closes the round).

Wiring of each new module into `runner.py` / `server.py` / `jobs.py` is Claude's final-fix
step, done at merge time — see contract rule 8.

Claude dispatches one slice at a time through the repaired channel, runs
`py -3 -m pytest tests -q` on the lane worktree, fixes what the executor missed, commits,
and advances the cursor.
