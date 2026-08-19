"""Unit tests for grok_delegate.anchors (R7-B goal anchor pre-validation).

Full scenario matrix from Service/Archive/GOAL-ROUND7-AUTONOMY.md R7-B. Uses tempfile
worktrees only — never touches the real repo. No subprocess, no git
mutation, no real grok.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate.anchors import (  # noqa: E402
    MAX_ANCHORS,
    extract_goal_anchors,
    validate_goal_anchors,
)


class AnchorScenarioTests(unittest.TestCase):
    """Every R7-B scenario: extract + validate against a fake worktree."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tmp.name) / "wt"
        self.worktree.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _touch(self, rel: str, body: str = "// stub\n") -> Path:
        """Create *rel* under the fake worktree (parents as needed)."""
        path = self.worktree / rel.replace("\\", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    # --- present / absent ----------------------------------------------------

    def test_path_present_in_worktree(self) -> None:
        """A relative path that exists under the worktree is ok, not missing."""
        self._touch("src/services/goals.ts")
        goal = "Implement the change in src/services/goals.ts and commit."
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], ["src/services/goals.ts"])
        self.assertEqual(result["missing"], [])
        self.assertTrue(result["ok"])

    def test_path_absent(self) -> None:
        """A cited relative path with no filesystem hit is reported missing."""
        goal = "Edit src/services/does-not-exist.ts carefully."
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], ["src/services/does-not-exist.ts"])
        self.assertEqual(result["missing"], ["src/services/does-not-exist.ts"])
        self.assertFalse(result["ok"])

    # --- line suffix ---------------------------------------------------------

    def test_line_suffix_stripped_before_exists(self) -> None:
        """file.ts:123 keeps the :line form in checked but strips it for exists()."""
        self._touch("src/file.ts")
        goal = "Start at src/file.ts:123 and refactor the handler."
        anchors = extract_goal_anchors(goal)
        self.assertEqual(anchors, ["src/file.ts:123"])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], ["src/file.ts:123"])
        self.assertEqual(result["missing"], [])
        self.assertTrue(result["ok"])

    # --- markup forms --------------------------------------------------------

    def test_backticked_path(self) -> None:
        """Backticked path-like tokens are extracted (and validated)."""
        self._touch("src/services/x.ts")
        goal = "Edit `src/services/x.ts` and leave a short note."
        self.assertEqual(extract_goal_anchors(goal), ["src/services/x.ts"])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], [])

    def test_markdown_link_path(self) -> None:
        """Path inside a markdown link [x](Service/Goals/Y.md) is extracted."""
        self._touch("Service/Goals/Y.md", "# Y\n")
        goal = "Follow [x](Service/Goals/Y.md) before changing behaviour."
        self.assertEqual(extract_goal_anchors(goal), ["Service/Goals/Y.md"])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], ["Service/Goals/Y.md"])
        self.assertEqual(result["missing"], [])

    # --- negative extraction -------------------------------------------------

    def test_url_not_extracted(self) -> None:
        """A URL https://example.com/a/b.ts must never become an anchor."""
        goal = "See https://example.com/a/b.ts for the upstream shape."
        self.assertEqual(extract_goal_anchors(goal), [])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], [])
        self.assertEqual(result["missing"], [])

    def test_prose_dot_not_extracted(self) -> None:
        """Dotted prose (version 1.2, i.e. foo.bar) must not look like a path."""
        goal = "e.g. version 1.2, i.e. foo.bar as words — no file paths here."
        self.assertEqual(extract_goal_anchors(goal), [])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], [])

    # --- Windows / absolute / glob -------------------------------------------

    def test_windows_backslash_path(self) -> None:
        r"""Windows backslash path src\services\x.ts extracts and resolves."""
        self._touch("src/services/x.ts")
        goal = r"Please touch src\services\x.ts only."
        anchors = extract_goal_anchors(goal)
        self.assertEqual(anchors, [r"src\services\x.ts"])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], [r"src\services\x.ts"])
        self.assertEqual(result["missing"], [])

    def test_absolute_path_never_present_in_worktree(self) -> None:
        """Absolute C:/x/y.ts is extracted but never a present-in-worktree hit."""
        # Even if a same-shaped relative tree exists, absolute must not hit.
        self._touch("x/y.ts")
        goal = "Compare against C:/x/y.ts for the external copy."
        anchors = extract_goal_anchors(goal)
        self.assertEqual(anchors, ["C:/x/y.ts"])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], ["C:/x/y.ts"])
        self.assertEqual(result["missing"], ["C:/x/y.ts"])
        self.assertFalse(result["ok"])

    def test_glob_src_star_star_ts(self) -> None:
        """Glob src/**/*.ts is present when at least one match exists under worktree."""
        self._touch("src/a.ts")
        self._touch("src/nested/b.ts")
        goal = "Type-check everything under src/**/*.ts before shipping."
        anchors = extract_goal_anchors(goal)
        self.assertEqual(anchors, ["src/**/*.ts"])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], ["src/**/*.ts"])
        self.assertEqual(result["missing"], [])

    def test_path_only_on_other_branch_counts_missing(self) -> None:
        """Filesystem-only check: a path living only on another branch is missing.

        Why: validate_goal_anchors must never walk git refs — a pure worktree
        exists() miss is enough. The fake worktree has no such file.
        """
        goal = "Port the helper from src/only_on_other_branch.ts into this lane."
        # Deliberately do not create the file — simulates "exists only elsewhere".
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], ["src/only_on_other_branch.ts"])
        self.assertEqual(result["missing"], ["src/only_on_other_branch.ts"])
        self.assertFalse(result["ok"])

    @unittest.skipUnless(sys.platform == "win32", "case-insensitive FS is a Windows trait")
    def test_windows_case_difference(self) -> None:
        """On Windows, Src/Services/X.ts matches an on-disk src/services/x.ts."""
        self._touch("src/services/x.ts")
        goal = "Edit Src/Services/X.ts with the corrected types."
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], ["Src/Services/X.ts"])
        self.assertEqual(result["missing"], [])
        self.assertTrue(result["ok"])

    # --- bounds / empty / all-missing / de-dupe ------------------------------

    def test_more_than_max_anchors_truncated(self) -> None:
        """More than MAX_ANCHORS unique paths truncates deterministically to first N."""
        paths = [f"pkg/mod{i}/file.py" for i in range(MAX_ANCHORS + 12)]
        goal = "Touch these: " + " ".join(paths)
        got = extract_goal_anchors(goal)
        self.assertEqual(len(got), MAX_ANCHORS)
        self.assertEqual(got, paths[:MAX_ANCHORS])
        # validate also only checks the truncated set
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], paths[:MAX_ANCHORS])
        self.assertEqual(len(result["missing"]), MAX_ANCHORS)
        self.assertFalse(result["ok"])

    def test_empty_goal_gives_empty_list(self) -> None:
        """Empty (or whitespace-only) goal yields no anchors."""
        self.assertEqual(extract_goal_anchors(""), [])
        self.assertEqual(extract_goal_anchors("   \n\t  "), [])
        result = validate_goal_anchors("", self.worktree)
        self.assertEqual(result, {"ok": True, "missing": [], "checked": []})

    def test_goal_with_no_paths_ok_empty_checked(self) -> None:
        """Goal with no path-like tokens → ok true and empty checked (spawn proceeds)."""
        goal = "Refactor carefully and keep the public API stable."
        self.assertEqual(extract_goal_anchors(goal), [])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], [])
        self.assertEqual(result["missing"], [])

    def test_every_anchor_missing_ok_false(self) -> None:
        """When every extracted anchor is missing → ok false and all listed in missing."""
        goal = "Wire src/ghost/a.ts into lib/ghost/b.ts and document it."
        result = validate_goal_anchors(goal, self.worktree)
        self.assertFalse(result["ok"])
        self.assertEqual(result["checked"], ["src/ghost/a.ts", "lib/ghost/b.ts"])
        self.assertEqual(result["missing"], ["src/ghost/a.ts", "lib/ghost/b.ts"])

    def test_dedupe_different_slashes(self) -> None:
        r"""Same path written twice with different slashes de-dupes to the first form."""
        self._touch("src/services/x.ts")
        goal = r"Edit src/services/x.ts and also src\services\x.ts if needed."
        anchors = extract_goal_anchors(goal)
        self.assertEqual(anchors, ["src/services/x.ts"])
        result = validate_goal_anchors(goal, self.worktree)
        self.assertEqual(result["checked"], ["src/services/x.ts"])
        self.assertEqual(result["missing"], [])
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()


class AnchorWiringInDelegateTests(unittest.TestCase):
    """Integrator wiring (R7-B): delegate must fail fast on an all-fictional goal.

    Why this is a separate class: the extraction/validation unit tests above prove the
    pure logic; this one proves the runner actually consults it BEFORE spawning, which is
    the whole point — one fabricated anchor previously burned three real dispatches.
    """

    def setUp(self) -> None:
        # .resolve() matters: on Windows a temp path can carry an 8.3 short
        # name (C:\Users\RUNNER~1\...), and the runner resolves the lanes parent
        # before handing it to git. The fake git below matches the worktree
        # target by string prefix, so an unresolved path here never matches,
        # never creates the directory, and the delegate fails
        # WORKTREE_MISSING_AFTER_ADD -- on CI only, never on a machine whose
        # user name is short enough to escape 8.3 mangling.
        self.repo = Path(tempfile.mkdtemp()).resolve()
        self.lanes = Path(tempfile.mkdtemp()).resolve()

    def _delegate(self, goal: str, *, fail_on_missing_anchors: bool = False):
        from grok_delegate import runner as runner_mod

        spawned: list[list[str]] = []

        def fake_subprocess(args, cwd, timeout):
            spawned.append([str(a) for a in args])
            return {
                "args": args,
                "returncode": 0,
                "stdout": '{"text": "done", "num_turns": 2}',
                "stderr": "",
                "timedOut": False,
            }

        def fake_git(args, cwd, timeout):
            argv = [str(a) for a in args]
            if "--version" in argv:
                out = "git version 2.45.0\n"
            elif "rev-parse" in argv:
                out = "abc123\n"
            else:
                out = ""
            if "worktree" in argv and "add" in argv:
                # Materialize the worktree so anchor checks have a real directory.
                # Pick the token under lanes_parent rather than guessing an index:
                # argv differs between `add -b <branch> <path> <base>` and
                # `add <path> <branch>`.
                lanes_prefix = str(self.lanes)
                targets = [t for t in argv if t.startswith(lanes_prefix)]
                if targets:
                    target = Path(targets[0])
                    # Nested on purpose: a bare filename is deliberately NOT an
                    # anchor (extraction requires a separator), so the "partial
                    # miss" case needs a real path WITH separators.
                    nested = target / "src" / "services"
                    nested.mkdir(parents=True, exist_ok=True)
                    (nested / "real-file.ts").write_text(
                        "export const x = 1;\n", encoding="utf-8"
                    )
            return {
                "args": argv,
                "returncode": 0,
                "stdout": out,
                "stderr": "",
                "timedOut": False,
            }

        result = runner_mod.delegate(
            goal=goal,
            lane=f"anchor-wiring-{abs(hash(goal)) % 10000}",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=3,
            git_runner=fake_git,
            subprocess_runner=fake_subprocess,
            which=lambda n: "/mock/grok",
            fail_on_missing_anchors=fail_on_missing_anchors,
        )
        return result, spawned

    def test_all_anchors_missing_fails_fast_when_opted_in(self) -> None:
        result, spawned = self._delegate(
            "Fix src/services/does-not-exist.ts and tests/also-missing.test.ts please.",
            fail_on_missing_anchors=True,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "ANCHOR_MISSING")
        self.assertEqual(spawned, [], "executor must never be spawned for a fictional goal")
        self.assertTrue(result.get("missing_anchors"))

    def test_partial_miss_still_spawns_and_reports(self) -> None:
        result, spawned = self._delegate(
            "Extend src/services/real-file.ts using src/services/gone.ts as reference."
        )
        self.assertTrue(result.get("ok"), msg=result.get("message"))
        self.assertEqual(len(spawned), 1, "a partially-valid goal must still run")
        self.assertIn("src/services/gone.ts", result.get("missing_anchors") or [])

    def test_goal_without_paths_spawns_normally(self) -> None:
        result, spawned = self._delegate("Improve the wording of the module docstring.")
        self.assertTrue(result.get("ok"), msg=result.get("message"))
        self.assertEqual(len(spawned), 1)
        self.assertEqual(result.get("missing_anchors"), [])

    def test_creation_goal_is_not_blocked_by_default(self) -> None:
        """R7-B correction: a goal that CREATES files cites paths that do not exist.

        Why this test exists: the first version hard-failed whenever every anchor was
        missing, which blocked a legitimate dispatch whose whole job was to deliver
        grok_delegate/verdict.py plus tests/test_verdict.py. Reporting is the default;
        blocking is opt-in.
        """
        result, spawned = self._delegate(
            "Deliver a NEW module grok_delegate/verdict.py plus tests/test_verdict.py."
        )
        self.assertTrue(result.get("ok"), msg=result.get("message"))
        self.assertEqual(len(spawned), 1, "a creation goal must still run")
        self.assertEqual(
            sorted(result.get("missing_anchors") or []),
            ["grok_delegate/verdict.py", "tests/test_verdict.py"],
        )
