"""Every gate that decides "may the host merge this" gets its own test.

A mutation audit removed four of these gates one at a time and the suite stayed
green all four times: nothing asserted TEST_EVIDENCE_MISSING, nothing asserted
either EXPECTED_ARTIFACT_* reason, and nothing checked that acceptance counts
only bridge-verifier results -- a rule this repository states in a comment.

A gate no test can kill is a gate that can be deleted by accident.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from grok_delegate import agent_runtime
from grok_delegate.contracts import finalize_receipt, validate_task_packet


def _task(**overrides):
    value = {
        "objective": "make the change",
        "role": "execute",
        "expected_artifacts": ["expected.txt"],
        "test_commands": ["python -m pytest -q"],
    }
    value.update(overrides)
    return value


def _receipt(**overrides):
    """A receipt that passes every gate, so each test can break exactly one."""
    value = {
        "status": "completed",
        "changed_files": ["expected.txt"],
        "full_changed_files": ["expected.txt"],
        "artifacts": ["expected.txt"],
        "tests": [
            {
                "command": "python -m pytest -q",
                "passed": True,
                "returncode": 0,
                "source": "bridge-verifier",
            }
        ],
    }
    value.update(overrides)
    return value


def test_the_baseline_receipt_is_accepted() -> None:
    """Without this, every test below could pass for the wrong reason."""
    out = finalize_receipt(_receipt(), _task())
    assert out["status"] == "completed"
    assert out["ok"] is True


# --- test evidence -------------------------------------------------------------


def test_no_test_result_is_not_acceptance() -> None:
    out = finalize_receipt(_receipt(tests=[]), _task())
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"
    assert out["ok"] is False


def test_the_agents_own_claim_does_not_satisfy_the_gate() -> None:
    """The rule the live capture earned: a chained command reported exit 0 while
    pytest failed, so the agent's own number is not evidence of anything."""
    agent_claim = [
        {
            "command": "python -m pytest -q",
            "passed": True,
            "returncode": 0,
            "source": "agent-reported",
        }
    ]
    out = finalize_receipt(_receipt(tests=agent_claim), _task())
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"


def test_a_result_for_a_different_command_does_not_count() -> None:
    other = [
        {
            "command": "python -m pytest tests/test_other.py",
            "passed": True,
            "returncode": 0,
            "source": "bridge-verifier",
        }
    ]
    out = finalize_receipt(_receipt(tests=other), _task())
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"


def test_a_pass_with_a_nonzero_return_code_does_not_count() -> None:
    contradictory = [
        {
            "command": "python -m pytest -q",
            "passed": True,
            "returncode": 1,
            "source": "bridge-verifier",
        }
    ]
    out = finalize_receipt(_receipt(tests=contradictory), _task())
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"


def test_one_declared_command_left_unrun_blocks_the_whole_job() -> None:
    task = _task(test_commands=["python -m pytest -q", "python -m pytest tests/slow"])
    out = finalize_receipt(_receipt(), task)
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"


def test_a_failing_test_is_reported_as_failed_not_blocked() -> None:
    failing = [
        {
            "command": "python -m pytest -q",
            "passed": False,
            "returncode": 1,
            "source": "bridge-verifier",
        }
    ]
    out = finalize_receipt(_receipt(tests=failing), _task())
    assert out["status"] == "failed"
    assert out["blocked_reason"] == "TEST_FAILED"


# --- expected artifacts --------------------------------------------------------


def test_an_expected_artifact_that_is_not_on_disk_blocks() -> None:
    out = finalize_receipt(_receipt(artifacts=[]), _task())
    assert out["status"] == "blocked"
    assert out["blocked_reason"].startswith("EXPECTED_ARTIFACT_MISSING")
    assert "expected.txt" in out["blocked_reason"]


def test_an_expected_artifact_this_run_never_touched_blocks() -> None:
    """Present on disk, unchanged by this run: left over, not delivered."""
    task = _task(expected_artifacts=["a.py", "b.py"])
    out = finalize_receipt(
        _receipt(
            changed_files=["a.py"],
            full_changed_files=["a.py"],
            artifacts=["a.py", "b.py"],
        ),
        task,
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "EXPECTED_ARTIFACT_NOT_CHANGED: b.py"


# --- foreign content in the lane ----------------------------------------------


def test_a_file_nobody_asked_for_blocks_the_job() -> None:
    out = finalize_receipt(
        _receipt(full_changed_files=["expected.txt", "attacker.py"]), _task()
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "UNEXPECTED_CHANGED_FILES: attacker.py"


def test_a_run_that_changed_nothing_still_reports_foreign_content() -> None:
    """The short-circuit that let a rejected job quietly seed the lane.

    `no_changes` returned before the unexpected-files check ran, so a noop run
    sitting on someone else's file reported only "nothing happened" -- and the
    bridge committed that file to the branch anyway.
    """
    out = finalize_receipt(
        _receipt(changed_files=[], full_changed_files=["attacker.py"], artifacts=[]),
        _task(),
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "UNEXPECTED_CHANGED_FILES: attacker.py"
    assert out["ok"] is False


def test_a_genuinely_empty_run_still_reports_no_changes() -> None:
    out = finalize_receipt(
        _receipt(changed_files=[], full_changed_files=[], artifacts=[]), _task()
    )
    assert out["status"] == "no_changes"
    assert out["blocked_reason"] == "EXECUTE_NO_CHANGES"


# --- the base a reused lane is judged against ---------------------------------


class _NoopWorker:
    name = "stdio"

    def run(self, _task, **_kwargs):
        return {
            "status": "completed",
            "session_id": "noop",
            "summary": "did nothing",
            "tests": [],
            "events": [],
            "worker_alive_after_shutdown": False,
        }


def test_a_reused_lane_cannot_hide_what_an_earlier_job_left(monkeypatch) -> None:
    """Second half of the same defect, and the more dangerous half.

    `base_ref` defaults to HEAD. Resolved inside the worktree, HEAD on a reused
    lane is the previous job's commit, so anything an earlier job committed --
    including a file a receipt had already refused -- dropped out of the diff and
    the next receipt came back clean with it still on the branch.
    """
    with tempfile.TemporaryDirectory() as raw:
        outer = Path(raw)
        root = outer / "repo"
        root.mkdir()
        monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(outer / "lanes"))

        def git(*args):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)

        git("init", "-q", "-b", "main")
        (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        (root / "test_acceptance.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        git("add", "-A")
        git("-c", "user.name=T", "-c", "user.email=t@e.invalid", "commit", "-q", "-m", "seed")

        def run_once(correlation: str):
            task = validate_task_packet(
                {
                    "objective": "noop",
                    "role": "execute",
                    "project_root": str(root),
                    "permission_profile": "workspace",
                    "max_turns": 5,
                    "timeout_seconds": 120,
                    "inputs": [],
                    "constraints": [],
                    "acceptance_criteria": [],
                    "expected_artifacts": ["expected.txt"],
                    "correlation_id": correlation,
                    "test_commands": [f"{sys.executable} -m pytest -q"],
                },
                allowed_roots=[root],
            )
            return agent_runtime.run_task(
                task,
                transport="stdio",
                lane="reuse-probe",
                router=agent_runtime.TransportRouter(
                    grok_bin="grok", adapters={"stdio": _NoopWorker()}
                ),
                cancel_event=threading.Event(),
            )

        first = run_once("reuse-one")
        lane_root = Path(first["worktree_path"])
        # Something the worker never asked for, left in the lane.
        (lane_root / "attacker.py").write_text("STALE = True\n", encoding="utf-8")

        second = run_once("reuse-two")
        assert "attacker.py" in second["full_changed_files"], (
            "a file left in the lane must stay visible to the next job's receipt"
        )
        assert second["status"] == "blocked"
        assert "attacker.py" in second["blocked_reason"]


# --- the verifier is not allowed to certify itself -----------------------------


def test_an_artifact_the_verifier_wrote_is_not_delivered_work() -> None:
    """A test suite that creates the expected file would otherwise pass the job.

    Acceptance is read after the verifier on purpose, so a test that reverts the
    artifact fails. The same ordering means a test that *creates* it looks like
    the worker did -- unless the two snapshots are compared.
    """
    out = finalize_receipt(
        _receipt(verifier_touched_files=["expected.txt"]), _task()
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "ARTIFACT_WRITTEN_BY_VERIFIER: expected.txt"


def test_the_verifier_touching_its_own_scratch_files_is_fine() -> None:
    out = finalize_receipt(
        _receipt(verifier_touched_files=[".pytest_cache/lastfailed"]), _task()
    )
    assert out["status"] == "completed"


class _WorkerThatDeliversHalf:
    """Writes one expected artifact and leaves the other to the test suite."""

    name = "stdio"

    def run(self, _task, **kwargs):
        (Path(kwargs["cwd"]) / "a.txt").write_text("FROM_WORKER\n", encoding="utf-8")
        return {
            "status": "completed",
            "session_id": "half",
            "summary": "wrote a.txt",
            "tests": [],
            "events": [],
            "worker_alive_after_shutdown": False,
        }


def test_end_to_end_a_test_that_creates_the_artifact_does_not_pass_the_job(monkeypatch) -> None:
    """Live shape of the defect: the worker delivers half, the suite the rest.

    The verifier only runs when the worker changed something, so the case that
    matters is a partial delivery -- and there the test's own output used to be
    credited to the worker, because acceptance compares against the tree from
    before the worker ran, not from before the tests ran.
    """
    with tempfile.TemporaryDirectory() as raw:
        outer = Path(raw)
        root = outer / "repo"
        root.mkdir()
        monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(outer / "lanes"))

        def git(*args):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)

        git("init", "-q", "-b", "main")
        (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        (root / "test_acceptance.py").write_text(
            "from pathlib import Path\n\n\ndef test_ok():\n"
            "    Path('b.txt').write_text('FROM_TEST', encoding='utf-8')\n"
            "    assert True\n",
            encoding="utf-8",
        )
        git("add", "-A")
        git("-c", "user.name=T", "-c", "user.email=t@e.invalid", "commit", "-q", "-m", "seed")

        task = validate_task_packet(
            {
                "objective": "produce a.txt and b.txt",
                "role": "execute",
                "project_root": str(root),
                "permission_profile": "workspace",
                "max_turns": 5,
                "timeout_seconds": 120,
                "inputs": [],
                "constraints": [],
                "acceptance_criteria": [],
                "expected_artifacts": ["a.txt", "b.txt"],
                "correlation_id": "verifier-authored",
                "test_commands": [f"{sys.executable} -m pytest -q"],
            },
            allowed_roots=[root],
        )
        receipt = agent_runtime.run_task(
            task,
            transport="stdio",
            lane="verifier-authored",
            router=agent_runtime.TransportRouter(
                grok_bin="grok", adapters={"stdio": _WorkerThatDeliversHalf()}
            ),
            cancel_event=threading.Event(),
        )

        assert "b.txt" in receipt["verifier_touched_files"]
        assert "a.txt" not in receipt["verifier_touched_files"], "the worker wrote a.txt"
        assert receipt["status"] == "blocked"
        assert receipt["blocked_reason"] == "ARTIFACT_WRITTEN_BY_VERIFIER: b.txt"
        assert receipt["ok"] is False
