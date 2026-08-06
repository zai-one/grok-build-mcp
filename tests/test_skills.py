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


def test_easy_install_is_the_path() -> None:
    easy = (ROOT / "docs" / "EASY.md").read_text(encoding="utf-8")
    assert "install.sh" in easy
    assert "grok login" in easy
    start = (ROOT / "docs" / "START_HERE.md").read_text(encoding="utf-8")
    assert "EASY.md" in start
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "install.sh" in readme
    assert "pip install -e" not in readme or "one command" in readme.lower()
