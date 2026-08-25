"""Long work is normal work here, and a host should be able to watch it.

The bridge is asynchronous: a dispatch returns a job id, `grok_agent_poll`
reports phase and elapsed time and can block until the job is terminal while
emitting notifications/progress, and `grok_agent_cancel` ends it. An operator
asked for exactly that -- "let it answer for as long as it needs, the MCP
watches and reports status" -- and hit two walls: a packet ceiling of one hour
that no setting could raise, and a navigator poll card that never asked the
bridge to wait, so the host checked once and came back whenever it liked.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grok_delegate.contracts import MAX_TIMEOUT_SECONDS, validate_task_packet  # noqa: E402
from grok_delegate.guard import GuardError  # noqa: E402
from grok_delegate.session import compile_card_args, poll_wait_seconds  # noqa: E402


class TimeoutCeilingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.base = {
            "objective": "read the tree and answer",
            "role": "consult",
            "project_root": str(self.root),
            "correlation_id": "c",
            "test_commands": [],
        }

    def _packet(self, **extra):
        return validate_task_packet({**self.base, **extra}, allowed_roots=[self.root])

    def test_a_job_may_run_longer_than_an_hour(self) -> None:
        """One hour was a cap no setting could raise, and it cut answers short.

        Measured on the operator's repository: dispatches asking for 2400 s came
        back ACP_TIMEOUT with the reply broken mid-sentence.
        """
        self.assertGreater(MAX_TIMEOUT_SECONDS, 3600, "an hour is not long work")
        self.assertEqual(self._packet(timeout_seconds=14_400)["timeout_seconds"], 14_400)

    def test_the_ceiling_is_still_finite(self) -> None:
        """A wedged worker holds a slot; something has to reap it eventually."""
        with self.assertRaises(GuardError):
            self._packet(timeout_seconds=MAX_TIMEOUT_SECONDS + 1)

    def test_a_packet_that_says_nothing_is_unchanged(self) -> None:
        """Raising a ceiling must not move anybody's default."""
        self.assertEqual(self._packet()["timeout_seconds"], 1800)


class WatchingPollTests(unittest.TestCase):
    """The navigator's poll card, and the setting that makes it wait."""

    def setUp(self) -> None:
        self._previous = os.environ.get("GROK_DELEGATE_POLL_WAIT_SECONDS")
        self.addCleanup(self._restore)
        self.sess = {"job_id": "job-abc", "session_id": "s"}

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("GROK_DELEGATE_POLL_WAIT_SECONDS", None)
        else:
            os.environ["GROK_DELEGATE_POLL_WAIT_SECONDS"] = self._previous

    def _set(self, value: str) -> None:
        os.environ["GROK_DELEGATE_POLL_WAIT_SECONDS"] = value

    def test_off_by_default_because_a_block_needs_the_hosts_consent(self) -> None:
        """Several MCP clients time a request out in a minute; blocking them
        without being asked would break hosts that work today."""
        os.environ.pop("GROK_DELEGATE_POLL_WAIT_SECONDS", None)
        self.assertEqual(compile_card_args("grok_agent_poll", self.sess), {"job_id": "job-abc"})

    def test_when_asked_the_card_tells_the_bridge_to_watch(self) -> None:
        self._set("120")
        self.assertEqual(
            compile_card_args("grok_agent_poll", self.sess),
            {"job_id": "job-abc", "wait_seconds": 120},
        )

    def test_a_card_can_never_ask_for_more_than_the_tool_accepts(self) -> None:
        self._set("99999")
        self.assertEqual(compile_card_args("grok_agent_poll", self.sess)["wait_seconds"], 1800)

    def test_nonsense_is_ignored_rather_than_crashing_a_card(self) -> None:
        for value in ("nonsense", "-5", ""):
            with self.subTest(value=value):
                self._set(value)
                self.assertNotIn("wait_seconds", compile_card_args("grok_agent_poll", self.sess))

    def test_cancel_never_waits(self) -> None:
        """Cancelling is the thing you do when you are done waiting."""
        self._set("120")
        self.assertEqual(
            compile_card_args("grok_agent_cancel", self.sess), {"job_id": "job-abc"}
        )

    def test_the_ceiling_matches_the_tool_schema(self) -> None:
        from grok_delegate.server import MAX_POLL_WAIT_SECONDS

        self._set(str(MAX_POLL_WAIT_SECONDS * 10))
        self.assertEqual(poll_wait_seconds(), MAX_POLL_WAIT_SECONDS)


if __name__ == "__main__":
    unittest.main()
