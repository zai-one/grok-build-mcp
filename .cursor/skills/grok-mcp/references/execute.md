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

Anti: chat-as-objective · parallel jobs · host edits mid-job · CLI version pin.
