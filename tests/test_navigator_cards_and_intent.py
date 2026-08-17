"""Three navigator defects the skeptic found, and the checks that keep them fixed.

A card is an instruction the host executes verbatim, so a card that cannot run
on the host's platform is worse than no card: it reads as a working answer.
"""

from __future__ import annotations

import sys

import pytest

from grok_delegate.session import _auto_mode, _install_command


READY = {"binary_ok": True, "auth_ok": True, "roots_ok": True, "ready": True}


# --- intent routing: a filename is not a verb ---------------------------------


@pytest.mark.parametrize(
    "goal",
    [
        "update tests/conftest.py to share the fixture",
        "add a case to tests/test_auth.py",
        "rewrite tests/helpers.py",
        "fix the flaky test in tests/test_round8_bridge.py",
    ],
)
def test_touching_a_file_under_tests_is_still_work(goal: str) -> None:
    """The bare substring `test` inside a path routed these to verify.

    The navigator then handed back poll/status cards for a job that had to
    write code, and the mode had been decided by a directory name.
    """
    assert _auto_mode(READY, "auto", goal) == "execute"


@pytest.mark.parametrize(
    "goal",
    [
        "run the tests and report what fails",
        "review the diff on the lane",
        "verify the receipt matches the worktree",
        "check whether the update landed",
    ],
)
def test_asking_about_state_is_still_verification(goal: str) -> None:
    assert _auto_mode(READY, "auto", goal) == "verify"


def test_an_explicit_intent_always_wins_over_the_guess() -> None:
    assert _auto_mode(READY, "brainstorm", "fix the parser") == "brainstorm"


def test_the_goals_own_first_verb_outranks_a_word_further_along() -> None:
    """"check whether the update landed" is a question, not an update job.

    Word matching cannot tell a verb from a noun. Goals are imperatives, so the
    leading verb is asked first and the ordered scan only runs when there is
    none.
    """
    assert _auto_mode(READY, "auto", "check whether the update landed") == "verify"
    assert _auto_mode(READY, "auto", "update the check that reviews receipts") == "execute"
    assert _auto_mode(READY, "auto", "please review the lane") == "verify"


def test_a_word_must_be_a_word_not_a_fragment() -> None:
    """`add ` used to need its trailing space to avoid matching "address"."""
    assert _auto_mode(READY, "auto", "summarise the address book schema") != "execute"
    assert _auto_mode(READY, "auto", "add the address column") == "execute"


def test_an_unrecognisable_goal_falls_back_to_operate() -> None:
    assert _auto_mode(READY, "auto", "grok") == "operate"


# --- install: name the installer this host actually has -----------------------


def test_the_install_card_matches_the_platform() -> None:
    command = _install_command()
    if sys.platform == "win32":
        assert "install.ps1" in command and "| bash" not in command
    else:
        assert "install.sh" in command


def test_both_installers_the_card_can_name_exist_in_the_repository() -> None:
    from pathlib import Path

    scripts = Path(__file__).resolve().parent.parent / "scripts"
    assert (scripts / "install.ps1").exists()
    assert (scripts / "install.sh").exists()
