# Skills

Router **`grok-mcp` v0.8** — Session Protocol first.

1. `grok_agent_session_begin` → mode + tools + skill_ref  
2. Do work with recommended tools only  
3. `grok_agent_session_tick` / `session_end`

```bash
python scripts/sync_skills.py
python scripts/verify_skills.py
python scripts/smoke_session.py
```
