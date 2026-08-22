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

## 0.26.1 — What the local run could not see

### A deep frame still killed the reader on 3.10 and 3.13

The guard added in 0.26.0 stops descending at forty levels and then measures the
rest with `json.dumps`, falling back to `str()`. Both recurse. So the protection
against a hostile frame ended in the same `RecursionError` one call further
down -- on every supported Python except the one this was written on. CPython
3.14 tolerates a five-thousand-level frame; 3.10 and 3.13 do not, and
`sys.setrecursionlimit` does not govern those C encoders either way.

The measurement walks an explicit stack now, bounded by node count rather than
by depth, so the shape of a frame no longer decides whether the reader survives
it. `_flatten_text`, which walks the same untrusted values for the search gate,
was iterative-ised for the same reason.

**This was found by CI, not by the local suite, and it had been red for two
releases.** 0.25.0 and 0.26.0 both shipped with a failing matrix. Two tests were
also Windows-only without saying so -- they shell out to `mklink /J`, which does
not exist on Linux -- and took the Ubuntu rows down with them. AGENTS.md now
says what finishing means: run the suite on the oldest supported interpreter,
and read `gh run list` instead of assuming.

---

## 0.26.0 — Eight readers, one bridge

Eight Grok audits read this bridge, one dimension each, with every `read:
file:line` citation re-checked against the tree. Four dimensions are new:
`audit.protocol` (the MCP surface), `audit.lifecycle` (a job from dispatch to
receipt), `audit.docs` (does the documentation still describe this code) and
`audit.tests` (would the tests notice if the code stopped working). Everything
below was reproduced by hand before it was changed; the claims that did not
reproduce were dropped, and two of them were exactly that.

### One poll can no longer be eight times its budget

The budget is the bridge's central promise -- a single poll stays small however
long the job ran -- and it did not hold. `_TRIM_ORDER` named six fields, and a
job's own shape could put the weight somewhere else entirely:

    64 declared test commands -> 138174 bytes against a 16384-byte budget
    4000 artifacts            -> 130984 bytes

with `economy_trimmed` reporting **nothing** in both cases, so the host could
not even tell. Verifier previews are shortened before rows are dropped, test and
artifact lists are trimmable, and a record that still does not fit has its
heaviest field removed until it does -- naming each one in `economy_trimmed` and
`economy_dropped`. The verdict is never what gets dropped.

### A malformed frame header no longer ends the session

One frame of `Content-Length: -5` made the stdio server exit with code 0:
`read(-5)` drains the pipe to EOF, the body no longer matches the declared
length, and the loop returns. From the host's side the bridge simply vanished
mid-session. A non-positive or absurd length is now answered with `-32700` and
the loop keeps listening. Both framings, because only one of them had a test.

### Two ways a receipt could say `completed` while proving nothing

An `execute` receipt with no commit was accepted if it wrote
`NOT_A_WRITE_ROLE` into `lane_commit.reason` -- an exemption meant for a role
that never reaches this branch. And a verifier row that says
`outcome: not_run` was counted as the run that proves the work, as long as it
also carried `passed: true`. Both are refused now.

### The search gate judges a pattern the way it judges a path

Measured against the live gate: `glob **/.env*` was refused, and the same glob
with `path: "."` alongside it was **allowed** -- naming any path key skipped
every pattern check. `**/*.pem`, `**/*.key`, `id_rsa/**` and
`.env.local/settings.json` were all allowed, because the gate read only the last
component and knew nothing about suffixes. A pattern inside a list
(`['../outside/**']`) was allowed while the same string alone was refused.

All four go through `looks_like_secret_path` now -- the same predicate the path
check and the mount validator use -- and list values are flattened rather than
stringified. In the other direction, the declared command
`py -3 -m pytest tests/api_key.py -q` was refused: a Python module named after
the thing it tests is not the thing.

### A short password is still a password

`password=hunter2` and `SECRET_KEY=secret` reached receipts intact: the bare
value rule requires eight characters, for a documented reason -- at a lower
floor it ate `token: str = ""` and the call in `password_hash = bcrypt(x)`. The
floor stays, and a second rule covers the words that are never anything else,
with those two shapes excluded.

### A reviewed lane counts as busy

`review_lane` shipped in 0.25.0 with a hole: the lane lock keys the lane a job
owns, and a read-only job standing in someone else's worktree owns none. With
`GROK_DELEGATE_CONCURRENCY=2` that meant two workers in one checkout, which is
the single thing that lock exists to prevent. A cancel or a failed mount also
returned without taking the mounted inputs back out or releasing an empty lane;
every exit now leaves the lane the way a normal finish does.

### The published schema means something at the boundary

`job_id: {"a": 1}` was stringified and looked up as `"{'a': 1}"`, and
`lane: {"a": 1}` started a job on a lane named after a dict's repr. An object or
an array where the schema declares a scalar is refused with
`ARGUMENTS_INVALID`. Scalar coercion is left alone: a numeric string is harmless
because bounds are enforced after it, and several fields have refusals that say
more than a type name would.

`cwd` was honoured by `grok_delegate` and `grok_delegate_start` and declared in
neither schema, so a client reading `tools/list` could not find it. It is
declared now.

### Documentation that no longer described this code

`audit.docs` found eight checkable claims and each is corrected: the README said
an unreadable `GROK_DELEGATE_REASONING_EFFORT` reads as "no preference" (it is
refused) and that a job which changed nothing comes back `blocked` (it comes
back `no_changes`); `docs/SECURITY.md` told operators to keep lanes outside the
repository, which is the opposite of the default; `docs/EASY.md` gave Windows
readers a POSIX venv path; the task-packet schema required `role` that the
role-specific tools set themselves; the receipt schema documented
`tests[].passed` as null-when-unknown and never mentioned `outcome` or
`verifier_touched_files`; and `lane_retained_reason` had two documented values
and five real ones.

### Every environment variable, written down

Twenty-one of the thirty-four `GROK_DELEGATE_*` variables the package reads were
documented nowhere -- including the two git timeouts an operator needs on
exactly the machine where jobs are timing out. `docs/ENVIRONMENT.md` lists all of
them with defaults, and a test fails if the code and the reference drift apart in
either direction.

---

## 0.25.0 — The answer arrives, the lane does not linger

### A long answer no longer counts as a runaway

`ACP_OUTPUT_LIMIT` killed jobs that had done nothing wrong. Measured on a live
stream: the median `agent_message_chunk` frame is 456 bytes and carries 2-8
bytes of text, so a 25 KB answer cost 1.5-5.8 MB against a 1-2.4 MB cap. Five
jobs told to keep their answer short passed; three without that instruction
died. The budget now counts what the agent produced -- text, not envelope --
and a separate, much larger allowance bounds the stream itself so an agent
emitting nothing but envelopes is still stopped.

A running command is charged once, not once per update. Grok resends the whole
accumulated `rawOutput` under the same `toolCallId` every time it grows, so one
three-minute pytest run arrived as hundreds of frames each carrying the entire
buffer so far -- the same bytes counted hundreds of times. This was found by
running the skeptic over these very changes and watching it die at
`ACP_OUTPUT_LIMIT`.

An overrun is also no longer a crash. It cancels the turn the way running out
of turns does: the edits already on disk stay, the verifier still judges them,
and the receipt says `output_truncated` with `output_payload_bytes` and
`output_cap_bytes`. **Breaking:** a job stopped by the output cap now reports
`status: "cancelled"` where it previously reported `"failed"`; an orchestrator
matching on `failed` to detect it should match `blocked_reason` instead.

### A lane that produced nothing no longer survives the job

Ten lanes had accumulated in this repository, four of them from runs whose only
product was the lane. A lane whose branch is still at the pinned base and whose
tree is clean is now removed when the job ends -- after the receipt, never
before, because acceptance is read from the tree the verifier left. Anything
else is kept and says why: `lane_retained_reason` names which of them it was: `WORK_PRESENT` when the
receipt shows work, and otherwise `UNCOMMITTED_CHANGES`, `LANE_HAS_COMMITS`,
`CLEANUP_DISABLED` or `NOT_A_LINKED_WORKTREE`. Off with `GROK_DELEGATE_LANE_CLEANUP=0`.

`grok_agent_status` now lists the lanes that exist, with branch and head, so
unmerged work is visible at the start of a session instead of being found by
accident weeks later.

Removal refuses anything that is not a linked worktree: a lane keeps a `.git`
file, a main checkout keeps a `.git` directory, so a lane-shaped branch name on
the wrong path cannot delete a repository.

### `mount_paths`: the brief that lives outside git

A lane is a checkout of a git ref, which is why it is safe -- an ignored `.env`
is not in it -- and why the task brief sitting in an ignored directory is not in
it either. Jobs worked from a spec they could not read. A task may now name
`mount_paths`, and exactly those paths are copied into the lane before the
worker starts. They must be inside the project, must be ignored by git (so they
cannot reach the lane commit, and a tracked file is refused as a mistake), must
not be a credential by name, must not be a symlink, and must fit in 32 MB. The
receipt names what was mounted, so a reviewer is not left wondering where a file
came from.

A mounted directory is checked all the way down, because mounting a directory
mounts every name in it. A credential anywhere inside refuses the whole mount,
and so does a link -- including a Windows junction, which is the reachable case:
measured on this machine, `os.symlink` needs a privilege the user does not have
while `mklink /J` needs none, and `Path.is_symlink()` answers False for what it
makes while `copytree` follows it straight out of the project.

### A skeptic can review a lane

Asked to review lane work, a read-only role ran in the main checkout, where
every lane path was outside its own directory and the permission gate refused
it. A task may now carry `review_lane`, and a read-only role with one runs
inside that worktree.

It is a separate field, not the existing `lane`, and soak is why: every
read-only job already passes `lane` as a label for itself -- the soak passes
`soak-consult` and `soak-skeptic` -- so reading that as "stand in that worktree"
turned two working calls into `LANE_NOT_FOUND` on the first run. A write role
that names `review_lane` is refused outright: it gets a lane of its own, and two
workers in one worktree is the thing the lane lock exists to prevent.

The role still creates no branch and commits nothing; an unknown lane is
`LANE_NOT_FOUND`. The bridge itself may put a declared `mount_paths` input in the
lane for the duration of the job and takes it out again afterwards, which is the
only thing that touches disk.

The lane must be a linked worktree of this repository, under this project's
lanes parent. Each of those was reachable on its own: `GROK_DELEGATE_LANES_PARENT`
can name a shared directory, and then a lane called `lib` resolved into a
neighbouring checkout, while a lane named after the project resolved into the
main checkout -- the one tree with the gitignored secrets this feature exists to
stay out of. `transport=legacy` refuses a review lane outright, because it runs
in `project_root` whatever cwd says.

This is also the answer to the read gate the CLI does not offer. Grok CLI 1.0.5
does not ask permission to read, so a `.env` in the working directory is
readable whatever the bridge denies -- measured, with zero calls to the gate. A
role that works in a lane instead of the main checkout is working in a tree that
does not contain ignored files at all, and `mount_paths` is the narrow way to
carry in what it genuinely needs.

### Three more routines

`hygiene.release` (a lane that produced nothing does not survive its job, one
that did is kept and says why), `security.mount` (only ignored, non-credential
paths cross into a lane, and nothing mounted reaches the lane commit), and
`economy.budget` (a chunk is charged for its text and not its frame, and a resent buffer
is charged once). All three run in the harness driver, so they cost seconds.

### What four skeptics found before this shipped

Each section above was reviewed by an independent Grok skeptic with its own
sixty turns, and every one came back with defects. They are fixed here; the list
is what changed, not a confession:

- **A lane name meant two lanes.** `lane="grok/x"` was slugified whole, so the
  one-job-per-lane check guarded `grok/grok-x` while the review path opened
  `grok/x`. A skeptic could walk into a worktree a running execute job owned.
- **Ignored work looked like an empty lane.** `git status --porcelain` says
  nothing about ignored files, and this repository ignores `*.log` and
  `Service/Audits/routines-*.json`. A job whose only product was one of those
  read as "produced nothing" and `worktree remove --force` took it. The receipt
  now decides: a lane is released only when changed files, artifacts, worker
  writes and the lane commit are all empty.
- **A removed checkout with a surviving branch reported itself as fully
  removed.** `lane_retained_reason` now survives a successful removal, and
  `lane_branch_deleted` says which half happened.
- **Ignore status was judged in the project while the file landed in the lane.**
  A `.gitignore` newer than the ref the lane was cut from meant a mounted file
  could reach the lane commit. It is asked in the lane now.
- **Junctions were invisible on Python 3.10 and 3.11.** `Path.is_junction()`
  arrived in 3.12 and this package supports 3.10, so on the two versions in
  between `copytree` walked straight through a junction. Detection now reads the
  reparse-point attribute, which has been there since 3.5.
- **The credential predicate read only the last component**, so `id_rsa/config`
  and `.env.local/settings.json` passed, and `.envrc` was refused by the search
  gate while the mount validator allowed it.
- **A refused mount left its earlier copies behind**, and a link already sitting
  in a reused lane was written through. Mounts roll back on refusal, the
  destination is checked for links, and what actually landed is measured after
  the copy rather than before it.
- **An overrun could still report success.** An agent that answered `end_turn`
  after being cancelled produced `status: completed` next to
  `blocked_reason: ACP_OUTPUT_LIMIT`; a job deadline landing inside the
  five-second cancel grace reported `ACP_TIMEOUT` instead of the real cause; and
  a permission request arriving after the cancel was still granted.
- **The websocket had no wire backstop at all**, so an agent streaming nothing
  but envelopes ran to the job deadline there while stdio stopped it. An
  oversized single frame also reported itself as the output budget; it has its
  own code now (`ACP_FRAME_TOO_LARGE`), because it is a protocol fault and not a
  truncated answer.
- **A frame nested twenty thousand deep killed the job.** `json.loads` raises
  `RecursionError`, which `except json.JSONDecodeError` did not catch, and the
  depth guard lived after the parse. Such a frame is malformed now, and the turn
  survives it.
- **Identifier keys were free at any size**, so a hundred kilobytes under the
  key `status` cost nothing. Keys are counted, and an exempt key stops being
  exempt once its value is too large to be an identifier.
- **The error path dropped `worker_written_files`**, so a job stopped by the
  output cap reported no writes and acceptance blamed the worker for files it
  had been given permission to write.

### One definition of "this path is a credential"

`looks_like_secret_path` moved to `guard.py`, along with the Windows name
normaliser that strips trailing dots and alternate data streams. The permission
gate and the mount validator now share it. Two lists for one rule is how
`id_rsa` came to be refused to `git show` and handed over by an ordinary read.

---

## 0.24.0 — Nothing waits twice

### A busy bridge no longer starves its own subprocess spawns

`GIT_TIMEOUT` on a lane that had nothing wrong with it. Measured on the same
stand as the 0.23.0 retry: `Popen(['git','--version'])` costs 5.7 ms in an idle
process, 1280-1920 ms with sixteen bytecode-hot threads, and 6.5 ms with the
same sixteen threads asleep. It is GIL contention, and the interpreter has the
knob for it. Each spawn now runs with a shorter switch interval, restored the
moment it returns: 22.9 / 22.5 / 24.4 ms across three runs, and a whole lane
preparation under that load fell from 39.4-53.0 s to 22.4-27.5 s against a 60 s
budget. Held process-wide the same setting would cost the stream reader half its
throughput, so the window is scoped to the spawn -- and for git, which finishes
in milliseconds, to the pipe read that follows it. Off with
`GROK_DELEGATE_SPAWN_PRIORITY=0`.

Reported symptom this fixes: hourly routine ticks passing about half the time,
with preflight measured at 28-75 s against the same budget, the outcome decided
by scheduling noise rather than by the repository.

### `grok_agent_status` and `session_begin` stop paying 13 s for the same answer

The session probe is `grok models`, and that command takes 12.7 s on this
machine -- measured three times, 12.72 / 12.76 / 12.80 -- which was essentially
all of what either call cost. A present session is now remembered for ten
minutes and concurrent callers share one probe instead of starting two. Absence
is never cached: that is the state the operator is about to fix with `grok
login`, and a remembered "no" would outlive the fix. `auth.cached` and
`auth.probe_seconds` in the status answer say which of the two you got.

The probe also starts in the background as soon as the host sends
`notifications/initialized`, so the first call reads an answer that was bought
while the host was still thinking. Off with `GROK_DELEGATE_PREWARM=0`.

### A compact poll no longer drops the lane commit

Acceptance asks for a commit in `grok/*`, and `lane_commit` was the field that
said whether there was one -- but it was not in the compact record, so every
host with the economy switches on saw changed files and a green verifier run
with no way to tell whether the work had been committed.

### The bridge's own project gets the economy defaults

`.mcp.json` in this repository started the server without
`GROK_DELEGATE_ECONOMY`, so working on the bridge cost 30.8 KB of wire per poll
where every other project cost 5.4 KB. Measured on the same job.

---

## 0.23.0 — A wait you can watch

### A verifier run that never happened is no longer reported as a failure

`passed` was computed as `returncode == 0 and not timedOut and not cancelled`. A
cancel or a timeout leaves `returncode` None, so both arrived as `passed: false`
— indistinguishable on the wire from a suite that genuinely failed. A live job
did exactly that: four files changed, fifty-one tests passing in the lane when
run by hand, and a receipt saying the verifier failed. An orchestrator counting
two such failures sends finished work to `blocked`.

A verifier row now carries `outcome`: `passed`, `failed`, or `not_run`, with
`not_run_reason` naming `cancelled`, `timeout` or `invalid_command`. `passed`
stays for existing readers but only where it means something — when the run did
not happen the field is **absent**, not false. The acceptance gate reads
`outcome`, so a cancelled verifier is `TEST_EVIDENCE_MISSING` (which it is)
rather than `TEST_FAILED` (which it is not), and a receipt written before this
field judges exactly as it did.

### `denied_tool_calls` says the worker reached for something it could not have

A refused call is the only trace of a worker going somewhere its role was never
allowed, and on this CLI a refused write ends the turn — so the count is often
the only explanation for a job that stopped early with nothing to show. Both
transports now report it, and it survives a compact poll. It is read off the
option the gate actually selected, so the number cannot disagree with what was
sent back.

### One call can now wait, and say that it is waiting

A job runs for minutes on a thread inside this server, finishes, runs its
verifier, commits its lane — and nothing wakes the host. `grok_agent_execute`
hands back a `job_id` and the receipt sits in the registry until somebody thinks
to ask again. From the other side of the pipe that is indistinguishable from
nothing happening, which is a fair reason to prefer a terminal where output at
least scrolls.

`grok_agent_poll` takes `wait_seconds` (0–1800). With it the call blocks until
the job is terminal and returns the finished receipt, emitting
`notifications/progress` every five seconds with the job's phase and how long it
has been in it. Absent or zero, the tool behaves exactly as before — nothing
moved for anyone not asking.

Progress goes out through the same framing the request arrived on, because this
server speaks both `Content-Length` and line-delimited and writing the wrong one
reaches nobody. The progress token is held for one dispatch and cleared in a
`finally`, so it can never address a call the client has already been answered
on. A notifier that raises — a client that closed its end — loses the progress
and not the job.

Whether a given client extends its own timeout on receiving progress is the
client's business, and worth one experiment before relying on it.
### A GIL-starved git probe is a slow lane, not a failed dispatch

`GIT_TIMEOUT` on `git --version` read as a broken git, and the search went to
antivirus. Spawn is what costs 500× under GIL contention (`Popen(['git','--version'])`
median of 8: idle 7.1ms vs 3258.7ms with 16 bytecode threads; sleeping threads
6.5ms). Probes now retry once before failing. The structured error carries
`spawn_seconds` (Popen only — a pipe-read stall leaves that field at 0.0, so
`wait_seconds` and `spawn_covers=popen` name the split) and reports the worst
Popen across both attempts, not the last: a starved first try at 3s with a
7ms retry used to land as `spawn_seconds=0.0071` and no cause, which sent
the next reader looking at antivirus. The starvation label is applied only
when that worst spawn matches the measurement (1s floor; a hung git that
spawned in milliseconds is a timeout without a cause). A successful
`git --version` is cached for the process lifetime so every lane does not
pay a spawn for a binary that has not changed; a failed probe is not
cached, because installing git after startup, or one transient error, used
to pin `GIT_MISSING` for the rest of a long-lived server. Checkout
(`git worktree add`) is not retried, and the settle loop that watches a
timed-out checkout does not retry either — it is already a loop.
`_spawn_git` kills the child on any exception from `communicate`, not only
`TimeoutExpired`; `kill()` is guarded and the drain is bounded, so a failed
kill cannot replace `KeyboardInterrupt` or hang the server.

---

## 0.22.0 — Promises the bridge can keep

### **Breaking:** the compat tools now honour the project opt-in

`grok_delegate_start` started a live worker in a directory carrying no
`.grok-mcp.json`, while `grok_agent_execute` refused the same directory with
`PROJECT_NOT_ENABLED`. The opt-in is described as the whole security model — a
project has to say yes before the bridge runs anything in it — and one
advertised tool walked straight past it. `grok_delegate` did too; only an
unrelated `BASE_UNREACHABLE` check happened to stop it first.

An existing setup calling the compat tools against a project with no config will
now get `PROJECT_NOT_ENABLED` with the usual `fix_with` hint instead of a running
job. `grok_delegate_plan` stays exempt: a plan reads and reports, and refusing it
would leave the caller unable to see what the gate is objecting to.

Found by the `audit.wiring` routine and reproduced before it was believed.

### A receipt that never mentioned a lane commit read as finished

`lane_commit: {}` was caught and a missing key was not, so a write receipt from a
transport that never fills the field in — `transport: legacy` is exactly one —
came back `completed` with nothing on any branch to merge. The previous round
called that harmless because `TEST_EVIDENCE_MISSING` catches the same receipts;
a gate that relies on another gate catching its misses is not a gate.

The check runs last and only when nothing else objected, because "no commit" is
the least informative thing that can be wrong with a receipt and must not
displace a reason that names the actual defect.

### A compact poll shortened the file list without saying so

Eighty changed files arrived as twenty-four with no count beside them, which
reads as "that is all this job touched" — the one thing a reviewer must not be
wrong about. `changed_files_omitted` and `changed_files_total` now say what was
left out. Separately, `full_changed_files` sat in the keep list and in no lift
list, so a finished job's compact poll dropped it entirely; it is the field that
separates this run's work from everything already in the lane.

### The poll budget was measured in the wrong unit

Every write in `server.py` serialises `ensure_ascii=False` and encodes UTF-8, but
the budget counted characters of the escaped form. For a Cyrillic receipt that
reads about three times the real size — three live audits failed a promise they
were inside — while counting unescaped characters would undercount it, since
Cyrillic is two bytes each. Both the budget and the harnesses now measure bytes
on the wire, which is the thing the host actually pays for.

### The read gate is not reachable on this CLI, so it stopped being a promise

`AGENTS.md` said `.env`, `id_rsa` and `*.pem` were refused in any form. True for
commands. For reads it was never true here, and now it is measured rather than
assumed: an instrumented run recorded **zero** permission calls for a read, and
the worker read `.env` and returned its contents. Neither `--deny Read(*)` nor
`--sandbox strict` changed that — both were tried through a live job.

So against a secret sitting in the working directory the bridge gives one thing:
the outbound redactor, and only for what looks like a secret.
`XAI_API_KEY=xai-…` is cut; `deployment_region=<string>` arrives whole. The path
denylist stays in the code — another client or a later CLI may ask — but the
documentation no longer describes it as protection you have.

Worth knowing which roles are exposed: a read-only role runs in the operator's
own checkout, where a gitignored `.env` really is on disk. A write role runs in a
lane worktree built from a git ref, where it is not there at all.

### A `.netrc` password now gets redacted

`.netrc` writes credentials with a space and no delimiter — `machine host login
user password hunter2` — so every assignment pattern walked past it. That mattered
little while the read gate was believed to work and matters a great deal now that
the redactor is the only defence. Gated on the file's own marker so the word
`password` in ordinary prose is left alone, and the username beside it is not
touched.

### `soak.py` stopped failing on a string that means two things

Running out of turns lands on `ACP_STOP_cancelled` exactly like a gate refusal
that killed the turn, and the first is a documented success — the verifier still
runs and the lane still commits. It now judges whether the work was verified and
committed, and reports turn exhaustion as a note rather than a failure.

---

## 0.21.0 — One poll, one budget

### A read-only worker that reached for a write lost its whole turn

Reported from a live session: `grok_agent_review` under the project's own preset
came back `status: cancelled`, `blocked_reason: ACP_STOP_cancelled`, with one
opening sentence and no verdict — twice — while the CLI's log recorded a
successful inference in under thirty seconds.

The preset is not the cause; the same call completes here. The capture in
`evidence/live-acp/session-permission-cancel.jsonl` shows what is: the worker
asked to edit a file, the bridge answered `reject_once` — the correct, non-fatal
answer — and the CLI sent `session/cancel` regardless. A refused write ends the
turn no matter how politely the bridge refuses, and a read-only role can never
legally write, so any write it reaches for costs everything.

No gate change can prevent that, so `build_prompt` now tells every read-only
worker plainly that it cannot write, that a refused write ends the turn, and
that its answer in the message is the deliverable. Previously a `consult` or
`review` call with no `test_commands` was told none of this.

### A search the bridge promised was allowed

`build_prompt` tells every worker that "reading and searching files is always
allowed". The gate then refused any request carrying no path key — which is
exactly what a repo-wide grep or glob looks like, since a search names *what* to
look for rather than *where*. The worker was denied the one operation it had
been promised, and a denial the client cannot offer a way past ends the turn:
the job comes back `ACP_STOP_cancelled` after a single opening sentence, with no
verdict and the CLI's own log showing a perfectly successful inference.

A search with no path is now judged as scoped to the session directory. It still
may not escape: `../` in any spelling, a leading slash, a drive letter, a UNC
prefix and `~` are refused, as is a pattern that goes hunting for a secret by
name. The permission matrix gained the shape it never had a row for.

### `id_rsa` was refused to a command and handed to a read

`AGENTS.md` promises `.env`, `id_rsa` and `*.pem` are refused in any form. The
command gate did refuse all three; the path denylist behind `read` and `search`
named `.env`, `auth.json`, `credentials.json` and the certificate suffixes — and
not `id_rsa`, `id_ed25519`, `.git-credentials`, `.netrc`, `.htpasswd` or
`.pgpass`. A private key sitting in the worktree was readable. Both lists now
agree on what a secret is.

### Ordinary filenames read as forbidden commands

`rm`, `del`, `ssh`, `curl` and `oauth` were matched anywhere in a command
string, not where a command can start. So a declared `pytest tests/test_form.py`
was refused for containing `rm`, `src/model.py` for containing `del`, and
`tests/test_oauth.py` for looking like a credential. A denied test command is
not a harmless no — the denial can end the worker's turn, so the job pays for
the whole cycle and returns nothing. Command names are now anchored where a
command can appear; secret *file* names still match anywhere they can appear as
a path.

Two smaller refusals went with them: `py -3.12 -m pytest` was rejected while
`py -3` passed, which is how an operator pins an interpreter; and a *directory*
named `go` or `py` made `go test ./...` look like a worker handing the verifier
its own binary, because the check asked whether the name existed rather than
whether it was a file.

### One poll now has one budget

Found by the audit routine on its first live run: a single poll came back at
17,725 characters against a documented ceiling of 16 KiB. Two causes, both real.
`compact_job_record` only runs for hosts that asked for compaction, so the typed
path had no ceiling of its own; and inside compaction, `ECONOMY_MAX_UNIFIED_DIFF`
is 16 KiB and so was the whole promise — "one poll
stays under 16 KiB whatever the job did". Every per-field cap was respected and
a compact record still came back at 22 KB, because the caps bound fields, not
the record. The host paid for it on the poll that matters most: the last one,
the only one carrying a diff.

The record is now fitted to `ECONOMY_MAX_RECORD` after assembly. Trimming is
ordered by what a reader can most afford to lose and is never silent —
`economy_trimmed` names each field that gave way and `economy_budget_chars`
says what it gave way to. A receipt that never approached the budget is left
exactly as it was, so nothing new appears on ordinary jobs.

### The opt-in tool ignored the allowlist it was handed

`handle_tool_call(..., allowed_roots=[x])` meant one thing for job tools and
nothing at all for `grok_agent_project`, which read module state instead. Any
embedder passing its own roots got `ALLOWED_ROOTS_EMPTY` from the single tool
whose job is to clear `PROJECT_NOT_ENABLED`. Hosts over stdio and HTTP never
saw this — they do not inject — which is why it survived until something did.

### Routines: the bridge audited by the bridge

`scripts/routines.py` is a standing catalogue where each routine names one
promise and reads its verdict off a receipt. `--harness-only` runs in seconds
without a worker; the rest pay for a live one. The `audit.*` routines hand Grok
a brief and take back a report file, and every `read: file:line` in that report
is checked against the tree — file present, line in range, quoted text nearby.
Without that check a worker could attach one real pytest run and invent the rest,
since `finalize_receipt` looks at the declared command and knows nothing about
the JSON beside it. Findings land in `Service/Audits/routines-<stamp>.json` with
the receipt slice that decided each one and the command that reproduces it.

The brief itself went through two live skeptic rounds before it was worth
running: the first version told a read-only role to write a file, which the gate
refuses before `edit` is ever reached, so the auditor would have produced
nothing at all.

---

## 0.20.0 — One lane, one worker

The four claims the previous round left unverified, taken one at a time. Two
were real and are fixed, one is real and turns out to be harmless, and one had
no honest probe behind it.

### A busy lane could read as free

The check asked `jobs.list_jobs(limit=64)` and looked through the answer -- a
newest-first page. Past sixty-four running jobs the oldest fell off the end and
its worktree read as available, which is the one outcome the check exists to
prevent: two workers in one checkout. It now scans the whole registry under the
lock instead of a page of it.

### One lane, spelled two ways

`grok_delegate_start` recorded the lane exactly as the caller wrote it, while
the agent tools ask for the normalised name. So `foo` and `grok/foo` were two
different lanes to the busy check and one directory on disk, and both could run
at once. The legacy tool now records what the check will look for.

### `transport: legacy` is missing the evidence fields, and it does not matter

The legacy write path sets none of `verifier_touched_files`, `lane_commit` or
`worker_written_files`. Confirmed by reading the branch that handles write
roles, not the read-only one next to it. It cannot produce a false success
anyway: a write role with no `bridge-verifier` test is already refused with
`TEST_EVIDENCE_MISSING`, and a test the agent says it ran is not evidence. Two
tests pin that, so the harmlessness stays deliberate rather than accidental.

### Not fixed

The cancel-versus-completion race. Every probe for it so far went through
`jobs.start_job`, which never registers a cancel event -- that happens a layer
up in `start_agent_job` -- so nothing was actually tested. The window, if it is
there, costs a wrong error message on a job that was finishing regardless.
Checking it properly needs a live job through the agent layer.

## 0.19.0 — Five that survived being checked

Ten claims came out of the 2026-08-19 skeptic sweep unverified. Reproducing them
one at a time left five, and the discards matter as much as the fixes: one of
them had been carried as known backlog since the previous audit, and acting on
it would have meant changing working code on the strength of a plausible story.

Two of the first verdicts were wrong the other way. A probe that called
`start_job` with the wrong signature caught its own `TypeError` and reported
"not reproduced" for a real bug; another compared the first textual occurrence
of two names in a function with a dozen early returns, and got the ordering
backwards. A verification harness is as capable of being wrong as the claim it
checks.

### A decorated secret name was still that secret

Windows strips trailing dots and spaces from a path component, and everything
after `::` names an alternate data stream of the same file. The denylist
compared the string as typed, so `auth.json.`, `auth.json ` and `.env::$DATA`
all reached exactly the files it exists to refuse. It now compares the name
Windows would open. `key.pem.` is caught too -- the suffix is taken from the
normalised name, because pathlib does not consider that one a `.pem`.

### A failed handoff left a job that could never finish

`start_job` marked the record `running` before handing the work to a thread. If
that handoff raised -- an executor refusing after shutdown does exactly this --
the record stayed `running` forever: `_evict_locked` drops only terminal
records, `LANE_BUSY` kept seeing it, and cancel answered `JOB_NOT_OWNED`. It now
terminalises, and the caller still gets the exception.

### Work nobody can review is not delivered work

The gate judged artifacts and tests and never looked at whether the lane commit
succeeded. A rejecting hook or a read-only checkout came back `completed` with
`sha: None` and nothing on the branch to merge. That is now
`LANE_COMMIT_MISSING`, carrying the reason.

### `C:secret.py` is not a local file

A drive-relative path names the process's own directory on that drive, which is
not the worktree. The revision-splitting rule skipped `C:\` and `C:/` only, so
the bare-colon form was reduced to `secret.py` and judged as an ordinary name.

### A tombstone is not work in progress

A record rehydrated from a dead server incarnation reads as `unknown`: no thread
owns it, nothing will finish it. Treating it as an idempotent replay meant the
same packet could not be retried until eviction happened to reach it.

### Not fixed, and why

`.. \out.txt` does not escape: `resolve()` strips the trailing space exactly as
Win32 does, and the gate refuses it. The lane is still committed before the
verdict, deliberately -- work stopped early costs review time, never the work,
and since 0.18.0 only attributed paths are staged.

## 0.18.0 — What the skeptics found, and what the clock found

Five skeptic jobs, five lenses, run through this bridge against this repository.
Four finished; none died on a gate refusal or a truncated report, which is what
0.15.0 and 0.16.0 were for. Findings verified before acting on them -- roughly
half held.

### The lane commit swept up work that was not the worker's

`commit_lane_work` staged with `git add -A`, so a branch a human is meant to
review carried a foreign MCP server's log and two `__pycache__` blobs -- files
0.17.0's own acceptance gate had already named as somebody else's. Judging them
foreign and committing them anyway was the bridge disagreeing with itself.

The lane now stages what the gate calls the worker's: approved writes plus the
expected artifacts. When that filter leaves nothing, the receipt says
`NOTHING_TO_COMMIT` rather than reporting an empty commit as work.

### Eight ways a secret reached a receipt

Every one verified by running it, and every one a shape a worker meets in files
it is allowed to read -- a Django settings module, a compose file, a JSON config:

- `SECRET_KEY=` and `AWS_SECRET_ACCESS_KEY=` -- the old pattern wanted `secret`
  to stand alone, so a key that merely contained it walked through.
- `{"api_key": …}` and `{"client_secret": …}`.
- `Authorization: Basic <base64>` lost only the word `Basic`.
- `PASSWORD="correct horse battery staple"` was cut at the first space.
- `redis://:pass@host` -- an empty user did not match at all.
- `postgres://u:pa@ss@host` was cut at the first `@`.
- `sk_live_…` -- an underscore where the list assumed a dash.

Also fixed the opposite failure, which the same skeptic warned about: a redactor
that eats the diff is one people switch off. `token: str = ""` and
`password_hash = bcrypt(x)` are left alone.

### A quiet job is no longer indistinguishable from a working one

When the model is full, the Grok CLI answers `500 … at capacity`, retries up to
fifteen times with backoff, and says nothing over ACP. From here that looked
exactly like thinking, and an operator cancelled live work forty seconds in.

Polls now carry `quiet_for_s`, and past 45 seconds of silence the bridge reports
what the CLI wrote in its own log about the process we spawned -- `worker_pid`
joins the two. A capacity refusal comes back named `PROVIDER_AT_CAPACITY` with
the attempt count, so waiting can be told from a broken job. Best effort by
construction: an unreadable log is no answer rather than an error, and
`GROK_DELEGATE_CLI_LOG=0` switches it off.

### The suite went green for two hours and eleven minutes

The first draft of the redaction fix wrapped the secret-word alternation in
`[A-Za-z0-9_.-]*` on both sides: two unbounded quantifiers around an
alternation. One line of a diff went from 0.19ms to 34ms, and `redact_text` runs
on every event and every line of stderr, so the suite went from two minutes to
2:10:44 -- passing the entire time. Caught by the clock, not by a red test.

Rewritten as a linear scan with the secret-word decision made in Python, and two
tests now assert what a redaction costs. A non-matching key also stopped hiding
the one behind it: `URL: ws://host?server-key=x` matched `URL`, swallowed the
whole address, and never examined `server-key`.

## 0.17.0 — Whose change is it

Every `execute` and `fix` job on this machine came back `blocked`:

    UNEXPECTED_CHANGED_FILES: __pycache__/app.cpython-314.pyc,
                              outputs/ads/logs/runtime.log,
                              tests/__pycache__/test_app...pyc

The worker had touched none of them. The `.pyc` files were the byproduct of the
test run the bridge itself had told it to make. The log belongs to a *different*
MCP server the Grok CLI has configured, which creates it relative to its working
directory -- and its working directory is the lane. It appeared in read-only
runs too, where the worker wrote nothing at all.

So the gate built to prevent a false success was producing a false failure, and
a gate that cries wolf is a gate people switch off.

### The bridge already knew, and never wrote it down

It approves every write the worker makes. Both transports now record the paths
those approvals covered and return them as `worker_written_files`, and the
acceptance gate judges only those. Anything else that moved in the tree is
reported as `foreign_changed_files` -- named, never hidden, because the operator
still has to know the branch is not only their work before merging it.

What deliberately did not change: a file the worker really did write and nobody
asked for still blocks; `ARTIFACT_WRITTEN_BY_VERIFIER` is untouched; and an
empty attribution list means judge everything, not accept everything, so an
older transport reporting nothing cannot read as innocence.

### `scripts/soak.py`

The suite proves units; this proves the product. One real job per role against a
scratch repository and a live Grok CLI, judged by the receipt rather than by
anything the agent says about itself, failing on the specific ways jobs have
actually died: a gate refusal that killed the turn, an output cap that truncated
it, a poll that cost the host more than 16 KiB. Grok side is deliberately not
economised. Exit code 0 only when every row passes; evidence lands in
`Service/Audits/soak-<stamp>.json` so runs can be compared instead of recalled.

It found the bug above on its first run.

### The loop is written down

`AGENTS.md` gains **Цикл доводки**: what "done" means as a list that can be
falsified, and the standing instruction to keep iterating without asking. Two
consecutive green soaks, a suite that grew with every fix, no gate-killed turns,
no truncated output, polls under 16 KiB, a skeptic that proved something by
running it, an executor whose work reached a lane. Budgets are asymmetric on
purpose: the worker is unmetered, the host's context is the thing to save.

## 0.16.0 — A read destroyed what it read

A skeptic run through this bridge, against this repository, reviewing the code
released here hours earlier. It ran the pytest it was handed -- the first time
that has been possible -- and found something the 844 tests could not.

### `grok_agent_poll` truncated the receipt it was reading

`_bounded_poll` copied the record with `dict()`, which is shallow, and the
`result` it then wrote into was the object still held in the job registry. So
bounding the events on the way out **shortened the durable receipt**. Poll once
with `limit: 1` and the tail was gone for good; poll again with `limit: 64` and
`events_total` counted the already-shortened list and reported 1 of 1. The
top-level list survived, which made a single record carry two different answers
to the same question.

Introduced in 0.15.0, by the change that was supposed to make polling cheap.
Nothing caught it because the tests built a record and threw it away instead of
asking whether it had changed -- so that is now what they assert.

### Read-only git the gate had no row for

`_ALLOWED_COMMAND` permitted `status`, `diff`, `log`, `show` and `rev-parse`.
It did not permit `ls-files`, and an audit job died asking for exactly that: the
refusal ended the turn and the job produced nothing. Added `ls-files`,
`ls-tree`, `shortlog`, `describe` and `blame` -- none of which reach further
than `show` and `diff` already did, while the forbidden contour (push, merge,
reset, clean, `.env`, keys, network tools) stays a separate check that still
refuses them even when declared. `git grep` is deliberately not added: its
useful forms need characters this gate strips anyway.

### Three guards against the class of mistake, not the three mistakes

The bugs fixed in 0.15.0 were all the same shape: something advertised in a
schema or a description, and something else happening in the code. 844 tests
did not catch any of them, because the suite tested behaviour someone had
thought about and nothing connected the advertised surface to the tested one.

- **The permission decision is now a generated matrix** rather than remembered
  examples: profile x tool kind x request shape, every cell spelled out, plus a
  check that the gate names no kind the matrix has no row for. Replayed against
  the 0.14.0 branch order, the read-only-runs-a-declared-command row fails --
  the bug would have been caught the moment the file existed.
- **Every tool parameter must say what proves it does anything.** A registry
  maps each of the 54 advertised parameters to its evidence, and the test fails
  both ways: a new parameter with no entry, and an entry for a parameter that no
  longer exists. `limit` shipped dead for two releases because nothing asked.
  Also asserts every tool sets `additionalProperties: false`, so a typo is a
  refusal rather than a silently ignored request.
- **A poll's cost is a property, not a byte count that rots.** Twenty times the
  events must cost the host under one percent more. On 0.14.0 a single poll of a
  1000-event job was 490,286 characters; it is now 10,396.

## 0.15.0 — Found by using it on itself

Two jobs were run through this bridge, against this repository, to audit it.
Both died the same way and produced nothing, and that is where these changes
come from -- not from reading the code, which had been read plenty.

### A skeptic could not run the test it was handed

`consult` and `skeptic` are locked to `permission_profile: read-only` by the
task contract, and in `permission_decision` the read-only branch matched before
the execute branch could ever be reached. So a skeptic could not run `pytest`,
could not run `git log`, could not reproduce anything -- it could read files and
assert about what it read. For a role whose whole job is to disbelieve, that is
the wrong shape.

A command declared in `test_commands` is the operator's own authorisation, so
the profile no longer vetoes it. `read-only` now means "changes nothing" rather
than "does nothing": `edit` and `write` are still refused, an undeclared command
is still refused in both profiles, and `_command_allowed` still stands between a
declared string and the filesystem.

### `grok_agent_poll` charged the host for the whole history, twice

`limit` was in the tool schema, was accepted by the unknown-argument check, and
was then never read on the path that takes a `job_id` -- the only path anyone
uses. Passing `limit: 1` returned exactly as much as passing nothing. And a
finished job carried its event list twice, once at the top level and once nested
inside `result`.

Polls now keep the newest `limit` events (default 20) in both places and report
`events_total` with `events_omitted`, because a list that quietly ends reads
like a job that quietly stopped.

### The worker is told what to do when the gate says no

`build_prompt` already said to run test commands verbatim. What it did not say
is that there are no other commands at all, and -- the part that actually cost
two jobs -- what to do when the work needs one. Both agents wrote an ad-hoc
script, were denied, and stopped, leaving nothing on disk to commit. The prompt
now states the closed world and gives a legal move: write down the command you
would have run, and finish the rest without it.

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
