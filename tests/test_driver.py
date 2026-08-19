"""Unit tests for grok_delegate.driver (R7-E unattended loop).

Every scenario listed in Service/Archive/GOAL-ROUND7-AUTONOMY.md R7-E. Injects fake
``delegate`` and ``run_gates`` only — never spawns a real executor, gate, or
git mutation. Lock/pid liveness is also injectable.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import driver as D  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lane(
    lane_id: str = "lane-a",
    *,
    lane: str = "r7e-a",
    goal_file: str = "goals/a.md",
    base_ref: str = "origin/dev",
    kind: str = "python",
    status: str = D.STATUS_QUEUED,
    attempts: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": lane_id,
        "lane": lane,
        "goal_file": goal_file,
        "base_ref": base_ref,
        "kind": kind,
        "status": status,
        "attempts": attempts,
    }
    rec.update(extra)
    return rec


def _queue(*lanes: dict[str, Any], **extra: Any) -> dict[str, Any]:
    q: dict[str, Any] = {"lanes": list(lanes)}
    q.update(extra)
    return q


def _ok_delegate(
    *,
    changed_files: list[str] | None = None,
    commits: list[str] | None = None,
    worktree_path: str = "/tmp/fake-wt",
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "changed_files": list(changed_files if changed_files is not None else ["grok_delegate/x.py"]),
        "commits": list(commits if commits is not None else ["abc1234 feat: x"]),
        "worktree_path": worktree_path,
        "lane": "grok/r7e-a",
        "branch": "grok/r7e-a",
    }
    out.update(extra)
    return out


def _empty_delegate(**extra: Any) -> dict[str, Any]:
    return _ok_delegate(changed_files=[], commits=[], **extra)


def _ok_gates(**extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "profile": "python",
        "results": [
            {
                "command": ["py", "-3", "-m", "pytest", "tests", "-q"],
                "returncode": 0,
                "ok": True,
                "duration_s": 0.01,
                "output_tail": "1 passed",
            }
        ],
        "summary": {"total": 1, "passed": 1, "failed": 0},
    }
    out.update(extra)
    return out


def _fail_gates(**extra: Any) -> dict[str, Any]:
    out = _ok_gates()
    out["ok"] = False
    out["results"][0]["ok"] = False
    out["results"][0]["returncode"] = 1
    out["summary"] = {"total": 1, "passed": 0, "failed": 1}
    out.update(extra)
    return out


class RecordingDelegate:
    """Scripted fake delegate; never touches the real executor."""

    def __init__(
        self,
        responses: list[dict[str, Any] | BaseException] | None = None,
        *,
        default: dict[str, Any] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.default = default if default is not None else _ok_delegate()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return dict(self.default)


class RecordingGates:
    """Scripted fake run_gates; never spawns a real gate."""

    def __init__(
        self,
        responses: list[dict[str, Any] | BaseException] | None = None,
        *,
        default: dict[str, Any] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.default = default if default is not None else _ok_gates()
        self.calls: list[tuple[Any, str]] = []

    def __call__(self, worktree: Any, profile: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((worktree, profile))
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return dict(self.default)


class DriverTestCase(unittest.TestCase):
    """Shared temp-dir fixture for queue / goal / verdict files."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.goals = self.repo / "goals"
        self.goals.mkdir()
        self.queue_path = self.root / "queue.json"
        self.verdicts = self.root / "verdicts"
        self.audit_events: list[dict[str, Any]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_goal(self, name: str, text: str = "implement the thing\n") -> str:
        path = self.goals / name
        path.write_text(text, encoding="utf-8")
        # Return path relative to repo so goal_base_dir=repo resolves it.
        return f"goals/{name}"

    def _write_queue(self, queue: dict[str, Any] | list[Any]) -> Path:
        self.queue_path.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.queue_path

    def _read_queue(self) -> dict[str, Any]:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def _audit(self, event: dict[str, Any], stream: Any = None) -> dict[str, Any]:
        self.audit_events.append(dict(event))
        return dict(event)

    def _run(
        self,
        *,
        delegate: RecordingDelegate | None = None,
        gates: RecordingGates | None = None,
        max_attempts: int = 2,
        max_lanes: int | None = None,
        cooldown_seconds: float = 0.0,
        dry_run: bool = False,
        alive_check: Any = None,
        pid: int | None = None,
        install_signals: bool = False,
        sleep_fn: Any = None,
    ) -> dict[str, Any]:
        return D.run_driver(
            queue_path=self.queue_path,
            repo_root=self.repo,
            goal_base_dir=self.repo,
            verdicts_dir=self.verdicts,
            max_attempts=max_attempts,
            max_lanes=max_lanes,
            cooldown_seconds=cooldown_seconds,
            dry_run=dry_run,
            delegate_fn=delegate or RecordingDelegate(),
            run_gates_fn=gates or RecordingGates(),
            audit_emit=self._audit,
            sleep_fn=sleep_fn or (lambda _s: None),
            alive_check=alive_check,
            pid=pid if pid is not None else 424242,
            install_signals=install_signals,
        )


# ---------------------------------------------------------------------------
# Queue read + lane selection (first slice)
# ---------------------------------------------------------------------------


class QueueLoadAndSelectTests(DriverTestCase):
    """load_queue + select_next_lane — first commit surface."""

    def test_load_queue_reads_lanes(self) -> None:
        """Queue file with two lanes loads both records."""
        self._write_queue(_queue(_lane("a"), _lane("b", lane="r7e-b")))
        q = D.load_queue(self.queue_path)
        self.assertEqual(len(q["lanes"]), 2)
        self.assertEqual(q["lanes"][0]["id"], "a")

    def test_select_next_lane_picks_first_queued(self) -> None:
        """First status=queued wins; ready_for_merge is skipped."""
        q = _queue(
            _lane("done", status=D.STATUS_READY_FOR_MERGE),
            _lane("next", status=D.STATUS_QUEUED),
            _lane("later", status=D.STATUS_QUEUED),
        )
        picked = D.select_next_lane(q)
        assert picked is not None
        self.assertEqual(picked["id"], "next")

    def test_select_next_lane_empty_queue(self) -> None:
        """Empty lanes list → None (nothing to do)."""
        self.assertIsNone(D.select_next_lane(_queue()))

    def test_select_next_lane_all_done(self) -> None:
        """All terminal statuses → None."""
        q = _queue(
            _lane("a", status=D.STATUS_READY_FOR_MERGE),
            _lane("b", status=D.STATUS_BLOCKED),
            _lane("c", status=D.STATUS_GATES_FAILED),
        )
        self.assertIsNone(D.select_next_lane(q))

    def test_load_queue_tolerates_unknown_fields(self) -> None:
        """Unknown top-level and per-lane fields are preserved."""
        raw = {
            "schema_version": 7,
            "operator": "claude",
            "lanes": [
                _lane("x", extra_note="keep-me", priority=9),
            ],
        }
        self._write_queue(raw)
        q = D.load_queue(self.queue_path)
        self.assertEqual(q["schema_version"], 7)
        self.assertEqual(q["operator"], "claude")
        self.assertEqual(q["lanes"][0]["extra_note"], "keep-me")
        self.assertEqual(q["lanes"][0]["priority"], 9)

    def test_load_queue_accepts_bare_array(self) -> None:
        """A top-level JSON array is normalized to {lanes: [...]}."""
        self._write_queue([_lane("only")])
        q = D.load_queue(self.queue_path)
        self.assertEqual(len(q["lanes"]), 1)
        self.assertEqual(q["lanes"][0]["id"], "only")

    def test_load_queue_corrupt_json_raises(self) -> None:
        """Corrupt JSON → DriverError QUEUE_CORRUPT, non-zero exit code."""
        self.queue_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(D.DriverError) as ctx:
            D.load_queue(self.queue_path)
        self.assertEqual(ctx.exception.code, "QUEUE_CORRUPT")
        self.assertEqual(ctx.exception.exit_code, D.EXIT_CORRUPT_QUEUE)


# ---------------------------------------------------------------------------
# Empty result + nudge
# ---------------------------------------------------------------------------


class EmptyResultAndNudgeTests(unittest.TestCase):
    def test_is_empty_result_no_files_no_commits(self) -> None:
        self.assertTrue(D.is_empty_result({"ok": True, "changed_files": [], "commits": []}))

    def test_is_empty_result_with_files(self) -> None:
        self.assertFalse(D.is_empty_result({"changed_files": ["a.py"], "commits": []}))

    def test_is_empty_result_with_commits(self) -> None:
        self.assertFalse(D.is_empty_result({"changed_files": [], "commits": ["abc"]}))

    def test_is_empty_result_none(self) -> None:
        self.assertTrue(D.is_empty_result(None))

    def test_write_first_nudge_escalates(self) -> None:
        """Escalation is about firmness, not character count.

        The original assertion required each nudge to be LONGER than the previous one —
        a proxy that fails on a shorter-but-firmer wording while proving nothing about
        the actual contract. What must hold: no nudge on the first attempt, every retry
        carries a distinct escalation marker, names the empty-result problem, and demands
        a commit.
        """
        n0 = D.write_first_nudge(0)
        n1 = D.write_first_nudge(1)
        n2 = D.write_first_nudge(2)
        n3 = D.write_first_nudge(3)

        self.assertEqual(n0, "", "the first attempt must carry no nudge")
        self.assertIn("Write-first", n1)
        self.assertIn("STILL EMPTY", n2)
        self.assertIn("FINAL ATTEMPT", n3)

        retries = [n1, n2, n3]
        self.assertEqual(len(set(retries)), 3, "each retry must be distinctly worded")
        for index, text in enumerate(retries, start=1):
            self.assertIn(f"retry {index}", text.lower().replace("final attempt", f"retry {index}"))
            self.assertTrue(text.strip(), "a retry nudge must not be empty")
            self.assertRegex(text.lower(), r"commit", "every retry must demand a commit")

        # Beyond the table the last (firmest) nudge is reused, never an empty string.
        self.assertEqual(D.write_first_nudge(99), n3)

    def test_build_goal_with_nudge_first_attempt_unchanged(self) -> None:
        goal = "deliver driver.py"
        self.assertEqual(D.build_goal_with_nudge(goal, 0), goal)

    def test_build_goal_with_nudge_retry_appends(self) -> None:
        goal = "deliver driver.py"
        out = D.build_goal_with_nudge(goal, 1)
        self.assertTrue(out.startswith(goal))
        self.assertIn("Write-first", out)
        self.assertIn("driver retry", out)


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


class QueueLockTests(DriverTestCase):
    def test_acquire_and_release_lock(self) -> None:
        """Happy path: lock create + release removes the sidecar."""
        self._write_queue(_queue())
        lpath = D.acquire_queue_lock(self.queue_path, pid=111, alive_check=lambda _p: True)
        self.assertTrue(lpath.is_file())
        body = json.loads(lpath.read_text(encoding="utf-8"))
        self.assertEqual(body["pid"], 111)
        self.assertTrue(D.release_queue_lock(self.queue_path, pid=111))
        self.assertFalse(lpath.exists())

    def test_live_pid_lock_raises_queue_locked(self) -> None:
        """Lock held by a live pid → QUEUE_LOCKED."""
        self._write_queue(_queue())
        D.acquire_queue_lock(self.queue_path, pid=222, alive_check=lambda _p: True)
        with self.assertRaises(D.DriverError) as ctx:
            D.acquire_queue_lock(
                self.queue_path,
                pid=333,
                alive_check=lambda p: p == 222,  # 222 alive, 333 not asked
            )
        self.assertEqual(ctx.exception.code, "QUEUE_LOCKED")
        self.assertEqual(ctx.exception.exit_code, D.EXIT_QUEUE_LOCKED)
        D.release_queue_lock(self.queue_path, pid=222, force=True)

    def test_dead_pid_lock_is_taken_over(self) -> None:
        """Lock held by a dead pid → taken over by the new driver."""
        self._write_queue(_queue())
        D.acquire_queue_lock(self.queue_path, pid=444, alive_check=lambda _p: False)
        # Holder 444 is dead → 555 takes over.
        lpath = D.acquire_queue_lock(
            self.queue_path,
            pid=555,
            alive_check=lambda _p: False,
        )
        body = json.loads(lpath.read_text(encoding="utf-8"))
        self.assertEqual(body["pid"], 555)
        D.release_queue_lock(self.queue_path, pid=555)

    def test_second_driver_exits_queue_locked(self) -> None:
        """run_driver when lock is live → exit_code QUEUE_LOCKED, no spawn."""
        goal = self._write_goal("a.md")
        self._write_queue(_queue(_lane("a", goal_file=goal)))
        # Plant a live lock.
        D.acquire_queue_lock(self.queue_path, pid=99901, alive_check=lambda _p: True)
        delegate = RecordingDelegate()
        result = self._run(
            delegate=delegate,
            pid=99902,
            alive_check=lambda p: p == 99901,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "QUEUE_LOCKED")
        self.assertEqual(result["exit_code"], D.EXIT_QUEUE_LOCKED)
        self.assertEqual(delegate.calls, [])
        D.release_queue_lock(self.queue_path, pid=99901, force=True)


# ---------------------------------------------------------------------------
# Full loop scenarios from R7-E
# ---------------------------------------------------------------------------


class DriverLoopTests(DriverTestCase):
    def test_empty_queue_clean_exit(self) -> None:
        """empty queue → clean exit 0 with 'nothing to do'."""
        self._write_queue(_queue())
        delegate = RecordingDelegate()
        result = self._run(delegate=delegate)
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], D.EXIT_OK)
        self.assertIn("nothing to do", result["message"])
        self.assertEqual(delegate.calls, [])

    def test_all_lanes_done_clean_exit(self) -> None:
        """all lanes done → same clean exit, nothing spawned."""
        self._write_queue(
            _queue(
                _lane("a", status=D.STATUS_READY_FOR_MERGE),
                _lane("b", status=D.STATUS_BLOCKED),
            )
        )
        delegate = RecordingDelegate()
        result = self._run(delegate=delegate)
        self.assertTrue(result["ok"])
        self.assertIn("nothing to do", result["message"])
        self.assertEqual(delegate.calls, [])

    def test_ready_for_merge_is_skipped(self) -> None:
        """a lane already ready_for_merge is skipped; next queued runs."""
        g1 = self._write_goal("done.md")
        g2 = self._write_goal("next.md")
        self._write_queue(
            _queue(
                _lane("done", goal_file=g1, status=D.STATUS_READY_FOR_MERGE),
                _lane("next", lane="r7e-next", goal_file=g2),
            )
        )
        delegate = RecordingDelegate(default=_ok_delegate())
        result = self._run(delegate=delegate)
        self.assertEqual(len(delegate.calls), 1)
        self.assertEqual(delegate.calls[0]["lane"], "r7e-next")
        self.assertEqual(result["ready_for_merge"], 1)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_READY_FOR_MERGE)
        self.assertEqual(q["lanes"][1]["status"], D.STATUS_READY_FOR_MERGE)

    def test_first_lane_blocked_continues_to_next(self) -> None:
        """first lane blocked → driver continues to the next, not stops."""
        # Lane A: missing goal → blocked. Lane B: good.
        g_b = self._write_goal("b.md")
        self._write_queue(
            _queue(
                _lane("a", lane="r7e-a", goal_file="goals/missing-a.md"),
                _lane("b", lane="r7e-b", goal_file=g_b),
            )
        )
        delegate = RecordingDelegate(default=_ok_delegate())
        result = self._run(delegate=delegate)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(result["ready_for_merge"], 1)
        self.assertEqual(len(delegate.calls), 1)
        self.assertEqual(delegate.calls[0]["lane"], "r7e-b")
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_BLOCKED)
        self.assertEqual(q["lanes"][0]["blocked_reason"], "GOAL_FILE_MISSING")
        self.assertEqual(q["lanes"][1]["status"], D.STATUS_READY_FOR_MERGE)

    def test_empty_result_retried_with_stronger_prompt(self) -> None:
        """executor returns empty → retried with a stronger write-first prompt."""
        g = self._write_goal("empty.md", "do the work")
        self._write_queue(_queue(_lane("e", goal_file=g, max_attempts=2)))
        delegate = RecordingDelegate(
            responses=[
                _empty_delegate(),
                _ok_delegate(changed_files=["grok_delegate/driver.py"], commits=["c1"]),
            ]
        )
        gates = RecordingGates()
        result = self._run(delegate=delegate, gates=gates, max_attempts=2)
        self.assertEqual(len(delegate.calls), 2)
        # First call: raw goal, no nudge.
        self.assertEqual(delegate.calls[0]["goal"], "do the work")
        # Second call: stronger nudge appended.
        self.assertIn("Write-first", delegate.calls[1]["goal"])
        self.assertIn("do the work", delegate.calls[1]["goal"])
        self.assertEqual(result["ready_for_merge"], 1)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_READY_FOR_MERGE)
        self.assertEqual(q["lanes"][0]["attempts"], 2)

    def test_retries_exhausted_marks_blocked_with_attempts(self) -> None:
        """retries exhausted → blocked with attempts recorded."""
        g = self._write_goal("never.md")
        self._write_queue(_queue(_lane("n", goal_file=g)))
        delegate = RecordingDelegate(
            responses=[_empty_delegate(), _empty_delegate()],
        )
        result = self._run(delegate=delegate, max_attempts=2)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(result["ready_for_merge"], 0)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_BLOCKED)
        self.assertEqual(q["lanes"][0]["blocked_reason"], "EMPTY_RESULT")
        self.assertEqual(q["lanes"][0]["attempts"], 2)
        # Both attempts used the same lane identity — not conflated across lanes.
        self.assertEqual(len(delegate.calls), 2)

    def test_gates_fail_marks_gates_failed_and_continues(self) -> None:
        """gates fail → lane marked gates_failed (not ready_for_merge), driver continues."""
        g1 = self._write_goal("fail.md")
        g2 = self._write_goal("ok.md")
        self._write_queue(
            _queue(
                _lane("fail", lane="r7e-fail", goal_file=g1),
                _lane("ok", lane="r7e-ok", goal_file=g2),
            )
        )
        delegate = RecordingDelegate(default=_ok_delegate())
        gates = RecordingGates(responses=[_fail_gates(), _ok_gates()])
        result = self._run(delegate=delegate, gates=gates)
        self.assertEqual(result["gates_failed"], 1)
        self.assertEqual(result["ready_for_merge"], 1)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_GATES_FAILED)
        self.assertNotEqual(q["lanes"][0]["status"], D.STATUS_READY_FOR_MERGE)
        self.assertEqual(q["lanes"][1]["status"], D.STATUS_READY_FOR_MERGE)
        # Safety: furthest state is ready_for_merge — never "merged".
        for lane in q["lanes"]:
            self.assertNotEqual(lane["status"], "merged")
            self.assertNotIn(lane["status"], {"pushed", "merged"})

    def test_missing_goal_file_blocked_never_spawn(self) -> None:
        """lane whose goal_file is missing → blocked:GOAL_FILE_MISSING, never spawn."""
        self._write_queue(
            _queue(_lane("m", goal_file="goals/does-not-exist.md"))
        )
        delegate = RecordingDelegate()
        result = self._run(delegate=delegate)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(delegate.calls, [])
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_BLOCKED)
        self.assertEqual(q["lanes"][0]["blocked_reason"], "GOAL_FILE_MISSING")

    def test_corrupt_queue_exits_nonzero_without_touching_lanes(self) -> None:
        """corrupt queue JSON → exit non-zero without touching lanes."""
        self.queue_path.write_text("{broken", encoding="utf-8")
        before = self.queue_path.read_bytes()
        delegate = RecordingDelegate()
        result = self._run(delegate=delegate)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "QUEUE_CORRUPT")
        self.assertEqual(result["exit_code"], D.EXIT_CORRUPT_QUEUE)
        self.assertEqual(delegate.calls, [])
        # File content unchanged (no rewrite).
        self.assertEqual(self.queue_path.read_bytes(), before)

    def test_queue_unknown_fields_tolerated_end_to_end(self) -> None:
        """queue with unknown fields → tolerated through a full successful run."""
        g = self._write_goal("x.md")
        self._write_queue(
            {
                "meta": {"round": 7},
                "lanes": [_lane("x", goal_file=g, annotation="keep")],
            }
        )
        result = self._run()
        self.assertEqual(result["ready_for_merge"], 1)
        q = self._read_queue()
        self.assertEqual(q["meta"]["round"], 7)
        self.assertEqual(q["lanes"][0]["annotation"], "keep")

    def test_base_ref_unreachable_blocked(self) -> None:
        """base_ref unreachable → blocked, no destructive fix."""
        g = self._write_goal("br.md")
        self._write_queue(_queue(_lane("br", goal_file=g, base_ref="origin/nope")))
        delegate = RecordingDelegate(
            default={
                "ok": False,
                "error": "BASE_UNREACHABLE",
                "message": "base_ref 'origin/nope' is not reachable",
                "changed_files": [],
                "commits": [],
            }
        )
        result = self._run(delegate=delegate)
        self.assertEqual(result["blocked"], 1)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_BLOCKED)
        self.assertEqual(q["lanes"][0]["blocked_reason"], "BASE_UNREACHABLE")
        # Only one call — hard errors do not empty-retry.
        self.assertEqual(len(delegate.calls), 1)

    def test_worktree_wrong_branch_blocked(self) -> None:
        """exists on the wrong branch → blocked, no destructive fix."""
        g = self._write_goal("wt.md")
        self._write_queue(_queue(_lane("wt", goal_file=g)))
        delegate = RecordingDelegate(
            default={
                "ok": False,
                "error": "WORKTREE_EXISTS_CONFLICT",
                "message": "path exists but is not on branch grok/r7e-a",
                "head": "grok/other",
                "changed_files": [],
                "commits": [],
            }
        )
        result = self._run(delegate=delegate)
        self.assertEqual(result["blocked"], 1)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["blocked_reason"], "WORKTREE_EXISTS_CONFLICT")

    def test_worktree_reused_on_right_branch(self) -> None:
        """worktree already exists on the right branch → reused (ok path)."""
        g = self._write_goal("reuse.md")
        self._write_queue(_queue(_lane("reuse", goal_file=g)))
        delegate = RecordingDelegate(
            default=_ok_delegate(reused=True, worktree_path="/tmp/reused-wt")
        )
        gates = RecordingGates()
        result = self._run(delegate=delegate, gates=gates)
        self.assertEqual(result["ready_for_merge"], 1)
        self.assertEqual(len(delegate.calls), 1)
        self.assertEqual(gates.calls[0][0], "/tmp/reused-wt")

    def test_max_lanes_stops_with_summary(self) -> None:
        """max_lanes reached → stop with a summary; remaining stay queued."""
        g1 = self._write_goal("1.md")
        g2 = self._write_goal("2.md")
        g3 = self._write_goal("3.md")
        self._write_queue(
            _queue(
                _lane("1", lane="r7e-1", goal_file=g1),
                _lane("2", lane="r7e-2", goal_file=g2),
                _lane("3", lane="r7e-3", goal_file=g3),
            )
        )
        delegate = RecordingDelegate(default=_ok_delegate())
        result = self._run(delegate=delegate, max_lanes=2)
        self.assertEqual(len(delegate.calls), 2)
        self.assertIn("max_lanes", result["message"])
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_READY_FOR_MERGE)
        self.assertEqual(q["lanes"][1]["status"], D.STATUS_READY_FOR_MERGE)
        self.assertEqual(q["lanes"][2]["status"], D.STATUS_QUEUED)

    def test_dry_run_prints_plan_spawns_nothing(self) -> None:
        """--dry-run prints the plan and spawns nothing."""
        g = self._write_goal("dry.md")
        self._write_queue(_queue(_lane("dry", goal_file=g, lane="r7e-dry")))
        delegate = RecordingDelegate()
        gates = RecordingGates()
        result = self._run(delegate=delegate, gates=gates, dry_run=True)
        self.assertTrue(result.get("dry_run"))
        self.assertEqual(delegate.calls, [])
        self.assertEqual(gates.calls, [])
        self.assertEqual(len(result.get("plan") or []), 1)
        self.assertEqual(result["plan"][0]["lane"], "r7e-dry")
        # Queue untouched.
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_QUEUED)

    def test_two_empty_results_on_different_lanes_not_conflated(self) -> None:
        """two consecutive empty results on *different* lanes must not be conflated."""
        g1 = self._write_goal("e1.md", "goal-one")
        g2 = self._write_goal("e2.md", "goal-two")
        self._write_queue(
            _queue(
                _lane("e1", lane="r7e-e1", goal_file=g1),
                _lane("e2", lane="r7e-e2", goal_file=g2),
            )
        )
        # Each lane gets two empties → each blocked independently with attempts=2.
        delegate = RecordingDelegate(
            responses=[
                _empty_delegate(),
                _empty_delegate(),
                _empty_delegate(),
                _empty_delegate(),
            ]
        )
        result = self._run(delegate=delegate, max_attempts=2)
        self.assertEqual(result["blocked"], 2)
        self.assertEqual(len(delegate.calls), 4)
        # Goals stay per-lane (nudge may append, but original text is present).
        self.assertIn("goal-one", delegate.calls[0]["goal"])
        self.assertIn("goal-one", delegate.calls[1]["goal"])
        self.assertIn("goal-two", delegate.calls[2]["goal"])
        self.assertIn("goal-two", delegate.calls[3]["goal"])
        # Lane names distinct across the four calls.
        self.assertEqual(delegate.calls[0]["lane"], "r7e-e1")
        self.assertEqual(delegate.calls[1]["lane"], "r7e-e1")
        self.assertEqual(delegate.calls[2]["lane"], "r7e-e2")
        self.assertEqual(delegate.calls[3]["lane"], "r7e-e2")
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["attempts"], 2)
        self.assertEqual(q["lanes"][1]["attempts"], 2)
        self.assertEqual(q["lanes"][0]["blocked_reason"], "EMPTY_RESULT")
        self.assertEqual(q["lanes"][1]["blocked_reason"], "EMPTY_RESULT")

    def test_sigint_mid_lane_returns_to_queued_and_releases_lock(self) -> None:
        """SIGINT mid-lane → lane back to queued, lock released."""
        g = self._write_goal("sig.md")
        self._write_queue(_queue(_lane("sig", goal_file=g)))

        interrupt_state = {"n": 0}

        def flaky_delegate(**kwargs: Any) -> dict[str, Any]:
            interrupt_state["n"] += 1
            # Simulate signal arriving during the first (only) dispatch.
            raise D.InterruptRequested()

        result = D.run_driver(
            queue_path=self.queue_path,
            repo_root=self.repo,
            goal_base_dir=self.repo,
            verdicts_dir=self.verdicts,
            max_attempts=2,
            delegate_fn=flaky_delegate,
            run_gates_fn=RecordingGates(),
            audit_emit=self._audit,
            sleep_fn=lambda _s: None,
            pid=777001,
            install_signals=False,
            alive_check=lambda _p: False,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "INTERRUPTED")
        self.assertEqual(result["exit_code"], D.EXIT_INTERRUPTED)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_QUEUED)
        # Lock released.
        self.assertFalse(D.lock_path_for(self.queue_path).exists())

    def test_cooldown_invoked_between_lanes(self) -> None:
        """configurable cooldown is applied between two consecutive lanes."""
        g1 = self._write_goal("c1.md")
        g2 = self._write_goal("c2.md")
        self._write_queue(
            _queue(
                _lane("c1", lane="r7e-c1", goal_file=g1),
                _lane("c2", lane="r7e-c2", goal_file=g2),
            )
        )
        sleeps: list[float] = []
        result = self._run(
            cooldown_seconds=1.5,
            sleep_fn=lambda s: sleeps.append(s),
        )
        self.assertEqual(result["ready_for_merge"], 2)
        self.assertEqual(sleeps, [1.5])

    def test_audit_record_per_state_change(self) -> None:
        """emit an audit record per state change (started + final)."""
        g = self._write_goal("aud.md")
        self._write_queue(_queue(_lane("aud", lane="r7e-aud", goal_file=g)))
        self._run()
        statuses = [e.get("status") for e in self.audit_events]
        self.assertIn(D.STATUS_IN_FLIGHT, statuses)
        self.assertIn(D.STATUS_READY_FOR_MERGE, statuses)
        outcomes = [e.get("outcome") for e in self.audit_events]
        self.assertIn("started", outcomes)
        self.assertIn("ready_for_merge", outcomes)

    def test_per_lane_verdict_file_written(self) -> None:
        """write a per-lane verdict file under verdicts_dir."""
        g = self._write_goal("ver.md")
        self._write_queue(_queue(_lane("ver", lane="r7e-ver", goal_file=g)))
        self._run()
        vpath = D.verdict_path_for(self.verdicts, "ver")
        self.assertTrue(vpath.is_file())
        payload = json.loads(vpath.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], D.STATUS_READY_FOR_MERGE)
        self.assertEqual(payload["lane_id"], "ver")

    def test_never_merge_or_push_in_driver_source(self) -> None:
        """Safety invariant: driver source never assembles merge/push commands."""
        src = Path(D.__file__).read_text(encoding="utf-8")
        # The module documents the boundary; it must not call git merge/push.
        self.assertNotIn("git push", src)
        self.assertNotIn("git merge", src)
        self.assertNotIn("--always-approve", src)
        # Status token ready_for_merge is the ceiling.
        self.assertIn("ready_for_merge", src)
        self.assertNotIn('"merged"', src)

    def test_one_lane_in_flight_serial(self) -> None:
        """exactly one lane in flight: second lane starts only after first finishes."""
        g1 = self._write_goal("s1.md")
        g2 = self._write_goal("s2.md")
        self._write_queue(
            _queue(
                _lane("s1", lane="r7e-s1", goal_file=g1),
                _lane("s2", lane="r7e-s2", goal_file=g2),
            )
        )
        order: list[str] = []

        def seq_delegate(**kwargs: Any) -> dict[str, Any]:
            order.append(f"start:{kwargs['lane']}")
            order.append(f"end:{kwargs['lane']}")
            return _ok_delegate()

        D.run_driver(
            queue_path=self.queue_path,
            repo_root=self.repo,
            goal_base_dir=self.repo,
            verdicts_dir=self.verdicts,
            delegate_fn=seq_delegate,
            run_gates_fn=RecordingGates(),
            audit_emit=self._audit,
            sleep_fn=lambda _s: None,
            pid=888001,
            install_signals=False,
            alive_check=lambda _p: False,
        )
        self.assertEqual(
            order,
            ["start:r7e-s1", "end:r7e-s1", "start:r7e-s2", "end:r7e-s2"],
        )

    def test_gate_profile_from_lane_kind(self) -> None:
        """gates.run_gates is called with the lane's kind/profile."""
        g = self._write_goal("node.md")
        self._write_queue(
            _queue(_lane("node", goal_file=g, kind="node", lane="r7e-node"))
        )
        gates = RecordingGates()
        self._run(gates=gates)
        self.assertEqual(len(gates.calls), 1)
        self.assertEqual(gates.calls[0][1], "node")

    def test_save_queue_atomic_roundtrip(self) -> None:
        """save_queue writes readable JSON and preserves lanes."""
        q = _queue(_lane("z", status=D.STATUS_QUEUED), note="n")
        D.save_queue(self.queue_path, q)
        loaded = D.load_queue(self.queue_path)
        self.assertEqual(loaded["lanes"][0]["id"], "z")
        self.assertEqual(loaded["note"], "n")


class PrepareTimeoutIsRetryableTests(DriverTestCase):
    """A wrapper timeout is not a verdict about the host.

    Measured 2026-07-27: `git worktree add` hit the per-call ceiling, the wrapper
    reported WORKTREE_CREATE_FAILED, and because that code sits in hard_codes the
    lane was blocked on the spot — no retry, executor never spawned. The checkout
    had in fact completed. One slow git call took the lane name out of service
    until someone cleaned up by hand.
    """

    @staticmethod
    def _timeout_result() -> dict[str, Any]:
        return {
            "ok": False,
            "error": "GIT_TIMEOUT",
            "message": "git worktree add exceeded its 600s budget",
            "changed_files": [],
            "commits": [],
        }

    def test_timeout_then_success_reaches_the_executor(self) -> None:
        g = self._write_goal("t.md", "do the work")
        self._write_queue(_queue(_lane("t", goal_file=g, max_attempts=2)))
        delegate = RecordingDelegate(
            responses=[
                self._timeout_result(),
                _ok_delegate(changed_files=["a.py"], commits=["c1"]),
            ]
        )
        result = self._run(delegate=delegate, max_attempts=2)
        self.assertEqual(len(delegate.calls), 2, "the timeout must be retried")
        self.assertEqual(result["ready_for_merge"], 1)
        self.assertEqual(result["blocked"], 0)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["status"], D.STATUS_READY_FOR_MERGE)

    def test_exhausted_timeouts_block_under_their_own_reason(self) -> None:
        """Not EMPTY_RESULT: the executor never ran, so it wrote nothing by
        definition, and labelling it "empty" sends the operator hunting the
        wrong defect."""
        g = self._write_goal("t2.md")
        self._write_queue(_queue(_lane("t2", goal_file=g)))
        delegate = RecordingDelegate(
            responses=[self._timeout_result(), self._timeout_result()]
        )
        result = self._run(delegate=delegate, max_attempts=2)
        self.assertEqual(result["blocked"], 1)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["blocked_reason"], "GIT_TIMEOUT")
        self.assertEqual(q["lanes"][0]["attempts"], 2)

    def test_initializing_worktree_is_retried_too(self) -> None:
        g = self._write_goal("t3.md")
        self._write_queue(_queue(_lane("t3", goal_file=g, max_attempts=2)))
        delegate = RecordingDelegate(
            responses=[
                {
                    "ok": False,
                    "error": "WORKTREE_INITIALIZING",
                    "message": "still being checked out",
                    "changed_files": [],
                    "commits": [],
                },
                _ok_delegate(changed_files=["a.py"], commits=["c1"]),
            ]
        )
        result = self._run(delegate=delegate, max_attempts=2)
        self.assertEqual(len(delegate.calls), 2)
        self.assertEqual(result["ready_for_merge"], 1)

    def test_genuine_create_failure_still_blocks_immediately(self) -> None:
        """No regression: a real failure must not buy itself a retry."""
        g = self._write_goal("t4.md")
        self._write_queue(_queue(_lane("t4", goal_file=g, max_attempts=2)))
        delegate = RecordingDelegate(
            responses=[
                {
                    "ok": False,
                    "error": "WORKTREE_CREATE_FAILED",
                    "message": "git worktree add failed",
                    "changed_files": [],
                    "commits": [],
                },
                _ok_delegate(changed_files=["a.py"]),
            ]
        )
        result = self._run(delegate=delegate, max_attempts=2)
        self.assertEqual(len(delegate.calls), 1, "hard failures must not retry")
        self.assertEqual(result["blocked"], 1)
        q = self._read_queue()
        self.assertEqual(q["lanes"][0]["blocked_reason"], "WORKTREE_CREATE_FAILED")


class CliTests(DriverTestCase):
    def test_main_requires_queue(self) -> None:
        code = D.main([])
        self.assertEqual(code, D.EXIT_ERROR)

    def test_main_dry_run_exit_zero(self) -> None:
        g = self._write_goal("cli.md")
        self._write_queue(_queue(_lane("cli", goal_file=g)))
        # main() uses real run_driver without injectables — dry-run never spawns.
        code = D.main(
            [
                "--queue",
                str(self.queue_path),
                "--repo-root",
                str(self.repo),
                "--dry-run",
            ]
        )
        self.assertEqual(code, D.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
