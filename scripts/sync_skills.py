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

# Ship the references inside the package so grok://guide/{topic} works for
# wheel/uvx installs too (same text, one source: skills/grok-mcp/references).
GUIDE = ROOT / "grok_delegate" / "guide"
if GUIDE.exists():
    shutil.rmtree(GUIDE)
shutil.copytree(SRC / "references", GUIDE)
print("synced", GUIDE.relative_to(ROOT))
