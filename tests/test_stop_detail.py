"""`ACP_STOP_cancelled` is three different things, and the receipt now says which.

An operator reported that a parallel launch kills the previous job, measured as
`ACP_STOP_cancelled` three times out of three. Reproduced here at the time: a
*single* execute job produced the identical receipt, so parallelism was not the
cause -- the worker had reached for a declared test command that failed argv
validation, the CLI ended the turn on the refusal, and the only trace was
`tests[0].not_run_reason` several levels down while `blocked_reason` said
something that sounded like an operator's cancel.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grok_delegate.agent_runtime import _CONCURRENCY_CEILING, stop_detail  # noqa: E402


class StopDetailTests(unittest.TestCase):
    def test_a_refused_permission_is_named(self) -> None:
        detail = stop_detail(
            {"blocked_reason": "ACP_STOP_cancelled", "denied_tool_calls": 2}
        )
        self.assertIsNotNone(detail)
        self.assertIn("permission_refused", detail)
        self.assertIn("2", detail)

    def test_a_test_command_the_verifier_could_not_run_is_named(self) -> None:
        """The case that cost an hour of forensics before this field existed."""
        detail = stop_detail(
            {
                "blocked_reason": "ACP_STOP_cancelled",
                "tests": [
                    {"command": 'py -3 -c "print(1)"', "not_run_reason": "invalid_command"}
                ],
            }
        )
        self.assertIsNotNone(detail)
        self.assertIn("invalid_command", detail)
        self.assertIn("py -3", detail)

    def test_it_does_not_guess_at_turn_exhaustion(self) -> None:
        """A tool call is not an ACP turn, so the two must not be compared.

        Measured on a live job: a packet asking for `max_turns=2` produced seven
        tool calls and still completed. A branch comparing them would have put a
        confident wrong reason on every cancel that happened to be chatty.
        """
        self.assertIsNone(
            stop_detail(
                {"blocked_reason": "ACP_STOP_cancelled", "tool_calls_made": 7, "max_turns": 2}
            )
        )

    def test_a_plain_cancel_says_nothing_rather_than_guessing(self) -> None:
        """An operator's cancel has no further explanation, and inventing one lies."""
        self.assertIsNone(stop_detail({"blocked_reason": "ACP_STOP_cancelled"}))

    def test_it_never_speaks_for_another_verdict(self) -> None:
        for reason in ("TEST_FAILED", "UNEXPECTED_CHANGED_FILES", "ACP_TIMEOUT", None):
            with self.subTest(reason=reason):
                self.assertIsNone(
                    stop_detail(
                        {
                            "blocked_reason": reason,
                            "denied_tool_calls": 3,
                        }
                    )
                )

    def test_a_refusal_outranks_a_turn_count(self) -> None:
        """Both can be true; the refusal is what the host has to act on."""
        detail = stop_detail(
            {
                "blocked_reason": "ACP_STOP_cancelled",
                "denied_tool_calls": 1,
                "tool_calls_made": 8,
            }
        )
        self.assertIn("permission_refused", detail)


class ConcurrencyCeilingTests(unittest.TestCase):
    def test_an_operator_may_ask_for_more_than_two_workers(self) -> None:
        """The old ceiling was 2, from the first commit, with nothing behind it.

        Measured before raising it: four read-only jobs at concurrency=4 finish
        in 15 s wall clock where three at concurrency=1 took 13.7, 30.8 and 95.0
        seconds, the last mostly waiting; four write jobs each preparing a
        worktree ran together in 124 s with every artifact produced.
        """
        self.assertGreaterEqual(
            _CONCURRENCY_CEILING, 4, "a host cannot have a fleet under this ceiling"
        )

    def test_the_default_is_sized_to_the_machine_not_pinned_at_one(self) -> None:
        """Pinning it at one was argued from a premise that misses the usage.

        "A lane is unmerged work someone reviews" holds for execute and fix. It
        does not hold for consult and review, which never prepare a worktree --
        `agent_runtime` says so itself -- so there is no branch and no git
        contention, and those are most of what a host dispatches. Serialising
        them cost an operator six jobs over forty minutes with one of them run.
        """
        from grok_delegate import agent_runtime

        for cores, expected in ((28, 4), (16, 4), (8, 4), (4, 2), (2, 1), (1, 1)):
            with self.subTest(cores=cores):
                self.assertEqual(
                    max(1, min(4, cores // 2)),
                    expected,
                    "a two-core laptop must still get one worker",
                )
        self.assertGreaterEqual(agent_runtime._default_concurrency(), 1)
        self.assertLessEqual(agent_runtime._default_concurrency(), 4)

    def test_an_operator_can_still_pin_it_to_one(self) -> None:
        """The old behaviour has to remain reachable, exactly."""
        self.assertEqual(max(1, min(int("1"), _CONCURRENCY_CEILING)), 1)


if __name__ == "__main__":
    unittest.main()
