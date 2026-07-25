"""R7-G1 chaos: executor misbehaviour (test-only).

Injects fake ``subprocess_runner`` + ``git_runner`` into
``runner.run_delegation`` / ``runner.delegate``. Never spawns a real grok,
gate, or git mutation. Each fault is one independent test with its own
temp dirs — a failure here is a ranked finding, not a production edit.
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

from grok_delegate import driver as driver_mod  # noqa: E402
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
    """Git mock that prepares a lane worktree and reports a scripted diff.

    Why: chaos cases need independent control over empty vs dirty vs committed
    work without touching the host repository.
    """

    def __init__(
        self,
        *,
        name_only: str = "",
        porcelain: str = "",
        diff_stat: str = "",
        commits_log: str = "",
        create_target: bool = True,
    ) -> None:
        self.name_only = name_only
        self.porcelain = porcelain
        self.diff_stat = diff_stat
        self.commits_log = commits_log
        self.create_target = create_target
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

        if "--version" in argv:
            return _ok_proc(argv, stdout="git version 2.45.0\n")

        if "rev-parse" in argv and "--verify" in argv:
            return _ok_proc(argv, stdout="abc123\n")

        if "rev-parse" in argv and "--abbrev-ref" in argv:
            return _ok_proc(argv, stdout="grok/unknown\n")

        if "status" in argv and "--porcelain" in argv:
            # Main-tree status (no -C) stays clean; worktree status uses porcelain.
            out = self.porcelain if "-C" in argv else ""
            return _ok_proc(argv, stdout=out)

        if "worktree" in argv and "add" in argv:
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
# (1) exit 0 + empty worktree — the dominant real failure
# ---------------------------------------------------------------------------


class EmptyWorktreeExitZeroTests(unittest.TestCase):
    """Executor exits 0 having done nothing — never success-with-work."""

    def setUp(self) -> None:
        self._repo_tmp = tempfile.TemporaryDirectory()
        self._lanes_tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._repo_tmp.name)
        self.lanes = Path(self._lanes_tmp.name)

    def tearDown(self) -> None:
        self._repo_tmp.cleanup()
        self._lanes_tmp.cleanup()

    def test_exit_zero_empty_worktree_is_empty_result_not_success_with_work(self) -> None:
        """Exit 0 + empty git → ok, no changed_files/commits; is_empty_result True.

        Why: this is the dominant real failure mode. The driver must treat it as
        empty (retry/block), never as success-with-work. Trust git fields, not
        the executor's exit code alone.
        """
        git = FakeGit(
            name_only="",
            porcelain="",
            diff_stat="",
            commits_log="",
        )
        # Truthful empty verdict: no files claimed, no commit claimed. A lying
        # verdict is a different fault class (see VERDICT_UNSUPPORTED tests).
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(
                files_written=[],
                committed=False,
                summary="looked around, wrote nothing",
            ),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-empty-wt",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        # Executor exit 0 surfaces as ok at the run layer.
        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result.get("status"), "ok")
        # Git reality: nothing changed, nothing committed.
        self.assertEqual(result.get("changed_files") or [], [])
        self.assertEqual(result.get("commits") or [], [])
        # Driver empty-result detector must see this as empty, never as work.
        self.assertTrue(
            driver_mod.is_empty_result(result),
            msg="exit-0 empty worktree must be is_empty_result, not success-with-work",
        )
        # Explicit anti-success-with-work: non-empty changed_files would mean work.
        self.assertFalse(
            bool(result.get("changed_files")) or bool(result.get("commits")),
            msg="empty worktree must not look like success-with-work",
        )
        # Executor was actually invoked once (failure is empty result, not miss).
        self.assertEqual(len(sp.calls), 1)

    def test_files_written_without_commit_are_not_lost(self) -> None:
        """Dirty worktree + zero commits must surface changed_files, empty commits.

        Why: uncommitted work is still real work. Losing the file list would
        hide partial progress and mis-rank the fault as empty-result.
        """
        git = FakeGit(
            name_only="grok_delegate/slice.py\n",
            porcelain=" M grok_delegate/slice.py\n",
            diff_stat=" grok_delegate/slice.py | 3 +++\n",
            commits_log="",
        )
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(
                files_written=["grok_delegate/slice.py"],
                committed=False,
                summary="wrote file but did not commit",
            ),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-uncommitted",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertTrue(result.get("changed_files"), msg=result)
        self.assertEqual(result.get("commits") or [], [])

    def test_undecodable_stdout_does_not_raise(self) -> None:
        """Replacement-character stdout must not crash the delegate path.

        Why: invalid executor bytes become U+FFFD after a replacement decode.
        The driver must still return a result dict rather than raising.
        """
        git = FakeGit(
            name_only="",
            porcelain="",
            diff_stat="",
            commits_log="",
        )
        sp = FakeExecutor(
            returncode=0,
            stdout="partial verdict \ufffd trailing garbage",
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-undecodable",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertIsInstance(result, dict)

    def test_timeout_marks_status_timeout(self) -> None:
        """timedOut + returncode 124 must map to status timeout and ok False.

        Why: a hung executor is not a normal failure; callers need a distinct
        status so they can retry or escalate rather than treat it as empty work.
        """
        git = FakeGit(
            name_only="",
            porcelain="",
            diff_stat="",
            commits_log="",
        )
        sp = FakeExecutor(
            returncode=124,
            timed_out=True,
            stdout="",
            stderr="timeout after 30s",
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-timeout",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertEqual(result.get("status"), "timeout")
        self.assertFalse(result.get("ok"), msg=result)

    def test_lying_verdict_is_unsupported(self) -> None:
        """Verdict claims commit+file while git is empty → VERDICT_UNSUPPORTED.

        Why: a lying machine-readable verdict must not be trusted over git
        reality. Empty worktree + claimed work is unsupported, not success.
        """
        git = FakeGit(
            name_only="",
            porcelain="",
            diff_stat="",
            commits_log="",
        )
        sp = FakeExecutor(
            returncode=0,
            stdout=_lane_verdict_stdout(
                files_written=["grok_delegate/lie.py"],
                committed=True,
                summary="claimed commit without writing",
            ),
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-lying-verdict",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertEqual(result.get("verdict_status"), "VERDICT_UNSUPPORTED")

    def test_huge_stdout_is_bounded(self) -> None:
        """~500k-char stdout must not leak wholesale into the result dict.

        Why: unbounded executor output would bloat reports and hide real
        fields. Every string field returned must be far shorter than input.
        """
        git = FakeGit(
            name_only="",
            porcelain="",
            diff_stat="",
            commits_log="",
        )
        huge = "X" * 500_000
        sp = FakeExecutor(
            returncode=0,
            stdout=huge,
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-huge-stdout",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertIsInstance(result, dict)
        for key, value in result.items():
            if isinstance(value, str):
                self.assertLess(
                    len(value),
                    len(huge) // 10,
                    msg=f"string field {key!r} must not carry the whole payload",
                )

    def test_negative_return_code_is_not_ok(self) -> None:
        """returncode -9 (signal kill) with timedOut False → ok False + status.

        Why: killed-by-signal is not success and not a quiet empty result;
        callers need a diagnosable non-empty status string.
        """
        git = FakeGit(
            name_only="",
            porcelain="",
            diff_stat="",
            commits_log="",
        )
        sp = FakeExecutor(
            returncode=-9,
            timed_out=False,
            stdout="",
            stderr="killed by signal",
        )

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-signal-kill",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: "/mock/grok",
        )

        self.assertFalse(result.get("ok"), msg=result)
        status = result.get("status")
        self.assertIsInstance(status, str)
        self.assertTrue(status, msg="status must be a diagnosable non-empty string")

    def test_missing_binary_reports_grok_missing(self) -> None:
        """missing True from subprocess must surface error GROK_MISSING.

        Why: a missing headless binary is not partial success; callers must
        get a hard missing signal without claimed work or empty-result retry.
        """
        git = FakeGit(
            name_only="",
            porcelain="",
            diff_stat="",
            commits_log="",
        )
        sp = FakeExecutor(missing=True)

        result = runner.delegate(
            goal="Implement the slice and commit your work.",
            lane="chaos-missing-binary",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda _n: None,
        )

        self.assertEqual(result.get("error"), "GROK_MISSING")
        self.assertFalse(result.get("ok"), msg=result)
        self.assertFalse(
            bool(result.get("changed_files")) or bool(result.get("commits")),
            msg="missing binary must not report partial success",
        )


if __name__ == "__main__":
    unittest.main()
