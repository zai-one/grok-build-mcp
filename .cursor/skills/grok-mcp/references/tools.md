# Tools

| Tool | Role |
|---|---|
| `grok_agent_session_begin` | plan + budget + session_id; optional `project_root` / artifacts / tests |
| `grok_agent_session_next` | **cheap host loop** — one typed card |
| `grok_agent_session_end` | receipt + budget_report |
| `grok_agent_execute` | write job; args = `{task: {…}}` |
| `grok_agent_poll` | `{job_id}` only |
| `grok_agent_status` | bridge + CLI + unpin + `update_hint` |

Other tools appear inside `card` from `session_next`, or as typed fallback.
