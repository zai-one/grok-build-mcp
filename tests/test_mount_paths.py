"""A lane is a git ref, so what git does not carry has to be put there on purpose.

The brief a job is supposed to work from often lives outside git -- and a lane,
being a checkout of a ref, does not have it. `mount_paths` is the narrow, named
way across, and its refusals matter more than its copying: the reason a lane is
safe at all is that an ignored `.env` is not in it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from grok_delegate.contracts import validate_task_packet
from grok_delegate.guard import GuardError, looks_like_secret_path
from grok_delegate.runner import (
    MOUNT_MAX_BYTES,
    ensure_lane_dir_ignored,
    mount_paths_into,
    prepare_worktree,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@e.invalid")
    git("config", "user.name", "T")
    (root / ".gitignore").write_text("briefs/\n*.local\n.env\n", encoding="utf-8")
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "seed")
    ensure_lane_dir_ignored(root)
    git("add", "-A")
    git("commit", "-qm", "ignore lanes")
    return root


def _lane(root: Path, name: str = "mounted") -> Path:
    out = prepare_worktree(
        repo_root=root, lane=name, base_ref="HEAD", lanes_parent=root / ".grok" / "lanes"
    )
    assert out.get("ok"), out
    return Path(out["worktree_path"])


def _task(root: Path, **overrides) -> dict:
    value = {
        "objective": "Do the thing",
        "role": "execute",
        "project_root": str(root),
        "permission_profile": "workspace",
        "max_turns": 5,
        "timeout_seconds": 60,
        "expected_artifacts": ["out.txt"],
        "test_commands": ["python -m pytest -q"],
        "correlation_id": "mount-test",
    }
    value.update(overrides)
    return value


# --- what the packet refuses ------------------------------------------------------


def test_a_credential_can_never_be_mounted(tmp_path: Path) -> None:
    """The one refusal that carries the whole design."""
    root = _repo(tmp_path)
    for name in (".env", "secrets/id_rsa", "certs/server.pem", "auth.json"):
        with pytest.raises(GuardError) as caught:
            validate_task_packet(_task(root, mount_paths=[name]), allowed_roots=[root])
        assert caught.value.code == "MOUNT_PATH_FORBIDDEN"


def test_a_mount_cannot_climb_out_of_the_project(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(GuardError) as caught:
        validate_task_packet(_task(root, mount_paths=["../elsewhere/brief.md"]), allowed_roots=[root])
    assert caught.value.code == "MOUNT_PATH_INVALID"


def test_a_mount_must_be_relative(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(GuardError) as caught:
        validate_task_packet(
            _task(root, mount_paths=[str(tmp_path / "brief.md")]), allowed_roots=[root]
        )
    assert caught.value.code == "MOUNT_PATH_INVALID"


def test_an_ordinary_mount_survives_validation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    packet = validate_task_packet(
        _task(root, mount_paths=["briefs/task.md"]), allowed_roots=[root]
    )
    assert packet["mount_paths"] == ["briefs/task.md"]


def test_a_packet_without_mounts_carries_an_empty_list(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    packet = validate_task_packet(_task(root), allowed_roots=[root])
    assert packet["mount_paths"] == []


def test_the_secret_predicate_knows_the_windows_spellings() -> None:
    assert looks_like_secret_path(Path(".env")) is True
    assert looks_like_secret_path(Path("auth.json.")) is True
    assert looks_like_secret_path(Path("deep/nested/id_ed25519")) is True
    assert looks_like_secret_path(Path("docs/environment.md")) is False


# --- what the copier refuses ------------------------------------------------------


def test_an_ignored_file_is_mounted(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("the brief\n", encoding="utf-8")
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs/task.md"])
    assert out["ok"] is True
    assert out["mounted"] == ["briefs/task.md"]
    assert (lane / "briefs" / "task.md").read_text(encoding="utf-8") == "the brief\n"


def test_an_ignored_directory_is_mounted_whole(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "briefs" / "sub").mkdir(parents=True)
    (root / "briefs" / "a.md").write_text("a\n", encoding="utf-8")
    (root / "briefs" / "sub" / "b.md").write_text("b\n", encoding="utf-8")
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs"])
    assert out["ok"] is True
    assert (lane / "briefs" / "sub" / "b.md").read_text(encoding="utf-8") == "b\n"


def test_a_mounted_file_does_not_pollute_the_lane_commit(tmp_path: Path) -> None:
    """Ignored in the project means ignored in the lane, which is the point."""
    root = _repo(tmp_path)
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("the brief\n", encoding="utf-8")
    lane = _lane(root)
    mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs/task.md"])
    status = subprocess.run(
        ["git", "-C", str(lane), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert status.stdout.strip() == ""


def test_a_tracked_file_is_refused(tmp_path: Path) -> None:
    """It is already in the lane; copying over it would edit a reviewed branch."""
    root = _repo(tmp_path)
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["README.md"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_TRACKED"


def test_a_missing_path_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs/absent.md"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_MISSING"


def test_mounting_without_a_lane_is_refused(tmp_path: Path) -> None:
    """Mounting into the project itself would just overwrite the operator's tree."""
    root = _repo(tmp_path)
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("x\n", encoding="utf-8")
    out = mount_paths_into(repo_root=root, worktree_path=root, paths=["briefs/task.md"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_WITHOUT_LANE"


def test_an_oversized_mount_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "briefs").mkdir()
    big = root / "briefs" / "big.bin"
    with big.open("wb") as handle:
        handle.write(b"\0" * (MOUNT_MAX_BYTES + 1))
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs/big.bin"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_TOO_LARGE"
    assert not (lane / "briefs" / "big.bin").exists()


def test_nothing_to_mount_is_not_an_error(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = mount_paths_into(repo_root=root, worktree_path=root, paths=[])
    assert out["ok"] is True
    assert out["mounted"] == []


def test_a_mounted_directory_cannot_smuggle_a_credential(tmp_path: Path) -> None:
    """A directory is mounted whole, so every name inside it is mounted too."""
    root = _repo(tmp_path)
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("brief\n", encoding="utf-8")
    (root / "briefs" / ".env").write_text("XAI_API_KEY=xai-fake\n", encoding="utf-8")
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_FORBIDDEN"
    assert not (lane / "briefs" / ".env").exists()


def test_a_mounted_directory_cannot_smuggle_a_symlink(tmp_path: Path) -> None:
    """`copytree(symlinks=False)` follows a link out of the project and copies
    what it points at, which is the whole hole in one line."""
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not for the lane\n", encoding="utf-8")
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("brief\n", encoding="utf-8")
    try:
        (root / "briefs" / "link.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this platform does not allow creating symlinks here")
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_SYMLINK"
    assert not (lane / "briefs" / "link.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows reparse point")
def test_a_mounted_directory_cannot_smuggle_a_junction(tmp_path: Path) -> None:
    """The reachable version of the symlink hole on the platform this runs on.

    `os.symlink` needs a privilege (WinError 1314 here); `mklink /J` needs none,
    and `Path.is_symlink()` answers False for what it makes.
    """
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not for the lane\n", encoding="utf-8")
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("brief\n", encoding="utf-8")
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "briefs" / "linked"), str(outside)],
        capture_output=True,
        text=True,
    )
    if made.returncode != 0:
        pytest.skip("this machine would not create a junction")
    lane = _lane(root)
    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_SYMLINK"
    assert not (lane / "briefs" / "linked" / "secret.txt").exists()
