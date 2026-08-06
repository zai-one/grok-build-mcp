# Tools

| Mode | Tools |
|---|---|
| **always first** | `grok_agent_session_begin` |
| progress | `grok_agent_session_tick` |
| finish | `grok_agent_session_end` |
| playbook | `grok_agent_economy` (optional; begin covers most) |
| brainstorm | `grok_agent_consult`, `grok_agent_review` |
| execute | `grok_agent_execute` → poll/tick |
| verify | poll/status/review |
| ops | `grok_delegate_doctor` if gate broken |

Prefer `session_*` over re-reading docs. `verbose=true` only when debugging.
