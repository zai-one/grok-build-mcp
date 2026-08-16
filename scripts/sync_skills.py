#!/usr/bin/env python3
"""Copy skills/grok-mcp → .claude/.codex/.agents mirrors."""
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills" / "grok-mcp"
for host in (".claude", ".codex", ".agents", ".cursor"):
    dest = ROOT / host / "skills" / "grok-mcp"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SRC, dest)
    print("synced", dest.relative_to(ROOT))
