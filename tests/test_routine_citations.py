"""The check that makes an invented citation not worth writing.

`scripts/routines.py` hands Grok a brief and takes back a JSON report. Nothing
downstream re-reads what that report quotes: `finalize_receipt` looks at the
declared test command and knows nothing about the file beside it, so one real
pytest run plus eight fabricated `read:` citations would land as `completed`.
`_citation_holds` is what closes that, which makes it exactly the kind of code
that has to be wrong in the safe direction -- a false accusation costs a real
finding, and this file pins both halves.

Importing the script also keeps it from rotting: it is not part of the package,
so nothing else would notice if it stopped parsing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "routines.py"


def _load():
    spec = importlib.util.spec_from_file_location("routines_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["routines_under_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


routines = _load()


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "gate.py").write_text(
        "\n".join(
            [
                "def decide(kind, task):",
                '    if kind == "think":',
                "        permitted = True",
                '    elif task.get("permission_profile") == "read-only":',
                '        permitted = kind in {"read", "search"}',
                "    return permitted",
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


# --- what must be caught ---------------------------------------------------------


def test_a_citation_to_a_file_that_does_not_exist_is_refused(tree: Path) -> None:
    why = routines._citation_holds(tree, "read: pkg/invented.py:12", "some code")
    assert why and "does not exist" in why


def test_a_line_past_the_end_of_the_file_is_refused(tree: Path) -> None:
    why = routines._citation_holds(tree, "read: pkg/gate.py:900", "")
    assert why and "900" in why


def test_text_that_is_nowhere_near_the_cited_line_is_refused(tree: Path) -> None:
    """The line resolving is not enough: the quote has to come from around it."""
    why = routines._citation_holds(
        tree, "read: pkg/gate.py:2", "raise RuntimeError('this line is not in the file')"
    )
    assert why and "within ten lines" in why


def test_a_probe_that_is_not_a_citation_at_all_is_refused(tree: Path) -> None:
    why = routines._citation_holds(tree, "read: I looked at the gate", "")
    assert why and "file:line" in why


# --- what must not be caught -----------------------------------------------------


def test_an_honest_citation_passes(tree: Path) -> None:
    assert routines._citation_holds(
        tree, "read: pkg/gate.py:4", 'elif task.get("permission_profile") == "read-only":'
    ) is None


def test_a_line_range_is_how_people_cite_a_block(tree: Path) -> None:
    """`file:4-6` would have failed as "not file:line" and killed a real finding."""
    assert routines._citation_holds(
        tree, "read: pkg/gate.py:4-6", 'permitted = kind in {"read", "search"}'
    ) is None


def test_a_quote_carrying_its_line_number_still_matches(tree: Path) -> None:
    """Reviewers paste `4:  elif ...`; that prefix is not in the file."""
    assert routines._citation_holds(
        tree, "read: pkg/gate.py:4", '4: elif task.get("permission_profile") == "read-only":'
    ) is None


def test_a_citation_with_nothing_quoted_is_accepted_on_the_line_alone(tree: Path) -> None:
    """Half a check is better than refusing a finding for being terse."""
    assert routines._citation_holds(tree, "read: pkg/gate.py:3", "") is None


def test_windows_separators_in_a_citation_resolve(tree: Path) -> None:
    assert routines._citation_holds(tree, "read: pkg\\gate.py:3", "") is None


# --- the catalogue itself --------------------------------------------------------


def test_every_routine_is_reachable_and_described() -> None:
    """A routine with no docstring cannot explain itself in `--list`."""
    assert routines.ROUTINES, "the catalogue is empty"
    for name, (dimension, driver, fn) in routines.ROUTINES.items():
        assert driver in {"harness", "grok"}, name
        assert name.startswith(dimension + "."), name
        assert (fn.__doc__ or "").strip(), name


def test_the_brief_still_carries_every_placeholder_it_fills() -> None:
    """A missing key raises at job start, minutes after the operator walked away."""
    filled = routines.BRIEF.format(
        report="r.json", title="t", body="b", files="f", command="c", dimension="d",
    )
    assert "{" not in filled.replace("{}", ""), "an unfilled placeholder survived"
    assert "r.json" in filled and "c" in filled
