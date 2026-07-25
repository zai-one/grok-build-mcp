"""Unit tests for grok_delegate.jobs_store (R7-D durable jobs).

Every scenario listed in GOAL-ROUND7-AUTONOMY.md R7-D. Uses tempfile and
mocked liveness (os.kill / alive_check); never spawns a real gate, grok, or
git mutation. Simulates server restart by load_jobs into a fresh dict.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import jobs_store as JS  # noqa: E402


def _record(
    job_id: str = "job-aaa111",
    *,
    state: str = JS.STATE_RUNNING,
    lane: str = "r7d-jobs",
    tool: str = "delegate_start",
    started_at: float | None = None,
    finished_at: float | None = None,
    pid: int | None = None,
    result: dict | None = None,
    error: str | None = None,
    **extra: object,
) -> dict:
    """Minimal job record shaped like jobs.start_job output."""
    rec: dict = {
        "job_id": job_id,
        "lane": lane,
        "tool": tool,
        "state": state,
        "started_at": time.time() if started_at is None else started_at,
        "finished_at": finished_at,
        "result": result,
        "error": error,
    }
    if pid is not None:
        rec["pid"] = pid
    rec.update(extra)
    return rec


class SaveAndLoadTests(unittest.TestCase):
    """save_job + load_jobs: first slice, atomic write and rehydrate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_job_writes_json_file(self) -> None:
        """write on start: save_job creates a readable JSON record."""
        rec = _record(state=JS.STATE_RUNNING, pid=12345)
        ok = JS.save_job(rec, self.jobs_dir)
        self.assertTrue(ok)
        path = JS.job_path(self.jobs_dir, rec["job_id"])
        self.assertTrue(path.is_file())
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["job_id"], rec["job_id"])
        self.assertEqual(on_disk["state"], JS.STATE_RUNNING)
        self.assertEqual(on_disk["pid"], 12345)

    def test_save_job_updates_on_done_and_error(self) -> None:
        """write on done/error: successive saves overwrite the same file."""
        rec = _record(job_id="job-lifecycle", state=JS.STATE_RUNNING, pid=99)
        self.assertTrue(JS.save_job(rec, self.jobs_dir))

        rec_done = dict(rec)
        rec_done["state"] = JS.STATE_DONE
        rec_done["finished_at"] = time.time()
        rec_done["result"] = {"ok": True, "changed_files": ["a.py"]}
        self.assertTrue(JS.save_job(rec_done, self.jobs_dir))
        loaded = JS.load_jobs(self.jobs_dir, alive_check=lambda _p: True)
        self.assertEqual(loaded["job-lifecycle"]["state"], JS.STATE_DONE)
        self.assertEqual(loaded["job-lifecycle"]["result"]["ok"], True)

        rec_err = dict(rec)
        rec_err["state"] = JS.STATE_ERROR
        rec_err["finished_at"] = time.time()
        rec_err["error"] = "boom"
        rec_err["result"] = None
        self.assertTrue(JS.save_job(rec_err, self.jobs_dir))
        loaded2 = JS.load_jobs(self.jobs_dir, alive_check=lambda _p: True)
        self.assertEqual(loaded2["job-lifecycle"]["state"], JS.STATE_ERROR)
        self.assertEqual(loaded2["job-lifecycle"]["error"], "boom")

    def test_load_jobs_rehydrates_after_simulated_restart(self) -> None:
        """rehydrate after simulated restart: disk → fresh registry dict."""
        for i, state in enumerate((JS.STATE_DONE, JS.STATE_ERROR, JS.STATE_RUNNING)):
            rec = _record(
                job_id=f"job-rehy{i}",
                state=state,
                started_at=1000.0 + i,
                finished_at=None if state == JS.STATE_RUNNING else 2000.0 + i,
                pid=1000 + i if state == JS.STATE_RUNNING else None,
                lane=f"lane-{i}",
            )
            self.assertTrue(JS.save_job(rec, self.jobs_dir))

        # "Restart": ignore any in-memory state; load from disk only.
        fresh_registry = JS.load_jobs(
            self.jobs_dir,
            alive_check=lambda pid: pid == 1002,  # only the running one is live
        )
        self.assertEqual(len(fresh_registry), 3)
        self.assertEqual(fresh_registry["job-rehy0"]["state"], JS.STATE_DONE)
        self.assertEqual(fresh_registry["job-rehy1"]["state"], JS.STATE_ERROR)
        self.assertEqual(fresh_registry["job-rehy2"]["state"], JS.STATE_RUNNING)
        self.assertEqual(fresh_registry["job-rehy0"]["lane"], "lane-0")

    def test_jobs_dir_missing_is_created(self) -> None:
        """jobs dir missing → created on save_job."""
        nested = self.jobs_dir / "nested" / "jobs"
        self.assertFalse(nested.exists())
        rec = _record(job_id="job-mkdir")
        self.assertTrue(JS.save_job(rec, nested))
        self.assertTrue(nested.is_dir())
        self.assertTrue(JS.job_path(nested, "job-mkdir").is_file())

    def test_load_jobs_missing_dir_returns_empty(self) -> None:
        """load_jobs on a missing directory returns {} without raising."""
        missing = self.jobs_dir / "does-not-exist"
        try:
            result = JS.load_jobs(missing)
        except Exception as exc:  # pragma: no cover
            self.fail(f"load_jobs raised {type(exc).__name__}: {exc}")
        self.assertEqual(result, {})

    def test_non_ascii_lane_names_round_trip(self) -> None:
        """non-ASCII lane names survive UTF-8 save/load."""
        rec = _record(
            job_id="job-unicode",
            state=JS.STATE_DONE,
            lane="полоса-αβγ-日本語",
            finished_at=time.time(),
            result={"ok": True, "summary": "готово ✓"},
        )
        self.assertTrue(JS.save_job(rec, self.jobs_dir))
        loaded = JS.load_jobs(self.jobs_dir)
        self.assertEqual(loaded["job-unicode"]["lane"], "полоса-αβγ-日本語")
        self.assertEqual(loaded["job-unicode"]["result"]["summary"], "готово ✓")

    def test_atomic_write_no_half_written_file_observed(self) -> None:
        """atomic write: readers never see a truncated/partial JSON body.

        We intercept os.replace and assert the temp file is complete JSON
        before the rename, and that the final path only appears after replace.
        """
        rec = _record(job_id="job-atomic", state=JS.STATE_DONE, finished_at=1.0)
        target = JS.job_path(self.jobs_dir, rec["job_id"])
        observed_before_replace: list[str] = []
        real_replace = os.replace

        def _spy_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            src_p = Path(src)
            # Temp file must already be valid complete JSON.
            body = src_p.read_text(encoding="utf-8")
            parsed = json.loads(body)
            self.assertEqual(parsed["job_id"], "job-atomic")
            # Final path must not yet exist as a half-written file.
            if target.exists():
                # Overwrite path: existing content must still be valid JSON.
                json.loads(target.read_text(encoding="utf-8"))
            observed_before_replace.append(body)
            real_replace(src, dst)

        with mock.patch("os.replace", side_effect=_spy_replace):
            self.assertTrue(JS.save_job(rec, self.jobs_dir))

        self.assertEqual(len(observed_before_replace), 1)
        final = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(final["job_id"], "job-atomic")
        # No leftover temp debris after successful replace.
        temps = [p for p in self.jobs_dir.iterdir() if p.name.startswith(".") and p.suffix == ".tmp"]
        self.assertEqual(temps, [])


class CorruptAndUnreadableTests(unittest.TestCase):
    """Corrupt / unreadable files are skipped; other jobs still load."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_corrupt_truncated_json_skipped_with_warning(self) -> None:
        """corrupt job file (truncated JSON) → skipped with warning."""
        good = _record(job_id="job-good", state=JS.STATE_DONE, finished_at=1.0)
        self.assertTrue(JS.save_job(good, self.jobs_dir))
        bad_path = self.jobs_dir / "job-bad.json"
        bad_path.write_text('{"job_id":"job-bad","state":"done"', encoding="utf-8")

        with self.assertLogs(JS.logger, level=logging.WARNING) as cm:
            loaded = JS.load_jobs(self.jobs_dir)

        self.assertIn("job-good", loaded)
        self.assertNotIn("job-bad", loaded)
        self.assertEqual(len(loaded), 1)
        self.assertTrue(any("corrupt" in line.lower() for line in cm.output))

    def test_unreadable_file_skipped_no_crash(self) -> None:
        """unreadable file (permission / OSError) → skipped, no crash."""
        good = _record(job_id="job-ok", state=JS.STATE_DONE, finished_at=1.0)
        self.assertTrue(JS.save_job(good, self.jobs_dir))
        blocked = self.jobs_dir / "job-blocked.json"
        blocked.write_text(json.dumps(_record(job_id="job-blocked", state=JS.STATE_DONE)), encoding="utf-8")

        real_read_text = Path.read_text

        def _flaky_read(self_path: Path, *args: object, **kwargs: object) -> str:
            if self_path.name == "job-blocked.json":
                raise PermissionError("simulated unreadable")
            return real_read_text(self_path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", _flaky_read):
            with self.assertLogs(JS.logger, level=logging.WARNING) as cm:
                try:
                    loaded = JS.load_jobs(self.jobs_dir)
                except Exception as exc:  # pragma: no cover
                    self.fail(f"load_jobs raised {type(exc).__name__}: {exc}")

        self.assertIn("job-ok", loaded)
        self.assertNotIn("job-blocked", loaded)
        self.assertTrue(any("unreadable" in line.lower() for line in cm.output))


class UnwritableDirTests(unittest.TestCase):
    """Unwritable jobs_dir degrades to no-op with warning, never raises."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_jobs_dir_unwritable_degrades_no_raise(self) -> None:
        """jobs dir unwritable → save_job returns False + warning, no raise."""
        rec = _record(job_id="job-nowrite")

        def _mkdir_fail(self_path: Path, *args: object, **kwargs: object) -> None:
            raise OSError("simulated read-only filesystem")

        # ensure_jobs_dir will try mkdir / probe; force failure via mkdir.
        with mock.patch.object(Path, "mkdir", _mkdir_fail):
            with self.assertLogs(JS.logger, level=logging.WARNING) as cm:
                try:
                    ok = JS.save_job(rec, self.jobs_dir / "readonly")
                except Exception as exc:  # pragma: no cover
                    self.fail(f"save_job raised {type(exc).__name__}: {exc}")
        self.assertFalse(ok)
        self.assertTrue(any("unwritable" in line.lower() or "uncreatable" in line.lower() for line in cm.output))

    def test_save_job_replace_failure_degrades(self) -> None:
        """OSError during atomic replace → False + warning, never raise."""
        rec = _record(job_id="job-replace-fail", state=JS.STATE_DONE, finished_at=1.0)
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertLogs(JS.logger, level=logging.WARNING) as cm:
                try:
                    ok = JS.save_job(rec, self.jobs_dir)
                except Exception as exc:  # pragma: no cover
                    self.fail(f"save_job raised {type(exc).__name__}: {exc}")
        self.assertFalse(ok)
        self.assertTrue(any("save_job" in line.lower() for line in cm.output))
        # Temp debris cleaned up (best effort).
        temps = list(self.jobs_dir.glob(".*.tmp"))
        self.assertEqual(temps, [])


class StaleRunningTests(unittest.TestCase):
    """Stale running records report unknown rather than lying."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_is_stale_running_dead_pid(self) -> None:
        """stale running with a dead pid → is_stale_running True."""
        rec = _record(state=JS.STATE_RUNNING, pid=4242)

        def _dead(_pid: int) -> bool:
            return False

        self.assertTrue(JS.is_stale_running(rec, alive_check=_dead))
        applied = JS.apply_stale_running(rec, alive_check=_dead)
        self.assertEqual(applied["state"], JS.STATE_UNKNOWN)
        # Original not mutated.
        self.assertEqual(rec["state"], JS.STATE_RUNNING)

    def test_is_stale_running_live_pid(self) -> None:
        """Live pid keeps running (not stale)."""
        rec = _record(state=JS.STATE_RUNNING, pid=os.getpid())
        self.assertFalse(JS.is_stale_running(rec, alive_check=lambda _p: True))

    def test_is_stale_running_missing_pid(self) -> None:
        """Running without a recorded pid cannot be proven live → stale."""
        rec = _record(state=JS.STATE_RUNNING)
        self.assertNotIn("pid", rec)
        self.assertTrue(JS.is_stale_running(rec))

    def test_is_stale_running_ignores_done(self) -> None:
        """Finished jobs are never stale-running."""
        rec = _record(state=JS.STATE_DONE, pid=1, finished_at=1.0)
        self.assertFalse(JS.is_stale_running(rec, alive_check=lambda _p: False))

    def test_load_jobs_marks_stale_running_unknown(self) -> None:
        """load_jobs: dead-pid running → state unknown in rehydrated registry."""
        rec = _record(job_id="job-stale", state=JS.STATE_RUNNING, pid=99999)
        self.assertTrue(JS.save_job(rec, self.jobs_dir))
        loaded = JS.load_jobs(self.jobs_dir, alive_check=lambda _p: False)
        self.assertEqual(loaded["job-stale"]["state"], JS.STATE_UNKNOWN)
        self.assertEqual(loaded["job-stale"]["error"], "STALE_RUNNING")

    def test_is_process_alive_psutil_free(self) -> None:
        """psutil-free liveness: POSIX uses os.kill(pid, 0); Win32 uses OpenProcess.

        Critical: on Windows os.kill terminates the target, so the production
        path must never call it for existence checks.
        """
        self.assertFalse(JS.is_process_alive(0))
        self.assertFalse(JS.is_process_alive(-1))
        self.assertFalse(JS.is_process_alive(True))  # type: ignore[arg-type]

        if sys.platform == "win32":
            with mock.patch.object(JS, "_windows_pid_alive", return_value=True) as alive:
                self.assertTrue(JS.is_process_alive(1234))
                alive.assert_called_once_with(1234)
            with mock.patch.object(JS, "_windows_pid_alive", return_value=False):
                self.assertFalse(JS.is_process_alive(1234))
            # Guard: production path must not touch os.kill on Windows.
            with mock.patch("os.kill") as kill:
                with mock.patch.object(JS, "_windows_pid_alive", return_value=False):
                    JS.is_process_alive(55)
                kill.assert_not_called()
            return

        with mock.patch("os.kill") as kill:
            kill.return_value = None
            self.assertTrue(JS.is_process_alive(1234))
            kill.assert_called_with(1234, 0)

        with mock.patch("os.kill", side_effect=ProcessLookupError()):
            self.assertFalse(JS.is_process_alive(1234))

        with mock.patch("os.kill", side_effect=PermissionError()):
            self.assertTrue(JS.is_process_alive(1234))


class EvictionTests(unittest.TestCase):
    """evict_on_disk keeps the newest max_jobs files."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_evict_on_disk_keeps_newest(self) -> None:
        """eviction keeps the newest MAX_JOBS on disk too."""
        for i in range(10):
            rec = _record(
                job_id=f"job-ev{i:02d}",
                state=JS.STATE_DONE,
                started_at=1000.0 + i,
                finished_at=2000.0 + i,
            )
            self.assertTrue(JS.save_job(rec, self.jobs_dir))

        removed = JS.evict_on_disk(self.jobs_dir, max_jobs=4)
        self.assertEqual(removed, 6)
        loaded = JS.load_jobs(self.jobs_dir)
        self.assertEqual(len(loaded), 4)
        # Newest started_at values survive.
        self.assertEqual(
            sorted(loaded.keys()),
            ["job-ev06", "job-ev07", "job-ev08", "job-ev09"],
        )

    def test_evict_prefers_active_over_finished(self) -> None:
        """A running job is retained even when older than finished ones."""
        running = _record(
            job_id="job-active",
            state=JS.STATE_RUNNING,
            started_at=1.0,
            pid=1,
        )
        self.assertTrue(JS.save_job(running, self.jobs_dir))
        for i in range(5):
            rec = _record(
                job_id=f"job-fin{i}",
                state=JS.STATE_DONE,
                started_at=100.0 + i,
                finished_at=200.0 + i,
            )
            self.assertTrue(JS.save_job(rec, self.jobs_dir))

        removed = JS.evict_on_disk(self.jobs_dir, max_jobs=3)
        self.assertEqual(removed, 3)
        loaded = JS.load_jobs(self.jobs_dir, alive_check=lambda _p: True)
        self.assertIn("job-active", loaded)
        self.assertEqual(len(loaded), 3)


class ConcurrentWritersTests(unittest.TestCase):
    """Two writers on the same job id: last-writer-wins without corruption."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_two_writers_last_writer_wins_no_corruption(self) -> None:
        """Sequential last-writer-wins; final file is valid complete JSON."""
        jid = "job-race"
        first = _record(job_id=jid, state=JS.STATE_RUNNING, pid=1, tool="first")
        second = _record(
            job_id=jid,
            state=JS.STATE_DONE,
            finished_at=9.0,
            tool="second",
            result={"ok": True},
        )
        self.assertTrue(JS.save_job(first, self.jobs_dir))
        self.assertTrue(JS.save_job(second, self.jobs_dir))

        path = JS.job_path(self.jobs_dir, jid)
        body = path.read_text(encoding="utf-8")
        # Must parse as complete JSON (no half-write corruption).
        data = json.loads(body)
        self.assertEqual(data["job_id"], jid)
        self.assertEqual(data["tool"], "second")
        self.assertEqual(data["state"], JS.STATE_DONE)

        loaded = JS.load_jobs(self.jobs_dir)
        self.assertEqual(loaded[jid]["tool"], "second")


class SafetyInvariantTests(unittest.TestCase):
    """R7-F: this module never assembles push/merge/always-approve."""

    def test_module_source_has_no_push_merge_always_approve(self) -> None:
        src = Path(JS.__file__).read_text(encoding="utf-8")
        lowered = src.lower()
        self.assertNotIn("git push", lowered)
        self.assertNotIn("git merge", lowered)
        self.assertNotIn("--always-approve", lowered)

    def test_save_job_without_job_id_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ok = JS.save_job({"state": "running"}, tmp)
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()


class JobsPersistenceWiringTests(unittest.TestCase):
    """Integrator wiring (R7-D): the live registry must actually persist and rehydrate.

    jobs_store is pure I/O; these tests prove jobs.start_job uses it, so a server restart
    no longer loses the status of lanes it already dispatched.
    """

    def setUp(self) -> None:
        from grok_delegate import jobs as jobs_mod

        self.jobs = jobs_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self.tmp.name) / "jobs"
        self.jobs.reset_jobs_for_tests()
        self.jobs.configure_jobs_dir(self.jobs_dir)

    def tearDown(self) -> None:
        self.jobs.configure_jobs_dir(None)
        self.jobs.reset_jobs_for_tests()
        self.tmp.cleanup()

    def _sync(self, fn):
        fn()

    def test_start_and_finish_are_written_to_disk(self) -> None:
        record = self.jobs.start_job(
            lambda: {"ok": True, "branch": "grok/persisted"},
            lane="persisted",
            thread_starter=self._sync,
        )
        job_id = record["job_id"]
        files = list(self.jobs_dir.glob("*.json"))
        self.assertEqual(len(files), 1, f"expected one job file, saw {files}")

        on_disk = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(on_disk["job_id"], job_id)
        self.assertEqual(on_disk["state"], self.jobs.STATE_DONE)
        self.assertEqual(on_disk["result"]["branch"], "grok/persisted")
        self.assertIsNotNone(on_disk.get("finished_at"))

    def test_pid_is_recorded_so_staleness_is_detectable(self) -> None:
        record = self.jobs.start_job(
            lambda: {"ok": True}, lane="pid", thread_starter=self._sync
        )
        self.assertEqual(record.get("pid"), os.getpid())

    def test_rehydrate_after_simulated_restart(self) -> None:
        self.jobs.start_job(
            lambda: {"ok": True, "branch": "grok/survivor"},
            lane="survivor",
            thread_starter=self._sync,
        )
        # Simulate a server restart: memory is gone, the jobs dir is not.
        self.jobs.reset_jobs_for_tests()
        self.assertEqual(self.jobs.list_jobs(limit=10), [])

        restored = self.jobs.rehydrate_jobs()
        self.assertEqual(restored, 1)
        lanes = [j["lane"] for j in self.jobs.list_jobs(limit=10)]
        self.assertIn("survivor", lanes)

    def test_failed_job_error_is_persisted(self) -> None:
        def boom():
            raise RuntimeError("kaboom")

        record = self.jobs.start_job(boom, lane="boom", thread_starter=self._sync)
        on_disk = json.loads(
            (self.jobs_dir / JS.job_filename(record["job_id"])).read_text(encoding="utf-8")
        )
        self.assertEqual(on_disk["state"], self.jobs.STATE_ERROR)
        self.assertIn("kaboom", on_disk["error"])

    def test_persistence_disabled_is_a_no_op(self) -> None:
        self.jobs.configure_jobs_dir(None)
        self.jobs.start_job(lambda: {"ok": True}, lane="nodisk", thread_starter=self._sync)
        self.assertFalse(self.jobs_dir.exists(), "no jobs dir must be created when disabled")

    def test_unwritable_jobs_dir_does_not_break_the_lane(self) -> None:
        # Point persistence at a path that cannot be a directory (a regular file).
        blocker = Path(self.tmp.name) / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        self.jobs.configure_jobs_dir(blocker)
        record = self.jobs.start_job(
            lambda: {"ok": True}, lane="degraded", thread_starter=self._sync
        )
        job = self.jobs.get_job(record["job_id"])
        assert job is not None
        self.assertEqual(job["state"], self.jobs.STATE_DONE, "lane must still complete")
