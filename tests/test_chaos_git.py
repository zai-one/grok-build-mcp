"""R7-G2 chaos: git misbehaviour (test-only).

Injects fake ``subprocess_runner`` + ``git_runner`` into ``runner.delegate`` /
``runner.collect_diff``. Never spawns a real grok, gate, or git mutation. Each
fault is one independent test with its own temp dirs — a failure here is a
ranked finding, not a production edit.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import runner  # noqa: E402


# ---------------------------------------------------------------------------
# Injectable fakes (no host git / no real executor)
# ---------------------------------------------------------------------------


def _ok_proc(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    missing: bool = False,
) -> dict[str, Any]:
    """Shape shared by the real runner's subprocess/git result dicts."""
    out: dict[str, Any] = {
        "args": [str(a) for a in args],
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timedOut": timed_out,
    }
    if missing:
        out["missing"] = True
    return out


class FakeGit:
    """Git mock with knobs for worktree / base / version fault injection.

    Why: chaos cases need independent control over prepare_worktree and
    collect_diff failure modes without touching the host repository.
    """

    def __init__(
        self,
        *,
        version_ok: bool = True,
        base_ok: bool = True,
        worktree_add_ok: bool = True,
        create_target: bool = True,
        existing_branch: str | None = None,
        name_only: str = "",
        porcelain: str = "",
        diff_stat: str = "",
        commits_log: str = "",
        log_ok: bool = True,
        all_fail: bool = False,
    ) -> None:
        self.version_ok = version_ok
        self.base_ok = base_ok
        self.worktree_add_ok = worktree_add_ok
        self.create_target = create_target
        self.existing_branch = existing_branch
        self.name_only = name_only
        self.porcelain = porcelain
        self.diff_stat = diff_stat
        self.commits_log = commits_log
        self.log_ok = log_ok
        self.all_fail = all_fail
        self.calls: list[list[str]] = []
        self.created_worktrees: list[Path] = []

    def __call__(
        self,
        args: Sequence[str],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)

        if self.all_fail:
            return _ok_proc(
                argv,
                returncode=128,
                stderr="fatal: mock git failure\n",
            )

        if "--version" in argv:
            if not self.version_ok:
                return _ok_proc(
                    argv,
                    returncode=127,
                    stderr="git not found",
                    missing=True,
                )
            return _ok_proc(argv, stdout="git version 2.45.0\n")

        if "rev-parse" in argv and "--verify" in argv:
            if not self.base_ok:
                return _ok_proc(
                    argv,
                    returncode=128,
                    stderr="fatal: Needed a single revision\n",
                )
            return _ok_proc(argv, stdout="abc123\n")

        if "rev-parse" in argv and "--abbrev-ref" in argv:
            branch = self.existing_branch or "grok/unknown"
            return _ok_proc(argv, stdout=f"{branch}\n")

        if "status" in argv and "--porcelain" in argv:
            out = self.porcelain if "-C" in argv else ""
            return _ok_proc(argv, stdout=out)

        if "worktree" in argv and "add" in argv:
            if not self.worktree_add_ok:
                return _ok_proc(
                    argv,
                    returncode=1,
                    stderr="fatal: worktree add failed\n",
                )
            path: Path | None = None
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
                self.created_worktrees.append(path)
            return _ok_proc(argv, stdout=f"Preparing worktree at {path}\n")

        if "log" in argv:
            if not self.log_ok:
                return _ok_proc(
                    argv,
                    returncode=128,
                    stderr="fatal: bad revision 'HEAD'\n",
                )
            return _ok_proc(argv, stdout=self.commits_log)

        if "diff" in argv and "--name-only" in argv:
            return _ok_proc(argv, stdout=self.name_only)

        if "diff" in argv and "--stat" in argv:
            return _ok_proc(argv, stdout=self.diff_stat)

        return _ok_proc(argv)


class FakeExecutor:
    """Headless-executor mock: scripted exit / stdout / hang / missing binary."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
        missing: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.missing = missing
        self.calls: list[list[str]] = []
        self.cwds: list[str | None] = []

    def __call__(
        self,
        args: Sequence[str],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        self.cwds.append(str(cwd) if cwd is not None else None)
        if self.missing:
            return _ok_proc(
                argv,
                returncode=127,
                stderr=f"binary not found: {argv[0] if argv else '?'}",
                missing=True,
            )
        if self.timed_out:
            return _ok_proc(
                argv,
                returncode=124,
                stdout=self.stdout,
                stderr=self.stderr or f"timeout after {timeout}s",
                timed_out=True,
            )
        return _ok_proc(
            argv,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def _lane_verdict_stdout(**overrides: Any) -> str:
    """Minimal machine-readable verdict JSON the executor might emit."""
    payload: dict[str, Any] = {
        "files_written": [],
        "committed": False,
        "tests_added": 0,
        "gates_run": False,
        "self_skeptic_findings": [],
        "blocked_reason": None,
        "summary": "executor finished",
        "num_turns": 2,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Chaos git fault injections
# ---------------------------------------------------------------------------


class ChaosGitTests(unittest.TestCase):
    """Git-layer faults must fail closed without spawning the executor."""

    def setUp(self) -> None:
        self._repo_tmp = tempfile.TemporaryDirectory()
        self._lanes_tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._repo_tmp.name)
        self.lanes = Path(self._lanes_tmp.name)

    def tearDown(self) -> None:
        self._repo_tmp.cleanup()
        self._lanes_tmp.cleanup()

    def test_worktree_add_failure_never_spawns(self) -> None:
        """worktree add failure → WORKTREE_CREATE_FAILED; executor never runs.

        Why: a failed worktree is not a partial success. Spawning grok against
        a missing path would burn a turn and hide the real git fault.
        """
        git = FakeGit(worktree_add_ok=False)
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(summary="should never run"),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-wt-add-fail",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertEqual(result.get("error"), "WORKTREE_CREATE_FAILED")
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(
            len(sp.calls),
            0,
            msg="executor must not spawn when worktree add fails",
        )

    def test_worktree_missing_after_add_is_detected(self) -> None:
        """worktree add succeeds but target dir is absent → WORKTREE_MISSING_AFTER_ADD.

        Why: a lying git (rc=0 without creating the path) must fail closed
        before any executor spawn, not treat the worktree as ready.
        """
        git = FakeGit(create_target=False)
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(summary="should never run"),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-wt-missing-after-add",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertEqual(result.get("error"), "WORKTREE_MISSING_AFTER_ADD")
        self.assertFalse(result.get("ok"), msg=result)

    def test_unreachable_base_ref_never_spawns(self) -> None:
        """rev-parse --verify failure → BASE_UNREACHABLE; executor never runs.

        Why: an unreachable base must not burn a turn or mutate the tree.
        """
        git = FakeGit(base_ok=False)
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(summary="should never run"),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-base-unreachable",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertEqual(result.get("error"), "BASE_UNREACHABLE")
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(
            len(sp.calls),
            0,
            msg="executor must not spawn when base ref is unreachable",
        )

    def test_missing_git_binary_never_spawns(self) -> None:
        """git --version reports missing → GIT_MISSING; executor never runs.

        Why: without a git binary the lane cannot prepare a worktree safely.
        """
        git = FakeGit(version_ok=False)
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(summary="should never run"),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-git-missing",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertEqual(result.get("error"), "GIT_MISSING")
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(
            len(sp.calls),
            0,
            msg="executor must not spawn when git binary is missing",
        )

    def test_branch_checked_out_elsewhere_is_conflict(self) -> None:
        """Pre-existing worktree dir on another branch → WORKTREE_EXISTS_CONFLICT.

        Why: colliding with an occupied lane path must fail closed and must not
        checkout, reset, clean, or rm anything under the repo.
        """
        lane = "chaos-wt-exists-conflict"
        target = self.lanes / lane
        target.mkdir(parents=True, exist_ok=True)
        (target / ".git").write_text("gitdir: mock\n", encoding="utf-8")

        git = FakeGit(existing_branch="other-lane-branch")
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(summary="should never run"),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane=lane,
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertEqual(result.get("error"), "WORKTREE_EXISTS_CONFLICT")
        self.assertFalse(result.get("ok"), msg=result)
        forbidden = {"checkout", "reset", "clean", "rm"}
        for call in git.calls:
            for arg in call:
                self.assertNotIn(
                    arg,
                    forbidden,
                    msg=f"forbidden git arg {arg!r} in call {call!r}",
                )

    def test_collect_diff_survives_failing_git(self) -> None:
        """collect_diff with every git call failing still returns a dict.

        Why: reporting must degrade to empty lists, not raise into the caller.
        """
        git = FakeGit(all_fail=True)

        out = runner.collect_diff(
            self.repo,  # positional worktree path — collect_diff has no repo_root kwarg
            base_ref="dev",
            git_runner=git,
        )

        self.assertIsInstance(out, dict)
        self.assertIsInstance(out.get("changed_files"), list)
        self.assertIsInstance(out.get("commits"), list)

    def test_collect_diff_survives_detached_head(self) -> None:
        """git log fails while diff succeeds → collect_diff still returns dict.

        Why: detached HEAD must not crash reporting; commits degrades to a list.
        """
        git = FakeGit(log_ok=False, name_only="a.py\n", diff_stat=" a.py | 1 +\n")

        out = runner.collect_diff(
            self.repo,  # positional worktree path — collect_diff has no repo_root kwarg
            base_ref="dev",
            git_runner=git,
        )

        self.assertIsInstance(out, dict)
        self.assertIsInstance(out.get("commits"), list)

    def test_no_git_call_ever_assembles_push_or_merge(self) -> None:
        """A successful delegate must never assemble git push or git merge.

        Why: safety invariant — lane git surface is local-only.
        """
        git = FakeGit()
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(summary="ok"),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-no-push-merge",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertTrue(result.get("ok"), msg=result)
        for call in git.calls:
            for arg in call:
                self.assertNotEqual(arg, "push", msg=call)
                self.assertNotEqual(arg, "merge", msg=call)


if __name__ == "__main__":
    unittest.main()
