# Tools

**Plan compiler = source of truth** (`session_begin.plan`).

| Tool | Role |
|---|---|
| `grok_agent_session_begin` | mode + plan + budget + deny + host_script |
| `grok_agent_session_tick` | step / budget_remaining / force_end |
| `grok_agent_session_end` | receipt + budget_report + lesson |

Other tools: only if listed in plan/recommended for this session.
