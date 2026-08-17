"""Two failures found by running a real delegation, not by reading the code.

A job refused to start because the task contract still defaulted `base_ref` to
`master`, a branch this repository no longer has. The receipt then hid the
reason: the envelope said state=error next to error=null, because the verdict
lives inside `result` and the compactor only read the top level.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from grok_delegate.contracts import validate_task_packet
from grok_delegate.economy import compact_job_record


def _packet(root: Path, **overrides):
    value = {
        "objective": "Return a bounded result",
        "role": "consult",
        "project_root": str(root),
        "permission_profile": "read-only",
        "max_turns": 5,
        "timeout_seconds": 10,
        "inputs": [],
        "constraints": [],
        "acceptance_criteria": [],
        "expected_artifacts": [],
        "correlation_id": "base-ref-test",
    }
    value.update(overrides)
    return value


# --- base_ref -----------------------------------------------------------------


def test_base_ref_defaults_to_head_not_a_branch_name() -> None:
    """`master` was wrong the moment a repo renamed its default branch.

    A job would then fail preflight with BASE_UNREACHABLE for a reason that had
    nothing to do with the task it was asked to do.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root), allowed_roots=[root])
        assert task["base_ref"] == "HEAD"


def test_an_explicit_base_ref_is_still_honoured() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root, base_ref="release/1.x"), allowed_roots=[root])
        assert task["base_ref"] == "release/1.x"


def test_the_published_schema_agrees_with_the_runtime() -> None:
    import json

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "grok-task-packet.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["base_ref"]["default"] == "HEAD"


# --- the receipt must say why -------------------------------------------------


def test_compact_receipt_lifts_the_blocked_reason_out_of_result(monkeypatch) -> None:
    """state=error beside error=null is the least useful receipt possible."""
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    record = {
        "job_id": "j1",
        "state": "error",
        "error": None,
        "result": {
            "status": "blocked",
            "blocked_reason": "BASE_UNREACHABLE",
            "changed_files": [],
        },
    }
    out = compact_job_record(record)
    assert out["blocked_reason"] == "BASE_UNREACHABLE"
    assert out["status"] == "blocked"


def test_lifting_never_overwrites_a_value_the_envelope_already_has(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    record = {
        "job_id": "j1",
        "state": "error",
        "blocked_reason": "ENVELOPE_WINS",
        "result": {"blocked_reason": "NESTED_LOSES"},
    }
    assert compact_job_record(record)["blocked_reason"] == "ENVELOPE_WINS"


def test_a_successful_receipt_still_carries_its_evidence(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    record = {
        "job_id": "j1",
        "state": "done",
        "result": {
            "status": "completed",
            "changed_files": ["a.py"],
            "worktree_path": "D:/lanes/x",
            "tests": [{"command": "pytest -q", "passed": True, "returncode": 0, "source": "bridge"}],
        },
    }
    out = compact_job_record(record)
    assert out["changed_files"] == ["a.py"]
    assert out["worktree_path"] == "D:/lanes/x"
    assert out["tests"][0]["passed"] is True


def test_compacting_is_a_no_op_on_a_record_without_result(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record({"job_id": "j1", "state": "running"})
    assert out["state"] == "running"
    assert out["economy_compact"] is True
