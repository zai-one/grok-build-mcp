"""A finished job has to survive the restart that empties the registry.

The persistence layer shipped in R7-D and was never switched on: it woke up only
for `GROK_DELEGATE_JOBS_DIR`, which nothing sets. Measured before this test
existed -- register a completed job, clear the registry the way a restart does,
poll: `JOB_UNKNOWN`, for work that was finished and committed to a lane. An
orchestrator reading that answer scores a success as a failure and redoes it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grok_delegate import jobs, jobs_store  # noqa: E402


class ResolveJobsDirTests(unittest.TestCase):
    """Where records go, and how an operator opts out of them going anywhere."""

    def test_an_unset_variable_still_persists(self) -> None:
        """The whole defect in one assertion: no configuration must not mean no memory."""
        self.assertEqual(jobs_store.resolve_jobs_dir({}), jobs_store.default_jobs_dir())
        self.assertIsNotNone(jobs_store.resolve_jobs_dir({}))

    def test_an_empty_value_is_not_an_opt_out(self) -> None:
        """An env var set to whitespace by a launcher is not a decision."""
        self.assertEqual(
            jobs_store.resolve_jobs_dir({"GROK_DELEGATE_JOBS_DIR": "   "}),
            jobs_store.default_jobs_dir(),
        )

    def test_the_operator_can_turn_it_off(self) -> None:
        for value in ("off", "0", "false", "no", "none", "OFF"):
            with self.subTest(value=value):
                self.assertIsNone(
                    jobs_store.resolve_jobs_dir({"GROK_DELEGATE_JOBS_DIR": value})
                )

    def test_an_explicit_path_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                jobs_store.resolve_jobs_dir({"GROK_DELEGATE_JOBS_DIR": tmp}), Path(tmp)
            )

    def test_the_default_is_a_user_state_directory_not_the_project(self) -> None:
        """One process serves every project, so the records cannot live in one of them."""
        target = jobs_store.default_jobs_dir()
        self.assertIn("grok-delegate", target.parts)
        self.assertEqual(target.name, "jobs")
        self.assertNotEqual(target, Path.cwd() / ".grok" / "jobs")


class SurvivesRestartTests(unittest.TestCase):
    """The acceptance case: finished job, new process, poll returns the answer."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.env = {"GROK_DELEGATE_JOBS_DIR": self._dir.name}
        self.addCleanup(jobs.configure_jobs_dir, None)
        self.addCleanup(jobs.reset_jobs_for_tests)

    def _restart(self) -> None:
        """What a restart is, from the registry's point of view."""
        jobs.reset_jobs_for_tests()
        jobs.configure_jobs_dir(None)
        self.assertIsNone(jobs.get_job(self.job_id), "the registry did not actually clear")
        jobs.configure_from_env(self.env)

    def _finished_job(self, result: dict) -> str:
        """A job that ran to completion, through the registry's own path."""
        jid = jobs.new_job_id()
        jobs.start_job(
            lambda: result,
            lane="grok/sweep",
            tool="grok_agent_execute",
            job_id=jid,
            thread_starter=lambda fn: fn(),  # inline, so the job is done on return
        )
        return jid

    def test_a_completed_job_polls_back_after_a_restart(self) -> None:
        jobs.configure_from_env(self.env)
        self.job_id = self._finished_job(
            {
                "ok": True,
                "status": "completed",
                "summary": "the answer the orchestrator would have thrown away",
                "branch": "grok/sweep",
            }
        )

        self._restart()

        record = jobs.get_job(self.job_id)
        self.assertIsNotNone(record, "poll after restart answered JOB_UNKNOWN")
        self.assertEqual(record["state"], "done")
        self.assertEqual(record["result"]["status"], "completed")
        self.assertIn("orchestrator", record["result"]["summary"])
        self.assertEqual(record["result"]["branch"], "grok/sweep")

    def test_turning_persistence_off_loses_it_as_documented(self) -> None:
        """The opt-out has to actually opt out, or the flag is decoration."""
        jobs.configure_from_env({"GROK_DELEGATE_JOBS_DIR": "off"})
        self.job_id = self._finished_job({"ok": True, "status": "completed"})
        self.assertEqual(os.listdir(self._dir.name), [])

        jobs.reset_jobs_for_tests()
        jobs.configure_from_env({"GROK_DELEGATE_JOBS_DIR": "off"})
        self.assertIsNone(jobs.get_job(self.job_id))

    def test_a_running_record_from_a_dead_server_comes_back_unknown(self) -> None:
        """Otherwise a poller waits forever on a job whose process died with its server.

        The record has to come from another incarnation to mean anything: a
        simulated restart inside one process leaves `server_pid` alive, and the
        registry is right to keep trusting it.
        """
        self.job_id = "job-from-a-dead-server"
        jobs_store.save_job(
            {
                "job_id": self.job_id,
                "state": "running",
                "lane": "grok/sweep",
                "server_pid": 2**31 - 1,  # cannot be alive
                "started_at": 1.0,
            },
            self._dir.name,
        )

        jobs.reset_jobs_for_tests()
        jobs.configure_from_env(self.env)

        record = jobs.get_job(self.job_id)
        self.assertIsNotNone(record, "the record was not rehydrated at all")
        self.assertEqual(
            record["state"], "unknown", "a job from a dead server cannot still be running"
        )

if __name__ == "__main__":
    unittest.main()


class EveryTransportKeepsItsRecordsTests(unittest.TestCase):
    """Persistence was wired into `serve_stdio` and nowhere else."""

    def test_http_configures_durable_jobs(self) -> None:
        """The same promise, the other documented transport.

        Both calls to `configure_durable_jobs` lived in `serve_stdio`, so a
        bridge served over HTTP -- the shape AGENTS.md documents for a VPS --
        kept its jobs in memory and answered JOB_UNKNOWN after a restart, while
        the changelog said finished jobs survive one.
        """
        from grok_delegate import http_server

        source = Path(http_server.__file__).read_text(encoding="utf-8")
        body = source[source.index("def serve_http("):]
        body = body[: body.find("\ndef ")]
        self.assertIn(
            "configure_durable_jobs()",
            body,
            "serve_http does not enable durable job records",
        )


class SharedDirectoryTests(unittest.TestCase):
    """One per-user directory, several servers on the machine."""

    def test_eviction_leaves_a_live_neighbours_record_alone(self) -> None:
        """Otherwise the fix for a lost job recreates it between two hosts.

        The default directory is per-user because one process serves every
        project a host opens -- but a Cursor and a Claude on one machine share
        it too, and `evict_on_disk` walks the whole directory. Either could trim
        the other's finished records to make room for its own, and the
        neighbour would answer JOB_UNKNOWN for completed work as soon as it
        restarted.
        """
        mine = os.getpid()
        live_neighbour = 4242
        dead = 2**31 - 1

        def alive(pid: int) -> bool:
            return pid in {mine, live_neighbour}

        self.assertTrue(
            jobs_store._evictable({"server_pid": mine}, owner_pid=mine, alive=alive)
        )
        self.assertFalse(
            jobs_store._evictable(
                {"server_pid": live_neighbour}, owner_pid=mine, alive=alive
            ),
            "a record owned by a running neighbour was treated as ours to delete",
        )
        self.assertTrue(
            jobs_store._evictable({"server_pid": dead}, owner_pid=mine, alive=alive),
            "a record from a dead server is debris",
        )
        self.assertTrue(
            jobs_store._evictable({}, owner_pid=mine, alive=alive),
            "a record with no owner belongs to nobody",
        )

    def test_a_live_neighbours_files_survive_a_full_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(8):
                jobs_store.save_job(
                    {
                        "job_id": f"job-mine{index:02d}",
                        "state": "done",
                        "server_pid": os.getpid(),
                        "started_at": 100.0 + index,
                        "finished_at": 200.0 + index,
                    },
                    tmp,
                )
            jobs_store.save_job(
                {
                    "job_id": "job-neighbour",
                    "state": "done",
                    "server_pid": 4242,
                    "started_at": 1.0,
                    "finished_at": 2.0,
                },
                tmp,
            )

            jobs_store.evict_on_disk(tmp, max_jobs=3, alive=lambda pid: pid in {os.getpid(), 4242})

            survivors = {p.stem for p in Path(tmp).glob("*.json")}
            self.assertIn(
                "job-neighbour",
                survivors,
                "the oldest record was a live neighbour's and was deleted anyway",
            )
