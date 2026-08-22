"""Every defect four skeptics found in one change, pinned so it cannot come back.

The changes under review claimed four things: the output budget measures the
agent, a lane that produced nothing is removed, `mount_paths` is a narrow door,
and a read-only role can review a lane. Each claim was true of the code the
tests exercised and false somewhere the tests did not go. These are those
places, in the order the reports ranked them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from grok_delegate import acp, agent_runtime
from grok_delegate.contracts import validate_task_packet
from grok_delegate.guard import looks_like_secret_path
from grok_delegate.runner import (
    ensure_lane_dir_ignored,
    mount_paths_into,
    prepare_worktree,
    release_lane,
)


def _repo(tmp_path: Path, ignore: str = "") -> Path:
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@e.invalid")
    git("config", "user.name", "T")
    (root / ".gitignore").write_text(ignore, encoding="utf-8")
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "seed")
    ensure_lane_dir_ignored(root)
    git("add", "-A")
    git("commit", "-qm", "ignore lanes")
    return root


def _lane(root: Path, name: str) -> dict:
    out = prepare_worktree(
        repo_root=root, lane=name, base_ref="HEAD", lanes_parent=root / ".grok" / "lanes"
    )
    assert out.get("ok"), out
    return out


# --- the lane a job produced nothing for, except an ignored file -----------------


def test_ignored_work_is_not_mistaken_for_an_empty_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git status` says nothing about ignored files, and `--force` deletes.

    This repository ignores `*.log` and `Service/Audits/routines-*.json`. A job
    whose only product was one of those looked like a lane that produced
    nothing, and the cleanup took the work with it.
    """
    root = _repo(tmp_path, ignore="*.log\n")
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))

    class _WritesIgnoredFile:
        name = "stdio"

        def run(self, task, *, cwd, cancel_event, event_sink=None):
            (Path(cwd) / "out.log").write_text("REAL WORK\n", encoding="utf-8")
            return {
                "status": "completed",
                "summary": "wrote the log",
                "tests": [],
                "events": [],
                "worker_written_files": ["out.log"],
            }

    task = validate_task_packet(
        {
            "objective": "produce the log",
            "role": "execute",
            "project_root": str(root),
            "permission_profile": "workspace",
            "max_turns": 5,
            "timeout_seconds": 60,
            "expected_artifacts": ["out.log"],
            "test_commands": [f"{sys.executable} -c pass"],
            "correlation_id": "ignored-work",
        },
        allowed_roots=[root],
    )
    receipt = agent_runtime.run_task(
        task,
        transport="stdio",
        lane="ignored-work",
        router=agent_runtime.TransportRouter(
            grok_bin="grok", adapters={"stdio": _WritesIgnoredFile()}
        ),
        cancel_event=threading.Event(),
    )
    lane_path = Path(receipt["worktree_path"])
    assert receipt["lane_released"] is False
    assert receipt["lane_retained_reason"] == "WORK_PRESENT"
    assert (lane_path / "out.log").read_text(encoding="utf-8") == "REAL WORK\n"


def test_a_failed_branch_delete_is_not_a_removed_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`removed: True` with a surviving branch is the next job's stale-branch error."""
    root = _repo(tmp_path)
    lane = _lane(root, "half-gone")
    base = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    real = agent_runtime.release_lane

    def only_the_checkout(args, cwd, timeout, **kwargs):
        from grok_delegate.runner import default_git_runner

        if list(args)[1:3] == ["branch", "-D"] or "branch" in list(args):
            return {"returncode": 1, "stdout": "", "stderr": "locked", "timedOut": False}
        return default_git_runner(args, cwd, timeout, **kwargs)

    out = release_lane(
        repo_root=root,
        worktree_path=lane["worktree_path"],
        branch=lane["branch"],
        base_sha=base,
        git_runner=only_the_checkout,
    )
    assert out["removed"] is True
    assert out["branch_deleted"] is False
    assert out["reason"] == "BRANCH_DELETE_FAILED"
    assert real is agent_runtime.release_lane


# --- a lane name has to mean one lane --------------------------------------------


def test_a_prefixed_lane_name_is_the_same_lane(tmp_path: Path) -> None:
    """`grok/x` slugified to `grok-x`, so the busy check guarded a different
    slot than the one the review path opened -- two workers, one worktree."""
    task = {"role": "skeptic", "correlation_id": "c"}
    assert agent_runtime._lane_name("grok/under-review", task) == "grok/under-review"
    assert agent_runtime._lane_name("GROK/Under-Review", task) == "grok/under-review"
    assert agent_runtime._lane_name("under-review", task) == "grok/under-review"


# --- a review lane must be a lane of this repository -----------------------------


def _review_task(root: Path) -> dict:
    return validate_task_packet(
        {
            "objective": "Review",
            "role": "skeptic",
            "project_root": str(root),
            "permission_profile": "read-only",
            "max_turns": 5,
            "timeout_seconds": 60,
            "correlation_id": "review-guard",
        },
        allowed_roots=[root],
    )


class _Recorder:
    name = "stdio"

    def __init__(self) -> None:
        self.cwd: Path | None = None

    def run(self, task, *, cwd, cancel_event, event_sink=None):
        self.cwd = Path(cwd)
        return {"status": "completed", "summary": "", "tests": [], "events": [],
                "worker_written_files": []}


def test_a_neighbouring_repository_is_not_a_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a shared lanes parent, `review_lane="lib"` used to land in the
    neighbour's checkout -- secrets and all."""
    parent = tmp_path / "work"
    parent.mkdir()
    root = _repo(parent)
    neighbour = parent / "lib"
    neighbour.mkdir()
    subprocess.run(["git", "-C", str(neighbour), "init", "-q"], check=True, capture_output=True)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(parent))

    worker = _Recorder()
    receipt = agent_runtime.run_task(
        _review_task(root),
        transport="stdio",
        lane="grok/lib",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
        review_lane="lib",
    )
    assert receipt["blocked_reason"] == "LANE_NOT_FOUND"
    assert worker.cwd is None


def test_the_main_checkout_is_not_a_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lane named after the project used to resolve to the project itself --
    the one tree with the gitignored secrets this feature exists to avoid."""
    parent = tmp_path / "work"
    parent.mkdir()
    root = _repo(parent)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(parent))

    worker = _Recorder()
    receipt = agent_runtime.run_task(
        _review_task(root),
        transport="stdio",
        lane="grok/repo",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
        review_lane="repo",
    )
    assert receipt["blocked_reason"] == "LANE_NOT_FOUND"
    assert worker.cwd is None


def test_legacy_transport_refuses_a_lane_it_cannot_honour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy runs in the project whatever cwd says; a receipt naming the lane
    would be a lie."""
    root = _repo(tmp_path)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))
    _lane(root, "legacy-review")

    receipt = agent_runtime.run_task(
        _review_task(root),
        transport="legacy",
        lane="grok/legacy-review",
        router=agent_runtime.TransportRouter(grok_bin="grok"),
        cancel_event=threading.Event(),
        review_lane="legacy-review",
    )
    assert receipt["blocked_reason"] == "REVIEW_LANE_UNSUPPORTED_TRANSPORT"


# --- mounts ----------------------------------------------------------------------


def test_ignore_is_judged_in_the_lane_not_the_project(tmp_path: Path) -> None:
    """The project's `.gitignore` can be newer than the ref the lane was cut
    from, and then "ignored here" says nothing about "ignored there"."""
    root = _repo(tmp_path)  # lane's .gitignore does not mention briefs/
    lane = _lane(root, "older-base")
    (root / ".gitignore").write_text("briefs/\n", encoding="utf-8")
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("brief\n", encoding="utf-8")

    out = mount_paths_into(
        repo_root=root, worktree_path=lane["worktree_path"], paths=["briefs/task.md"]
    )
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_TRACKED"
    assert not (Path(lane["worktree_path"]) / "briefs" / "task.md").exists()


def test_a_partial_mount_is_rolled_back(tmp_path: Path) -> None:
    """Refusing halfway is still writing into somebody's lane."""
    root = _repo(tmp_path, ignore="briefs/\n")
    (root / "briefs").mkdir()
    (root / "briefs" / "first.md").write_text("ok\n", encoding="utf-8")
    lane = Path(_lane(root, "partial")["worktree_path"])

    out = mount_paths_into(
        repo_root=root,
        worktree_path=lane,
        paths=["briefs/first.md", "briefs/absent.md"],
    )
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_MISSING"
    assert out["mounted"] == []
    assert not (lane / "briefs" / "first.md").exists()


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows reparse point")
def test_a_link_in_the_lane_is_not_written_through(tmp_path: Path) -> None:
    """A reused lane can hold a link where the mount wants to write."""
    root = _repo(tmp_path, ignore="briefs/\n")
    (root / "briefs").mkdir()
    (root / "briefs" / "task.md").write_text("brief\n", encoding="utf-8")
    lane = Path(_lane(root, "reused")["worktree_path"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (lane / "briefs").parent.mkdir(parents=True, exist_ok=True)
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(lane / "briefs"), str(outside)],
        capture_output=True,
        text=True,
    )
    if made.returncode != 0:
        pytest.skip("this machine would not create a junction")

    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs/task.md"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_TARGET_ESCAPE"
    assert not (outside / "task.md").exists()


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows reparse point")
def test_a_link_on_the_way_to_the_source_is_refused(tmp_path: Path) -> None:
    """`resolve()` walks through a link, so the check saw only the target while
    the copy landed under the link's own name in the lane."""
    # `briefs` is the junction's own name and must be ignored too, or creating
    # it dirties the tree and `prepare_worktree` refuses before the test starts.
    root = _repo(tmp_path, ignore="hidden/\nbriefs\n")
    (root / "hidden" / "specs").mkdir(parents=True)
    (root / "hidden" / "specs" / "a.md").write_text("a\n", encoding="utf-8")
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "briefs"), str(root / "hidden" / "specs")],
        capture_output=True,
        text=True,
    )
    if made.returncode != 0:
        pytest.skip("this machine would not create a junction")
    lane = Path(_lane(root, "through-link")["worktree_path"])

    out = mount_paths_into(repo_root=root, worktree_path=lane, paths=["briefs"])
    assert out["ok"] is False
    assert out["error"] == "MOUNT_PATH_SYMLINK"


def test_the_secret_predicate_reads_every_component() -> None:
    """`id_rsa/config` is a private key directory; the tail is not the whole path."""
    assert looks_like_secret_path(Path("id_rsa/config")) is True
    assert looks_like_secret_path(Path(".env.local/settings.json")) is True
    assert looks_like_secret_path(Path("certs.pem/readme.md")) is True
    # `.envrc` is a credential file the search gate has always refused.
    assert looks_like_secret_path(Path(".envrc")) is True
    assert looks_like_secret_path(Path("docs/environment.md")) is False
    assert looks_like_secret_path(Path("src/app.py")) is False


# --- the output budget ------------------------------------------------------------


def test_a_deeply_nested_frame_does_not_kill_the_reader(tmp_path: Path) -> None:
    """`json.loads` blows the stack long before the depth guard is reached.

    Measured on this interpreter: 5000 levels parse, 20000 raise RecursionError
    -- which `except json.JSONDecodeError` did not catch, so one hostile frame
    killed the job before anything could count it.
    """
    depth = 20_000
    hostile = '{"a":' * depth + "1" + "}" * depth
    with pytest.raises(RecursionError):
        json.loads(hostile)

    fake = Path(__file__).resolve().parent / "fake_acp_agent.py"
    task = validate_task_packet(
        {
            "objective": "DEEP_FRAME_FIXTURE",
            "role": "consult",
            "project_root": str(tmp_path),
            "permission_profile": "read-only",
            "max_turns": 5,
            "timeout_seconds": 30,
            "correlation_id": "deep-frame",
        },
        allowed_roots=[tmp_path],
    )
    transport = acp.StdioACPTransport(
        grok_bin="grok",
        popen_factory=lambda _argv, **kwargs: subprocess.Popen(
            [sys.executable, str(fake)], **kwargs
        ),
    )
    result = transport.run(task, cwd=tmp_path, cancel_event=threading.Event())
    # The frame is dropped as malformed and the turn still finishes.
    assert result["status"] == "completed"
    assert result["summary"].strip() == "survived"


def test_an_oversized_frame_is_not_the_output_budget() -> None:
    """It is one bad frame, not a truncated answer, and the codes now differ."""
    source = (Path(acp.__file__)).read_text(encoding="utf-8")
    assert "ACP_FRAME_TOO_LARGE" in source
    assert "WebSocket frame exceeded configured cap" not in source


def test_an_identifier_key_is_only_free_while_it_is_small() -> None:
    small = {"params": {"update": {"status": "in_progress"}}}
    huge = {"params": {"update": {"status": "X" * 100_000}}}
    assert acp.payload_bytes(small) < 100
    assert acp.payload_bytes(huge) > 100_000


def test_a_zero_cost_first_frame_still_enters_the_ledger(monkeypatch) -> None:
    """`if already` read a zero-byte first frame as "never seen", so once the
    ledger filled, that call's growth was charged in full all over again.

    Counting keys made a genuinely free frame impossible to build -- every frame
    carrying a `toolCallId` pays for `params` and `update` -- so the size is
    stubbed here. The branch is still reachable in principle and still wrong if
    it tests truthiness instead of membership.
    """
    sizes = {"first": 0, "grown": 300}
    state = {"value": "first"}
    monkeypatch.setattr(acp, "payload_bytes", lambda *_a, **_k: sizes[state["value"]])

    seen: dict[str, int] = {}
    frame = {"params": {"update": {"toolCallId": "a"}}}
    assert acp.charge_payload(frame, seen) == 0
    assert "a" in seen, "a call that cost nothing is still a call we have seen"

    # Fill the ledger with other calls, the way a thought/tool flood does.
    monkeypatch.setattr(acp, "payload_bytes", lambda *_a, **_k: 1)
    for index in range(acp._TOOL_LEDGER_MAX + 10):
        acp.charge_payload({"params": {"update": {"toolCallId": f"c{index}"}}}, seen)

    state["value"] = "grown"
    monkeypatch.setattr(acp, "payload_bytes", lambda *_a, **_k: sizes[state["value"]])
    assert acp.charge_payload(frame, seen) == 300
    assert acp.charge_payload(frame, seen) == 0


def test_a_lane_label_is_not_a_review_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every read-only job already passes `lane` as a name for itself.

    Soak passes `soak-consult` and `soak-skeptic`; treating that as "stand in
    that worktree" turned two working calls into LANE_NOT_FOUND. Reviewing a
    lane is `task.review_lane`, and nothing else.
    """
    root = _repo(tmp_path)
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(root / ".grok" / "lanes"))
    worker = _Recorder()
    receipt = agent_runtime.run_task(
        _review_task(root),
        transport="stdio",
        lane="grok/soak-skeptic",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": worker}),
        cancel_event=threading.Event(),
    )
    assert receipt["blocked_reason"] is None
    assert worker.cwd == root.resolve()


def test_a_write_role_cannot_be_sent_into_someone_elses_lane(tmp_path: Path) -> None:
    """It gets a lane of its own; two workers in one worktree is the thing to prevent."""
    from grok_delegate.guard import GuardError as _GuardError

    root = _repo(tmp_path)
    with pytest.raises(_GuardError) as caught:
        validate_task_packet(
            {
                "objective": "write",
                "role": "execute",
                "project_root": str(root),
                "permission_profile": "workspace",
                "max_turns": 5,
                "timeout_seconds": 60,
                "expected_artifacts": ["out.txt"],
                "test_commands": ["python -c pass"],
                "correlation_id": "write-review",
                "review_lane": "someone-else",
            },
            allowed_roots=[root],
        )
    assert caught.value.code == "REVIEW_LANE_NOT_FOR_WRITE_ROLE"
