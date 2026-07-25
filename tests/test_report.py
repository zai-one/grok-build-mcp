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


class WiringAuditTests(unittest.TestCase):
    """R7 audit: the round-7 modules must be WIRED, not merely present.

    An audit after the round found three integration gaps that all tests missed because
    every suite exercised its module in isolation: report.py was imported by nobody, the
    live server never switched durable jobs on, and jobs.py did not export (or even define)
    the state its own rehydrate path can return.
    """

    def test_driver_imports_and_writes_the_round_report(self) -> None:
        from grok_delegate import driver

        self.assertTrue(hasattr(driver, "build_round_report"))
        self.assertTrue(hasattr(driver, "render_round_report_markdown"))
        self.assertTrue(callable(getattr(driver, "_write_round_report", None)))

    def test_round_report_file_is_written_atomically(self) -> None:
        import tempfile
        from pathlib import Path as _Path

        from grok_delegate import driver

        with tempfile.TemporaryDirectory() as td:
            vdir = _Path(td) / "verdicts"
            queue = {
                "lanes": [
                    {"id": "L1", "lane": "good", "status": "ready_for_merge", "attempts": 1}
                ]
            }
            path = driver._write_round_report(queue=queue, verdicts_dir=vdir, repo_root=td)
            self.assertIsNotNone(path)
            target = _Path(str(path))
            self.assertTrue(target.exists())
            body = target.read_text(encoding="utf-8")
            self.assertIn("| id | lane | status |", body)
            self.assertIn("ready_for_merge", body)
            # No temp file left behind (atomic replace, not a partial write).
            self.assertEqual(list(vdir.glob("*.tmp")), [])

    def test_report_failure_never_breaks_the_round(self) -> None:
        from grok_delegate import driver

        # A verdicts path that cannot be a directory must degrade to None, not raise.
        import tempfile
        from pathlib import Path as _Path

        with tempfile.TemporaryDirectory() as td:
            blocker = _Path(td) / "not-a-dir"
            blocker.write_text("x", encoding="utf-8")
            path = driver._write_round_report(
                queue={"lanes": []}, verdicts_dir=blocker, repo_root=td
            )
            self.assertIsNone(path)

    def test_server_enables_durable_jobs_from_env(self) -> None:
        import tempfile
        from pathlib import Path as _Path

        from grok_delegate import jobs, server

        self.assertTrue(callable(getattr(server, "configure_durable_jobs", None)))
        try:
            with tempfile.TemporaryDirectory() as td:
                target = _Path(td) / "jobs"
                used = server.configure_durable_jobs({"GROK_DELEGATE_JOBS_DIR": str(target)})
                self.assertEqual(_Path(str(used)), target)

                # Persistence is really on: a finished job lands on disk.
                jobs.reset_jobs_for_tests()
                jobs.start_job(
                    lambda: {"ok": True}, lane="wired", thread_starter=lambda fn: fn()
                )
                self.assertTrue(list(target.glob("*.json")))
        finally:
            jobs.configure_jobs_dir(None)
            jobs.reset_jobs_for_tests()

    def test_server_leaves_jobs_in_memory_when_env_is_unset(self) -> None:
        from grok_delegate import server

        self.assertIsNone(server.configure_durable_jobs({}))

    def test_jobs_exports_its_public_surface(self) -> None:
        from grok_delegate import jobs

        for name in ("configure_jobs_dir", "rehydrate_jobs", "STATE_UNKNOWN"):
            self.assertIn(name, jobs.__all__, f"jobs.{name} must be exported")
            self.assertTrue(hasattr(jobs, name))
        # The rehydrate path can hand back this state, so the constants must agree.
        from grok_delegate import jobs_store

        self.assertEqual(jobs.STATE_UNKNOWN, jobs_store.STATE_UNKNOWN)
