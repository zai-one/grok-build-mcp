"""Two things a job was getting wrong while looking fine.

The lane commit swept the whole tree with `git add -A`, so a branch a human is
meant to review carried a foreign MCP server's log and two `__pycache__` blobs
-- files the acceptance gate had already named as somebody else's work. Judging
them foreign and committing them anyway was the bridge disagreeing with itself.

And a job that had gone quiet looked exactly like a job that was thinking. That
is the shape a provider outage takes here: the CLI answers `500 ... at capacity`
by retrying internally, says nothing over ACP, and an operator with no other
signal cancels work that was only waiting its turn.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from grok_delegate import worker_health
from grok_delegate.runner import commit_lane_work
from grok_delegate.server import _annotate_silence


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)


@pytest.fixture()
def lane(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@e.invalid", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    _git(root, "checkout", "-q", "-b", "grok/lane")
    return root


def _committed(root: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


# --- the lane carries the worker's work, and only that ---------------------------


def test_only_attributed_paths_reach_the_branch(lane: Path) -> None:
    (lane / "app.py").write_text("x = 2\n", encoding="utf-8")
    (lane / "junk.pyc").write_bytes(b"\x00")
    (lane / "outputs").mkdir()
    (lane / "outputs" / "someone-else.log").write_text("", encoding="utf-8")

    out = commit_lane_work(lane, branch="grok/lane", correlation_id="c", paths=["app.py"])

    assert out["committed"] is True, out
    assert _committed(lane) == {"app.py"}


def test_without_attribution_the_old_sweep_stands(lane: Path) -> None:
    """No attribution means the gate judges every path, so the sweep is safe."""
    (lane / "app.py").write_text("x = 2\n", encoding="utf-8")
    (lane / "extra.txt").write_text("hi\n", encoding="utf-8")

    commit_lane_work(lane, branch="grok/lane", correlation_id="c", paths=None)

    assert _committed(lane) == {"app.py", "extra.txt"}


def test_a_deletion_of_an_attributed_file_is_committed(lane: Path) -> None:
    (lane / "app.py").unlink()
    out = commit_lane_work(lane, branch="grok/lane", correlation_id="c", paths=["app.py"])
    assert out["committed"] is True
    assert not (lane / "app.py").exists()


def test_when_the_filter_leaves_nothing_it_says_so(lane: Path) -> None:
    """Committing an empty change and calling it work is the same old lie."""
    (lane / "stray.txt").write_text("not mine\n", encoding="utf-8")
    out = commit_lane_work(lane, branch="grok/lane", correlation_id="c", paths=["app.py"])
    assert out["committed"] is False
    assert out["reason"] == "NOTHING_TO_COMMIT"
    assert out["sha"] is None


def test_a_branch_that_is_not_a_lane_is_still_refused(lane: Path) -> None:
    _git(lane, "checkout", "-q", "main")
    out = commit_lane_work(lane, branch="main", correlation_id="c", paths=["app.py"])
    assert out["committed"] is False
    assert out["reason"] == "NOT_A_LANE_BRANCH"


# --- why the job went quiet ------------------------------------------------------


def _log(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "unified.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    return path


CAPACITY = (
    "API error (status 500 Internal Server Error): error: The model is currently at "
    "capacity due to high demand. Please try again in a few minutes."
)


def test_the_provider_being_full_is_named_as_such(tmp_path, monkeypatch) -> None:
    """Observed for real: six projects on one machine, same hour, same message."""
    path = _log(
        tmp_path,
        {"pid": 42, "lvl": "warn", "msg": "shell.turn.inference_retry", "ts": "2026-08-19T21:24:14Z",
         "ctx": {"attempt": 1, "max_retries": 15, "reason": CAPACITY}},
        {"pid": 42, "lvl": "error", "msg": "shell.turn.inference_failed", "ts": "2026-08-19T21:24:53Z",
         "ctx": {}},
    )
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(path))
    out = worker_health.diagnose_worker(42)
    assert out["reason"] == "PROVIDER_AT_CAPACITY"
    assert out["attempt"] == 1 and out["max_retries"] == 15
    assert "capacity" in out["detail"]


def test_the_last_record_is_not_always_the_one_that_explains(tmp_path, monkeypatch) -> None:
    """`inference_failed` carries no context; taking the newest lost the reason."""
    path = _log(
        tmp_path,
        {"pid": 7, "lvl": "warn", "msg": "shell.turn.inference_retry", "ts": "1",
         "ctx": {"reason": CAPACITY}},
        {"pid": 7, "lvl": "error", "msg": "shell.turn.inference_failed", "ts": "2", "ctx": {}},
    )
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(path))
    assert "capacity" in worker_health.diagnose_worker(7)["detail"]


def test_another_process_is_not_our_process(tmp_path, monkeypatch) -> None:
    """One log, every Grok session on the machine."""
    path = _log(tmp_path, {"pid": 1, "lvl": "warn", "msg": "retry", "ctx": {"reason": CAPACITY}})
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(path))
    assert worker_health.diagnose_worker(2) is None


@pytest.mark.parametrize("pid", [None, "", "abc", -0.5])
def test_a_pid_we_cannot_use_is_no_answer(pid, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(_log(tmp_path, {"pid": 1})))
    assert worker_health.diagnose_worker(pid) is None


def test_the_switch_turns_it_off(tmp_path, monkeypatch) -> None:
    path = _log(tmp_path, {"pid": 9, "lvl": "warn", "msg": "retry", "ctx": {"reason": CAPACITY}})
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(path))
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG", "0")
    assert worker_health.diagnose_worker(9) is None


def test_a_file_that_is_not_there_is_no_answer_not_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(tmp_path / "absent.jsonl"))
    assert worker_health.diagnose_worker(1) is None


def test_garbage_lines_do_not_raise(tmp_path, monkeypatch) -> None:
    """Another program owns this format; it owes us nothing."""
    path = tmp_path / "unified.jsonl"
    path.write_text("not json\n{\"broken\": \n\x00\n", encoding="utf-8")
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(path))
    assert worker_health.diagnose_worker(1) is None


def test_an_informational_record_is_not_a_diagnosis(tmp_path, monkeypatch) -> None:
    path = _log(tmp_path, {"pid": 3, "lvl": "info", "msg": "shell.turn.start", "ctx": {}})
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(path))
    assert worker_health.diagnose_worker(3) is None


# --- the poll says how long the silence has lasted -------------------------------


def _record(seconds_ago: float, state: str = "running") -> dict:
    when = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return {
        "state": state,
        "worker_pid": 999999,
        "events": [{"sequence": 0, "kind": "notification", "at": when.isoformat()}],
    }


def test_a_quiet_job_says_how_quiet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(tmp_path / "absent.jsonl"))
    out = _annotate_silence(_record(120))
    assert out["quiet_for_s"] >= 119
    assert out["last_event_at"]


def test_a_job_that_just_spoke_is_not_called_quiet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_CLI_LOG_FILE", str(tmp_path / "absent.jsonl"))
    out = _annotate_silence(_record(1))
    assert out["quiet_for_s"] < worker_health.SILENCE_HINT_SECONDS
    assert "worker_health" not in out


def test_a_finished_job_is_left_alone() -> None:
    out = _annotate_silence(_record(500, state="done"))
    assert "quiet_for_s" not in out


def test_a_record_with_no_events_is_left_alone() -> None:
    assert _annotate_silence({"state": "running", "events": []}) == {
        "state": "running",
        "events": [],
    }
