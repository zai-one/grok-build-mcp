"""Whose change is it? The gate used to answer "the worker's" for everything.

Found by running a real execute job against a scratch repository: it came back
`blocked` with `UNEXPECTED_CHANGED_FILES` naming `__pycache__/*.pyc` and
`outputs/ads/logs/runtime.log`. The pyc files were the byproduct of the test run
the bridge itself had told the worker to make. The log belonged to a different
MCP server the Grok CLI had configured, which creates it relative to its working
directory -- and its working directory is the lane. The worker had touched
neither, and every write job on that machine was blocked because of them.

The bridge already knows which files are the worker's: it approved each write
itself. It just never wrote that down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grok_delegate.acp import approved_write_paths
from grok_delegate.contracts import finalize_receipt

ALLOW = {"kind": "allow_once", "optionId": "A"}
REJECT = {"kind": "reject_once", "optionId": "R"}


def _params(kind: str, raw: dict) -> dict:
    return {"options": [ALLOW, REJECT], "toolCall": {"kind": kind, "rawInput": raw}}


def _task(**over) -> dict:
    task = {
        "role": "execute",
        "objective": "x",
        "expected_artifacts": ["app.py"],
        "test_commands": ["pytest -q"],
    }
    task.update(over)
    return task


def _receipt(**over) -> dict:
    receipt = {
        "status": "completed",
        "changed_files": ["app.py"],
        "full_changed_files": ["app.py"],
        "artifacts": ["app.py"],
        "tests": [{"source": "bridge-verifier", "command": "pytest -q",
                   "passed": True, "returncode": 0}],
    }
    receipt.update(over)
    return receipt


# --- what the gate learns from its own decisions ---------------------------------


def test_an_approved_write_names_the_file(tmp_path) -> None:
    got = approved_write_paths(
        _params("write", {"path": str(tmp_path / "src" / "app.py")}),
        {"outcome": "selected", "optionId": "A"},
        tmp_path,
    )
    assert got == ["src/app.py"]


def test_a_refused_write_names_nothing(tmp_path) -> None:
    assert approved_write_paths(
        _params("write", {"path": str(tmp_path / "app.py")}),
        {"outcome": "selected", "optionId": "R"},
        tmp_path,
    ) == []


def test_a_cancelled_decision_names_nothing(tmp_path) -> None:
    assert approved_write_paths(
        _params("write", {"path": str(tmp_path / "app.py")}), {"outcome": "cancelled"}, tmp_path
    ) == []


@pytest.mark.parametrize("kind", ["read", "search", "execute", "think"])
def test_only_writes_count_as_authorship(kind: str, tmp_path) -> None:
    """Running a test is not writing its `__pycache__`, even when it produces it."""
    assert approved_write_paths(
        _params(kind, {"path": str(tmp_path / "app.py"), "command": "pytest -q"}),
        {"outcome": "selected", "optionId": "A"},
        tmp_path,
    ) == []


def test_a_path_outside_the_worktree_is_not_recorded(tmp_path) -> None:
    assert approved_write_paths(
        _params("write", {"path": str(tmp_path.parent / "elsewhere.txt")}),
        {"outcome": "selected", "optionId": "A"},
        tmp_path,
    ) == []


# --- what the acceptance gate does with it ---------------------------------------


def test_a_file_the_worker_never_wrote_does_not_block_it() -> None:
    """The exact shape that blocked every execute job on a real machine."""
    out = finalize_receipt(
        _receipt(
            full_changed_files=["app.py", "__pycache__/app.pyc", "outputs/ads/logs/runtime.log"],
            worker_written_files=["app.py"],
        ),
        _task(),
    )
    assert out["status"] == "completed", out.get("blocked_reason")
    assert out["foreign_changed_files"] == ["__pycache__/app.pyc", "outputs/ads/logs/runtime.log"]


def test_the_operator_is_still_told_the_lane_is_not_only_their_work() -> None:
    """Not blocking is not the same as hiding; they still merge this branch."""
    out = finalize_receipt(
        _receipt(full_changed_files=["app.py", "stray.txt"], worker_written_files=["app.py"]),
        _task(),
    )
    assert "stray.txt" in out["foreign_changed_files"]


def test_a_file_the_worker_did_write_and_was_not_asked_for_still_blocks() -> None:
    """The gate's whole reason to exist has to survive the fix."""
    out = finalize_receipt(
        _receipt(
            changed_files=["app.py", "secrets.py"],
            full_changed_files=["app.py", "secrets.py"],
            worker_written_files=["app.py", "secrets.py"],
        ),
        _task(),
    )
    assert out["status"] == "blocked"
    assert "UNEXPECTED_CHANGED_FILES" in out["blocked_reason"]
    assert "secrets.py" in out["blocked_reason"]


def test_with_no_attribution_at_all_everything_is_judged() -> None:
    """An older transport reports nothing; silence must not read as innocence."""
    out = finalize_receipt(
        _receipt(full_changed_files=["app.py", "stray.txt"], worker_written_files=[]),
        _task(),
    )
    assert out["status"] == "blocked"
    assert "stray.txt" in out["blocked_reason"]


def test_a_job_that_changed_nothing_but_sits_on_foreign_files_is_not_blocked() -> None:
    """It used to be, and the files were never the worker's to answer for."""
    out = finalize_receipt(
        _receipt(
            changed_files=[],
            full_changed_files=["outputs/ads/logs/runtime.log"],
            artifacts=[],
            worker_written_files=[],
            **{},
        ),
        _task(),
    )
    # No attribution here, so the old judgement stands -- this pins the fallback
    # rather than the fix, and says so.
    assert out["status"] == "blocked"


def test_the_verifier_check_is_untouched_by_attribution() -> None:
    """A test that writes the artifact still fails; that gate is separate."""
    out = finalize_receipt(
        _receipt(
            verifier_touched_files=["app.py"],
            worker_written_files=["app.py"],
        ),
        _task(),
    )
    assert out["status"] == "blocked"
    assert "ARTIFACT_WRITTEN_BY_VERIFIER" in out["blocked_reason"]
