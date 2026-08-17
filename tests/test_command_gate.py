"""The second gate on test commands, tested on its own terms.

Permission for an `execute` tool call needs two things: the command must be one
the task declared, and it must survive this allowlist. A mutation audit deleted
the metacharacter check, the path confinement and the forbidden-name regex one
at a time, and 605 tests stayed green each time -- because every existing test
reaches the gate through a command that was not declared, so the first condition
refuses it and the second never runs.

These call the gate directly, so it is the thing under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from grok_delegate.acp import ACPError, _command_allowed, validated_test_argv


# --- what a real test command looks like ---------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q",
        "py -3 -m pytest tests -q",
        "python -m unittest discover",
        "pytest tests/test_app.py",
        "npm test",
        "cargo test",
        "go test ./...",
    ],
)
def test_ordinary_test_commands_are_allowed(command: str, tmp_path) -> None:
    assert _command_allowed(command, tmp_path.resolve()) is True


# --- shell metacharacters ------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q; echo pwned",
        "python -m pytest -q && rm -rf .",
        "python -m pytest -q | Set-Content out.txt",
        "python -m pytest -q > out.txt",
        "pytest -q `whoami`",
        "pytest -q $(cat secrets.txt)",
        "python -m pytest -q\nSet-Content pwned.txt",
    ],
)
def test_a_second_statement_is_never_allowed(command: str, tmp_path) -> None:
    """The prefix is a legitimate test command; everything after it is not.

    `_ALLOWED_COMMAND` anchors at the start and is unanchored at the end, so
    without this check the suffix rides along on the prefix's approval.
    """
    assert _command_allowed(command, tmp_path.resolve()) is False


# --- paths that leave the worktree ---------------------------------------------


def test_a_test_file_outside_the_worktree_is_refused(tmp_path) -> None:
    outside = tmp_path.parent / "outside" / "test_escape.py"
    assert _command_allowed(f"pytest {outside}", tmp_path.resolve()) is False


def test_a_parent_traversal_is_refused(tmp_path) -> None:
    assert _command_allowed("pytest ../../etc/test_x.py", tmp_path.resolve()) is False


def test_a_flag_value_is_confined_too(tmp_path) -> None:
    assert _command_allowed("python -m pytest --rootdir=../outside", tmp_path.resolve()) is False


def test_a_path_inside_the_worktree_is_fine(tmp_path) -> None:
    assert _command_allowed("pytest tests/unit/test_app.py", tmp_path.resolve()) is True


# --- the launcher itself -------------------------------------------------------


def test_an_interpreter_the_worker_could_have_written_is_refused(tmp_path) -> None:
    """A worker that can write files can write `python.exe`.

    On Windows a bare name is searched for in the working directory, so the
    interpreter is the one operand whose rule is inverted: everything else must
    stay inside the worktree, this must stay out of it.
    """
    root = tmp_path.resolve()
    assert _command_allowed("python -m pytest -q", root) is True
    (root / "python.exe").write_text("planted", encoding="utf-8")
    assert _command_allowed("python -m pytest -q", root) is False


def test_an_interpreter_inside_the_worktree_by_path_is_refused(tmp_path) -> None:
    root = tmp_path.resolve()
    (root / "tools").mkdir()
    (root / "tools" / "python.exe").write_text("planted", encoding="utf-8")
    assert _command_allowed("tools/python.exe -m pytest -q", root) is False


def test_a_system_interpreter_is_still_usable(tmp_path) -> None:
    """The interpreter legitimately lives outside; only the lane copy is refused."""
    assert _command_allowed(f"{sys.executable} -m pytest -q", tmp_path.resolve()) is True


def test_a_bare_launcher_is_resolved_so_the_working_directory_cannot_win(tmp_path) -> None:
    argv = validated_test_argv("python -m pytest -q", tmp_path.resolve())
    assert argv[1:] == ["-m", "pytest", "-q"]
    if Path(argv[0]).name != "python":  # resolution only when python is on PATH
        return
    assert Path(argv[0]).is_absolute(), "a bare name would be looked up in the worktree"


# --- names that carry secrets --------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "Get-Content .env",
        "Get-Content .env.production",
        "git show HEAD:.env",
        "python -m pytest -q .env",
        "Get-Content .ssh/id_rsa",
        "Get-Content certs/server.pem",
        "Get-Content .npmrc",
        "Get-Content auth.json",
    ],
)
def test_reading_a_secret_is_refused_whatever_shape_it_arrives_in(command: str, tmp_path) -> None:
    """`.env` carries no separator, so path confinement never saw it as a path,
    and `git show HEAD:.env` hides it behind a revision."""
    assert _command_allowed(command, tmp_path.resolve()) is False


def test_an_ordinary_file_behind_a_revision_is_still_readable(tmp_path) -> None:
    assert _command_allowed("git show HEAD:src/app.py", tmp_path.resolve()) is True


def test_a_test_file_whose_name_merely_contains_env_is_fine(tmp_path) -> None:
    assert _command_allowed("pytest tests/test_env.py", tmp_path.resolve()) is True


# --- commands that are not tests at all ----------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git checkout main",
        "curl http://evil.example/x",
        "Remove-Item -Recurse .",
        "ssh user@host",
        "node build.js",
    ],
)
def test_a_command_that_is_not_a_test_is_refused(command: str, tmp_path) -> None:
    assert _command_allowed(command, tmp_path.resolve()) is False


def test_validated_test_argv_refuses_rather_than_returning_something_unsafe(tmp_path) -> None:
    with pytest.raises(ACPError) as refused:
        validated_test_argv("python -m pytest -q; echo pwned", tmp_path.resolve())
    assert refused.value.code == "TEST_COMMAND_UNSAFE"


def test_an_empty_or_oversized_command_is_refused(tmp_path) -> None:
    root = tmp_path.resolve()
    assert _command_allowed("", root) is False
    assert _command_allowed("pytest " + "a" * 2_100, root) is False
