#!/usr/bin/env python3
"""Unit tests for grok_delegate.gates (R7-A fixed-command gate runner).

Standalone from the large test_grok_delegate suite. Injects a fake
subprocess runner only — never spawns a real pytest/npx/git process.
"""

from __future__ import annotations

import inspect
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Sequence
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import gates  # noqa: E402


def _ok_proc(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "ok\n",
    stderr: str = "",
    timed_out: bool = False,
    missing: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "args": list(args),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timedOut": timed_out,
    }
    if missing:
        out["missing"] = True
    return out


class RecordingRunner:
    """Configurable fake subprocess runner; never touches the real OS spawn."""

    def __init__(
        self,
        *,
        by_index: dict[int, dict[str, Any] | BaseException] | None = None,
        default: dict[str, Any] | BaseException | None = None,
        raise_on_binary: str | None = None,
    ) -> None:
        self.by_index = dict(by_index or {})
        self.default = default
        self.raise_on_binary = raise_on_binary
        self.calls: list[tuple[list[str], Path | None, float]] = []
        self._lock = threading.Lock()
        self._n = 0

    def __call__(
        self,
        args: Sequence[str],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        with self._lock:
            self.calls.append((argv, Path(cwd) if cwd is not None else None, float(timeout)))
            idx = self._n
            self._n += 1

        if self.raise_on_binary and argv and argv[0] == self.raise_on_binary:
            raise FileNotFoundError(self.raise_on_binary)

        if idx in self.by_index:
            item = self.by_index[idx]
            if isinstance(item, BaseException):
                raise item
            return dict(item)

        if self.default is not None:
            if isinstance(self.default, BaseException):
                raise self.default
            return dict(self.default)

        return _ok_proc(argv)


class GateRunnerTests(unittest.TestCase):
    """Every R7-A scenario from Service/Archive/GOAL-ROUND7-AUTONOMY.md."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tmp.name) / "wt-a"
        self.worktree.mkdir()
        # Python profile needs no node_modules; node profile tests create it.
        (self.worktree / "tests").mkdir()
        (self.worktree / "tests" / "sample.py").write_text("# sample\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _node_worktree(self, name: str = "wt-node") -> Path:
        wt = Path(self.tmp.name) / name
        wt.mkdir(exist_ok=True)
        (wt / "node_modules").mkdir(exist_ok=True)
        return wt

    # --- API surface ---------------------------------------------------------

    def test_unknown_profile_returns_gate_profile_unknown(self) -> None:
        runner = RecordingRunner()
        result = gates.run_gates(
            self.worktree,
            "rust-clippy",
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GATE_PROFILE_UNKNOWN")
        self.assertIn("unknown gate profile", result.get("message", ""))
        self.assertEqual(result.get("results"), [])
        self.assertEqual(runner.calls, [])

    def test_run_gates_has_no_command_parameter(self) -> None:
        params = inspect.signature(gates.run_gates).parameters
        self.assertNotIn("command", params)
        self.assertNotIn("commands", params)
        self.assertNotIn("cmd", params)
        # Explicit public surface from gates module contract.
        self.assertEqual(
            set(params),
            {"worktree", "profile", "paths", "timeout_seconds", "subprocess_runner"},
        )

    # --- Path confinement ----------------------------------------------------

    def test_absolute_path_in_paths_rejected(self) -> None:
        runner = RecordingRunner()
        abs_path = str(self.worktree / "tests" / "sample.py")
        self.assertTrue(Path(abs_path).is_absolute())
        result = gates.run_gates(
            self.worktree,
            "python",
            paths=[abs_path],
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GATE_PATH_ESCAPE")
        self.assertIn("absolute", result.get("message", "").lower())
        self.assertEqual(runner.calls, [])

    def test_dotdot_escape_rejected(self) -> None:
        runner = RecordingRunner()
        result = gates.run_gates(
            self.worktree,
            "python",
            paths=["../outside.py"],
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GATE_PATH_ESCAPE")
        self.assertEqual(runner.calls, [])

    def test_empty_paths_list_behaves_like_none(self) -> None:
        runner = RecordingRunner()
        with_none = gates.run_gates(
            self.worktree,
            "python",
            paths=None,
            subprocess_runner=runner,
        )
        runner_empty = RecordingRunner()
        with_empty = gates.run_gates(
            self.worktree,
            "python",
            paths=[],
            subprocess_runner=runner_empty,
        )
        self.assertTrue(with_none.get("ok"), with_none)
        self.assertTrue(with_empty.get("ok"), with_empty)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(len(runner_empty.calls), 1)
        # Neither call appends path args beyond the hardcoded profile tuple.
        base = list(gates.GATE_PROFILES["python"][0])
        self.assertEqual(runner.calls[0][0], base)
        self.assertEqual(runner_empty.calls[0][0], base)

    def test_path_with_spaces_stays_one_argv_cell(self) -> None:
        spaced = self.worktree / "tests" / "my suite"
        spaced.mkdir()
        (spaced / "t.py").write_text("x=1\n", encoding="utf-8")
        runner = RecordingRunner()
        result = gates.run_gates(
            self.worktree,
            "python",
            paths=["tests/my suite"],
            subprocess_runner=runner,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(runner.calls), 1)
        argv = runner.calls[0][0]
        self.assertIn("tests/my suite", argv)
        # Spaces must not be shell-split into multiple cells.
        self.assertNotIn("my", argv)
        self.assertNotIn("suite", argv)
        self.assertEqual(argv[-1], "tests/my suite")

    # --- Environment / binary ------------------------------------------------

    def test_missing_node_modules_reported_clearly(self) -> None:
        bare = Path(self.tmp.name) / "wt-no-nm"
        bare.mkdir()
        runner = RecordingRunner()
        result = gates.run_gates(
            bare,
            "node",
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GATE_ENV_MISSING")
        self.assertIn("node_modules", result.get("message", ""))
        self.assertEqual(result.get("results"), [])
        self.assertEqual(runner.calls, [])

    def test_binary_absent_handled_without_crashing(self) -> None:
        runner = RecordingRunner(raise_on_binary="py")
        result = gates.run_gates(
            self.worktree,
            "python",
            subprocess_runner=runner,
        )
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("ok"))
        self.assertEqual(len(result.get("results") or []), 1)
        cmd = result["results"][0]
        self.assertFalse(cmd.get("ok"))
        self.assertEqual(cmd.get("reason"), "GATE_BINARY_MISSING")
        self.assertEqual(cmd.get("returncode"), 127)
        self.assertIn("binary not found", cmd.get("output_tail", "").lower())

    def test_binary_absent_via_missing_flag(self) -> None:
        """Runner may also return missing=True instead of raising."""
        runner = RecordingRunner(
            default=_ok_proc(["py"], returncode=127, stderr="", missing=True),
        )
        result = gates.run_gates(
            self.worktree,
            "python",
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        cmd = result["results"][0]
        self.assertEqual(cmd.get("reason"), "GATE_BINARY_MISSING")
        self.assertFalse(cmd.get("ok"))

    # --- Exit / timeout / multi-command --------------------------------------

    def test_nonzero_exit_makes_aggregate_false(self) -> None:
        runner = RecordingRunner(
            default=_ok_proc(["py"], returncode=1, stdout="", stderr="1 failed\n"),
        )
        result = gates.run_gates(
            self.worktree,
            "python",
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GATE_FAILED")
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["summary"]["passed"], 0)
        cmd = result["results"][0]
        self.assertFalse(cmd.get("ok"))
        self.assertEqual(cmd.get("reason"), "GATE_COMMAND_FAILED")
        self.assertEqual(cmd.get("returncode"), 1)

    def test_timeout_marked_and_remaining_commands_still_reported(self) -> None:
        wt = self._node_worktree("wt-timeout")
        runner = RecordingRunner(
            by_index={
                0: _ok_proc(
                    ["npx", "tsc", "--noEmit"],
                    returncode=124,
                    stderr="timeout after 1s",
                    timed_out=True,
                ),
                1: _ok_proc(["npx", "vitest", "run", "--reporter=basic"]),
                2: _ok_proc(["npx", "eslint"]),
            }
        )
        result = gates.run_gates(
            wt,
            "node",
            timeout_seconds=1.0,
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual(len(runner.calls), 3)
        first = result["results"][0]
        self.assertTrue(first.get("timed_out"))
        self.assertFalse(first.get("ok"))
        self.assertEqual(first.get("reason"), "GATE_TIMEOUT")
        # Later commands still ran and are present.
        self.assertTrue(result["results"][1].get("ok"))
        self.assertTrue(result["results"][2].get("ok"))
        self.assertEqual(result["summary"]["timed_out"], 1)
        self.assertEqual(result["summary"]["total"], 3)

    def test_first_command_failure_does_not_short_circuit(self) -> None:
        wt = self._node_worktree("wt-noshort")
        runner = RecordingRunner(
            by_index={
                0: _ok_proc(
                    ["npx", "tsc", "--noEmit"],
                    returncode=2,
                    stderr="error TS2304\n",
                ),
                1: _ok_proc(["npx", "vitest", "run", "--reporter=basic"]),
                2: _ok_proc(["npx", "eslint"]),
            }
        )
        result = gates.run_gates(
            wt,
            "node",
            subprocess_runner=runner,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(len(runner.calls), 3, "must not short-circuit after first failure")
        self.assertEqual(len(result["results"]), 3)
        self.assertFalse(result["results"][0].get("ok"))
        self.assertTrue(result["results"][1].get("ok"))
        self.assertTrue(result["results"][2].get("ok"))
        self.assertEqual(result["summary"]["passed"], 2)
        self.assertEqual(result["summary"]["failed"], 1)

    # --- Output bounds / redaction -------------------------------------------

    def test_huge_output_truncated_with_marker(self) -> None:
        huge = "HEAD-" + ("x" * (gates.DEFAULT_OUTPUT_CHAR_CAP + 2000)) + "-TAIL-END"
        runner = RecordingRunner(
            default=_ok_proc(["py"], stdout=huge),
        )
        result = gates.run_gates(
            self.worktree,
            "python",
            subprocess_runner=runner,
        )
        self.assertTrue(result.get("ok"), result)
        tail = result["results"][0]["output_tail"]
        self.assertTrue(
            tail.startswith(gates._TRUNCATE_MARKER) or gates._TRUNCATE_MARKER.strip() in tail,
            f"expected truncation marker in {tail[:80]!r}",
        )
        # Tail retained: end of the original payload survives.
        self.assertIn("TAIL-END", tail[-40:])
        # Head of the huge payload is dropped (marker + last char_cap chars only).
        self.assertNotIn("HEAD-", tail)
        # Bound roughly at cap + marker.
        self.assertLessEqual(
            len(tail),
            gates.DEFAULT_OUTPUT_CHAR_CAP + len(gates._TRUNCATE_MARKER) + 8,
        )

    def test_planted_secret_redacted_from_output(self) -> None:
        planted = (
            "api_key=sk-live-super-secret-value\n"
            "Authorization: Bearer abc.def.ghi\n"
            "password: hunter2\n"
            "BEGIN PRIVATE KEY\nMIIE...\n"
            "see ~/.grok/auth.json for more\n"
            "all good otherwise\n"
        )
        runner = RecordingRunner(
            default=_ok_proc(["py"], stdout=planted, stderr=""),
        )
        result = gates.run_gates(
            self.worktree,
            "python",
            subprocess_runner=runner,
        )
        self.assertTrue(result.get("ok"), result)
        # Serialize the whole report — secrets must not leak anywhere.
        blob = repr(result)
        self.assertNotIn("sk-live-super-secret-value", blob)
        self.assertNotIn("Bearer abc.def.ghi", blob)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("BEGIN PRIVATE KEY", blob)
        self.assertNotIn("auth.json", blob.lower().replace("[redacted-auth-path]", ""))
        tail = result["results"][0]["output_tail"]
        self.assertIn("[REDACTED]", tail)
        self.assertIn("all good otherwise", tail)

    # --- Isolation ------------------------------------------------------------

    def test_two_worktrees_do_not_interfere(self) -> None:
        wt_a = self.worktree
        wt_b = Path(self.tmp.name) / "wt-b"
        wt_b.mkdir()
        (wt_b / "tests").mkdir()

        lock = threading.Lock()
        calls_a: list[tuple[list[str], Path | None, float]] = []
        calls_b: list[tuple[list[str], Path | None, float]] = []

        def runner_a(
            args: Sequence[str],
            cwd: Path | None,
            timeout: float,
        ) -> dict[str, Any]:
            argv = [str(a) for a in args]
            with lock:
                calls_a.append((argv, Path(cwd) if cwd else None, float(timeout)))
            return _ok_proc(argv, stdout="from-a\n")

        def runner_b(
            args: Sequence[str],
            cwd: Path | None,
            timeout: float,
        ) -> dict[str, Any]:
            argv = [str(a) for a in args]
            with lock:
                calls_b.append((argv, Path(cwd) if cwd else None, float(timeout)))
            return _ok_proc(argv, stdout="from-b\n")

        results: dict[str, Any] = {}
        errors: list[BaseException] = []

        def run_a() -> None:
            try:
                results["a"] = gates.run_gates(
                    wt_a,
                    "python",
                    paths=["tests/sample.py"],
                    subprocess_runner=runner_a,
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def run_b() -> None:
            try:
                results["b"] = gates.run_gates(
                    wt_b,
                    "python",
                    paths=["tests"],
                    subprocess_runner=runner_b,
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=run_a)
        t2 = threading.Thread(target=run_b)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertTrue(results["a"].get("ok"), results.get("a"))
        self.assertTrue(results["b"].get("ok"), results.get("b"))

        self.assertEqual(len(calls_a), 1)
        self.assertEqual(len(calls_b), 1)
        cwd_a = calls_a[0][1]
        cwd_b = calls_b[0][1]
        self.assertIsNotNone(cwd_a)
        self.assertIsNotNone(cwd_b)
        assert cwd_a is not None and cwd_b is not None
        self.assertEqual(cwd_a.resolve(), wt_a.resolve())
        self.assertEqual(cwd_b.resolve(), wt_b.resolve())
        self.assertNotEqual(cwd_a.resolve(), cwd_b.resolve())
        # Path args stayed with their own worktree call.
        self.assertIn("tests/sample.py", calls_a[0][0])
        self.assertIn("tests", calls_b[0][0])
        self.assertNotIn("tests/sample.py", calls_b[0][0])
        # Reports point at distinct worktrees.
        self.assertEqual(Path(results["a"]["worktree"]).resolve(), wt_a.resolve())
        self.assertEqual(Path(results["b"]["worktree"]).resolve(), wt_b.resolve())
        self.assertIn("from-a", results["a"]["results"][0]["output_tail"])
        self.assertIn("from-b", results["b"]["results"][0]["output_tail"])

    def test_magicmock_runner_is_accepted(self) -> None:
        """unittest.mock injection path — still no real process."""
        fake = mock.MagicMock(
            return_value={
                "returncode": 0,
                "stdout": "mocked\n",
                "stderr": "",
                "timedOut": False,
            }
        )
        result = gates.run_gates(
            self.worktree,
            "python",
            subprocess_runner=fake,
        )
        self.assertTrue(result.get("ok"), result)
        fake.assert_called_once()
        args, kwargs = fake.call_args
        self.assertEqual(list(args[0])[:5], ["py", "-3", "-m", "pytest", "tests"])
        self.assertEqual(Path(args[1]).resolve(), self.worktree.resolve())

    def test_all_commands_pass_aggregate_true(self) -> None:
        runner = RecordingRunner()
        result = gates.run_gates(
            self.worktree,
            "python",
            subprocess_runner=runner,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("error", result)
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["passed"], 1)
        self.assertEqual(result["summary"]["failed"], 0)

    def test_node_paths_appended_only_where_accepted(self) -> None:
        wt = self._node_worktree("wt-path-accept")
        runner = RecordingRunner()
        result = gates.run_gates(
            wt,
            "node",
            paths=["src/foo.ts"],
            subprocess_runner=runner,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(runner.calls), 3)
        # tsc --noEmit does not accept free paths (profile flag False).
        self.assertEqual(runner.calls[0][0], list(gates.GATE_PROFILES["node"][0]))
        self.assertNotIn("src/foo.ts", runner.calls[0][0])
        # vitest and eslint do.
        self.assertIn("src/foo.ts", runner.calls[1][0])
        self.assertIn("src/foo.ts", runner.calls[2][0])


if __name__ == "__main__":
    unittest.main()
