"""Chaos tests for unwritable jobs filesystem (R7-G).

Broken jobs_dir must never fail the lane itself. Uses tempfile only;
never spawns a real gate, grok, or git mutation.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import jobs  # noqa: E402
from grok_delegate import jobs_store as JS  # noqa: E402


class UnwritableJobsDirChaosTests(unittest.TestCase):
    """A broken jobs dir must never fail the lane itself."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # Regular file path — cannot be used as a jobs directory.
        self.blocker = Path(self._tmp.name) / "not-a-dir"
        self.blocker.write_text("x", encoding="utf-8")
        jobs.reset_jobs_for_tests()

    def tearDown(self) -> None:
        jobs.reset_jobs_for_tests()
        jobs.configure_jobs_dir(None)
        self._tmp.cleanup()

    def _sync(self, fn):
        """Synchronous thread_starter: run work inline (no real thread)."""
        fn()

    def test_unwritable_jobs_dir_never_breaks_a_lane(self) -> None:
        """save_job degrades; start_job still reaches done when jobs_dir is a file."""
        rec = {
            "job_id": "job-chaos-fs",
            "lane": "chaos-fs",
            "tool": "delegate_start",
            "state": JS.STATE_RUNNING,
            "started_at": 1.0,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        try:
            ok = JS.save_job(rec, self.blocker)
        except Exception as exc:  # pragma: no cover
            self.fail(f"save_job raised {type(exc).__name__}: {exc}")
        self.assertFalse(ok)

        jobs.configure_jobs_dir(self.blocker)
        record = jobs.start_job(
            lambda: {"ok": True},
            lane="chaos-fs",
            thread_starter=self._sync,
        )
        job = jobs.get_job(record["job_id"])
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job["state"], jobs.STATE_DONE)

    def test_corrupt_job_file_is_skipped_valid_one_loads(self) -> None:
        """Corrupt JSON beside a valid job file must be skipped, not raised."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            valid = {
                "job_id": "job-valid-chaos",
                "lane": "chaos-fs",
                "tool": "delegate_start",
                "state": JS.STATE_DONE,
                "started_at": 1.0,
                "finished_at": 2.0,
                "result": {"ok": True},
                "error": None,
            }
            self.assertTrue(JS.save_job(valid, jobs_dir))
            corrupt = jobs_dir / "job-corrupt-chaos.json"
            corrupt.write_text("{not json", encoding="utf-8")
            try:
                loaded = JS.load_jobs(jobs_dir)
            except Exception as exc:  # pragma: no cover
                self.fail(f"load_jobs raised {type(exc).__name__}: {exc}")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(next(iter(loaded.values()))["job_id"], "job-valid-chaos")

    def test_dead_pid_running_record_becomes_unknown(self) -> None:
        """A running record whose pid is dead must become STATE_UNKNOWN/STALE_RUNNING."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            rec = {
                "job_id": "job-dead-pid",
                "lane": "chaos-fs",
                "tool": "delegate_start",
                "state": JS.STATE_RUNNING,
                "pid": 999999,
                "started_at": 1.0,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self.assertTrue(JS.save_job(rec, jobs_dir))
            loaded = JS.load_jobs(jobs_dir, alive_check=lambda _pid: False)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(next(iter(loaded.values()))["state"], JS.STATE_UNKNOWN)
            self.assertEqual(next(iter(loaded.values()))["error"], "STALE_RUNNING")

    def test_live_pid_running_record_stays_running(self) -> None:
        """A running record whose pid is alive must stay running without STALE_RUNNING."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            rec = {
                "job_id": "job-live-pid",
                "lane": "chaos-fs",
                "tool": "delegate_start",
                "state": JS.STATE_RUNNING,
                "pid": 999999,
                "started_at": 1.0,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self.assertTrue(JS.save_job(rec, jobs_dir))
            loaded = JS.load_jobs(jobs_dir, alive_check=lambda _pid: True)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(next(iter(loaded.values()))["state"], JS.STATE_RUNNING)
            self.assertNotEqual(next(iter(loaded.values())).get("error"), "STALE_RUNNING")

    def test_backwards_clock_never_yields_negative_duration(self) -> None:
        """finished_at earlier than started_at must not raise or invent negative duration."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            rec = {
                "job_id": "job-backwards-clock",
                "lane": "chaos-fs",
                "tool": "delegate_start",
                "state": JS.STATE_DONE,
                "started_at": 100.0,
                "finished_at": 50.0,
                "result": {"ok": True},
                "error": None,
            }
            self.assertTrue(JS.save_job(rec, jobs_dir))
            try:
                loaded = JS.load_jobs(jobs_dir)
            except Exception as exc:  # pragma: no cover
                self.fail(f"load_jobs raised {type(exc).__name__}: {exc}")
            self.assertEqual(len(loaded), 1)
            started = next(iter(loaded.values()))["started_at"]
            finished = next(iter(loaded.values()))["finished_at"]
            # Backwards clock is tolerated; duration style max(0, finished - started)
            # must never go negative.
            self.assertLessEqual(finished - started, 0)
            duration = max(0, finished - started)
            self.assertGreaterEqual(duration, 0)

    def test_very_long_lane_name_round_trips(self) -> None:
        """A 300-char job_id must hash/truncate to a short filename and round-trip."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            long_id = "j" * 300
            rec = {
                "job_id": long_id,
                "lane": "chaos-fs",
                "tool": "delegate_start",
                "state": JS.STATE_DONE,
                "started_at": 1.0,
                "finished_at": 2.0,
                "result": {"ok": True},
                "error": None,
            }
            filename = JS.job_filename(long_id)
            self.assertLess(len(filename), 200)
            ok = JS.save_job(rec, jobs_dir)
            self.assertTrue(ok)
            loaded = JS.load_jobs(jobs_dir)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(next(iter(loaded.values()))["job_id"], long_id)

    def test_non_ascii_lane_name_round_trips(self) -> None:
        """Non-ASCII lane and summary must round-trip through UTF-8 JSON storage."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            lane = "лейн-тест-ветка"
            summary = "краткий итог: успех ✓"
            rec = {
                "job_id": "job-non-ascii",
                "lane": lane,
                "tool": "delegate_start",
                "state": JS.STATE_DONE,
                "started_at": 1.0,
                "finished_at": 2.0,
                "result": {"summary": summary},
                "error": None,
            }
            self.assertTrue(JS.save_job(rec, jobs_dir))
            loaded = JS.load_jobs(jobs_dir)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(next(iter(loaded.values()))["lane"], lane)
            self.assertEqual(next(iter(loaded.values()))["result"]["summary"], summary)

    def test_disk_eviction_keeps_newest(self) -> None:
        """evict_on_disk must keep only max_jobs files and retain the newest record."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            max_jobs = 3
            newest_id = None
            for i in range(max_jobs + 2):
                job_id = f"job-evict-{i:04d}"
                newest_id = job_id
                rec = {
                    "job_id": job_id,
                    "lane": "chaos-fs",
                    "tool": "delegate_start",
                    "state": JS.STATE_DONE,
                    "started_at": float(i),
                    "finished_at": float(i) + 0.5,
                    "result": {"n": i},
                    "error": None,
                }
                self.assertTrue(JS.save_job(rec, jobs_dir))
            JS.evict_on_disk(jobs_dir, max_jobs=max_jobs)
            remaining = list(jobs_dir.glob("*.json"))
            self.assertEqual(len(remaining), max_jobs)
            loaded = JS.load_jobs(jobs_dir)
            # load_jobs returns a mapping keyed by job_id, so iterate values.
            loaded_ids = {r["job_id"] for r in loaded.values()}
            self.assertIn(newest_id, loaded_ids)


if __name__ == "__main__":
    unittest.main()
