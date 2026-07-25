"""End-to-end autonomy tests for driver rounds (mocked delegate/gates only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from grok_delegate import driver, report


def _write_queue(tmp: Path, lanes: list[dict[str, Any]]) -> Path:
    """Persist a queue JSON and goal files so load_queue can round-trip statuses."""
    for lane in lanes:
        goal = Path(lane["goal_file"])
        goal.parent.mkdir(parents=True, exist_ok=True)
        if not goal.exists():
            goal.write_text(f"goal for lane {lane['lane']}\n", encoding="utf-8")
    queue_path = tmp / "queue.json"
    queue_path.write_text(
        json.dumps({"lanes": lanes}, indent=2) + "\n",
        encoding="utf-8",
    )
    return queue_path


def _lane_spec(
    tmp: Path,
    *,
    lane_id: str,
    name: str,
    goal_extra: str = "",
) -> dict[str, Any]:
    goal_file = tmp / "goals" / f"{name}.txt"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text(
        f"lane={name}\ngoal for lane {name}\n{goal_extra}".rstrip() + "\n",
        encoding="utf-8",
    )
    return {
        "id": lane_id,
        "lane": name,
        "goal_file": str(goal_file),
        "base_ref": "master",
        "kind": "impl",
        "status": "queued",
        "attempts": 0,
    }


def _extract_lane_name(kwargs: dict[str, Any]) -> str:
    """Discover which lane a fake is handling without reading driver internals."""
    for key in ("lane", "lane_name", "name", "id"):
        val = kwargs.get(key)
        if isinstance(val, str) and val:
            # Prefer explicit lane names over ids like L1.
            if val in {"good", "empty", "gatefail", "liar"}:
                return val
    for key in ("lane", "job", "item", "entry", "spec"):
        val = kwargs.get(key)
        if isinstance(val, dict):
            for sub in ("lane", "lane_name", "name", "id"):
                inner = val.get(sub)
                if isinstance(inner, str) and inner in {
                    "good",
                    "empty",
                    "gatefail",
                    "liar",
                }:
                    return inner
            goal_file = val.get("goal_file")
            if isinstance(goal_file, str):
                text = Path(goal_file).read_text(encoding="utf-8")
                for name in ("good", "empty", "gatefail", "liar"):
                    if name in text or f"lane={name}" in text:
                        return name
    for key in ("goal_file", "goal_path", "goal"):
        val = kwargs.get(key)
        if isinstance(val, str):
            path = Path(val)
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8")
            else:
                text = val
            for name in ("good", "empty", "gatefail", "liar"):
                if f"lane={name}" in text or f"lane {name}" in text or name in text:
                    return name
    # Last resort: scan any string-ish values for planted lane markers.
    for val in kwargs.values():
        if isinstance(val, str):
            for name in ("good", "empty", "gatefail", "liar"):
                if f"lane={name}" in val or f"/{name}.txt" in val.replace("\\", "/"):
                    return name
        if isinstance(val, dict):
            nested = _extract_lane_name(val)
            if nested:
                return nested
    return ""


def _good_result(worktree: str | None = None) -> dict[str, Any]:
    """Successful lane. A real worktree path is required: the driver fails closed
    with GATE_NO_WORKTREE when it cannot run gates, so a fake without one can never
    legitimately reach ready_for_merge."""
    return {
        "worktree_path": worktree,
        "branch": "grok/fake",
        "ok": True,
        "changed_files": ["src/x.py"],
        "commits": ["abc1 feat"],
        "turns_used": 5,
        "verdict_status": "ok",
        "verdict": {
            "files_written": ["src/x.py"],
            "committed": True,
            "tests_added": 2,
            "gates_run": False,
            "self_skeptic_findings": [],
            "blocked_reason": None,
            "summary": "done",
        },
    }


def _empty_result() -> dict[str, Any]:
    return {
        "ok": True,
        "changed_files": [],
        "commits": [],
        "turns_used": 2,
    }


def _liar_result() -> dict[str, Any]:
    return {
        "ok": True,
        "changed_files": [],
        "commits": [],
        "turns_used": 3,
        "verdict_status": "VERDICT_UNSUPPORTED",
        "verdict": {
            "files_written": ["src/never.py"],
            "committed": True,
            "tests_added": 9,
            "gates_run": True,
            "self_skeptic_findings": [],
            "blocked_reason": None,
            "summary": "claims",
        },
    }


def _make_fakes(
    *,
    planted_secret_in_gates: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any, Any]:
    """Build recording delegate/gates fakes keyed by lane name.

    A real (empty) directory stands in for the lane worktree: the driver fails closed
    with GATE_NO_WORKTREE rather than promoting a lane it could not gate, so a fake
    result without one can never reach ready_for_merge.
    """
    delegate_calls: list[dict[str, Any]] = []
    gates_calls: list[dict[str, Any]] = []
    fake_worktree = tempfile.mkdtemp(prefix="e2e-wt-")
    # The driver calls run_gates positionally (worktree, profile), so the lane name is
    # not in its kwargs. Delegate always runs first for a lane, so remember it there.
    current_lane = {"name": ""}

    def delegate_fn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        delegate_calls.append({"args": list(args), **kwargs})
        name = _extract_lane_name(kwargs)
        current_lane["name"] = name
        if name == "empty":
            return _empty_result()
        if name == "liar":
            return _liar_result()
        # good and gatefail share the successful delegate payload
        return _good_result(fake_worktree)

    def run_gates_fn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        gates_calls.append({"args": list(args), **kwargs})
        name = _extract_lane_name(kwargs) or current_lane["name"]
        if name == "gatefail":
            return {"ok": False, "results": [{"output_tail": "1 failed"}]}
        tail = "3 passed"
        if planted_secret_in_gates:
            tail = f"3 passed PLANTED-SECRET-VALUE"
        return {"ok": True, "results": [{"output_tail": tail}]}

    return delegate_calls, gates_calls, delegate_fn, run_gates_fn


def _flatten_arg_values(obj: Any) -> list[Any]:
    """Collect nested argument values for push/merge/secret assertions."""
    out: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(k)
            out.extend(_flatten_arg_values(v))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            out.extend(_flatten_arg_values(item))
    else:
        out.append(obj)
    return out


class TestE2EAutonomy(unittest.TestCase):
    """Driver autonomy round behavior with fully mocked side effects."""

    def test_good_lane_reaches_ready_for_merge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            queue_path = _write_queue(tmp, [_lane_spec(tmp, lane_id="L1", name="good")])
            _d_calls, _g_calls, delegate_fn, run_gates_fn = _make_fakes()
            rc = driver.run_driver(
                queue_path=queue_path,
                repo_root=tmp,
                lanes_parent=None,
                max_attempts=2,
                cooldown_seconds=0.0,
                delegate_fn=delegate_fn,
                run_gates_fn=run_gates_fn,
                sleep_fn=lambda _s: None,
            )
            # run_driver returns a summary mapping (not a bare exit code): it carries
            # exit_code plus per-lane outcomes, which is what an unattended round needs.
            self.assertIsInstance(rc, dict)
            self.assertEqual(rc.get("exit_code"), 0)
            self.assertEqual(rc.get("ready_for_merge"), 1)
            loaded = driver.load_queue(queue_path)
            self.assertEqual(loaded["lanes"][0]["status"], driver.STATUS_READY_FOR_MERGE)

    def test_empty_lane_is_retried_then_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            queue_path = _write_queue(tmp, [_lane_spec(tmp, lane_id="L1", name="empty")])
            delegate_calls, _g_calls, delegate_fn, run_gates_fn = _make_fakes()
            driver.run_driver(
                queue_path=queue_path,
                repo_root=tmp,
                lanes_parent=None,
                max_attempts=2,
                cooldown_seconds=0.0,
                delegate_fn=delegate_fn,
                run_gates_fn=run_gates_fn,
                sleep_fn=lambda _s: None,
            )
            self.assertGreater(len(delegate_calls), 1)
            loaded = driver.load_queue(queue_path)
            self.assertEqual(loaded["lanes"][0]["status"], driver.STATUS_BLOCKED)

    def test_gate_failure_does_not_stop_the_round(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            queue_path = _write_queue(
                tmp,
                [
                    _lane_spec(tmp, lane_id="L1", name="gatefail"),
                    _lane_spec(tmp, lane_id="L2", name="good"),
                ],
            )
            _d_calls, _g_calls, delegate_fn, run_gates_fn = _make_fakes()
            driver.run_driver(
                queue_path=queue_path,
                repo_root=tmp,
                lanes_parent=None,
                max_attempts=2,
                cooldown_seconds=0.0,
                delegate_fn=delegate_fn,
                run_gates_fn=run_gates_fn,
                sleep_fn=lambda _s: None,
            )
            loaded = driver.load_queue(queue_path)
            by_name = {lane["lane"]: lane for lane in loaded["lanes"]}
            self.assertEqual(by_name["gatefail"]["status"], driver.STATUS_GATES_FAILED)
            self.assertEqual(by_name["good"]["status"], driver.STATUS_READY_FOR_MERGE)

    def test_lying_lane_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            queue_path = _write_queue(tmp, [_lane_spec(tmp, lane_id="L1", name="liar")])
            _d_calls, _g_calls, delegate_fn, run_gates_fn = _make_fakes()
            driver.run_driver(
                queue_path=queue_path,
                repo_root=tmp,
                lanes_parent=None,
                max_attempts=2,
                cooldown_seconds=0.0,
                delegate_fn=delegate_fn,
                run_gates_fn=run_gates_fn,
                sleep_fn=lambda _s: None,
            )
            loaded = driver.load_queue(queue_path)
            self.assertNotEqual(
                loaded["lanes"][0]["status"],
                driver.STATUS_READY_FOR_MERGE,
            )

    def test_round_report_lists_real_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            queue_path = _write_queue(
                tmp,
                [
                    _lane_spec(tmp, lane_id="L1", name="good"),
                    _lane_spec(tmp, lane_id="L2", name="empty"),
                ],
            )
            _d_calls, _g_calls, delegate_fn, run_gates_fn = _make_fakes()
            driver.run_driver(
                queue_path=queue_path,
                repo_root=tmp,
                lanes_parent=None,
                max_attempts=2,
                cooldown_seconds=0.0,
                delegate_fn=delegate_fn,
                run_gates_fn=run_gates_fn,
                sleep_fn=lambda _s: None,
            )
            queue = driver.load_queue(queue_path)
            built = report.build_round_report(
                queue,
                {},
                {},
                repo_root=tmp,
            )
            statuses = [lane["status"] for lane in queue["lanes"]]
            totals = built["totals"]
            # totals counts must match on-disk statuses
            for status in set(statuses):
                expected = statuses.count(status)
                self.assertEqual(
                    totals.get(status, 0),
                    expected,
                    f"totals[{status!r}] mismatch for statuses={statuses!r}",
                )
            ready = built["ready_for_merge"]
            ready_names = {
                item if isinstance(item, str) else item.get("lane") or item.get("id")
                for item in ready
            }
            self.assertEqual(ready_names, {"good"})

    def test_no_push_merge_and_no_secret_leak(self) -> None:
        secret = "PLANTED-SECRET-VALUE"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            queue_path = _write_queue(
                tmp,
                [
                    _lane_spec(
                        tmp,
                        lane_id="L1",
                        name="good",
                        goal_extra=secret,
                    )
                ],
            )
            delegate_calls, gates_calls, delegate_fn, run_gates_fn = _make_fakes(
                planted_secret_in_gates=True,
            )
            driver.run_driver(
                queue_path=queue_path,
                repo_root=tmp,
                lanes_parent=None,
                max_attempts=2,
                cooldown_seconds=0.0,
                delegate_fn=delegate_fn,
                run_gates_fn=run_gates_fn,
                sleep_fn=lambda _s: None,
            )
            all_args = []
            for call in delegate_calls + gates_calls:
                all_args.extend(_flatten_arg_values(call))
            for arg in all_args:
                if isinstance(arg, str):
                    self.assertNotEqual(arg, "push")
                    self.assertNotEqual(arg, "merge")

            loaded = driver.load_queue(queue_path)
            for lane in loaded["lanes"]:
                self.assertNotEqual(lane.get("status"), "merged")

            md = report.render_round_report_markdown(
                report.build_round_report(loaded, {}, {})
            )
            self.assertNotIn(secret, md)


if __name__ == "__main__":
    unittest.main()
