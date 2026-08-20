""""The suite is red" and "the suite never finished" are different facts.

A single boolean could not tell them apart. A cancel or a timeout leaves
`returncode` None, and `passed = returncode == 0 and ...` then reported False --
identical on the wire to a genuine failure. A live job did exactly that: four
files changed, fifty-one tests passing in the lane when run by hand, and the
receipt saying the verifier failed. An orchestrator that counts two such
failures sends finished work to `blocked`.

So a verifier row now carries `outcome`, and `passed` survives only where it
means something: absent when the run did not happen, rather than false.
"""

from __future__ import annotations

import threading

import pytest

from grok_delegate import agent_runtime
from grok_delegate.contracts import finalize_receipt

COMMAND = "py -3 -m pytest tests -q"


def _run(monkeypatch, outcome: dict) -> dict:
    """One verifier row, with the process result the runner would have seen."""
    monkeypatch.setattr(agent_runtime, "validated_test_argv", lambda c, cwd: ["py", "-3"])
    monkeypatch.setattr(agent_runtime, "_run_owned_process", lambda *a, **k: outcome)
    rows = agent_runtime._run_bridge_tests(
        cwd=None, task={"test_commands": [COMMAND], "timeout_seconds": 60},
        cancel_event=threading.Event(),
    )
    assert len(rows) == 1
    return rows[0]


def test_a_cancelled_run_is_not_a_failed_one(monkeypatch) -> None:
    row = _run(monkeypatch, {"returncode": None, "cancelled": True, "timedOut": False,
                             "stdout": "", "stderr": ""})
    assert row["outcome"] == "not_run"
    assert row["not_run_reason"] == "cancelled"
    assert "passed" not in row, "an absent boolean is the point; false is the bug"


def test_a_timed_out_run_is_not_a_failed_one(monkeypatch) -> None:
    row = _run(monkeypatch, {"returncode": None, "cancelled": False, "timedOut": True,
                             "stdout": "", "stderr": ""})
    assert row["outcome"] == "not_run"
    assert row["not_run_reason"] == "timeout"
    assert "passed" not in row


def test_a_red_suite_is_still_a_red_suite(monkeypatch) -> None:
    row = _run(monkeypatch, {"returncode": 1, "cancelled": False, "timedOut": False,
                             "stdout": "1 failed", "stderr": ""})
    assert row["outcome"] == "failed"
    assert row["passed"] is False


def test_a_green_suite_still_says_so(monkeypatch) -> None:
    row = _run(monkeypatch, {"returncode": 0, "cancelled": False, "timedOut": False,
                             "stdout": "51 passed", "stderr": ""})
    assert row["outcome"] == "passed"
    assert row["passed"] is True


def test_a_command_the_gate_refuses_was_never_run(monkeypatch) -> None:
    """Refusing to run something is not evidence that it fails."""
    def refuse(_command, _cwd):
        raise ValueError("not a declared command")

    monkeypatch.setattr(agent_runtime, "validated_test_argv", refuse)
    rows = agent_runtime._run_bridge_tests(
        cwd=None, task={"test_commands": [COMMAND]}, cancel_event=threading.Event()
    )
    assert rows[0]["outcome"] == "not_run"
    assert rows[0]["not_run_reason"] == "invalid_command"
    assert "passed" not in rows[0]


# --- what the acceptance gate now does with it -----------------------------------


def _receipt(tests: list[dict]) -> dict:
    return {
        "status": "completed", "changed_files": ["app.py"], "full_changed_files": ["app.py"],
        "artifacts": ["app.py"], "worker_written_files": ["app.py"], "tests": tests,
        "lane_commit": {"ok": True, "committed": True, "reason": None, "sha": "abc1234"},
    }


_TASK = {"role": "execute", "objective": "x", "expected_artifacts": ["app.py"],
         "test_commands": [COMMAND]}


def test_a_run_that_never_happened_is_not_reported_as_a_red_suite() -> None:
    """It is missing evidence, which is a different verdict with a different fix."""
    out = finalize_receipt(
        _receipt([{"command": COMMAND, "outcome": "not_run", "not_run_reason": "cancelled",
                   "returncode": None, "source": "bridge-verifier"}]),
        _TASK,
    )
    assert out["blocked_reason"] != "TEST_FAILED"
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"


def test_a_genuinely_failing_run_is_still_TEST_FAILED() -> None:
    out = finalize_receipt(
        _receipt([{"command": COMMAND, "outcome": "failed", "passed": False,
                   "returncode": 1, "source": "bridge-verifier"}]),
        _TASK,
    )
    assert out["blocked_reason"] == "TEST_FAILED"


def test_an_old_shaped_row_without_outcome_still_judges_the_same() -> None:
    """Receipts predating this field must not change meaning under it."""
    assert finalize_receipt(
        _receipt([{"command": COMMAND, "passed": False, "returncode": 1,
                   "source": "bridge-verifier"}]), _TASK,
    )["blocked_reason"] == "TEST_FAILED"
    assert finalize_receipt(
        _receipt([{"command": COMMAND, "passed": True, "returncode": 0,
                   "source": "bridge-verifier"}]), _TASK,
    )["status"] == "completed"


# --- and it has to survive the trip to the host ----------------------------------


def test_the_compact_poll_carries_the_outcome(monkeypatch) -> None:
    """A compact poll that kept only `passed` would hand the host `passed: None`
    for a run that never happened -- the same ambiguity in a new place."""
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record({
        "ok": True, "job_id": "j", "state": "done",
        "result": {"status": "completed", "tests": [
            {"command": COMMAND, "outcome": "not_run", "not_run_reason": "cancelled",
             "returncode": None, "source": "bridge-verifier"},
        ]},
    })
    row = out["tests"][0]
    assert row["outcome"] == "not_run"
    assert row["not_run_reason"] == "cancelled"
    assert "passed" not in row


def test_the_compact_poll_still_carries_passed_when_there_is_one(monkeypatch) -> None:
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record({
        "ok": True, "job_id": "j", "state": "done",
        "result": {"status": "completed", "tests": [
            {"command": COMMAND, "outcome": "failed", "passed": False,
             "returncode": 1, "source": "bridge-verifier"},
        ]},
    })
    assert out["tests"][0]["passed"] is False


def test_denied_tool_calls_reaches_the_host(monkeypatch) -> None:
    """On this CLI a refused write ends the turn, so the count is often the only
    trace of why a job stopped early. A receipt that dropped it explains nothing."""
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record({
        "ok": True, "job_id": "j", "state": "done",
        "result": {"status": "completed", "denied_tool_calls": 3, "summary": "x"},
    })
    assert out["denied_tool_calls"] == 3


def test_a_refusal_is_counted_and_an_allow_is_not() -> None:
    """Read off the option the gate actually selected, so the count cannot
    disagree with what was sent back."""
    from grok_delegate.acp import _is_refusal

    options = [{"optionId": "a", "kind": "allow_once"},
               {"optionId": "r", "kind": "reject_once"}]
    assert _is_refusal({"options": options}, {"outcome": "selected", "optionId": "r"}) is True
    assert _is_refusal({"options": options}, {"outcome": "selected", "optionId": "a"}) is False
    # No option to refuse with is still the worker not getting what it asked for.
    assert _is_refusal({"options": []}, {"outcome": "cancelled"}) is True
