---
name: create-agent-skill
description: >
  Create portable Agent Skills (SKILL.md) for Claude Code, Codex CLI, Cursor,
  and other hosts. Use when user asks for a new skill/plugin for MCP setup or ops.
version: 0.5.1
---

# create-agent-skill

> Produce **portable** skills (YAML frontmatter + markdown). Unofficial repos only.

## Output layout (write all mirrors)

```text
skills/<name>/SKILL.md              # canonical
.claude/skills/<name>/SKILL.md      # Claude Code
.codex/skills/<name>/SKILL.md       # Codex CLI
.agents/skills/<name>/SKILL.md      # Agent Skills / Cursor-friendly
```

Optional: `scripts/`, `references/` under the skill folder.

## Required frontmatter

```yaml
---
name: short-kebab-name
description: >
  One or two sentences. Include WHEN to use and HARD constraints
  (e.g. requires grok login). Agents load description first (~100 tokens).
version: 0.x.y
---
```

## Required sections for *this* project’s skills

1. **Unofficial disclaimer** (not xAI/OpenAI official)
2. **HARD GATE** — which CLI must exist (`grok` or `codex`) + login command
3. **Minimal commands** with placeholders only (`<REPO_PATH>`, never secrets)
4. **Success checks** (self-test / probe / tool name)
5. **Never** list (no OAuth in config, no secret commits)
6. **Doc links** relative to repo

## Style

- Short tables over essays
- Token-cheap: tell agent to open files, not paste them whole
- No fake product claims

## Validate

```bash
python scripts/verify_skills.py
```

Must exit 0 before claiming the skill is ready.
