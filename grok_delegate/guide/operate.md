# Operate

`grok_agent_session_begin` → loop `grok_agent_session_next` → `grok_agent_session_end`.

- `card.kind=host_cmd`: run `card.cmd` (user shell)
- `card.kind=mcp_tool`: call `card.tool` with `card.args` (already schema-valid)
- `card.kind=end`: `grok_agent_session_end`
- `done=true` or `force_end`: stop after end

If `mcp_tool` fails schema/host validation, stop the navigator and use typed
consult → execute → poll → review. See `references/execute.md`.

Unpin is default. Check `grok_agent_status.compatibility.update_hint` when the
bridge looks stale.
