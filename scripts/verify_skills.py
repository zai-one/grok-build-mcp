#!/usr/bin/env python3
"""Validate Agent Skills layout and required content (no network)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_DIRS = [
    ROOT / "skills",
    ROOT / ".claude" / "skills",
    ROOT / ".codex" / "skills",
    ROOT / ".agents" / "skills",
]
REQUIRED_SKILLS = {"install-grok-mcp", "grok-delegate", "create-agent-skill"}
FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def main() -> int:
    errors: list[str] = []
    found: dict[str, list[Path]] = {}
    for base in HOST_DIRS:
        if not base.is_dir():
            errors.append(f"missing skills root: {base.relative_to(ROOT)}")
            continue
        for skill_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            name = skill_dir.name
            found.setdefault(name, []).append(skill_md)
            if not skill_md.is_file():
                errors.append(f"missing {skill_md.relative_to(ROOT)}")
                continue
            text = skill_md.read_text(encoding="utf-8")
            m = FRONT.match(text)
            if not m:
                errors.append(f"no YAML frontmatter: {skill_md.relative_to(ROOT)}")
                continue
            fm = m.group(1)
            if f"name: {name}" not in fm and f"name: {name}\n" not in fm:
                if re.search(rf"^name:\s*{re.escape(name)}\s*$", fm, re.M) is None:
                    errors.append(f"frontmatter name mismatch: {skill_md.relative_to(ROOT)}")
            if "description:" not in fm:
                errors.append(f"missing description: {skill_md.relative_to(ROOT)}")
            body = text[m.end() :]
            low = body.lower()
            if "unofficial" not in low:
                errors.append(f"missing unofficial disclaimer: {skill_md.relative_to(ROOT)}")
            if name != "create-agent-skill":
                if "grok login" not in low and "grok --version" not in low:
                    errors.append(f"missing Grok CLI gate: {skill_md.relative_to(ROOT)}")
            for banned in ("sk-live", "xai-", "BEGIN RSA", "ghp_"):
                if banned.lower() in text.lower() and "planted" not in text.lower():
                    errors.append(f"possible secret pattern {banned}: {skill_md.relative_to(ROOT)}")
    for req in REQUIRED_SKILLS:
        if req not in found:
            errors.append(f"required skill missing everywhere: {req}")
        else:
            # require presence in all host roots that exist
            for base in HOST_DIRS:
                if base.is_dir() and not (base / req / "SKILL.md").is_file():
                    errors.append(f"skill {req} missing under {base.relative_to(ROOT)}")
    if errors:
        print("SKILL VERIFY FAIL")
        for e in errors:
            print(" -", e)
        return 1
    print("SKILL VERIFY PASS")
    for name, paths in sorted(found.items()):
        print(f"  {name}: {len(paths)} mirrors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
