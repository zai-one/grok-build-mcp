"""Findings from the 2026-08-19 sweep that survived an attempt to reproduce them.

Ten claims went into the attempt and five came out. That ratio is the point:
three of the discards were things the previous audit had carried as known
backlog for a month, and fixing them would have been changing working code on
the strength of a plausible story.

Two of the first verdicts were wrong in the other direction -- a probe that
called `start_job` with the wrong signature reported "not reproduced" when the
bug was real, and a probe comparing the first textual occurrence of two names
reported the same about ordering. A verification harness is as capable of being
wrong as the claim it checks, and "not reproduced" deserves the same suspicion
as "confirmed".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from grok_delegate import jobs
from grok_delegate.acp import _command_allowed, _paths_confined, _win_name
from grok_delegate.contracts import finalize_receipt


# --- P3: the denylist read the name that was typed, not the one Windows opens ---


@pytest.fixture()
def root(tmp_path) -> Path:
    return tmp_path.resolve()


@pytest.mark.parametrize(
    "name",
    [
        "auth.json", "auth.json.", "auth.json ", "auth.json...",
        ".env", ".env.", ".env ", ".env::$DATA", ".env.local",
        "credentials.json.", ".npmrc.", ".pypirc ",
        "key.pem.", "id.pem", "cert.pfx ", "x.p12.", "sub/.env",
    ],
)
def test_a_decorated_secret_name_is_still_that_secret(name: str, root: Path) -> None:
    """Win32 strips trailing dots and spaces, and `::` names a stream.

    `auth.json.`, `auth.json ` and `.env::$DATA` all opened exactly the file the
    denylist exists to refuse, and all three were accepted.
    """
    assert _paths_confined({"path": name}, root) is False, name


@pytest.mark.parametrize(
    "name",
    ["app.py", "notes.txt", "src/main.py", "tests/test_env.py", "environment.py",
     "docs/pemberton.md", "envoy.conf"],
)
def test_an_ordinary_file_is_not_caught_by_the_widened_check(name: str, root: Path) -> None:
    assert _paths_confined({"path": name}, root) is True, name


def test_the_normalised_name_is_what_windows_would_open() -> None:
    assert _win_name("auth.json.") == "auth.json"
    assert _win_name("auth.json ") == "auth.json"
    assert _win_name(".env::$DATA") == ".env"
    assert _win_name("C:") == "c:", "a drive letter is not a stream"


# --- P2: `C:secret.py` is relative to a directory that is not the worktree -------


def test_a_drive_relative_path_in_a_command_is_refused(root: Path) -> None:
    """`C:foo` means "foo in this process's directory on C:", which is not here.

    The revision-splitting rule skipped `C:\\` and `C:/` only, so the bare-colon
    form was reduced to `secret.py` and judged as an ordinary local name.
    """
    assert _command_allowed("py -3 -m pytest C:secret.py", root) is False


@pytest.mark.skipif(os.name != "nt", reason="a drive letter only means a drive on Windows")
def test_an_absolute_drive_path_is_still_judged_as_a_path(root: Path) -> None:
    """Windows-only on purpose, and CI is why.

    `C:\\outside\\secret.py` is an absolute path on Windows and an ordinary
    relative filename on POSIX, where a backslash is just a character. Asserting
    the Windows answer everywhere failed on Linux against code that was right --
    the test was making a claim about the platform, not about the gate.
    """
    assert _command_allowed("py -3 -m pytest C:\\outside\\secret.py", root) is False


def test_an_ordinary_relative_test_path_still_runs(root: Path) -> None:
    (root / "tests").mkdir(exist_ok=True)
    assert _command_allowed("py -3 -m pytest tests", root) is True


# --- C1: a handoff that fails left a job that could never finish ----------------


def test_a_failed_handoff_terminalises_the_record() -> None:
    """`_evict_locked` drops only terminal records, so this one stayed forever:
    LANE_BUSY kept seeing it and cancel answered JOB_NOT_OWNED."""
    def refuses(_fn):
        raise RuntimeError("executor is shut down")

    jobs._JOBS.clear()
    with pytest.raises(RuntimeError):
        jobs.start_job(lambda: {"ok": True}, lane="grok/x", tool="grok_agent_execute",
                       job_id="job-zombie", thread_starter=refuses)
    record = jobs.get_job("job-zombie")
    assert record is not None, "the record should stay, so the failure is visible"
    assert record["state"] != jobs.STATE_RUNNING
    assert "executor is shut down" in str(record.get("error"))


def test_the_caller_still_sees_the_failure() -> None:
    """Terminalising must not swallow it: the caller has to know the job never ran."""
    jobs._JOBS.clear()
    with pytest.raises(RuntimeError):
        jobs.start_job(lambda: None, job_id="job-raise",
                       thread_starter=lambda _fn: (_ for _ in ()).throw(RuntimeError("no")))


# --- C5: a tombstone is not work in progress ------------------------------------


def test_an_unknown_record_can_be_forgotten() -> None:
    jobs._JOBS.clear()
    jobs._JOBS["job-dead"] = {"job_id": "job-dead", "state": "unknown"}
    assert jobs.forget_job("job-dead") is True
    assert jobs.get_job("job-dead") is None


def test_forgetting_something_that_is_not_there_is_not_an_error() -> None:
    assert jobs.forget_job("job-never-existed") is False


# --- G2: work nobody can review is not delivered work ---------------------------


def _receipt(**over) -> dict:
    receipt = {
        "status": "completed",
        "changed_files": ["app.py"],
        "full_changed_files": ["app.py"],
        "artifacts": ["app.py"],
        "worker_written_files": ["app.py"],
        "tests": [{"source": "bridge-verifier", "command": "pytest -q",
                   "passed": True, "returncode": 0}],
        "lane_commit": {"ok": True, "committed": True, "reason": None, "sha": "abc1234"},
    }
    receipt.update(over)
    return receipt


_TASK = {"role": "execute", "objective": "x", "expected_artifacts": ["app.py"],
         "test_commands": ["pytest -q"]}


def test_a_lane_commit_that_failed_is_not_a_completed_job() -> None:
    """A rejecting hook or a read-only checkout used to read as success with
    `sha: None` and nothing on the branch to merge."""
    out = finalize_receipt(
        _receipt(lane_commit={"ok": False, "committed": False,
                              "reason": "COMMIT_FAILED", "sha": None}),
        _TASK,
    )
    assert out["status"] == "blocked"
    assert "LANE_COMMIT_MISSING" in out["blocked_reason"]
    assert "COMMIT_FAILED" in out["blocked_reason"]


def test_a_real_commit_still_passes() -> None:
    assert finalize_receipt(_receipt(), _TASK)["status"] == "completed"


def test_a_read_only_role_is_not_asked_for_a_commit() -> None:
    """`NOT_A_WRITE_ROLE` is the honest absence of a commit, not a failed one.

    Judged on a read-only packet, which is what the name says. It used to be
    judged on `_TASK` -- an execute packet -- so what it actually asserted was
    that a *write* job may skip its commit by writing `NOT_A_WRITE_ROLE` into
    the field, and that is now refused.
    """
    read_only_task = {
        **_TASK,
        "role": "skeptic",
        "permission_profile": "read-only",
        "expected_artifacts": [],
        "test_commands": [],
    }
    out = finalize_receipt(
        _receipt(lane_commit={"ok": True, "committed": False,
                              "reason": "NOT_A_WRITE_ROLE", "sha": None}),
        read_only_task,
    )
    assert out["status"] == "completed"


def test_a_write_role_cannot_claim_the_read_only_exemption() -> None:
    """The other half, which nothing asserted: an execute job with no commit
    and that reason in the field was accepted."""
    out = finalize_receipt(
        _receipt(lane_commit={"ok": True, "committed": False,
                              "reason": "NOT_A_WRITE_ROLE", "sha": None}),
        _TASK,
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "LANE_COMMIT_MISSING: NOT_A_WRITE_ROLE"


# --- C2/C3: one lane, one worker, however many jobs the registry holds ----------


def test_a_busy_lane_is_found_past_the_page_size() -> None:
    """The check asked `list_jobs(limit=64)`, which is a newest-first page.

    With more running jobs than that, the oldest fell off the end and its
    worktree read as free -- and two workers in one checkout is the single thing
    this check exists to prevent.
    """
    import time

    jobs._JOBS.clear()
    for i in range(70):
        jobs._JOBS[f"j{i:03d}"] = {
            "job_id": f"j{i:03d}", "lane": f"grok/lane-{i:03d}", "state": jobs.STATE_RUNNING,
            "started_at": time.time() + i, "server_pid": 1, "tool": "x",
        }
    assert jobs.lane_is_busy("grok/lane-000") is not None, "the oldest lane is still taken"
    assert jobs.lane_is_busy("grok/lane-069") is not None
    assert jobs.lane_is_busy("grok/never-used") is None


def test_a_finished_job_does_not_hold_its_lane() -> None:
    jobs._JOBS.clear()
    jobs._JOBS["j"] = {"job_id": "j", "lane": "grok/done", "state": "done", "server_pid": 1}
    assert jobs.lane_is_busy("grok/done") is None


def test_an_empty_lane_name_matches_nothing() -> None:
    jobs._JOBS.clear()
    jobs._JOBS["j"] = {"job_id": "j", "lane": "", "state": jobs.STATE_RUNNING, "server_pid": 1}
    assert jobs.lane_is_busy("") is None


def test_the_registry_records_the_name_the_agent_will_look_for() -> None:
    """`grok_delegate_start` stored the raw lane and the agent tools ask for the
    normalised one, so `foo` and `grok/foo` were two lanes to the check and one
    worktree on disk."""
    from grok_delegate.guard import normalize_lane

    assert str(normalize_lane("foo")) == "grok/foo"
    jobs._JOBS.clear()
    jobs._JOBS["j"] = {"job_id": "j", "lane": str(normalize_lane("foo")),
                       "state": jobs.STATE_RUNNING, "server_pid": 1}
    assert jobs.lane_is_busy("grok/foo") is not None


# --- G3: the legacy transport cannot produce a false success -------------------


def test_a_write_receipt_with_no_verifier_evidence_is_blocked() -> None:
    """`transport: legacy` sets none of verifier_touched_files, lane_commit or
    worker_written_files. Confirmed -- and harmless, because a write role with no
    bridge-verifier test is already refused. Pinned so it stays that way."""
    out = finalize_receipt(
        {"status": "completed", "changed_files": ["app.py"], "full_changed_files": ["app.py"],
         "artifacts": ["app.py"], "tests": []},
        _TASK,
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"


def test_a_test_the_agent_says_it_ran_is_not_evidence() -> None:
    out = finalize_receipt(
        {"status": "completed", "changed_files": ["app.py"], "full_changed_files": ["app.py"],
         "artifacts": ["app.py"],
         "tests": [{"source": "agent-reported", "command": "pytest -q",
                    "passed": True, "returncode": 0}]},
        _TASK,
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"
