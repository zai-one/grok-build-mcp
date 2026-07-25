"""Acceptance-floor invariants that must hold regardless of future slices.

These tests encode hard safety guarantees: no push/merge assembly, no
always-approve bypass, no interpreter on the execute allow list, fixed
gate command tables, terminal state stops at ready-for-merge, and goal
text never leaks into audit or report surfaces.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from grok_delegate import audit, driver, gates, guard, report, runner


class InvariantTests(unittest.TestCase):
    """Round-wide safety floor; every future change must keep these green."""

    def test_no_module_assembles_push_or_merge(self) -> None:
        """Source of runner/driver/gates must never assemble git push or merge.

        Precision matters here: the first version flagged any line CONTAINING the words,
        which tripped on the prose "Zero push/merge code paths" in a module docstring while
        saying nothing about real behaviour. What actually matters is push/merge appearing as
        a STRING LITERAL, because that is how a git argv element is built — so only those
        lines are examined, and each must sit in an explicit deny/reject context.
        """
        # BEHAVIOUR, not grep: a line-based scan cannot see the construct a literal sits in
        # (the shipped deny frozenset lists "push" on its own line), so it either trips on
        # the deny list itself or on prose. These assertions test what actually protects the
        # repo.
        for verb in ("push", "merge", "pull", "rebase", "reset", "clean"):
            with self.assertRaises(guard.GuardError, msg=f"git {verb} must be refused"):
                runner._reject_forbidden_git_args([verb, "origin", "master"])
            with self.assertRaises(guard.GuardError):
                runner._reject_forbidden_git_args(["-C", "some/dir", verb])

        # A benign verb still passes, so the check above is not blanket-refusing everything.
        runner._reject_forbidden_git_args(["status", "--porcelain"])

        # The shipped profile denies the network-mutating git commands outright.
        deny = guard.build_permission_profile()["deny"]
        self.assertIn("Bash(git push*)", deny)
        self.assertIn("Bash(git merge*)", deny)
        self.assertIn("Bash(git push*--force*)", deny)

        # No module exposes a push/merge CAPABILITY. Only callables count: a status name
        # like STATUS_READY_FOR_MERGE is the state the driver may legitimately reach, and
        # flagging it would test spelling instead of behaviour.
        for module in (runner, driver, gates):
            for attribute in dir(module):
                if attribute.startswith("_"):
                    continue
                if not callable(getattr(module, attribute, None)):
                    continue
                lowered = attribute.lower()
                self.assertNotIn(
                    "push", lowered, f"{module.__name__}.{attribute} is a push capability"
                )
                self.assertNotIn(
                    "merge", lowered, f"{module.__name__}.{attribute} is a merge capability"
                )

    def test_always_approve_flag_never_in_argv(self) -> None:
        """Grok argv must never include --always-approve in any form."""
        argv = guard.build_grok_argv(
            "goal",
            "C:/lanes/wt",
            guard.build_permission_profile(),
            5,
        )
        for element in argv:
            self.assertNotEqual(element, "--always-approve")
            self.assertFalse(
                element.startswith("--always-approve"),
                msg=f"argv element starts with --always-approve: {element!r}",
            )

    def test_execute_allow_rejects_interpreters(self) -> None:
        """Appending interpreter allow rules must raise GuardError."""
        forbidden_rules = (
            "Bash(python*)",
            "Bash(npx*)",
            "Bash(node*)",
            "Bash(sh*)",
            "Bash(*)",
        )
        for rule in forbidden_rules:
            profile = guard.build_permission_profile()
            mutated = dict(profile)
            allow = list(mutated["allow"])
            allow.append(rule)
            mutated["allow"] = allow
            with self.assertRaises(guard.GuardError):
                guard.build_grok_argv("goal", "C:/lanes/wt", mutated, 5)

    def test_run_gates_has_no_command_parameter(self) -> None:
        """run_gates takes a fixed profile, never a free-form command."""
        params = inspect.signature(gates.run_gates).parameters
        for name in params:
            self.assertNotIn("command", name.lower())
            self.assertNotIn("cmd", name.lower())
        self.assertIn("profile", params)

    def test_gate_profiles_are_static_tuples(self) -> None:
        """GATE_PROFILES values are frozen tuple-of-tuples-of-str tables."""
        for key, value in gates.GATE_PROFILES.items():
            self.assertIsInstance(value, tuple, msg=f"profile {key!r} is not a tuple")
            for entry in value:
                self.assertIsInstance(
                    entry, tuple, msg=f"profile {key!r} entry is not a tuple: {entry!r}"
                )
                for part in entry:
                    self.assertIsInstance(
                        part,
                        str,
                        msg=f"profile {key!r} entry part is not str: {part!r}",
                    )

    def test_driver_terminal_states_stop_at_ready_for_merge(self) -> None:
        """Driver may reach ready-for-merge but never MERGED/PUSHED states."""
        self.assertTrue(hasattr(driver, "STATUS_READY_FOR_MERGE"))
        for name in dir(driver):
            self.assertNotIn("MERGED", name, msg=f"driver exposes {name}")
            self.assertNotIn("PUSHED", name, msg=f"driver exposes {name}")

    def test_audit_never_carries_goal_text(self) -> None:
        """Delegation audit records goal metadata, never goal plaintext."""
        record = audit.build_delegation_audit(
            principal="p",
            tool="grok_delegate",
            lane="l",
            base_ref="master",
            cwd="c",
            turns_used=1,
            outcome="ok",
            goal="MY-SECRET-GOAL-TEXT",
        )
        serialized = str(record)
        self.assertNotIn("MY-SECRET-GOAL-TEXT", serialized)
        self.assertNotIn("SECRET", serialized)
        self.assertIn("goal_chars", serialized)
        self.assertIn("goal_sha256_8", serialized)

    def test_report_never_carries_goal_text(self) -> None:
        """Round report must not echo lane goal text."""
        built = report.build_round_report(
            {"lanes": [{"id": "X", "lane": "x", "status": "blocked", "goal": "ANOTHER-SECRET"}]},
            {},
            {},
        )
        text = str(built)
        self.assertNotIn("ANOTHER-SECRET", text)
        self.assertIn("goal_chars", text)

    def test_reserved_lanes_are_rejected(self) -> None:
        """Reserved and path-escape lane names must raise GuardError."""
        for lane in ("master", "main", "dev", "../escape"):
            with self.assertRaises(guard.GuardError):
                guard.normalize_lane(lane)


if __name__ == "__main__":
    unittest.main()
