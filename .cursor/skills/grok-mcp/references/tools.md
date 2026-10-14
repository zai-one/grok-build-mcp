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

## Tool map (every tool)

- `grok_agent_economy` — Token-economy playbook
- `grok_agent_update` — Update bridge
- `grok_agent_check_update` — Check for update
- `grok_agent_project` — Project preset
- `grok_agent_session_begin` — Begin session
- `grok_agent_session_tick` — Session tick
- `grok_agent_session_next` — Next session card
- `grok_agent_session_end` — End session
- `grok_agent_status` — Agent status
- `grok_agent_start` — Start agent job
- `grok_agent_poll` — Poll agent job
- `grok_agent_cancel` — Cancel agent job
- `grok_agent_consult` — Consult (read-only)
- `grok_agent_review` — Review diff
- `grok_agent_execute` — Execute in worktree
- `grok_agent_fix` — Fix in worktree
- `grok_delegate` — Delegate (sync)
- `grok_delegate_plan` — Plan only
- `grok_delegate_start` — Delegate (background)
- `grok_delegate_poll` — Poll delegation
- `grok_delegate_status` — Bridge status
- `grok_delegate_doctor` — grok doctor
- `grok_delegate_models` — List models
- `grok_delegate_inspect` — grok inspect
