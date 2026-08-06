from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_verify_skills_script_passes() -> None:
    script = ROOT / "scripts" / "verify_skills.py"
    assert script.is_file()
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_start_here_mentions_cli_gate() -> None:
    text = (ROOT / "docs" / "START_HERE.md").read_text(encoding="utf-8")
    assert "grok login" in text
    assert "does nothing useful until" in text.lower() or "ALL of these are true" in text
