# Skills

One **router skill**: `grok-mcp` (Agent Skills layout).

```text
skills/grok-mcp/
  SKILL.md           # router
  references/        # install, operate, update, executor, verifier, brainstorm, feedback, …
  scripts/           # check_ready, update_mcp, draft_issue
  templates/         # goals, receipts, issues
  assets/            # flow diagram (md)
```

Mirrored to `.claude/skills/`, `.codex/skills/`, `.agents/skills/`.

```bash
python scripts/sync_skills.py
python scripts/verify_skills.py
```

Tell the host: *use skill `grok-mcp`*.
