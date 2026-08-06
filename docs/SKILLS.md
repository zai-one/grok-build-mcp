# Skills (Claude · Codex · Cursor · others)

> Unofficial project. Skills teach **agents** how to install and operate this MCP
> **after** Grok CLI is installed and `grok login` succeeded.

## Portable layout

The same `SKILL.md` files are mirrored under:

| Path | Host |
|---|---|
| `skills/<name>/` | Canonical copy |
| `.claude/skills/<name>/` | [Claude Code](https://code.claude.com/docs/en/skills) |
| `.codex/skills/<name>/` | Codex CLI project skills |
| `.agents/skills/<name>/` | Agent Skills open standard / Cursor-friendly |

## Bundled skills

| Skill | Purpose |
|---|---|
| **install-grok-mcp** | Setup only — hard gate on CLI+login |
| **grok-delegate** | Runtime economy sequence + security |
| **create-agent-skill** | Author new portable skills for any host |

## Install a skill for your host

**Claude Code (project):** already in `.claude/skills/` when you clone.

**Claude Code (personal):**

```bash
cp -R .claude/skills/install-grok-mcp ~/.claude/skills/
cp -R .claude/skills/grok-delegate ~/.claude/skills/
```

**Codex CLI:**

```bash
cp -R .codex/skills/* ~/.codex/skills/   # or ~/.agents/skills/
```

**Any Agent Skills host:** copy from `skills/` or `.agents/skills/`.

Then say: *Use skill install-grok-mcp* or *Use skill grok-delegate*.

## Validate

```bash
python scripts/verify_skills.py
```

Must print `SKILL VERIFY PASS`.
