"""Lanes live inside the project, under a dot-directory, and git is told.

A lane holds unmerged work someone is going to review. That is project state,
not a cache, so it belongs with the project -- the way `.venv`, `.tox` and
`node_modules` do. What makes it safe rather than a mess is the leading dot:
pytest's default `norecursedirs` skips `.*`, ripgrep and most indexers skip
hidden directories, and one `.gitignore` line hides it from git.

A worktree in the visible source tree would be walked by all three, so that
stays refused.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from grok_delegate import server
from grok_delegate.guard import GuardError
from grok_delegate.runner import (
    LANE_HOME_DIRNAME,
    LANE_SUBDIRNAME,
    ensure_lane_dir_ignored,
    in_project_lanes_parent,
    is_hidden_inside,
    prepare_worktree,
    resolve_lanes_parent,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.invalid", "commit", "-q", "-m", "seed")
    return root


# --- where the default points --------------------------------------------------


def test_the_default_is_inside_the_project(tmp_path) -> None:
    parent = in_project_lanes_parent(tmp_path)
    assert parent == tmp_path.resolve() / LANE_HOME_DIRNAME / LANE_SUBDIRNAME
    assert LANE_HOME_DIRNAME.startswith("."), "the dot is the reason this is safe"


def test_an_explicit_path_still_wins(tmp_path) -> None:
    elsewhere = tmp_path.parent / "operator-choice"
    assert resolve_lanes_parent(tmp_path, elsewhere) == elsewhere.resolve()


# --- the rule that replaced a flat refusal -------------------------------------


@pytest.mark.parametrize(
    ("relative", "allowed"),
    [
        ((".grok", "lanes", "x"), True),
        ((".anything", "x"), True),
        (("lanes", "x"), False),
        (("src", ".grok", "x"), False),
        (("x",), False),
    ],
)
def test_only_a_leading_dot_segment_is_accepted(tmp_path, relative, allowed) -> None:
    """`src/.grok/x` is refused too: its first segment is an ordinary directory,
    and tools walking the tree reach the lane through it."""
    assert is_hidden_inside(tmp_path.joinpath(*relative), tmp_path) is allowed


def test_a_lanes_parent_in_the_source_tree_is_refused(tmp_path) -> None:
    with pytest.raises(GuardError) as refused:
        server.resolve_trusted_lanes_parent(
            {"lanes_parent": str(tmp_path / "lanes")}, repo_root=tmp_path
        )
    assert refused.value.code == "LANES_PARENT_INSIDE_REPO"


def test_a_worktree_in_the_source_tree_is_refused(tmp_path) -> None:
    root = _repo(tmp_path / "repo")
    result = prepare_worktree(
        repo_root=root,
        lane="visible",
        base_ref="HEAD",
        lanes_parent=root / "lanes",
        require_clean_base=False,
    )
    assert result.get("error") == "WORKTREE_INSIDE_REPO"


def test_a_worktree_under_the_dot_directory_is_created(tmp_path) -> None:
    root = _repo(tmp_path / "repo")
    result = prepare_worktree(
        repo_root=root,
        lane="hidden",
        base_ref="HEAD",
        lanes_parent=in_project_lanes_parent(root),
        require_clean_base=False,
    )
    assert result.get("ok") is True, result
    lane = Path(result["worktree_path"])
    assert lane.is_dir()
    assert is_hidden_inside(lane, root)
    assert (lane / "app.py").exists(), "the lane is a real checkout"


# --- git is told, so the operator's status stays clean --------------------------


def test_the_lane_directory_is_added_to_gitignore(tmp_path) -> None:
    root = _repo(tmp_path / "repo")
    outcome = ensure_lane_dir_ignored(root)
    assert outcome["ignored"] is True
    assert outcome["written"] is True
    assert LANE_HOME_DIRNAME + "/" in (root / ".gitignore").read_text(encoding="utf-8")


def test_calling_it_twice_writes_once(tmp_path) -> None:
    """It runs on every job, so it has to be safe to run on every job."""
    root = _repo(tmp_path / "repo")
    ensure_lane_dir_ignored(root)
    before = (root / ".gitignore").read_text(encoding="utf-8")
    second = ensure_lane_dir_ignored(root)
    assert second["written"] is False
    assert (root / ".gitignore").read_text(encoding="utf-8") == before


def test_an_existing_rule_elsewhere_is_respected(tmp_path) -> None:
    """git answers the question, so a rule in info/exclude counts as well."""
    root = _repo(tmp_path / "repo")
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(LANE_HOME_DIRNAME + "/\n", encoding="utf-8")
    outcome = ensure_lane_dir_ignored(root)
    assert outcome["written"] is False
    assert not (root / ".gitignore").exists()


def test_the_lane_is_invisible_to_the_project_once_ignored(tmp_path) -> None:
    """The whole point: the operator's own `git status` stays about their work."""
    root = _repo(tmp_path / "repo")
    ensure_lane_dir_ignored(root)
    prepare_worktree(
        repo_root=root,
        lane="quiet",
        base_ref="HEAD",
        lanes_parent=in_project_lanes_parent(root),
        require_clean_base=False,
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
    ).stdout
    assert LANE_HOME_DIRNAME not in status, status


@pytest.mark.skipif(sys.platform not in {"win32", "darwin", "linux"}, reason="needs a filesystem")
def test_pytest_does_not_collect_tests_from_inside_a_lane(tmp_path) -> None:
    """Proof for the claim the dot-prefix rests on.

    Without it, running the project's own suite would also collect every test
    in every lane -- N copies of the repository, failing for reasons that have
    nothing to do with the project.
    """
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_real.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    lane_tests = root / LANE_HOME_DIRNAME / LANE_SUBDIRNAME / "x" / "tests"
    lane_tests.mkdir(parents=True)
    (lane_tests / "test_real.py").write_text("def test_lane():\n    assert False\n", encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert "1 passed" in done.stdout, done.stdout
