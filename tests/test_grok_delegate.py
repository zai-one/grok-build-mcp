#!/usr/bin/env python3
"""Unit tests for grok_delegate (mocked git + subprocess only).

No real grok spawn, no real git mutation. Drives shipped guard/runner/audit/
server entry points. Adversarial cases assert behavior of argv/profile/runner
paths (R4), not theater-only substring presence.
"""

from __future__ import annotations

import io
import json
import logging
import time
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "grok_delegate"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import audit as audit_mod  # noqa: E402
from grok_delegate import driver  # noqa: E402
from grok_delegate import guard  # noqa: E402
from grok_delegate import runner  # noqa: E402
from grok_delegate import server  # noqa: E402


class MockGit:
    """Configurable git mock — never touches the host repository."""

    def __init__(
        self,
        *,
        version_ok: bool = True,
        base_ok: bool = True,
        dirty: bool = False,
        worktree_add_ok: bool = True,
        existing_branch: str | None = None,
        name_only: str = "grok_delegate/guard.py\n",
        porcelain: str = " M grok_delegate/guard.py\n",
        diff_stat: str = " grok_delegate/guard.py | 10 ++++++++++\n 1 file changed\n",
        create_target: bool = True,
    ) -> None:
        self.version_ok = version_ok
        self.base_ok = base_ok
        self.dirty = dirty
        self.worktree_add_ok = worktree_add_ok
        self.existing_branch = existing_branch
        self.name_only = name_only
        self.porcelain = porcelain
        self.diff_stat = diff_stat
        self.create_target = create_target
        self.calls: list[list[str]] = []

    def __call__(
        self,
        args: list[str] | tuple[str, ...],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)

        lowered = [a.lower() for a in argv]
        for forbidden in ("push", "merge"):
            if forbidden in lowered:
                return {
                    "args": argv,
                    "returncode": 99,
                    "stdout": "",
                    "stderr": f"mock refused git {forbidden}",
                    "timedOut": False,
                }

        if argv[:1] == ["--version"] or (argv and argv[0] == "--version"):
            if not self.version_ok:
                return {
                    "args": argv,
                    "returncode": 127,
                    "stdout": "",
                    "stderr": "git not found",
                    "timedOut": False,
                    "missing": True,
                }
            return {
                "args": argv,
                "returncode": 0,
                "stdout": "git version 2.45.0\n",
                "stderr": "",
                "timedOut": False,
            }

        if "rev-parse" in argv and "--verify" in argv:
            if not self.base_ok:
                return {
                    "args": argv,
                    "returncode": 128,
                    "stdout": "",
                    "stderr": "fatal: Needed a single revision\n",
                    "timedOut": False,
                }
            return {
                "args": argv,
                "returncode": 0,
                "stdout": "abc123\n",
                "stderr": "",
                "timedOut": False,
            }

        if "status" in argv and "--porcelain" in argv:
            if self.dirty and "-C" not in argv:
                return {
                    "args": argv,
                    "returncode": 0,
                    "stdout": " M dirty.txt\n",
                    "stderr": "",
                    "timedOut": False,
                }
            return {
                "args": argv,
                "returncode": 0,
                "stdout": self.porcelain if "-C" in argv else "",
                "stderr": "",
                "timedOut": False,
            }

        if "worktree" in argv and "add" in argv:
            if not self.worktree_add_ok:
                return {
                    "args": argv,
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "fatal: worktree add failed\n",
                    "timedOut": False,
                }
            path = None
            if "-b" in argv:
                i = argv.index("-b")
                if i + 2 < len(argv):
                    path = Path(argv[i + 2])
            else:
                add_i = argv.index("add")
                if add_i + 1 < len(argv):
                    path = Path(argv[add_i + 1])
            if path is not None and self.create_target:
                path.mkdir(parents=True, exist_ok=True)
                (path / ".git").write_text("gitdir: mock\n", encoding="utf-8")
            return {
                "args": argv,
                "returncode": 0,
                "stdout": f"Preparing worktree at {path}\n",
                "stderr": "",
                "timedOut": False,
            }

        if "rev-parse" in argv and "--abbrev-ref" in argv:
            branch = self.existing_branch or "grok/unknown"
            return {
                "args": argv,
                "returncode": 0,
                "stdout": f"{branch}\n",
                "stderr": "",
                "timedOut": False,
            }

        if "diff" in argv and "--name-only" in argv:
            return {
                "args": argv,
                "returncode": 0,
                "stdout": self.name_only,
                "stderr": "",
                "timedOut": False,
            }

        if "diff" in argv and "--stat" in argv:
            return {
                "args": argv,
                "returncode": 0,
                "stdout": self.diff_stat,
                "stderr": "",
                "timedOut": False,
            }

        return {
            "args": argv,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "timedOut": False,
        }


class MockSubprocess:
    def __init__(
        self,
        *,
        ok: bool = True,
        stdout: str | None = None,
        missing: bool = False,
        timed_out: bool = False,
    ) -> None:
        self.ok = ok
        self.stdout = stdout if stdout is not None else json.dumps(
            {"summary": "done", "turns_used": 3}
        )
        self.missing = missing
        self.timed_out = timed_out
        self.calls: list[list[str]] = []

    def __call__(
        self,
        args: list[str] | tuple[str, ...],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        if self.missing:
            return {
                "args": argv,
                "returncode": 127,
                "stdout": "",
                "stderr": "not found",
                "timedOut": False,
                "missing": True,
            }
        if self.timed_out:
            return {
                "args": argv,
                "returncode": 124,
                "stdout": "",
                "stderr": "timeout",
                "timedOut": True,
            }
        return {
            "args": argv,
            "returncode": 0 if self.ok else 1,
            "stdout": self.stdout,
            "stderr": "",
            "timedOut": False,
        }


def _assert_argv_headless_and_safe(test: unittest.TestCase, argv: list[str]) -> None:
    """Behavior checks on shipped argv (R1/B2/R3)."""
    test.assertNotIn(guard.ALWAYS_APPROVE_FLAG, argv)
    test.assertNotIn("bypassPermissions", argv)
    test.assertTrue(
        guard.argv_has_headless_interface(argv),
        f"argv missing headless interface: {argv}",
    )
    test.assertIn("--cwd", argv)
    test.assertIn("--output-format", argv)
    test.assertIn("json", argv)
    test.assertIn("--permission-mode", argv)
    mode_i = argv.index("--permission-mode")
    test.assertNotEqual(argv[mode_i + 1], guard.BYPASS_PERMISSIONS_MODE)
    # Must not be bare interactive positional alone: last flag-pair is --single <goal>
    test.assertIn(guard.HEADLESS_SINGLE_LONG, argv)
    si = argv.index(guard.HEADLESS_SINGLE_LONG)
    test.assertLess(si + 1, len(argv))
    test.assertFalse(str(argv[si + 1]).startswith("--"))


class GuardTests(unittest.TestCase):
    def test_normalize_lane_accepts_slug_and_prefix(self) -> None:
        self.assertEqual(guard.normalize_lane("my-slice"), "grok/my-slice")
        self.assertEqual(guard.normalize_lane("grok/my-slice"), "grok/my-slice")

    def test_reserved_lane_reject_dev_master_main(self) -> None:
        for name in ("dev", "master", "main", "grok/dev", "grok/master", "grok/main"):
            with self.subTest(name=name):
                with self.assertRaises(guard.GuardError) as ctx:
                    guard.normalize_lane(name)
                self.assertEqual(ctx.exception.code, "LANE_RESERVED")

    def test_empty_and_invalid_lane_reject(self) -> None:
        for name in ("", "  ", None, "UPPER", "has space", "a/b/c", "-bad"):
            with self.subTest(name=name):
                with self.assertRaises(guard.GuardError):
                    guard.normalize_lane(name)  # type: ignore[arg-type]

    def test_enforce_bounds_hard_cap(self) -> None:
        self.assertEqual(guard.enforce_bounds(10), 10)
        self.assertEqual(guard.enforce_bounds(None), guard.HARD_CAP_MAX_TURNS)
        with self.assertRaises(guard.GuardError) as ctx:
            guard.enforce_bounds(guard.HARD_CAP_MAX_TURNS + 1)
        self.assertEqual(ctx.exception.code, "MAX_TURNS_CAP")
        with self.assertRaises(guard.GuardError):
            guard.enforce_bounds(0)

    def test_profile_denies_push_merge_and_no_interpreters(self) -> None:
        profile = guard.build_permission_profile(plan_only=False)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))
        self.assertTrue(guard.profile_denies_cwd_escape(profile))
        self.assertFalse(guard.profile_allows_interpreters(profile))
        # Structural: exact deny rules present
        self.assertIn("Bash(git push*)", profile["deny"])
        self.assertIn("Bash(git merge*)", profile["deny"])
        self.assertIn("Write([A-Za-z]:/**)", profile["deny"])
        self.assertIn("Edit([A-Za-z]:/**)", profile["deny"])
        self.assertIn("Read(~/.grok/**)", profile["deny"])
        self.assertIn("Bash(rm -rf*)", profile["deny"])
        # R2: interpreters not in allow
        allow = profile["allow"]
        self.assertNotIn("Bash(python*)", allow)
        self.assertNotIn("Bash(pytest*)", allow)
        self.assertNotIn("Bash(npm*)", allow)

    def test_plan_only_read_only_profile(self) -> None:
        profile = guard.build_permission_profile(plan_only=True)
        deny = profile["deny"]
        tools = profile["disallowed_tools"]
        self.assertIn("Write(**)", deny)
        self.assertIn("Edit(**)", deny)
        self.assertIn("Bash(*)", deny)
        self.assertIn("Write", tools)
        self.assertIn("Bash", tools)
        self.assertEqual(profile["permission_mode"], guard.PERMISSION_MODE_PLAN)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))

    def test_argv_uses_headless_single_not_positional(self) -> None:
        profile = guard.build_permission_profile(False)
        goal = "implement feature X"
        argv = guard.build_grok_argv(
            goal,
            "/tmp/wt",
            profile,
            12,
            model="grok-4",
            plan_only=False,
        )
        _assert_argv_headless_and_safe(self, argv)
        self.assertIn("--max-turns", argv)
        self.assertIn("12", argv)
        self.assertIn("--no-plan", argv)
        self.assertIn("--deny", argv)
        self.assertIn("--disallowed-tools", argv)
        self.assertIn("--model", argv)
        # Goal is value of --single, not a bare trailing interactive prompt only
        si = argv.index("--single")
        self.assertEqual(argv[si + 1], goal)
        # No bare goal without headless flag: if goal appears, it must be after --single
        goal_positions = [i for i, a in enumerate(argv) if a == goal]
        self.assertEqual(goal_positions, [si + 1])
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")

    def test_argv_plan_only_omits_no_plan_uses_plan_mode(self) -> None:
        profile = guard.build_permission_profile(True)
        argv = guard.build_grok_argv(
            "plan only",
            "/tmp/wt",
            profile,
            5,
            plan_only=True,
        )
        self.assertNotIn("--no-plan", argv)
        self.assertNotIn(guard.ALWAYS_APPROVE_FLAG, argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        self.assertTrue(guard.argv_has_headless_interface(argv))

    def test_assert_argv_safe_blocks_smuggled_always_approve(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(["grok", "--always-approve", "x"])
        self.assertEqual(ctx.exception.code, "ALWAYS_APPROVE_FORBIDDEN")

    def test_assert_argv_safe_requires_headless(self) -> None:
        # Bare interactive positional is rejected
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(
                [
                    "grok",
                    "--cwd",
                    "/wt",
                    "--output-format",
                    "json",
                    "--permission-mode",
                    "dontAsk",
                    "interactive goal only",
                ]
            )
        self.assertEqual(ctx.exception.code, "ARGV_NOT_HEADLESS")

    def test_assert_argv_safe_blocks_bypass_permissions(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(
                [
                    "grok",
                    "--cwd",
                    "/wt",
                    "--output-format",
                    "json",
                    "--permission-mode",
                    "bypassPermissions",
                    "--single",
                    "x",
                ]
            )
        self.assertEqual(ctx.exception.code, "PERMISSION_MODE_FORBIDDEN")

    def test_build_argv_rejects_interpreter_allow(self) -> None:
        profile = {
            "allow": ["Bash(python*)"],
            "deny": [],
            "disallowed_tools": [],
            "permission_mode": "dontAsk",
        }
        with self.assertRaises(guard.GuardError) as ctx:
            guard.build_grok_argv("g", "/wt", profile, 3)
        self.assertEqual(ctx.exception.code, "ALLOW_INTERPRETER_FORBIDDEN")

    def test_validate_grok_bin_client_forbidden(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_grok_bin("C:/evil/python.exe", from_client=True)
        self.assertEqual(ctx.exception.code, "GROK_BIN_CLIENT_FORBIDDEN")

    def test_validate_grok_bin_rejects_python_path(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_grok_bin("C:/Python/python.exe", from_client=False)
        self.assertEqual(ctx.exception.code, "GROK_BIN_INVALID")


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.lanes = Path(self.tmp.name) / "pcp-lanes"
        self.lanes.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_prepare_rejects_target_inside_repo(self) -> None:
        git = MockGit()
        inside = self.repo / "nested-lanes"
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=inside,
            git_runner=git,
            require_clean_base=True,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "WORKTREE_INSIDE_REPO")
        add_calls = [c for c in git.calls if "worktree" in c and "add" in c]
        self.assertEqual(add_calls, [])

    def test_prepare_rejects_dirty_base(self) -> None:
        git = MockGit(dirty=True)
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "BASE_DIRTY")
        add_calls = [c for c in git.calls if "worktree" in c and "add" in c]
        self.assertEqual(add_calls, [])

    def test_prepare_rejects_missing_git(self) -> None:
        git = MockGit(version_ok=False)
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GIT_MISSING")

    def test_prepare_rejects_unreachable_base(self) -> None:
        git = MockGit(base_ok=False)
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "BASE_UNREACHABLE")

    def test_prepare_rejects_reserved_lane(self) -> None:
        git = MockGit()
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="dev",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "LANE_RESERVED")

    def test_prepare_success_creates_external_worktree(self) -> None:
        git = MockGit()
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="demo-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("lane"), "grok/demo-slice")
        self.assertEqual(result.get("branch"), "grok/demo-slice")
        wt = Path(result["worktree_path"])
        self.assertTrue(wt.exists())
        self.assertFalse(runner.is_path_inside(wt, self.repo))
        add_calls = [c for c in git.calls if "worktree" in c and "add" in c]
        self.assertEqual(len(add_calls), 1)
        self.assertIn("-b", add_calls[0])
        self.assertIn("grok/demo-slice", add_calls[0])

    def test_run_delegation_argv_safe_and_no_spawn_on_missing(self) -> None:
        sp = MockSubprocess(missing=True)
        result = runner.run_delegation(
            goal="do thing",
            worktree_path=self.lanes / "x",
            max_turns=5,
            subprocess_runner=sp,
            which=lambda _n: None,
            grok_bin="grok",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GROK_MISSING")
        self.assertEqual(sp.calls, [])

    def test_run_delegation_never_passes_always_approve_and_is_headless(self) -> None:
        sp = MockSubprocess(ok=True)
        (self.lanes / "wt").mkdir()
        result = runner.run_delegation(
            goal="implement safely",
            worktree_path=self.lanes / "wt",
            max_turns=7,
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(sp.calls), 1)
        argv = sp.calls[0]
        _assert_argv_headless_and_safe(self, argv)
        self.assertIn("--max-turns", argv)
        self.assertIn("7", argv)

    def test_run_delegation_rejects_over_cap_before_spawn(self) -> None:
        sp = MockSubprocess(ok=True)
        result = runner.run_delegation(
            goal="x",
            worktree_path=self.lanes / "wt",
            max_turns=guard.HARD_CAP_MAX_TURNS + 50,
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "MAX_TURNS_CAP")
        self.assertEqual(sp.calls, [])

    def test_run_delegation_rejects_invalid_grok_bin_before_spawn(self) -> None:
        sp = MockSubprocess(ok=True)
        result = runner.run_delegation(
            goal="x",
            worktree_path=self.lanes / "wt",
            max_turns=3,
            subprocess_runner=sp,
            which=lambda n: n,
            grok_bin="python.exe",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GROK_BIN_INVALID")
        self.assertEqual(sp.calls, [])

    def test_collect_diff_no_full_patch(self) -> None:
        git = MockGit()
        out = runner.collect_diff(self.lanes / "wt", git_runner=git)
        self.assertTrue(out.get("ok"))
        self.assertIn("guard.py", " ".join(out["changed_files"]))
        self.assertIn("file changed", out["diffstat"])
        for c in git.calls:
            if "diff" in c:
                self.assertTrue("--stat" in c or "--name-only" in c)

    def test_delegate_happy_path_mocked(self) -> None:
        git = MockGit()
        sp = MockSubprocess(ok=True)
        result = runner.delegate(
            goal="add tests",
            lane="happy-path",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("lane"), "grok/happy-path")
        self.assertEqual(result.get("branch"), "grok/happy-path")
        self.assertIn("worktree_path", result)
        self.assertIsInstance(result.get("changed_files"), list)
        self.assertIn("diffstat", result)
        for c in git.calls:
            self.assertNotIn("push", c)
            self.assertNotIn("merge", c)
        self.assertEqual(len(sp.calls), 1)
        _assert_argv_headless_and_safe(self, sp.calls[0])

    def test_delegate_fail_closed_dirty_does_not_spawn_when_requested(self) -> None:
        """Strict mode is still available and still fails closed before spawning."""
        git = MockGit(dirty=True)
        sp = MockSubprocess(ok=True)
        result = runner.delegate(
            goal="should not run",
            lane="blocked",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            require_clean_base=True,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "BASE_DIRTY")
        self.assertEqual(sp.calls, [])

    def test_delegate_default_tolerates_dirty_main_tree(self) -> None:
        """R6: a lane branches from a COMMITTED base_ref, so main-tree dirt is
        irrelevant. The old default rejected every dispatch with BASE_DIRTY until
        the caller stashed unrelated work."""
        git = MockGit(dirty=True)
        sp = MockSubprocess(ok=True)
        result = runner.delegate(
            goal="runs anyway",
            lane="dirty-tolerated",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
        )
        self.assertTrue(result.get("ok"), msg=result.get("message"))
        self.assertEqual(len(sp.calls), 1)

    def test_default_git_runner_rejects_push_verb(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            runner._reject_forbidden_git_args(["push", "origin", "dev"])
        self.assertEqual(ctx.exception.code, "GIT_VERB_FORBIDDEN")

    def test_default_git_runner_rejects_merge_verb(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            runner._reject_forbidden_git_args(["merge", "dev"])
        self.assertEqual(ctx.exception.code, "GIT_VERB_FORBIDDEN")

    def test_subprocess_rejects_always_approve(self) -> None:
        with self.assertRaises(guard.GuardError):
            runner._reject_always_approve(["grok", "--always-approve"])


class AuditTests(unittest.TestCase):
    def test_emit_allowlisted_fields_only(self) -> None:
        buf = io.StringIO()
        event = audit_mod.build_delegation_audit(
            principal="local-dev",
            tool="grok_delegate",
            lane="grok/x",
            base_ref="origin/dev",
            cwd="/lanes/x",
            turns_used=3,
            outcome="ok",
            goal="super secret goal text that must not appear",
            changed_file_count=2,
        )
        event["goal"] = "super secret goal text that must not appear"
        event["diff"] = "+++ full patch"
        event["api_key"] = "should-be-dropped"
        safe = audit_mod.emit(event, stream=buf)
        line = buf.getvalue()
        self.assertNotIn("super secret", line)
        self.assertNotIn("full patch", line)
        self.assertNotIn("should-be-dropped", line)
        self.assertNotIn("~/.grok", line)
        self.assertIn("goal_chars", line)
        self.assertIn("goal_sha256_8", line)
        self.assertEqual(safe["principal"], "local-dev")
        self.assertEqual(safe["tool"], "grok_delegate")
        self.assertEqual(safe["outcome"], "ok")

    def test_sanitize_rejects_auth_path_leak(self) -> None:
        with self.assertRaises(audit_mod.AuditError):
            audit_mod.sanitize_event(
                {
                    "principal": "x",
                    "tool": "t",
                    "lane": "l",
                    "base_ref": "b",
                    "cwd": "/home/user/.grok/auth.json",
                    "turns_used": 1,
                    "outcome": "ok",
                }
            )

    def test_no_secret_patterns_in_emit(self) -> None:
        buf = io.StringIO()
        audit_mod.emit(
            {
                "principal": "local-dev",
                "tool": "grok_delegate",
                "lane": "grok/a",
                "base_ref": "origin/dev",
                "cwd": "/tmp/wt",
                "turns_used": 1,
                "outcome": "error",
                "error": "GROK_MISSING",
            },
            stream=buf,
        )
        data = json.loads(buf.getvalue())
        for key in data:
            self.assertIn(key, audit_mod.ALLOWED_FIELDS | {"ts"})


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.lanes = Path(self.tmp.name) / "pcp-lanes"
        self.lanes.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_handle_tool_call_delegate_mocked(self) -> None:
        git = MockGit()
        sp = MockSubprocess(ok=True)
        buf = io.StringIO()
        result = server.handle_tool_call(
            "grok_delegate",
            {
                "goal": "ship feature",
                "lane": "mcp-demo",
                "max_turns": 6,
                "lanes_parent": str(self.lanes),
            },
            repo_root=self.repo,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
            audit_stream=buf,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("lane"), "grok/mcp-demo")
        self.assertIn("diffstat", result)
        audit_line = buf.getvalue()
        self.assertIn("grok_delegate", audit_line)
        self.assertNotIn("ship feature", audit_line)
        self.assertEqual(len(sp.calls), 1)
        _assert_argv_headless_and_safe(self, sp.calls[0])

    def test_handle_tool_plan_sets_plan_only(self) -> None:
        git = MockGit()
        sp = MockSubprocess(ok=True)
        result = server.handle_tool_call(
            "grok_delegate_plan",
            {
                "goal": "plan the work",
                "lane": "plan-demo",
                "lanes_parent": str(self.lanes),
            },
            repo_root=self.repo,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(sp.calls), 1)
        argv = sp.calls[0]
        self.assertNotIn("--no-plan", argv)
        self.assertNotIn("--always-approve", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        _assert_argv_headless_and_safe(self, argv)

    def test_handle_jsonrpc_tools_list(self) -> None:
        resp = server.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        assert resp is not None
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("grok_delegate", names)
        self.assertIn("grok_delegate_plan", names)
        schema = resp["result"]["tools"][0]["inputSchema"]
        self.assertNotIn("grok_bin", schema.get("properties", {}))
        self.assertEqual(schema.get("additionalProperties"), False)

    def test_handle_jsonrpc_initialize(self) -> None:
        resp = server.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {},
            }
        )
        assert resp is not None
        self.assertEqual(resp["result"]["serverInfo"]["name"], "grok-delegate")

    def test_reserved_lane_via_tool(self) -> None:
        result = server.handle_tool_call(
            "grok_delegate",
            {"goal": "nope", "lane": "master", "lanes_parent": str(self.lanes)},
            repo_root=self.repo,
            git_runner=MockGit(),
            subprocess_runner=MockSubprocess(),
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "LANE_RESERVED")

    def test_client_grok_bin_rejected(self) -> None:
        result = server.handle_tool_call(
            "grok_delegate",
            {
                "goal": "nope",
                "lane": "x",
                "lanes_parent": str(self.lanes),
                "grok_bin": "C:/Windows/System32/cmd.exe",
            },
            repo_root=self.repo,
            git_runner=MockGit(),
            subprocess_runner=MockSubprocess(),
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GROK_BIN_CLIENT_FORBIDDEN")

    def test_client_repo_root_mismatch_rejected_when_env_set(self) -> None:
        sp = MockSubprocess(ok=True)
        with mock.patch.dict(
            os.environ,
            {"GROK_DELEGATE_REPO_ROOT": str(self.repo.resolve())},
            clear=False,
        ):
            result = server.handle_tool_call(
                "grok_delegate",
                {
                    "goal": "nope",
                    "lane": "x",
                    "lanes_parent": str(self.lanes),
                    "repo_root": str(Path(self.tmp.name) / "other"),
                },
                repo_root=self.repo,
                git_runner=MockGit(),
                subprocess_runner=sp,
                which=lambda n: "/mock/grok",
                audit_stream=io.StringIO(),
            )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "REPO_ROOT_UNTRUSTED")
        self.assertEqual(sp.calls, [])

    def test_client_lanes_parent_inside_repo_rejected(self) -> None:
        sp = MockSubprocess(ok=True)
        result = server.handle_tool_call(
            "grok_delegate",
            {
                "goal": "nope",
                "lane": "x",
                "lanes_parent": str(self.repo / "inside"),
            },
            repo_root=self.repo,
            git_runner=MockGit(),
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "LANES_PARENT_INSIDE_REPO")
        self.assertEqual(sp.calls, [])


class StaticBoundaryTests(unittest.TestCase):
    """Structural checks on shipped sources (no product surface / no push)."""

    def test_no_src_imports(self) -> None:
        for path in _PKG.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"from\s+src\b|import\s+src\b")

    def test_no_push_merge_helpers_in_runner(self) -> None:
        text = (_PKG / "runner.py").read_text(encoding="utf-8")
        self.assertNotIn("def push", text)
        self.assertNotIn("def merge", text)
        self.assertNotIn('["push"', text)
        self.assertNotIn('["merge"', text)
        self.assertNotIn('git", "push"', text)
        self.assertNotIn('git", "merge"', text)
        self.assertIn("_FORBIDDEN_GIT_VERBS", text)
        self.assertIn("GIT_VERB_FORBIDDEN", text)

    def test_never_reads_auth_json(self) -> None:
        guard_text = (_PKG / "guard.py").read_text(encoding="utf-8")
        self.assertIn("auth.json", guard_text)
        self.assertIn("_DENY_AUTH", guard_text)
        for path in (
            _PKG / "runner.py",
            _PKG / "server.py",
            _PKG / "audit.py",
            _PKG / "guard.py",
        ):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("read_auth", src)
            self.assertNotIn("load_auth", src)
            self.assertNotIn('Path.home() / ".grok"', src)
            self.assertNotIn("Path.home()/'.grok'", src)
            self.assertNotIn('open(os.path.expanduser("~/.grok', src)

    def test_package_modules_exist(self) -> None:
        for name in ("guard.py", "runner.py", "audit.py", "server.py", "README.md"):
            self.assertTrue((_PKG / name).is_file(), name)

    def test_schema_has_no_grok_bin(self) -> None:
        self.assertNotIn("grok_bin", server._INPUT_SCHEMA["properties"])
        self.assertFalse(server._INPUT_SCHEMA.get("additionalProperties", True))


class AdversarialBypassMapTests(unittest.TestCase):
    """Behavior-level bypass map (R4) — drives shipped functions, not theater."""

    def test_vector_escape_cwd_profile_has_windows_and_unc_denies(self) -> None:
        # file:symbol guard.build_permission_profile / profile_denies_cwd_escape
        profile = guard.build_permission_profile(False)
        self.assertTrue(guard.profile_denies_cwd_escape(profile))
        deny = profile["deny"]
        self.assertIn("Write([A-Za-z]:/**)", deny)
        self.assertIn("Edit([A-Za-z]:/**)", deny)
        self.assertIn("Write(//**)", deny)
        # Relative ../ is NOT claimed closed — helper must not rely on //** alone.
        # Build argv and ensure permission-mode + cwd are present (real path).
        argv = guard.build_grok_argv("g", r"C:\lanes\wt", profile, 3)
        self.assertIn("--cwd", argv)
        self.assertIn(r"C:\lanes\wt", argv)
        self.assertIn("--permission-mode", argv)
        self.assertNotEqual(
            argv[argv.index("--permission-mode") + 1],
            "bypassPermissions",
        )

    def test_vector_main_tree_closed_by_prepare(self) -> None:
        # file:symbol runner.prepare_worktree / is_path_inside
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            git = MockGit()
            result = runner.prepare_worktree(
                repo_root=repo,
                lane="x",
                lanes_parent=repo / "inside",
                git_runner=git,
            )
            self.assertEqual(result.get("error"), "WORKTREE_INSIDE_REPO")

    def test_vector_reserved_lane_closed(self) -> None:
        # file:symbol guard.normalize_lane
        with self.assertRaises(guard.GuardError):
            guard.normalize_lane("dev")

    def test_vector_push_merge_closed_by_profile_and_runner(self) -> None:
        # file:symbol guard.build_permission_profile + runner._reject_forbidden_git_args
        profile = guard.build_permission_profile(False)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))
        argv = guard.build_grok_argv("g", "/wt", profile, 3)
        # Deny rules are actually on argv (behavior of builder), not just profile dict.
        deny_values = [argv[i + 1] for i, a in enumerate(argv) if a == "--deny"]
        self.assertTrue(any(v.startswith("Bash(git push") for v in deny_values))
        self.assertTrue(any(v.startswith("Bash(git merge") for v in deny_values))
        with self.assertRaises(guard.GuardError):
            runner._reject_forbidden_git_args(["push", "origin", "HEAD"])

    def test_vector_unbounded_turns_closed(self) -> None:
        # file:symbol guard.enforce_bounds
        with self.assertRaises(guard.GuardError) as ctx:
            guard.enforce_bounds(10_000)
        self.assertEqual(ctx.exception.code, "MAX_TURNS_CAP")

    def test_vector_cred_leak_closed_by_audit(self) -> None:
        # file:symbol audit.sanitize_event / emit
        with self.assertRaises(audit_mod.AuditError):
            audit_mod.sanitize_event(
                {
                    "principal": "p",
                    "tool": "t",
                    "lane": "l",
                    "base_ref": "b",
                    "cwd": "C:/Users/x/.grok/auth.json",
                    "turns_used": 1,
                    "outcome": "ok",
                }
            )

    def test_vector_destructive_shell_denied_and_interpreters_not_allowed(self) -> None:
        # file:symbol guard.build_permission_profile / profile_allows_interpreters
        profile = guard.build_permission_profile(False)
        deny = profile["deny"]
        self.assertIn("Bash(rm -rf*)", deny)
        self.assertFalse(guard.profile_allows_interpreters(profile))
        # Builder rejects smuggling interpreters into allow at argv construction time.
        bad = {
            "allow": list(profile["allow"]) + ["Bash(python*)"],
            "deny": list(profile["deny"]),
            "disallowed_tools": list(profile["disallowed_tools"]),
            "permission_mode": profile["permission_mode"],
        }
        with self.assertRaises(guard.GuardError) as ctx:
            guard.build_grok_argv("g", "/wt", bad, 3)
        self.assertEqual(ctx.exception.code, "ALLOW_INTERPRETER_FORBIDDEN")

    def test_vector_always_approve_smuggle_closed(self) -> None:
        # file:symbol guard.build_grok_argv / assert_argv_safe / runner._reject_always_approve
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv("g", "/wt", profile, 3)
        self.assertNotIn("--always-approve", argv)
        with self.assertRaises(guard.GuardError):
            guard.assert_argv_safe(["grok", "--always-approve"])

    def test_vector_headless_required_on_shipped_builder(self) -> None:
        # file:symbol guard.build_grok_argv / argv_has_headless_interface
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv("coding goal", "/wt", profile, 4)
        self.assertTrue(guard.argv_has_headless_interface(argv))
        self.assertIn("--single", argv)
        # Failure mode: bare interactive argv fails assert_argv_safe
        bare = ["grok", "--cwd", "/wt", "--output-format", "json", "--permission-mode", "dontAsk", "goal"]
        self.assertFalse(guard.argv_has_headless_interface(bare))
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(bare)
        self.assertEqual(ctx.exception.code, "ARGV_NOT_HEADLESS")

    def test_vector_client_grok_bin_closed(self) -> None:
        # file:symbol server.resolve_server_grok_bin / guard.validate_grok_bin
        with self.assertRaises(guard.GuardError) as ctx:
            server.resolve_server_grok_bin({"grok_bin": "C:/evil/cmd.exe"})
        self.assertEqual(ctx.exception.code, "GROK_BIN_CLIENT_FORBIDDEN")


class MultiRootAllowlistTests(unittest.TestCase):
    """Task B — allowlist multi-root fail-closed (accept / reject / .. / empty)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root_a = (base / "proj-a").resolve()
        self.root_b = (base / "proj-b").resolve()
        self.other = (base / "other").resolve()
        self.root_a.mkdir()
        self.root_b.mkdir()
        self.other.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_accept_on_allowlist(self) -> None:
        allow = [self.root_a, self.root_b]
        got = server.resolve_trusted_repo_root(
            {"repo_root": str(self.root_b)},
            allowed_roots=allow,
        )
        self.assertTrue(guard.paths_equal(got, self.root_b))

    def test_reject_off_allowlist(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            server.resolve_trusted_repo_root(
                {"repo_root": str(self.other)},
                allowed_roots=[self.root_a],
            )
        self.assertEqual(ctx.exception.code, "REPO_ROOT_UNTRUSTED")

    def test_reject_dotdot_escape(self) -> None:
        # root_a/../other resolves to other — not on allowlist of root_a only.
        sneaky = str(self.root_a / ".." / "other")
        with self.assertRaises(guard.GuardError) as ctx:
            server.resolve_trusted_repo_root(
                {"repo_root": sneaky},
                allowed_roots=[self.root_a],
            )
        self.assertEqual(ctx.exception.code, "REPO_ROOT_UNTRUSTED")

    def test_empty_allowlist_fail_closed(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GROK_DELEGATE_ALLOWED_ROOTS", None)
            os.environ.pop("GROK_DELEGATE_REPO_ROOT", None)
            with self.assertRaises(guard.GuardError) as ctx:
                server.resolve_trusted_repo_root({}, allowed_roots=[])
            self.assertEqual(ctx.exception.code, "ALLOWED_ROOTS_EMPTY")
            self.assertIn("GROK_DELEGATE_ALLOWED_ROOTS", ctx.exception.message)

    def test_default_root_is_first_allowlist_entry(self) -> None:
        got = server.resolve_trusted_repo_root(
            {},
            allowed_roots=[self.root_a, self.root_b],
        )
        self.assertTrue(guard.paths_equal(got, self.root_a))

    def test_lanes_parent_defaults_to_sibling_pcp_lanes(self) -> None:
        parent = server.resolve_trusted_lanes_parent({}, repo_root=self.root_a)
        assert parent is not None
        self.assertEqual(parent.name, "pcp-lanes")
        self.assertFalse(runner.is_path_inside(parent, self.root_a))

    def test_parse_allowed_roots_semicolon_and_json(self) -> None:
        parts = guard.parse_allowed_roots_env(f"{self.root_a};{self.root_b}")
        self.assertEqual(len(parts), 2)
        jparts = guard.parse_allowed_roots_env(
            json.dumps([str(self.root_a), str(self.root_b)])
        )
        self.assertEqual(len(jparts), 2)

    def test_status_lanes_parent_matches_resolve_under_env_pin(self) -> None:
        """status.lanes_parent_by_root must use same path as resolve_trusted_lanes_parent."""
        pinned = (Path(self.tmp.name) / "pinned-lanes").resolve()
        pinned.mkdir()
        sp = MockSubprocess(ok=True)
        # Minimal status probes (version/models) so handler returns ok=True.
        class _StatusSp(MockSubprocess):
            def __call__(self, args, cwd, timeout):  # type: ignore[no-untyped-def]
                argv = [str(a) for a in args]
                self.calls.append(argv)
                verb = next((a for a in argv[1:] if not a.startswith("-")), "")
                if verb == "version":
                    body = json.dumps({"currentVersion": "0.2.111", "channel": "stable"})
                elif verb == "models":
                    body = "You are logged in with grok.com.\n"
                else:
                    body = "{}"
                return {
                    "args": argv,
                    "returncode": 0,
                    "stdout": body,
                    "stderr": "",
                    "timedOut": False,
                }

        with mock.patch.dict(
            os.environ,
            {"GROK_DELEGATE_LANES_PARENT": str(pinned)},
            clear=False,
        ):
            expected = server.resolve_trusted_lanes_parent({}, repo_root=self.root_a)
            assert expected is not None
            result = server.handle_tool_call(
                "grok_delegate_status",
                {},
                allowed_roots=[self.root_a],
                subprocess_runner=_StatusSp(),
                which=lambda n: n,
                git_runner=MockGit(),
            )
        self.assertTrue(result.get("ok"), result)
        lanes_map = result["roots"]["lanes_parent_by_root"]
        root_key = str(self.root_a.resolve())
        # Status must report the env pin, not the sibling default.
        self.assertIn(root_key, lanes_map)
        self.assertTrue(
            guard.paths_equal(lanes_map[root_key], expected),
            f"status lanes={lanes_map[root_key]!r} != resolve={expected!r}",
        )
        self.assertTrue(guard.paths_equal(lanes_map[root_key], pinned))
        self.assertNotEqual(
            Path(lanes_map[root_key]).name,
            "pcp-lanes",
            "status must not invent sibling pcp-lanes when env pin is set",
        )


class StatusToolsTests(unittest.TestCase):
    """Task A — four read-only status tools via shipped handlers."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _mock_cli(self, stdout_by_verb: dict[str, str]) -> MockSubprocess:
        """Subprocess mock that answers version/doctor/models/inspect by argv verb."""

        class _StatusMock(MockSubprocess):
            def __call__(self, args, cwd, timeout):  # type: ignore[no-untyped-def]
                argv = [str(a) for a in args]
                self.calls.append(argv)
                # Find verb
                verb = None
                for a in argv[1:]:
                    if not a.startswith("-"):
                        verb = a.lower()
                        break
                    if a in {"--version", "-v"}:
                        verb = a
                        break
                body = stdout_by_verb.get(verb or "", "")
                if verb is None:
                    return {
                        "args": argv,
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "no verb",
                        "timedOut": False,
                    }
                return {
                    "args": argv,
                    "returncode": 0,
                    "stdout": body,
                    "stderr": "",
                    "timedOut": False,
                }

        return _StatusMock()  # type: ignore[return-value]

    def test_tools_list_includes_status_tools(self) -> None:
        resp = server.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        assert resp is not None
        names = {t["name"] for t in resp["result"]["tools"]}
        for n in (
            "grok_delegate_status",
            "grok_delegate_doctor",
            "grok_delegate_models",
            "grok_delegate_inspect",
        ):
            self.assertIn(n, names)

    def test_status_structured_no_secrets(self) -> None:
        sp = self._mock_cli(
            {
                "version": json.dumps(
                    {"currentVersion": "0.2.111", "channel": "stable"}
                ),
                "models": "You are logged in with grok.com.\nDefault model: grok-4.5\n",
            }
        )
        result = server.handle_tool_call(
            "grok_delegate_status",
            {},
            allowed_roots=[self.root],
            subprocess_runner=sp,
            which=lambda n: n,
            git_runner=MockGit(),
        )
        self.assertTrue(result.get("ok"), result)
        self.assertIn("roots", result)
        self.assertEqual(result["roots"]["count"], 1)
        self.assertIn(str(self.root.resolve()), result["roots"]["allowed"][0])
        self.assertFalse(result["auth"]["auth_json_read"])
        self.assertIn("sandbox", result)
        self.assertIn("workspace", result["sandbox"]["known_profiles"])
        blob = json.dumps(result)
        self.assertNotIn("auth.json", blob.lower().replace("auth_json_read", ""))
        # auth_json_read key is fine; raw path must not appear
        self.assertNotIn(".grok/auth", blob)

    def test_doctor_never_calls_fix(self) -> None:
        sp = self._mock_cli(
            {
                "doctor": json.dumps(
                    {"schemaVersion": "1", "counts": {"issues": 0}}
                ),
            }
        )
        result = server.handle_tool_call(
            "grok_delegate_doctor",
            {},
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertIn("doctor", result)
        self.assertEqual(len(sp.calls), 1)
        self.assertIn("doctor", sp.calls[0])
        self.assertIn("--json", sp.calls[0])
        self.assertNotIn("fix", [a.lower() for a in sp.calls[0]])

    def test_models_tool(self) -> None:
        sp = self._mock_cli(
            {"models": "You are logged in with grok.com.\n* grok-4.5\n"}
        )
        result = server.handle_tool_call(
            "grok_delegate_models",
            {},
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertIn("grok-4.5", result.get("models_text", ""))

    def test_inspect_requires_allowlisted_root(self) -> None:
        sp = self._mock_cli(
            {"inspect": json.dumps({"grokVersion": "0.2.111", "cwd": str(self.root)})}
        )
        # Off-list → reject before spawn
        bad = server.handle_tool_call(
            "grok_delegate_inspect",
            {"repo_root": str(Path(self.tmp.name) / "nope")},
            allowed_roots=[self.root],
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertFalse(bad.get("ok"))
        self.assertEqual(bad.get("error"), "REPO_ROOT_UNTRUSTED")
        self.assertEqual(sp.calls, [])

        ok = server.handle_tool_call(
            "grok_delegate_inspect",
            {"repo_root": str(self.root)},
            allowed_roots=[self.root],
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertTrue(ok.get("ok"), ok)
        self.assertIn("inspect", ok)
        self.assertEqual(len(sp.calls), 1)

    def test_readonly_cli_rejects_mutating_verbs(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            runner._assert_readonly_cli_args(["doctor", "fix"])
        self.assertEqual(ctx.exception.code, "CLI_VERB_FORBIDDEN")
        with self.assertRaises(guard.GuardError):
            runner._assert_readonly_cli_args(["logout"])
        with self.assertRaises(guard.GuardError):
            runner._assert_readonly_cli_args(["plugin", "list"])


class SandboxAndFlagsTests(unittest.TestCase):
    """Task C/D — sandbox, --tools, extra CLI params, path confinement."""

    def test_argv_includes_sandbox_and_tools_by_default(self) -> None:
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv("g", r"C:\lanes\wt", profile, 3)
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace")
        self.assertIn("--tools", argv)
        tools = argv[argv.index("--tools") + 1]
        self.assertIn("Read", tools)
        self.assertIn("Write", tools)
        self.assertNotIn(guard.ALWAYS_APPROVE_FLAG, argv)
        self.assertNotIn("bypassPermissions", argv)

    def test_plan_argv_uses_read_only_sandbox(self) -> None:
        profile = guard.build_permission_profile(True)
        argv = guard.build_grok_argv("plan", r"C:\lanes\wt", profile, 2, plan_only=True)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")

    def test_sandbox_off_omits_flag(self) -> None:
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv(
            "g", r"C:\lanes\wt", profile, 3, sandbox="off"
        )
        self.assertNotIn("--sandbox", argv)

    def test_unknown_sandbox_rejected(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_sandbox_profile("invented-profile")
        self.assertEqual(ctx.exception.code, "SANDBOX_INVALID")

    def test_extra_flags_on_argv(self) -> None:
        profile = guard.build_permission_profile(False)
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        argv = guard.build_grok_argv(
            "g",
            r"C:\lanes\wt",
            profile,
            5,
            model="grok-4.5",
            reasoning_effort="high",
            rules="be careful",
            json_schema=schema,
            no_subagents=True,
            disable_web_search=True,
        )
        self.assertIn("--model", argv)
        self.assertIn("grok-4.5", argv)
        self.assertIn("--reasoning-effort", argv)
        self.assertIn("high", argv)
        self.assertIn("--rules", argv)
        self.assertIn("--json-schema", argv)
        self.assertIn("--no-subagents", argv)
        self.assertIn("--disable-web-search", argv)
        # No CLI worktree flags — own prepare_worktree remains
        self.assertNotIn("--worktree", argv)
        self.assertNotIn("-w", argv)

    def test_session_resume_and_fork(self) -> None:
        profile = guard.build_permission_profile(False)
        sid = "019f959a-a609-7cd1-a2fd-47b9f698ed0c"
        argv = guard.build_grok_argv(
            "g",
            r"C:\lanes\wt",
            profile,
            3,
            resume=sid,
            fork_session=True,
            session_id="019f959a-a609-7cd1-a2fd-47b9f698ed0d",
        )
        self.assertIn("--resume", argv)
        self.assertIn(sid, argv)
        self.assertIn("--fork-session", argv)
        self.assertIn("--session-id", argv)

    def test_fork_without_resume_rejected(self) -> None:
        profile = guard.build_permission_profile(False)
        with self.assertRaises(guard.GuardError) as ctx:
            guard.build_grok_argv(
                "g", r"C:\lanes\wt", profile, 3, fork_session=True
            )
        self.assertEqual(ctx.exception.code, "FORK_REQUIRES_RESUME")

    def test_confine_path_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "wt"
            root.mkdir()
            (root / "ok.txt").write_text("x", encoding="utf-8")
            ok = guard.confine_path_to_root("ok.txt", root)
            self.assertTrue(str(ok).endswith("ok.txt"))
            with self.assertRaises(guard.GuardError) as ctx:
                guard.confine_path_to_root("../outside.txt", root)
            self.assertEqual(ctx.exception.code, "PATH_ESCAPE")

    def test_run_delegation_argv_has_sandbox(self) -> None:
        sp = MockSubprocess(ok=True)
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td) / "wt"
            wt.mkdir()
            result = runner.run_delegation(
                goal="implement safely",
                worktree_path=wt,
                max_turns=4,
                subprocess_runner=sp,
                which=lambda n: n,
            )
            self.assertTrue(result.get("ok"), result)
            argv = sp.calls[0]
            self.assertIn("--sandbox", argv)
            self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace")
            _assert_argv_headless_and_safe(self, argv)

    def test_invalid_reasoning_effort_rejected(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_reasoning_effort("ludicrous")
        self.assertEqual(ctx.exception.code, "REASONING_EFFORT_INVALID")

    def test_invalid_session_id_rejected(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_session_id("not-a-uuid")
        self.assertEqual(ctx.exception.code, "SESSION_ID_INVALID")

    def test_resume_false_omits_resume_flag(self) -> None:
        """JSON false / bool False must not become the string 'False' as session id."""
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv(
            "g",
            r"C:\lanes\wt",
            profile,
            3,
            resume=False,
        )
        self.assertNotIn("--resume", argv)
        self.assertNotIn("False", argv)

    def test_delegate_kwargs_resume_false_from_tool_args(self) -> None:
        """Server path: client resume=false → no SESSION_ID_INVALID, no --resume."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            lanes = Path(td) / "pcp-lanes"
            repo.mkdir()
            lanes.mkdir()
            sp = MockSubprocess(ok=True)
            result = server.handle_tool_call(
                "grok_delegate",
                {
                    "goal": "noop",
                    "lane": "resume-false",
                    "lanes_parent": str(lanes),
                    "max_turns": 2,
                    "resume": False,
                },
                repo_root=repo,
                git_runner=MockGit(),
                subprocess_runner=sp,
                which=lambda n: n,
                audit_stream=io.StringIO(),
            )
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(len(sp.calls), 1)
            argv = sp.calls[0]
            self.assertNotIn("--resume", argv)
            self.assertNotIn("False", argv)


class SelfTestEntryTests(unittest.TestCase):
    """Task E — self-test entry exists and drives real list_tools path."""

    def test_main_module_exposes_self_test_flag(self) -> None:
        main_src = (_PKG / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("--self-test", main_src)
        self.assertIn("--smoke-delegate", main_src)
        self.assertIn("handle_jsonrpc", main_src)
        self.assertIn("grok_delegate_status", main_src)

    def test_no_destructive_tools_registered(self) -> None:
        names = [t["name"] for t in server.list_tools()]
        banned = (
            "sessions_delete",
            "doctor_fix",
            "logout",
            "update",
            "plugin",
            "mcp_config",
        )
        for b in banned:
            self.assertNotIn(b, names)
        # Doctor tool must remain read-only (never expose fix as a tool name/action).
        doctor = next(t for t in server.list_tools() if t["name"] == "grok_delegate_doctor")
        self.assertIn("never", doctor["description"].lower())
        self.assertIn("--json", doctor["description"].lower())


class SubprocessDecodingTests(unittest.TestCase):
    """Regression: production runners must decode as UTF-8, not the OS locale.

    On Windows ``text=True`` alone decodes with cp1252, which raises
    UnicodeDecodeError inside subprocess' reader thread on the first non-cp1252
    byte — losing the delegation result. Goals and summaries here are Russian,
    so this is a live path, not a theoretical one.
    """

    NON_ASCII = "привет-мир-✓"

    def test_default_subprocess_runner_handles_non_ascii_stdout(self) -> None:
        # Write raw UTF-8 bytes, bypassing the child's locale text layer — this
        # mirrors grok (a Rust binary that always emits UTF-8) rather than
        # testing Python's own stdout encoding.
        code = (
            "import sys;"
            f"sys.stdout.buffer.write({self.NON_ASCII!r}.encode('utf-8'))"
        )
        result = runner.default_subprocess_runner(
            [sys.executable, "-c", code], None, 60.0
        )
        self.assertEqual(result.get("returncode"), 0, msg=result.get("stderr"))
        self.assertIn(self.NON_ASCII, result.get("stdout") or "")

    def test_default_git_runner_handles_non_ascii_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            for args in (
                ["init", "-q"],
                ["config", "user.email", "t@example.invalid"],
                ["config", "user.name", "T"],
            ):
                runner.default_git_runner(args, repo, 60.0)
            (repo / "f.txt").write_text("x\n", encoding="utf-8")
            runner.default_git_runner(["add", "."], repo, 60.0)
            runner.default_git_runner(
                ["commit", "-q", "-m", self.NON_ASCII], repo, 60.0
            )
            log = runner.default_git_runner(
                ["log", "--format=%s", "-1"], repo, 60.0
            )
            self.assertEqual(log.get("returncode"), 0, msg=log.get("stderr"))
            self.assertIn("привет", log.get("stdout") or "")


class RoundSixPermissionRegressionTests(unittest.TestCase):
    """R6: the permission profile must not block the executor's own job.

    Measured on Windows before these fixes: a delegation that had to READ a file
    died after 1-2 turns without touching one (blanket absolute-read deny), and an
    allowed `git commit`/`git add` was refused whenever the message or path merely
    contained "prod"/"root" (substring-anywhere bash denies).
    """

    @staticmethod
    def _matches(rules: Any, command: str) -> str | None:
        """First Bash rule whose glob matches the whole command, else None."""
        import fnmatch

        for rule in rules:
            text = str(rule)
            if not text.startswith("Bash(") or not text.endswith(")"):
                continue
            if fnmatch.fnmatchcase(command, text[len("Bash(") : -1]):
                return text
        return None

    def test_execute_profile_allows_in_worktree_absolute_reads(self) -> None:
        profile = guard.build_permission_profile()
        self.assertNotIn("Read([A-Za-z]:/**)", profile["deny"])
        # UNC escape and absolute WRITE/EDIT containment stay in place.
        self.assertIn("Read(//**)", profile["deny"])
        self.assertIn("Write([A-Za-z]:/**)", profile["deny"])
        self.assertIn("Edit([A-Za-z]:/**)", profile["deny"])

    def test_execute_allow_covers_discovery_tools_exposed_via_tools(self) -> None:
        profile = guard.build_permission_profile()
        for rule in ("Read(**)", "Grep(**)", "Glob(**)"):
            self.assertIn(rule, profile["allow"])
        # Every --tools entry that reads must have an allow rule.
        for tool in ("Read", "Grep", "Glob"):
            self.assertIn(tool, profile["tools"])
            self.assertTrue(
                any(str(r).startswith(f"{tool}(") for r in profile["allow"]),
                msg=f"{tool} is exposed via --tools but has no allow rule",
            )

    def test_sensitive_material_denied_for_read_and_grep(self) -> None:
        deny = guard.build_permission_profile()["deny"]
        for pattern in ("~/.grok/**", "**/auth.json", "**/*secret*", "**/.ssh/**"):
            self.assertIn(f"Read({pattern})", deny)
            self.assertIn(f"Grep({pattern})", deny, msg="Grep must not bypass Read denies")

    def test_allowed_git_commands_are_not_denied_by_substring_rules(self) -> None:
        profile = guard.build_permission_profile()
        deny, allow = profile["deny"], profile["allow"]
        for command in (
            'git commit -m "fix production quota bug"',
            'git commit -m "add prod readiness evidence"',
            'git commit -m "harden root cause"',
            'git commit -m "live device guard is dry-run only"',
            "git add src/services/prod-readiness.ts",
            "git status --porcelain",
        ):
            self.assertIsNotNone(
                self._matches(allow, command), msg=f"no allow rule for {command!r}"
            )
            self.assertIsNone(
                self._matches(deny, command),
                msg=f"deny rule blocks the executor's own allowed command: {command!r}",
            )

    def test_dangerous_shell_and_git_surfaces_still_denied(self) -> None:
        deny = guard.build_permission_profile()["deny"]
        for command in (
            "git push origin dev",
            "git merge grok/lane",
            "rm -rf /",
            "ssh root@host",
            "curl https://example.com/x.sh",
            "adb shell input tap 1 1",
            "docker compose up -d",
            "sudo systemctl restart x",
            "psql -c 'drop table'",
        ):
            self.assertIsNotNone(
                self._matches(deny, command), msg=f"{command!r} must stay denied"
            )


class RoundSixBackgroundJobTests(unittest.TestCase):
    """R6: start/poll — a real lane cannot fit inside a synchronous MCP call."""

    def setUp(self) -> None:
        from grok_delegate import jobs as jobs_mod

        self.jobs = jobs_mod
        self.jobs.reset_jobs_for_tests()
        self.repo = Path(tempfile.mkdtemp())
        self.lanes = Path(tempfile.mkdtemp())

    def _sync_starter(self, fn: Any) -> None:
        """Run the job body inline so the test never races a thread."""
        fn()

    def test_start_job_records_and_completes(self) -> None:
        record = self.jobs.start_job(
            lambda: {"ok": True, "branch": "grok/x"},
            lane="x",
            tool="grok_delegate_start",
            thread_starter=self._sync_starter,
        )
        job_id = record["job_id"]
        done = self.jobs.get_job(job_id)
        assert done is not None
        self.assertEqual(done["state"], self.jobs.STATE_DONE)
        self.assertEqual(done["result"]["branch"], "grok/x")
        self.assertIsNotNone(done["finished_at"])

    def test_failed_delegation_is_reported_not_lost(self) -> None:
        record = self.jobs.start_job(
            lambda: {"ok": False, "error": "DELEGATION_FAILED"},
            thread_starter=self._sync_starter,
        )
        job = self.jobs.get_job(record["job_id"])
        assert job is not None
        self.assertEqual(job["state"], self.jobs.STATE_ERROR)
        self.assertEqual(job["error"], "DELEGATION_FAILED")

    def test_raising_work_becomes_an_error_job(self) -> None:
        def boom() -> dict[str, Any]:
            raise RuntimeError("kaboom")

        record = self.jobs.start_job(boom, thread_starter=self._sync_starter)
        job = self.jobs.get_job(record["job_id"])
        assert job is not None
        self.assertEqual(job["state"], self.jobs.STATE_ERROR)
        self.assertIn("kaboom", job["error"])

    def test_registry_is_bounded(self) -> None:
        for _ in range(self.jobs.MAX_JOBS + 10):
            self.jobs.start_job(lambda: {"ok": True}, thread_starter=self._sync_starter)
        self.assertLessEqual(len(self.jobs.list_jobs(limit=1000)), self.jobs.MAX_JOBS)

    def test_start_tool_returns_job_id_without_running_executor_inline(self) -> None:
        sp = MockSubprocess(ok=True)
        result = server.handle_tool_call(
            server.TOOL_START,
            {"goal": "do the lane", "lane": "async-lane", "max_turns": 3},
            repo_root=self.repo,
            allowed_roots=[self.repo],
            git_runner=MockGit(),
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertTrue(result.get("ok"), msg=result)
        self.assertIn("job_id", result)
        self.assertEqual(result.get("poll_with"), server.TOOL_POLL)

        # The background thread may still be finishing; poll until it settles.
        job_id = result["job_id"]
        for _ in range(200):
            job = self.jobs.get_job(job_id)
            if job and job["state"] != self.jobs.STATE_RUNNING:
                break
            time.sleep(0.01)
        job = self.jobs.get_job(job_id)
        assert job is not None
        self.assertNotEqual(job["state"], self.jobs.STATE_RUNNING)

    def test_poll_unknown_job_fails_closed(self) -> None:
        out = server.handle_tool_call(
            server.TOOL_POLL, {"job_id": "job-does-not-exist"}
        )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "JOB_UNKNOWN")

    def test_poll_without_job_id_lists_jobs(self) -> None:
        self.jobs.start_job(
            lambda: {"ok": True}, lane="listed", thread_starter=self._sync_starter
        )
        out = server.handle_tool_call(server.TOOL_POLL, {})
        self.assertTrue(out.get("ok"))
        self.assertTrue(any(j.get("lane") == "listed" for j in out["jobs"]))

    def test_start_and_poll_are_advertised(self) -> None:
        names = {t["name"] for t in server.list_tools()}
        self.assertIn(server.TOOL_START, names)
        self.assertIn(server.TOOL_POLL, names)


class RoundSixPathPolicyTests(unittest.TestCase):
    """R6: the executor is always told the relative-path policy."""

    def _rules_values(self, **kwargs: Any) -> list[str]:
        argv = guard.build_grok_argv(
            "goal",
            "C:/lanes/wt",
            guard.build_permission_profile(),
            5,
            **kwargs,
        )
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--rules"]

    def test_path_policy_is_injected_without_caller_rules(self) -> None:
        values = self._rules_values()
        self.assertEqual(len(values), 1)
        self.assertIn("relative", values[0].lower())
        self.assertIn("denied", values[0].lower())

    def test_path_policy_is_appended_to_caller_rules(self) -> None:
        values = self._rules_values(rules="Never push. Commit your work.")
        self.assertEqual(len(values), 1)
        self.assertIn("Never push.", values[0])
        self.assertIn(guard.PATH_POLICY_RULE, values[0])


class RoundSixDiffReportingTests(unittest.TestCase):
    """R6: committed lane work must be reported, not shown as an empty diff."""

    class _FakeGit:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def __call__(self, args: Any, cwd: Any, timeout: float) -> dict[str, Any]:
            argv = [str(a) for a in args]
            self.calls.append(argv)
            joined = " ".join(argv)
            # Clean tree: the executor already committed everything.
            if "--name-only" in argv and "dev...HEAD" in argv:
                out = "src/services/a.ts\nsrc/services/a.test.ts\n"
            elif "--stat" in argv and "dev...HEAD" in argv:
                out = " src/services/a.ts | 5 +++++\n 2 files changed\n"
            elif "log" in argv and "dev..HEAD" in argv:
                out = "abc1234 feat: lane work\n"
            elif "--name-only" in argv or "--stat" in argv or "--porcelain" in joined:
                out = ""
            else:
                out = ""
            return {
                "args": argv,
                "returncode": 0,
                "stdout": out,
                "stderr": "",
                "timedOut": False,
            }

    def test_collect_diff_without_base_ref_keeps_head_only_behavior(self) -> None:
        git = self._FakeGit()
        out = runner.collect_diff(Path("wt"), git_runner=git)
        self.assertEqual(out["changed_files"], [])
        self.assertEqual(out["commits"], [])

    def test_collect_diff_reports_committed_work_against_base_ref(self) -> None:
        git = self._FakeGit()
        out = runner.collect_diff(Path("wt"), git_runner=git, base_ref="dev")
        self.assertIn("src/services/a.ts", out["changed_files"])
        self.assertIn("src/services/a.test.ts", out["changed_files"])
        self.assertEqual(out["commits"], ["abc1234 feat: lane work"])
        self.assertIn("2 files changed", out["diffstat"])
        # Still no full unified patch payload.
        for call in git.calls:
            if "diff" in call:
                self.assertTrue("--stat" in call or "--name-only" in call)

    def test_delegate_reports_commits_for_a_committed_lane(self) -> None:
        sp = MockSubprocess(ok=True)
        git = MockGit(name_only="", porcelain="", diff_stat="")
        result = runner.delegate(
            goal="do the lane",
            lane="r6-commits",
            repo_root=Path(tempfile.mkdtemp()),
            lanes_parent=Path(tempfile.mkdtemp()),
            base_ref="dev",
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
        )
        self.assertTrue(result.get("ok"), msg=result.get("message"))
        self.assertIn("commits", result)
        # The base-ref diff/log must actually be requested.
        self.assertTrue(
            any("dev...HEAD" in " ".join(c) for c in git.calls),
            msg="collect_diff did not diff against base_ref",
        )
        self.assertTrue(
            any("dev..HEAD" in " ".join(c) for c in git.calls),
            msg="collect_diff did not log lane commits",
        )


class RoundSevenEmptyLaneAtMcpBoundaryTests(unittest.TestCase):
    """An MCP caller must not read an empty execute lane as done work.

    runner.delegate reports ok at the run layer on purpose and leaves emptiness
    to driver.is_empty_result; the server is where a client that never sees the
    driver gets told. Measured 2026-07-26: a lane spent two turns reading files
    and came back ok:true / state:done.
    """

    def _lane(self, **over: Any) -> dict[str, Any]:
        base = {
            "ok": True,
            "lane": "grok/x",
            "status": "ok",
            "changed_files": [],
            "commits": [],
            "summary": "looked around, wrote nothing",
        }
        base.update(over)
        return base

    def test_empty_execute_lane_is_not_ok(self) -> None:
        marked = server.mark_empty_execute_lane(self._lane(), plan_only=False)
        self.assertFalse(marked["ok"])
        self.assertEqual(marked["status"], "no_changes")
        self.assertEqual(marked["error"], "EXECUTE_NO_CHANGES")
        self.assertTrue(marked["is_empty_result"])
        # The executor's own account survives - that is where the reason lives.
        self.assertEqual(marked["summary"], "looked around, wrote nothing")

    def test_plan_lane_is_exempt(self) -> None:
        marked = server.mark_empty_execute_lane(self._lane(), plan_only=True)
        self.assertTrue(marked["ok"])
        self.assertNotIn("error", marked)

    def test_lane_with_changed_files_untouched(self) -> None:
        lane = self._lane(changed_files=["a.py"])
        self.assertEqual(server.mark_empty_execute_lane(lane, plan_only=False), lane)

    def test_lane_with_commits_only_untouched(self) -> None:
        lane = self._lane(commits=["abc123 msg"])
        self.assertEqual(server.mark_empty_execute_lane(lane, plan_only=False), lane)

    def test_already_failed_lane_keeps_its_own_error(self) -> None:
        lane = self._lane(ok=False, error="DELEGATION_FAILED", status="error")
        marked = server.mark_empty_execute_lane(lane, plan_only=False)
        self.assertEqual(marked["error"], "DELEGATION_FAILED")

    def test_input_is_not_mutated(self) -> None:
        lane = self._lane()
        server.mark_empty_execute_lane(lane, plan_only=False)
        self.assertTrue(lane["ok"], msg="caller's dict must survive untouched")


class RoundSevenGitTimeoutTests(unittest.TestCase):
    """Lane prep had a hardcoded 60s per git call and no way to raise it."""

    def test_default_when_unset(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GROK_DELEGATE_GIT_TIMEOUT_SECONDS", None)
            self.assertEqual(runner.git_timeout_seconds(), runner.DEFAULT_GIT_TIMEOUT_SECONDS)

    def test_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"GROK_DELEGATE_GIT_TIMEOUT_SECONDS": "180"}):
            self.assertEqual(runner.git_timeout_seconds(), 180.0)

    def test_garbage_and_nonpositive_fall_back(self) -> None:
        for raw in ("", "abc", "0", "-5"):
            with mock.patch.dict(os.environ, {"GROK_DELEGATE_GIT_TIMEOUT_SECONDS": raw}):
                self.assertEqual(
                    runner.git_timeout_seconds(),
                    runner.DEFAULT_GIT_TIMEOUT_SECONDS,
                    msg=raw,
                )

    def test_capped(self) -> None:
        with mock.patch.dict(os.environ, {"GROK_DELEGATE_GIT_TIMEOUT_SECONDS": "99999"}):
            self.assertEqual(runner.git_timeout_seconds(), 3600.0)


class _TimeoutGit:
    """git double that can time out a chosen verb and report worktree lock state.

    ``settled`` decides what the post-timeout probe sees: the worktree either
    finished its checkout (the real, measured case — the killed parent's child
    completes it) or is still initializing.
    """

    def __init__(
        self,
        *,
        branch: str = "grok/canary",
        timeout_on: str | None = None,
        settled: bool = True,
        locked: bool = False,
        present: bool = True,
        create_target: bool = True,
    ) -> None:
        self.branch = branch
        self.timeout_on = timeout_on
        self.settled = settled
        self.locked = locked
        self.present = present
        self.create_target = create_target
        self.calls: list[list[str]] = []
        self.timeouts: list[tuple[list[str], float]] = []
        self.target: Path | None = None

    def timeout_for(self, token: str) -> float | None:
        for argv, timeout in self.timeouts:
            if token in argv:
                return timeout
        return None

    def _ok(self, argv: list[str], stdout: str = "") -> dict[str, Any]:
        return {
            "args": argv,
            "returncode": 0,
            "stdout": stdout,
            "stderr": "",
            "timedOut": False,
        }

    def _timeout(self, argv: list[str], timeout: float) -> dict[str, Any]:
        return {
            "args": argv,
            "returncode": 124,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "timedOut": True,
        }

    def __call__(
        self,
        args: "list[str] | tuple[str, ...]",
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        self.timeouts.append((argv, timeout))

        timed_out = bool(self.timeout_on) and self.timeout_on in argv

        if "worktree" in argv and "add" in argv:
            # Order matters, and it is the whole point of this double: killing
            # the wrapper's subprocess does not cancel the checkout. git has
            # already registered the worktree and spawned the child that lays out
            # the tree, and on Windows that child outlives the terminated parent.
            # So the target appears on disk even on the call that "failed".
            path = None
            if "-b" in argv:
                i = argv.index("-b")
                if i + 2 < len(argv):
                    path = Path(argv[i + 2])
            else:
                i = argv.index("add")
                if i + 1 < len(argv):
                    path = Path(argv[i + 1])
            if path is not None:
                self.target = path
                if self.create_target:
                    path.mkdir(parents=True, exist_ok=True)
            if timed_out:
                return self._timeout(argv, timeout)
            return self._ok(argv)

        if timed_out:
            return self._timeout(argv, timeout)

        if argv[0] == "--version":
            return self._ok(argv, "git version 2.54.0\n")
        if "rev-parse" in argv and "--verify" in argv:
            return self._ok(argv, "abc123\n")
        if "rev-parse" in argv and "--abbrev-ref" in argv:
            if not self.settled:
                return {
                    "args": argv,
                    "returncode": 128,
                    "stdout": "",
                    "stderr": "fatal: not a git repository\n",
                    "timedOut": False,
                }
            return self._ok(argv, f"{self.branch}\n")
        if "status" in argv and "--porcelain" in argv:
            return self._ok(argv)
        if "worktree" in argv and "list" in argv:
            if not self.present or self.target is None:
                return self._ok(argv, "")
            block = [f"worktree {self.target.as_posix()}", "HEAD abc123"]
            block.append(f"branch refs/heads/{self.branch}")
            if self.locked:
                block.append("locked initializing")
            return self._ok(argv, "\n".join(block) + "\n\n")
        return self._ok(argv)


class RoundEightCheckoutTimeoutBudgetTests(unittest.TestCase):
    """A probe and a checkout are not the same operation and cannot share a budget.

    `git --version` does milliseconds of work; `git worktree add` lays out the
    whole tree and legitimately runs for minutes on a large repo. One number for
    both set the checkout ceiling by what a probe needs.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.lanes = Path(self.tmp.name) / "pcp-lanes"
        self.lanes.mkdir()
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for var in (
            "GROK_DELEGATE_GIT_TIMEOUT_SECONDS",
            "GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS",
        ):
            os.environ.pop(var, None)

    def tearDown(self) -> None:
        self._env.stop()
        self.tmp.cleanup()

    def test_checkout_default_is_far_above_the_probe_default(self) -> None:
        self.assertGreater(
            runner.DEFAULT_GIT_CHECKOUT_TIMEOUT_SECONDS,
            runner.DEFAULT_GIT_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            runner.git_checkout_timeout_seconds(),
            runner.DEFAULT_GIT_CHECKOUT_TIMEOUT_SECONDS,
        )

    def test_checkout_env_override(self) -> None:
        os.environ["GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS"] = "1200"
        self.assertEqual(runner.git_checkout_timeout_seconds(), 1200.0)

    def test_checkout_never_below_the_probe_budget(self) -> None:
        """Raising only the probe knob means "slow host" — honour it everywhere."""
        os.environ["GROK_DELEGATE_GIT_TIMEOUT_SECONDS"] = "900"
        os.environ["GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS"] = "120"
        self.assertEqual(runner.git_checkout_timeout_seconds(), 900.0)

    def test_checkout_capped(self) -> None:
        os.environ["GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS"] = "99999"
        self.assertEqual(runner.git_checkout_timeout_seconds(), 3600.0)

    def test_checkout_garbage_falls_back(self) -> None:
        for raw in ("abc", "0", "-5"):
            os.environ["GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS"] = raw
            self.assertEqual(
                runner.git_checkout_timeout_seconds(),
                runner.DEFAULT_GIT_CHECKOUT_TIMEOUT_SECONDS,
                msg=raw,
            )

    def test_prepare_spends_the_checkout_budget_on_the_checkout(self) -> None:
        git = _TimeoutGit(branch="grok/budget")
        runner.prepare_worktree(
            repo_root=self.repo,
            lane="budget",
            lanes_parent=self.lanes,
            git_runner=git,
            timeout=7.0,
            checkout_timeout=99.0,
        )
        self.assertEqual(git.timeout_for("--version"), 7.0)
        self.assertEqual(git.timeout_for("add"), 99.0)

    def test_direct_callers_keep_a_single_budget(self) -> None:
        """checkout_timeout defaults to timeout, so old call sites are unchanged."""
        git = _TimeoutGit(branch="grok/budget")
        runner.prepare_worktree(
            repo_root=self.repo,
            lane="budget",
            lanes_parent=self.lanes,
            git_runner=git,
            timeout=7.0,
        )
        self.assertEqual(git.timeout_for("add"), 7.0)


class RoundEightGitTimeoutIsNotAFailureTests(unittest.TestCase):
    """The claim's Defect 1: a wrapper timeout was reported as an environment refusal.

    Measured 2026-07-27 on this host: `git worktree add` hit the 60s ceiling and
    the wrapper returned WORKTREE_CREATE_FAILED — while a complete 34-entry
    worktree sat on disk, clean, on the right branch, lock released. The same
    barrier had previously been reported as GIT_MISSING and BASE_UNREACHABLE, so
    a working channel read as a broken environment for over a day.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.lanes = Path(self.tmp.name) / "pcp-lanes"
        self.lanes.mkdir()
        self._sleep_patch = mock.patch.object(runner, "_settle_sleep", lambda _s: None)
        self._sleep_patch.start()

    def tearDown(self) -> None:
        self._sleep_patch.stop()
        self.tmp.cleanup()

    def _prepare(self, git: _TimeoutGit, lane: str = "canary") -> dict[str, Any]:
        return runner.prepare_worktree(
            repo_root=self.repo,
            lane=lane,
            lanes_parent=self.lanes,
            git_runner=git,
            timeout=5.0,
            checkout_timeout=9.0,
        )

    def test_version_timeout_is_not_git_missing(self) -> None:
        git = _TimeoutGit(timeout_on="--version")
        result = self._prepare(git)
        self.assertEqual(result.get("error"), "GIT_TIMEOUT")
        self.assertNotEqual(result.get("error"), "GIT_MISSING")
        self.assertEqual(result.get("step"), "--version")

    def test_base_rev_parse_timeout_is_not_base_unreachable(self) -> None:
        git = _TimeoutGit(timeout_on="--verify")
        result = self._prepare(git)
        self.assertEqual(result.get("error"), "GIT_TIMEOUT")
        self.assertNotEqual(result.get("error"), "BASE_UNREACHABLE")

    def test_timed_out_checkout_that_finished_is_a_success(self) -> None:
        """The measured case: the tree is there, complete, on the right branch."""
        git = _TimeoutGit(
            branch="grok/canary", timeout_on="add", settled=True, locked=False
        )
        result = self._prepare(git)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("recovered_after_timeout"))
        self.assertEqual(result.get("branch"), "grok/canary")
        self.assertEqual(result.get("timeout_seconds"), 9.0)

    def test_timed_out_checkout_still_initializing_is_retryable(self) -> None:
        git = _TimeoutGit(branch="grok/canary", timeout_on="add", locked=True)
        result = self._prepare(git)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GIT_TIMEOUT")
        self.assertNotEqual(result.get("error"), "WORKTREE_CREATE_FAILED")

    def test_timeout_message_names_the_real_cause(self) -> None:
        """`message` said "git worktree add failed"; the truth was only in `detail`."""
        git = _TimeoutGit(branch="grok/canary", timeout_on="add", locked=True)
        result = self._prepare(git)
        self.assertIn("budget", str(result.get("message")).lower())
        self.assertNotIn("worktree add failed", str(result.get("message")))
        self.assertEqual(result.get("step"), "worktree add")
        self.assertEqual(result.get("timeout_seconds"), 9.0)

    def test_genuine_checkout_failure_is_still_a_hard_error(self) -> None:
        """No regression: a real `worktree add` failure keeps its own code."""
        git = MockGit(worktree_add_ok=False)
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="canary",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertEqual(result.get("error"), "WORKTREE_CREATE_FAILED")

    def test_initializing_worktree_is_not_handed_to_the_executor(self) -> None:
        """A lane left mid-checkout must not be reused as if it were ready."""
        target = runner.worktree_path_for_lane(self.lanes, "grok/canary")
        target.mkdir(parents=True)
        git = _TimeoutGit(branch="grok/canary", locked=True)
        git.target = target
        result = self._prepare(git)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "WORKTREE_INITIALIZING")

    def test_settled_existing_worktree_is_reused(self) -> None:
        """Defect 3's recovery path: the next attempt adopts what was left behind."""
        target = runner.worktree_path_for_lane(self.lanes, "grok/canary")
        target.mkdir(parents=True)
        git = _TimeoutGit(branch="grok/canary", locked=False)
        git.target = target
        result = self._prepare(git)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("reused"))


class RoundEightTimeoutIsRetryableTests(unittest.TestCase):
    """The claim's Defect 1b: a hard code blocks the lane on the spot.

    WORKTREE_CREATE_FAILED sat in driver.hard_codes, so one starved git call took
    the lane out of service without a single retry — and the executor never ran.
    """

    def test_git_timeout_is_not_a_hard_block(self) -> None:
        self.assertIn("GIT_TIMEOUT", driver.RETRYABLE_PREPARE_CODES)
        self.assertIn("WORKTREE_INITIALIZING", driver.RETRYABLE_PREPARE_CODES)

    def test_hard_codes_and_retryable_codes_are_disjoint(self) -> None:
        """A code cannot be both retryable and terminal."""
        source = Path(driver.__file__).read_text(encoding="utf-8")
        block = source.split("hard_codes = {", 1)[1].split("}", 1)[0]
        hard = {
            line.strip().strip('",')
            for line in block.splitlines()
            if line.strip().startswith('"')
        }
        self.assertTrue(hard, "could not parse hard_codes")
        self.assertEqual(hard & set(driver.RETRYABLE_PREPARE_CODES), set())


class RoundEightStepLabelTests(unittest.TestCase):
    """`last_step` answers "what is it doing", so it must stay readable."""

    def test_verb_and_flags_survive(self) -> None:
        self.assertEqual(
            runner._step_label(["git", "rev-parse", "--verify", "dev"]),
            "git rev-parse --verify dev",
        )

    def test_absolute_paths_are_stripped(self) -> None:
        label = runner._step_label(
            ["git", "-C", "D:/ZAI/pcp-lanes/canary", "rev-parse", "--abbrev-ref", "HEAD"]
        )
        self.assertEqual(label, "git rev-parse --abbrev-ref HEAD")
        self.assertNotIn("pcp-lanes", label)

    def test_label_is_bounded(self) -> None:
        long_cmd = ["git", "log"] + [f"--flag{i}" for i in range(20)]
        self.assertLessEqual(len(runner._step_label(long_cmd).split()), 4)


class RoundEightServerLoggingTests(unittest.TestCase):
    """The modules logged; nothing collected it.

    Searching the whole project for a trace of a running dispatch turned up
    neither .log nor .jsonl, so a stuck lane could only be diagnosed by watching
    the filesystem by hand — which is how "the channel is dead" got believed.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._logger = logging.getLogger("grok_delegate")
        self._saved = list(self._logger.handlers)
        self._saved_level = self._logger.level
        self._saved_propagate = self._logger.propagate

    def tearDown(self) -> None:
        for h in list(self._logger.handlers):
            if h not in self._saved:
                self._logger.removeHandler(h)
                h.close()
        self._logger.handlers = list(self._saved)
        self._logger.setLevel(self._saved_level)
        self._logger.propagate = self._saved_propagate
        self.tmp.cleanup()

    def test_disabled_without_configuration(self) -> None:
        """No env, no file: the server must not litter the disk unasked."""
        self.assertIsNone(server.configure_logging({}))

    def test_explicit_path_is_written(self) -> None:
        target = self.dir / "nested" / "gd.log"
        result = server.configure_logging({"GROK_DELEGATE_LOG_FILE": str(target)})
        self.assertEqual(result, target)
        logging.getLogger("grok_delegate.runner").warning("probe-line")
        self.assertTrue(target.is_file())
        self.assertIn("probe-line", target.read_text(encoding="utf-8"))

    def test_defaults_alongside_durable_jobs(self) -> None:
        result = server.configure_logging({"GROK_DELEGATE_JOBS_DIR": str(self.dir)})
        self.assertEqual(result, self.dir / "grok-delegate.log")

    def test_never_propagates_to_a_stdout_root_handler(self) -> None:
        """stdout is the JSON-RPC channel; a log line there corrupts it."""
        server.configure_logging({"GROK_DELEGATE_LOG_FILE": str(self.dir / "a.log")})
        self.assertFalse(logging.getLogger("grok_delegate").propagate)

    def test_reconfigure_does_not_stack_handlers(self) -> None:
        for _ in range(3):
            server.configure_logging({"GROK_DELEGATE_LOG_FILE": str(self.dir / "b.log")})
        tagged = [
            h
            for h in logging.getLogger("grok_delegate").handlers
            if getattr(h, "_gd_tag", None) == server._LOG_HANDLER_TAG
        ]
        self.assertEqual(len(tagged), 1)

    def test_unwritable_path_degrades_instead_of_raising(self) -> None:
        """A bad log path must never stop the server from serving."""
        blocker = self.dir / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        self.assertIsNone(
            server.configure_logging({"GROK_DELEGATE_LOG_FILE": str(blocker / "c.log")})
        )


class RoundEightLaneVerdictToggleTests(unittest.TestCase):
    """The lane verdict schema is what ends a run; a caller must be able to drop it.

    With --json-schema in argv the executor's deliverable IS that object, so on a
    goal it has to read the codebase to start, it emits one describing intent and
    the lane closes with nothing done. Measured 2026-07-26: five lanes in a row
    empty for that reason; the same goal with lane_verdict off produced a real
    two-file diff in four turns.
    """

    def test_default_is_on(self) -> None:
        kwargs = server._delegate_kwargs_from_args({})
        self.assertTrue(kwargs["lane_verdict"])

    def test_can_be_turned_off(self) -> None:
        kwargs = server._delegate_kwargs_from_args({"lane_verdict": False})
        self.assertFalse(kwargs["lane_verdict"])

    def test_exposed_in_the_tool_schema(self) -> None:
        for tool in server.list_tools():
            if tool["name"] in (server.TOOL_DELEGATE, server.TOOL_START):
                props = tool["inputSchema"]["properties"]
                self.assertIn("lane_verdict", props, tool["name"])
                self.assertEqual(props["lane_verdict"]["type"], "boolean")

    def test_schema_absent_from_argv_when_off(self) -> None:
        """The whole point: no --json-schema reaches the executor."""
        argv = guard.build_grok_argv(
            "do the work",
            "C:/wt",
            guard.build_permission_profile(),
            10,
            grok_bin="grok",
            json_schema=None,
        )
        self.assertNotIn("--json-schema", argv)

    def test_schema_present_in_argv_when_supplied(self) -> None:
        argv = guard.build_grok_argv(
            "do the work",
            "C:/wt",
            guard.build_permission_profile(),
            10,
            grok_bin="grok",
            json_schema='{"type":"object"}',
        )
        self.assertIn("--json-schema", argv)


if __name__ == "__main__":
    unittest.main()
