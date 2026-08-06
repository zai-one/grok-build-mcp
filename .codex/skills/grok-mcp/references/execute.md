# Execute

1. Gate OK.
2. One-line objective + paths + 1–3 tests (`templates/goal-brief.md`).
3. `grok_agent_execute` max_turns 8–16.
4. `grok_agent_poll` job_id; read summary/changed_files/tests only.
5. Receipt; human merges `grok/*` (no push/merge).

Anti: chat-as-objective · parallel jobs · host edits mid-job.
