from __future__ import annotations

import re
import unittest
from typing import Any

from grok_delegate import report as R


def _lane(
    *,
    id: str,
    lane: str = "lane",
    status: str = "blocked",
    attempts: int = 0,
    turns_used: int = 0,
    changed_files: list[str] | None = None,
    commits: list[str] | None = None,
    blocked_reason: str | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
    goal: str | None = None,
    worktree_path: str | None = None,
    verdict: Any = None,
    verdict_status: str | None = None,
) -> dict[str, Any]:
    """Minimal queue lane dict; defaults keep tests focused on one assertion."""
    return {
        "id": id,
        "lane": lane,
        "status": status,
        "attempts": attempts,
        "turns_used": turns_used,
        "changed_files": changed_files if changed_files is not None else [],
        "commits": commits if commits is not None else [],
        "blocked_reason": blocked_reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "goal": goal,
        "worktree_path": worktree_path,
        "verdict": verdict,
        "verdict_status": verdict_status,
    }


def _queue(*lanes: dict[str, Any]) -> dict[str, Any]:
    return {"lanes": list(lanes)}


class TestBuildRoundReport(unittest.TestCase):
    def test_lanes_are_sorted_by_id(self) -> None:
        queue = _queue(
            _lane(id="I3"),
            _lane(id="I1"),
            _lane(id="I2"),
        )
        report = R.build_round_report(queue, {}, {})
        self.assertEqual([entry["id"] for entry in report["lanes"]], ["I1", "I2", "I3"])

    def test_totals_count_each_status(self) -> None:
        queue = _queue(
            _lane(id="A", status="ready_for_merge"),
            _lane(id="B", status="blocked"),
            _lane(id="C", status="blocked"),
        )
        report = R.build_round_report(queue, {}, {})
        self.assertEqual(report["totals"], {"blocked": 2, "ready_for_merge": 1})

    def test_ready_for_merge_lists_lane_names_sorted(self) -> None:
        queue = _queue(
            _lane(id="Z", lane="zeta", status="ready_for_merge"),
            _lane(id="A", lane="alpha", status="ready_for_merge"),
        )
        report = R.build_round_report(queue, {}, {})
        self.assertEqual(report["ready_for_merge"], ["alpha", "zeta"])

    def test_goal_text_is_never_reported(self) -> None:
        secret = "top secret goal"
        queue = _queue(_lane(id="G1", goal=secret))
        report = R.build_round_report(queue, {}, {})
        self.assertNotIn(secret, str(report))
        self.assertEqual(len(report["lanes"]), 1)
        entry = report["lanes"][0]
        self.assertEqual(entry["goal_chars"], 15)
        self.assertRegex(entry["goal_sha256_8"], r"^[0-9a-f]{8}$")

    def test_backwards_clock_reports_no_duration(self) -> None:
        queue = _queue(_lane(id="T1", started_at=100.0, finished_at=90.0))
        report = R.build_round_report(queue, {}, {})
        self.assertEqual(len(report["lanes"]), 1)
        self.assertIsNone(report["lanes"][0]["duration_s"])

    def test_duration_is_rounded(self) -> None:
        queue = _queue(_lane(id="T2", started_at=10.0, finished_at=22.3456))
        report = R.build_round_report(queue, {}, {})
        self.assertEqual(len(report["lanes"]), 1)
        self.assertEqual(report["lanes"][0]["duration_s"], 12.346)

    def test_gate_states_ok_failed_absent(self) -> None:
        queue = _queue(
            _lane(id="G_OK"),
            _lane(id="G_FAIL"),
            _lane(id="G_ABSENT"),
        )
        gate_reports = {
            "G_OK": {"ok": True},
            "G_FAIL": {"ok": False},
        }
        report = R.build_round_report(queue, {}, gate_reports)
        by_id = {entry["id"]: entry for entry in report["lanes"]}
        self.assertEqual(by_id["G_OK"]["gate"], R.GATE_OK)
        self.assertEqual(by_id["G_FAIL"]["gate"], R.GATE_FAILED)
        self.assertEqual(by_id["G_ABSENT"]["gate"], R.GATE_ABSENT)

    def test_gate_tail_is_bounded(self) -> None:
        queue = _queue(_lane(id="TAIL"))
        gate_reports = {
            "TAIL": {"ok": False, "results": [{"output_tail": "X" * 5000}]},
        }
        report = R.build_round_report(queue, {}, gate_reports)
        self.assertEqual(len(report["lanes"]), 1)
        gate_tail = report["lanes"][0].get("gate_tail", "")
        self.assertLessEqual(len(gate_tail), R.MAX_GATE_TAIL_CHARS + 20)

    def test_malformed_input_never_raises(self) -> None:
        report_none = R.build_round_report(None, None, None)
        self.assertIsInstance(report_none, dict)
        self.assertEqual(report_none["lanes"], [])

        report_bad = R.build_round_report({"lanes": ["not a mapping", 42]}, None, None)
        self.assertIsInstance(report_bad, dict)
        self.assertEqual(report_bad["lanes"], [])

    def test_render_is_deterministic_and_has_totals(self) -> None:
        queue = _queue(
            _lane(id="R1", lane="alpha", status="ready_for_merge"),
            _lane(id="R2", lane="beta", status="blocked"),
        )
        gate_reports = {"R1": {"ok": True}}
        report_a = R.build_round_report(queue, {}, gate_reports, generated_at="2026-01-01T00:00:00Z")
        report_b = R.build_round_report(queue, {}, gate_reports, generated_at="2026-01-01T00:00:00Z")
        rendered_a = R.render_round_report_markdown(report_a)
        rendered_b = R.render_round_report_markdown(report_b)
        self.assertEqual(rendered_a, rendered_b)
        first_line = rendered_a.splitlines()[0] if rendered_a else ""
        self.assertIn("| id | lane | status |", first_line)
        self.assertTrue(
            any(line.startswith("Totals:") for line in rendered_a.splitlines()),
            msg="expected a line starting with 'Totals:'",
        )


if __name__ == "__main__":
    unittest.main()
