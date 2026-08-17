"""The receipt has to answer two questions the host would otherwise re-derive.

"Did the tests pass?" and "where is the work?" were both answerable only by
opening the repository: `tests` came back empty whenever the worker stopped
short of `completed`, and a worker that ran out of turns left its edits
uncommitted in a worktree the lane janitor would eventually remove.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from grok_delegate import agent_runtime
from grok_delegate.agent_runtime import _resolve_base_sha, _verify_or_explain
from grok_delegate.contracts import validate_task_packet
from grok_delegate.runner import commit_lane_work


class _Result(dict):
    """Shape the runner's git_runner returns."""

    def __init__(self, returncode=0, stdout="", stderr="", timedOut=False):
        super().__init__(returncode=returncode, stdout=stdout, stderr=stderr, timedOut=timedOut)

    def get(self, key, default=None):  # noqa: D102 - dict already does this
        return super().get(key, default)


def _fake_git(script):
    """A git keyed on the verb, so no test here needs a repository."""
    seen: list[list[str]] = []

    def run(args, _cwd, _timeout):
        argv = [str(a) for a in args]
        seen.append(argv)
        for verb, result in script.items():
            if verb in argv:
                return result
        return _Result(returncode=1, stderr="unexpected argv")

    run.seen = seen  # type: ignore[attr-defined]
    return run


# --- why a receipt has no test result -----------------------------------------


def _task(**overrides):
    value = {"test_commands": ["python -m pytest -q"]}
    value.update(overrides)
    return value


def test_a_read_only_role_says_so_rather_than_returning_an_empty_list(tmp_path) -> None:
    tests, reason = _verify_or_explain(
        tmp_path, _task(), threading.Event(), write_role=False, changed=["a.py"]
    )
    assert (tests, reason) == ([], "NOT_A_WRITE_ROLE")


def test_no_changes_is_distinguishable_from_no_tests_run(tmp_path) -> None:
    _tests, reason = _verify_or_explain(
        tmp_path, _task(), threading.Event(), write_role=True, changed=[]
    )
    assert reason == "NO_CHANGES"


def test_a_task_that_declared_no_test_command_says_that(tmp_path) -> None:
    _tests, reason = _verify_or_explain(
        tmp_path, _task(test_commands=[]), threading.Event(), write_role=True, changed=["a.py"]
    )
    assert reason == "NO_TEST_COMMANDS"


def test_an_operator_cancel_skips_the_verifier(tmp_path) -> None:
    cancelled = threading.Event()
    cancelled.set()
    _tests, reason = _verify_or_explain(
        tmp_path, _task(), cancelled, write_role=True, changed=["a.py"]
    )
    assert reason == "CANCELLED"


def test_the_verifier_runs_even_though_the_agent_never_finished(tmp_path) -> None:
    """The case that made `tests` useless: max_turns exhausted, edits on disk.

    Whether the agent finished its turn and whether its code passes are
    different questions. Gating the second on the first threw away the answer
    exactly when the host needed it.
    """
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    tests, reason = _verify_or_explain(
        tmp_path,
        _task(test_commands=[f"{sys.executable} -m pytest -q"], timeout_seconds=120),
        threading.Event(),
        write_role=True,
        changed=["test_sample.py"],
    )
    assert reason is None
    assert [t["source"] for t in tests] == ["bridge-verifier"]
    assert tests[0]["passed"] is True


def test_a_failing_test_is_reported_as_failing(tmp_path) -> None:
    (tmp_path / "test_sample.py").write_text("def test_no():\n    assert False\n", encoding="utf-8")
    tests, reason = _verify_or_explain(
        tmp_path,
        _task(test_commands=[f"{sys.executable} -m pytest -q"], timeout_seconds=120),
        threading.Event(),
        write_role=True,
        changed=["test_sample.py"],
    )
    assert reason is None
    assert tests[0]["passed"] is False
    assert tests[0]["returncode"] != 0


# --- committing the worker's leftovers ----------------------------------------


def test_a_lane_commit_is_made_when_work_is_left_uncommitted(tmp_path) -> None:
    git = _fake_git(
        {
            "status": _Result(stdout=" M app.py\n"),
            "rev-parse": _Result(stdout="grok/lane-1\n"),
            "add": _Result(),
            "commit": _Result(stdout="[grok/lane-1 abc1234] worker output\n"),
        }
    )
    out = commit_lane_work(tmp_path, branch="grok/lane-1", correlation_id="c-1", git_runner=git)
    assert out["committed"] is True
    assert any("commit" in argv for argv in git.seen)


def test_the_bridge_refuses_to_commit_to_anything_but_a_lane(tmp_path) -> None:
    """The one outcome worse than losing the work is committing it to main."""
    git = _fake_git({"status": _Result(stdout=" M app.py\n")})
    out = commit_lane_work(tmp_path, branch="main", correlation_id="c-1", git_runner=git)
    assert out == {"ok": True, "committed": False, "reason": "NOT_A_LANE_BRANCH", "sha": None}
    assert git.seen == []


def test_a_stale_branch_argument_is_not_enough_to_commit(tmp_path) -> None:
    """git is asked what branch this really is; the caller is not believed."""
    git = _fake_git(
        {"status": _Result(stdout=" M app.py\n"), "rev-parse": _Result(stdout="main\n")}
    )
    out = commit_lane_work(tmp_path, branch="grok/lane-1", correlation_id="c-1", git_runner=git)
    assert out["reason"] == "BRANCH_MISMATCH"
    assert not any("commit" in argv for argv in git.seen)


def test_a_clean_worktree_is_left_alone(tmp_path) -> None:
    git = _fake_git({"status": _Result(stdout="")})
    out = commit_lane_work(tmp_path, branch="grok/lane-1", correlation_id="c-1", git_runner=git)
    assert out["reason"] == "NOTHING_TO_COMMIT"
    assert not any("commit" in argv for argv in git.seen)


def test_a_rejected_commit_is_reported_and_never_retried_with_no_verify(tmp_path) -> None:
    git = _fake_git(
        {
            "status": _Result(stdout=" M app.py\n"),
            "rev-parse": _Result(stdout="grok/lane-1\n"),
            "add": _Result(),
            "commit": _Result(returncode=1, stderr="pre-commit hook refused"),
        }
    )
    out = commit_lane_work(tmp_path, branch="grok/lane-1", correlation_id="c-1", git_runner=git)
    assert out == {"ok": False, "committed": False, "reason": "COMMIT_FAILED", "sha": None}
    assert not any("--no-verify" in argv for argv in git.seen)


def test_the_commit_is_attributed_to_the_bridge_without_touching_config(tmp_path) -> None:
    git = _fake_git(
        {
            "status": _Result(stdout=" M app.py\n"),
            "rev-parse": _Result(stdout="grok/lane-1\n"),
            "add": _Result(),
            "commit": _Result(),
        }
    )
    commit_lane_work(tmp_path, branch="grok/lane-1", correlation_id="c-1", git_runner=git)
    commit_argv = next(argv for argv in git.seen if "commit" in argv)
    assert "user.name=grok-delegate" in commit_argv
    assert "config" not in commit_argv


# --- the base a diff is taken against -----------------------------------------


def test_a_moving_base_is_pinned_to_a_commit(tmp_path) -> None:
    """HEAD is the default base and HEAD moves the moment the lane commits."""
    sha = "a" * 40
    git = _fake_git({"rev-parse": _Result(stdout=sha + "\n")})
    assert _resolve_base_sha(tmp_path, "HEAD", git) == sha


def test_an_unresolvable_base_falls_back_to_the_literal_ref(tmp_path) -> None:
    git = _fake_git({"rev-parse": _Result(returncode=128, stderr="unknown revision")})
    assert _resolve_base_sha(tmp_path, "release/1.x", git) == "release/1.x"


# --- end to end: an unfinished job still hands back reviewable work ------------


class _UnfinishedWorker:
    """Writes the artifact, then stops the way an out-of-turns worker stops."""

    name = "stdio"

    def run(self, _task, **kwargs):
        (Path(kwargs["cwd"]) / "expected.txt").write_text("WORK\n", encoding="utf-8")
        return {
            "status": "cancelled",
            "session_id": "unfinished",
            "summary": "ran out of turns mid-review",
            "tests": [],
            "events": [],
            "blocked_reason": "ACP_STOP_cancelled",
            "worker_alive_after_shutdown": False,
        }


def test_an_out_of_turns_job_still_commits_and_still_reports_its_tests(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as raw:
        outer = Path(raw)
        root = outer / "repo"
        root.mkdir()
        lanes = outer / "lanes"
        monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(lanes))

        def git(*args):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)

        git("init", "-b", "master")
        (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        (root / "test_acceptance.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        git("add", ".")
        git("-c", "user.name=T", "-c", "user.email=t@e.invalid", "commit", "-m", "baseline")

        task = validate_task_packet(
            {
                "objective": "leave work behind",
                "role": "execute",
                "project_root": str(root),
                "permission_profile": "workspace",
                "max_turns": 5,
                "timeout_seconds": 120,
                "inputs": [],
                "constraints": [],
                "acceptance_criteria": [],
                "expected_artifacts": ["expected.txt"],
                "correlation_id": "unfinished-job",
                "test_commands": [f"{sys.executable} -m pytest -q"],
            },
            allowed_roots=[root],
        )
        result = agent_runtime.run_task(
            task,
            transport="stdio",
            lane="unfinished",
            router=agent_runtime.TransportRouter(
                grok_bin="grok", adapters={"stdio": _UnfinishedWorker()}
            ),
            cancel_event=threading.Event(),
        )

        # The agent did not finish, and the receipt says so...
        assert result["status"] == "cancelled"
        # ...but it still answers both questions the host would otherwise
        # have to open the repository for.
        assert result["tests_skipped_reason"] is None
        assert result["tests"] and result["tests"][0]["passed"] is True
        assert result["lane_commit"]["committed"] is True
        assert result["commits"], "the work must be reachable as a commit"

        lane_root = Path(result["worktree_path"])
        log = subprocess.run(
            ["git", "-C", str(lane_root), "log", "--oneline", "master..HEAD"],
            capture_output=True,
            text=True,
        )
        assert log.stdout.strip(), "git log base..lane must not be empty"
        assert "expected.txt" in result["changed_files"]
