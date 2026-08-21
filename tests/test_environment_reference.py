"""A knob nobody wrote down is a knob nobody knows about.

Twenty-one of the bridge's thirty-four `GROK_DELEGATE_*` variables were readable
in the code and documented nowhere -- including the two git timeouts an operator
needs on exactly the machine where jobs are timing out. This keeps the reference
and the code from drifting apart again, in both directions: a new variable has
to be documented, and a documented one that no longer exists has to go.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "docs" / "ENVIRONMENT.md"

#: Names the reference mentions as *not* this bridge's, so they are allowed to
#: appear there without being read by the package.
FOREIGN = {"GROK_DELEGATE_"}


def _names_in_code() -> set[str]:
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((ROOT / "grok_delegate").glob("*.py"))
    )
    return set(re.findall(r"GROK_DELEGATE_[A-Z0-9_]+", text))


def _names_in_reference() -> set[str]:
    return set(re.findall(r"GROK_DELEGATE_[A-Z0-9_]+", REFERENCE.read_text(encoding="utf-8")))


def test_every_variable_the_code_reads_is_documented() -> None:
    missing = sorted(_names_in_code() - _names_in_reference())
    assert not missing, (
        "these are read by the package and appear nowhere in docs/ENVIRONMENT.md: "
        f"{missing}"
    )


def test_the_reference_does_not_invent_variables() -> None:
    invented = sorted(_names_in_reference() - _names_in_code() - FOREIGN)
    assert not invented, (
        "these are documented and nothing in the package reads them: " f"{invented}"
    )


def test_the_reference_is_reachable_from_the_readme() -> None:
    """A file nobody links to is a file nobody finds."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/ENVIRONMENT.md" in readme
