# Tools

| Mode | Tools |
|---|---|
| ready | `grok_agent_status` |
| playbook | `grok_agent_economy` once/session |
| brainstorm | `grok_agent_consult`, `grok_agent_review` |
| execute | `grok_agent_execute` → `grok_agent_poll` |
| verify | poll/status; review optional |
| ops | doctor/models/inspect if broken |

Prefer `grok_agent_*` over legacy `grok_delegate_*`.
