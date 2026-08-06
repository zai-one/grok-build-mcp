# Execute

After `session_begin` intent=execute (gate ready):

1. Tight objective + paths + 1–3 tests.
2. `grok_agent_execute` max_turns 8–16.
3. `session_tick` / poll job_id.
4. `session_end` receipt; human merges `grok/*`.

Anti: chat-as-objective · parallel jobs · host edits mid-job.
