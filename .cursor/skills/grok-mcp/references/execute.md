# Execute

After `session_begin` intent=execute (gate ready):

`session_next` is the cheap loop. The execute card is a **typed**
`grok_agent_execute` call: `task` must include `objective`, `project_root`,
`correlation_id`, `expected_artifacts`, `test_commands`. Pass those on
`session_begin` when you know them.

The poll card is `{job_id}` only. The server binds `job_id` from execute.
Never send `session_id` to poll (`additionalProperties: false`).

Fallback if a card is rejected:

1. Tight objective + paths + 1–3 tests.
2. `grok_agent_execute` with the full `task` packet, `max_turns` 8–16.
3. `grok_agent_poll` `{job_id}`.
4. Host reads changed_files / diffstat / bounded unified_diff / tests / worktree_path.
5. `session_end`; human merges `grok/*`.

## When the job needs something git does not carry

A lane is a checkout of a git ref, so a brief in an ignored directory is not in
it. Name it: `task.mount_paths: ["briefs/task.md"]`. The bridge copies those
paths in before the worker starts and takes them out afterwards, and refuses
anything that is not ignored by the lane, is a link, or is named like a
credential. The receipt lists `mounted_paths`.

## Reviewing a lane

`task.review_lane: "<slug>"` puts a read-only role (`consult`, `skeptic`) inside
that lane's worktree instead of the project. Read-only still: no branch, no
commit. An unknown lane is `LANE_NOT_FOUND`. The top-level `lane` argument is
just the job's name and does not do this.

Anti: chat-as-objective · parallel jobs · host edits mid-job · CLI version pin.
