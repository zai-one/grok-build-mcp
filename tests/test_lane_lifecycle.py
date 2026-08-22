"""A lane is unmerged work, and unmerged work has to be visible and finite.

Ten lanes accumulated in this repository alone, four of them from runs whose
only product was the lane itself. Two things follow: a lane that holds nothing
should not survive the job that made it, and a lane that holds something has to
be listed somewhere an operator actually looks.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from grok_delegate import agent_runtime
from grok_delegate.contracts import validate_task_packet
from grok_delegate.runner import (
    ensure_lane_dir_ignored,
    list_lanes,
    prepare_worktree,
    release_lane,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@e.invalid")
    git("config", "user.name", "T")
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "seed")
    ensure_lane_dir_ignored(root)
    git("add", "-A")
    git("commit", "-qm", "ignore lanes")
    return root


def _head(path: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _lane(root: Path, name: str) -> dict:
    out = prepare_worktree(
        repo_root=root, lane=name, base_ref="HEAD", lanes_parent=root / ".grok" / "lanes"
    )
    assert out.get("ok"), out
    return out


def _branches(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


# --- releasing ------------------------------------------------------------------


def test_a_lane_that_produced_nothing_is_removed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lane = _lane(root, "empty")
    out = release_lane(
        repo_root=root,
        worktree_path=lane["worktree_path"],
        branch=lane["branch"],
        base_sha=_head(root),
    )
    assert out["removed"] is True
    assert not Path(lane["worktree_path"]).exists()
    assert "grok/empty" not in _branches(root)


def test_a_lane_with_uncommitted_work_is_kept_and_says_why(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lane = _lane(root, "dirty")
    (Path(lane["worktree_path"]) / "work.txt").write_text("in progress\n", encoding="utf-8")
    out = release_lane(
        repo_root=root,
        worktree_path=lane["worktree_path"],
        branch=lane["branch"],
        base_sha=_head(root),
    )
    assert out["removed"] is False
    assert out["reason"] == "UNCOMMITTED_CHANGES"
    assert Path(lane["worktree_path"]).exists()


def test_a_lane_with_a_commit_is_kept(tmp_path: Path) -> None:
    """The bridge does not merge and does not judge: a commit is a human's call."""
    root = _repo(tmp_path)
    lane = _lane(root, "committed")
    worktree = Path(lane["worktree_path"])
    (worktree / "work.txt").write_text("done\n", encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-qm", "worker output"]):
        subprocess.run(["git", "-C", str(worktree), *args], check=True, capture_output=True)
    out = release_lane(
        repo_root=root,
        worktree_path=worktree,
        branch=lane["branch"],
        base_sha=_head(root),
    )
    assert out["removed"] is False
    assert out["reason"] == "LANE_HAS_COMMITS"
    assert worktree.exists()


def test_a_branch_that_is_not_a_lane_is_never_touched(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lane = _lane(root, "safe")
    out = release_lane(
        repo_root=root,
        worktree_path=lane["worktree_path"],
        branch="main",
        base_sha=_head(root),
    )
    assert out["removed"] is False
    assert out["reason"] == "NOT_A_LANE_BRANCH"
    assert Path(lane["worktree_path"]).exists()


def test_the_switch_turns_cleanup_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_LANE_CLEANUP", "0")
    root = _repo(tmp_path)
    lane = _lane(root, "kept")
    out = release_lane(
        repo_root=root,
        worktree_path=lane["worktree_path"],
        branch=lane["branch"],
        base_sha=_head(root),
    )
    assert out["removed"] is False
    assert out["reason"] == "CLEANUP_DISABLED"
    assert Path(lane["worktree_path"]).exists()


def test_releasing_a_lane_twice_is_not_an_error(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lane = _lane(root, "twice")
    base = _head(root)
    first = release_lane(
        repo_root=root, worktree_path=lane["worktree_path"], branch=lane["branch"], base_sha=base
    )
    second = release_lane(
        repo_root=root, worktree_path=lane["worktree_path"], branch=lane["branch"], base_sha=base
    )
    assert first["removed"] is True
    assert second["removed"] is False
    assert second["reason"] == "ALREADY_GONE"


def test_an_unknown_base_keeps_the_lane(tmp_path: Path) -> None:
    """Not knowing what the job started from is not a reason to delete."""
    root = _repo(tmp_path)
    lane = _lane(root, "unknown-base")
    out = release_lane(
        repo_root=root, worktree_path=lane["worktree_path"], branch=lane["branch"], base_sha=""
    )
    assert out["removed"] is False
    assert Path(lane["worktree_path"]).exists()


# --- listing --------------------------------------------------------------------


def test_live_lanes_are_listed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _lane(root, "alpha")
    _lane(root, "beta")
    lanes = list_lanes(root, lanes_parent=root / ".grok" / "lanes")
    branches = sorted(lane["branch"] for lane in lanes)
    assert branches == ["grok/alpha", "grok/beta"]
    assert all(lane.get("head") for lane in lanes)


def test_the_project_itself_is_not_a_lane(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lanes = list_lanes(root, lanes_parent=root / ".grok" / "lanes")
    assert lanes == []


# --- a skeptic standing in the lane ---------------------------------------------


class _NoWorker:
    name = "stdio"

    def __init__(self, watch: str | None = None) -> None:
        self.cwd: Path | None = None
        self.watch = watch
        #: Whether the watched path existed *while the worker ran*, which is the
        #: only moment a mount is supposed to be there.
        self.saw_watched: bool | None = None

    def run(self, task, *, cwd, cancel_event, event_sink=None):
        self.cwd = Path(cwd)
        if self.watch is not None:
            self.saw_watched = (Path(cwd) / self.watch).exists()
        return {
            "status": "completed",
            "summary": "read the lane",
            "tests": [],
            "events": [],
            "worker_written_files": [],
        }


def _review_task(root: Path) -> dict:
    return validate_task_packet(
        {
            "objective": "Review the lane",
            "role": "skeptic",
            "project_root": str(root),
            "permission_profile": "read-only",
            "max_turns": 5,
            "timeout_seconds": 60,
            "correlation_id": "lane-review",
        },
        allowed_roots=[root],
    )


def test_a_skeptic_asked_for_a_lane_stands_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))
    lane = _lane(root, "under-review")
    worker = _NoWorker()
    agent_runtime.run_task(
        _review_task(root),
        transport="stdio",
        lane="grok/under-review",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
        review_lane="under-review",
    )
    assert worker.cwd == Path(lane["worktree_path"]).resolve()


def test_a_skeptic_without_a_lane_still_stands_in_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))
    worker = _NoWorker()
    agent_runtime.run_task(
        _review_task(root),
        transport="stdio",
        lane="grok/whatever",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
    )
    assert worker.cwd == root.resolve()


def test_reviewing_a_lane_that_does_not_exist_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only role must not create a branch to review one."""
    root = _repo(tmp_path)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))
    worker = _NoWorker()
    receipt = agent_runtime.run_task(
        _review_task(root),
        transport="stdio",
        lane="grok/missing",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
        review_lane="missing",
    )
    assert receipt["blocked_reason"] == "LANE_NOT_FOUND"
    assert worker.cwd is None
    assert "grok/missing" not in _branches(root)


def test_a_main_checkout_is_never_removed(tmp_path: Path) -> None:
    """A lane-shaped branch name must not be enough to delete a repository.

    A linked worktree keeps a `.git` file; a main checkout keeps a `.git`
    directory. That difference is the whole check, and it is local and cheap.
    """
    root = _repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "checkout", "-q", "-b", "grok/pretend"], check=True, capture_output=True
    )
    out = release_lane(
        repo_root=root, worktree_path=root, branch="grok/pretend", base_sha=_head(root)
    )
    assert out["removed"] is False
    assert out["reason"] == "NOT_A_LINKED_WORKTREE"
    assert (root / "README.md").exists()


def test_status_shows_the_lanes_that_exist(tmp_path: Path) -> None:
    """An operator reads status at the start of a session; that is where
    unmerged work has to be visible."""
    from grok_delegate.status import build_status_report

    root = _repo(tmp_path)
    _lane(root, "visible")
    report = build_status_report(
        allowed_roots=[root],
        lanes_parent_map={str(root): root / ".grok" / "lanes"},
        which=lambda _name: None,
    )
    assert [lane["branch"] for lane in report["lanes"]] == ["grok/visible"]
    assert report["lanes_total"] == 1


def test_a_long_lane_list_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository that collected lanes for a month must not turn a status
    call into a page of paths -- but the count still tells the truth."""
    import grok_delegate.status as status_mod

    fake = [{"path": f"p{index}", "branch": f"grok/l{index}", "head": "0" * 12} for index in range(50)]
    monkeypatch.setattr(status_mod, "list_lanes", lambda *_a, **_k: list(fake))
    root = _repo(tmp_path)
    report = status_mod.build_status_report(allowed_roots=[root], which=lambda _name: None)
    assert len(report["lanes"]) == 32
    assert report["lanes_total"] == 50


def test_a_skeptic_reviewing_a_lane_can_be_given_the_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The combination that makes both features worth having.

    A lane is built from a git ref, so the brief sitting in an ignored directory
    is not in it -- and a skeptic standing in that lane cannot read the task it
    is reviewing against unless it is mounted.
    """
    root = _repo(tmp_path)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))
    gitignore = root / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "briefs/" + chr(10), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "ignore briefs"], check=True,
                   capture_output=True)
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("what to check" + chr(10), encoding="utf-8")

    lane = _lane(root, "with-brief")
    worker = _NoWorker(watch="briefs/task.md")
    task = validate_task_packet(
        {
            "objective": "Review the lane against the brief",
            "role": "skeptic",
            "project_root": str(root),
            "permission_profile": "read-only",
            "max_turns": 5,
            "timeout_seconds": 60,
            "correlation_id": "lane-review-brief",
            "mount_paths": ["briefs/task.md"],
        },
        allowed_roots=[root],
    )
    receipt = agent_runtime.run_task(
        task,
        transport="stdio",
        lane="grok/with-brief",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
        review_lane="with-brief",
    )
    assert receipt["mounted_paths"] == ["briefs/task.md"]
    assert worker.cwd == Path(lane["worktree_path"]).resolve()
    # There while the worker ran, gone afterwards: a mount is an input, and a
    # lane a human will read should not hold files no commit explains.
    assert worker.saw_watched is True
    assert not (Path(lane["worktree_path"]) / "briefs" / "task.md").exists()
    assert not (Path(lane["worktree_path"]) / "briefs").exists()


def test_mount_paths_without_a_lane_is_refused_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silently skipping the mount would hand the worker a task it cannot read."""
    root = _repo(tmp_path)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))
    gitignore = root / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "briefs/" + chr(10), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "ignore briefs"], check=True,
                   capture_output=True)
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("brief" + chr(10), encoding="utf-8")

    worker = _NoWorker()
    task = validate_task_packet(
        {
            "objective": "Consult with a brief",
            "role": "consult",
            "project_root": str(root),
            "permission_profile": "read-only",
            "max_turns": 5,
            "timeout_seconds": 60,
            "correlation_id": "consult-brief",
            "mount_paths": ["briefs/task.md"],
        },
        allowed_roots=[root],
    )
    receipt = agent_runtime.run_task(
        task,
        transport="stdio",
        lane="grok/no-lane",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
    )
    assert receipt["blocked_reason"] == "MOUNT_WITHOUT_LANE"
    assert worker.cwd is None


def test_status_says_where_a_root_came_from(tmp_path) -> None:
    """Two fields described the channel that is off; the one that is on was absent.

    `host_root_trusted` reads like "this root can be trusted" and is only the
    GROK_DELEGATE_TRUST_HOST_ROOTS flag, off by default and about an env-provided
    path. Directories are actually granted through MCP `roots/list`, which is on
    by default -- so an operator reading this block saw `host_root: null` while
    the host had declared a directory and the bridge had accepted it.
    """
    from grok_delegate import host_roots as host_roots_module
    from grok_delegate.status import build_status_report

    host_roots_module.reset_for_tests()
    report = build_status_report(allowed_roots=[tmp_path])
    roots = report["roots"]

    assert "mcp_roots_enabled" in roots, "the channel that grants roots is not reported"
    assert roots["mcp_roots_enabled"] is True, "MCP roots are on by default"
    assert roots["mcp_roots"] == [], "nothing was declared in this test"

    host_roots_module.apply_roots_response(
        {"result": {"roots": [{"uri": tmp_path.as_uri(), "name": "declared"}]}}
    )
    try:
        after = build_status_report(allowed_roots=[tmp_path])["roots"]
        assert after["mcp_roots"], "a declared root is invisible in status"
        assert str(tmp_path) in after["mcp_roots"][0]
    finally:
        host_roots_module.reset_for_tests()


def test_a_lane_whose_directory_was_deleted_can_be_reopened(tmp_path) -> None:
    """git keeps the registration; without a prune the lane is stuck forever.

    An operator who removes `<project>/.grok/lanes/<slug>` by hand leaves the
    worktree registered. `worktree add` then fails with "missing but already
    registered", which the existing retry does not recognise -- it only looks
    for "already exists" / "already checked out" -- so the lane answered
    WORKTREE_CREATE_FAILED on every later job while its branch sat there intact.
    """
    import shutil
    import subprocess

    from grok_delegate.runner import prepare_worktree

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", "seed"],
        check=True,
    )

    lanes = tmp_path / "lanes"
    first = prepare_worktree(
        repo_root=repo, lane="stuck", base_ref="HEAD", lanes_parent=lanes
    )
    assert first.get("ok"), first
    worktree = Path(first["worktree_path"])
    assert worktree.is_dir()

    # what an operator does when a lane looks like clutter
    shutil.rmtree(worktree)

    again = prepare_worktree(
        repo_root=repo, lane="stuck", base_ref="HEAD", lanes_parent=lanes
    )
    assert again.get("ok"), (
        f"the lane could not be reopened after its directory was removed: {again}"
    )
    assert Path(again["worktree_path"]).is_dir()
