"""Starved git probes retry once; checkout does not; version is cached.

Drive everything with an injected fake git runner. No sleep, no real git, no
real GIL contention. The measurement this exists to honour: on this host,
``Popen(['git','--version'])`` median of 8 is 7.1ms idle vs 3258.7ms with 16
GIL-holding threads (sleeping threads 6.5ms). Spawn, not the child.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any, Sequence

import pytest

from grok_delegate import agent_runtime, runner
from grok_delegate.contracts import validate_task_packet


STARVED_SPAWN_SECONDS = 3.2587
IDLE_SPAWN_SECONDS = 0.0071


class ProbeGit:
    """Fake git: remaining timeouts per verb, then success. Never calls git."""

    def __init__(
        self,
        *,
        timeout_remaining: dict[str, int] | None = None,
        spawn_seconds: float = STARVED_SPAWN_SECONDS,
        branch: str = "grok/probe",
        locked: bool = False,
        create_target: bool = True,
    ) -> None:
        self.timeout_remaining = dict(timeout_remaining or {})
        self.spawn_seconds = spawn_seconds
        self.branch = branch
        self.locked = locked
        self.create_target = create_target
        self.calls: list[list[str]] = []
        self.target: Path | None = None

    def _kind(self, argv: list[str]) -> str:
        if "worktree" in argv and "add" in argv:
            return "add"
        if "--version" in argv:
            return "--version"
        if "rev-parse" in argv and "--verify" in argv:
            return "--verify"
        if "rev-parse" in argv and "--abbrev-ref" in argv:
            return "--abbrev-ref"
        if "status" in argv:
            return "status"
        if "worktree" in argv and "list" in argv:
            return "list"
        return argv[0] if argv else ""

    def _timeout(self, argv: list[str], timeout: float) -> dict[str, Any]:
        return {
            "args": argv,
            "returncode": 124,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "timedOut": True,
            "spawn_seconds": self.spawn_seconds,
        }

    def _ok(self, argv: list[str], stdout: str = "") -> dict[str, Any]:
        return {
            "args": argv,
            "returncode": 0,
            "stdout": stdout,
            "stderr": "",
            "timedOut": False,
            "spawn_seconds": 0.0071,
        }

    def __call__(
        self,
        args: Sequence[str],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        kind = self._kind(argv)
        remaining = self.timeout_remaining.get(kind, 0)
        if remaining > 0:
            self.timeout_remaining[kind] = remaining - 1
            if kind == "add":
                self._maybe_create(argv)
            return self._timeout(argv, timeout)

        if kind == "add":
            self._maybe_create(argv)
            return self._ok(argv)
        if kind == "--version":
            return self._ok(argv, "git version 2.45.0\n")
        if kind == "--verify":
            return self._ok(argv, "abc123\n")
        if kind == "--abbrev-ref":
            return self._ok(argv, f"{self.branch}\n")
        if kind == "status":
            return self._ok(argv)
        if kind == "list":
            if self.target is None:
                return self._ok(argv, "")
            block = [
                f"worktree {self.target.as_posix()}",
                "HEAD abc123",
                f"branch refs/heads/{self.branch}",
            ]
            if self.locked:
                block.append("locked initializing")
            return self._ok(argv, "\n".join(block) + "\n\n")
        return self._ok(argv)

    def _maybe_create(self, argv: list[str]) -> None:
        path: Path | None = None
        if "-b" in argv:
            i = argv.index("-b")
            if i + 2 < len(argv):
                path = Path(argv[i + 2])
        else:
            i = argv.index("add")
            if i + 1 < len(argv):
                path = Path(argv[i + 1])
        if path is None:
            return
        self.target = path
        if self.create_target:
            path.mkdir(parents=True, exist_ok=True)


def _count(calls: list[list[str]], kind: str) -> int:
    n = 0
    for argv in calls:
        if kind == "add":
            if "worktree" in argv and "add" in argv:
                n += 1
        elif kind in argv:
            n += 1
    return n


@pytest.fixture(autouse=True)
def _isolate_cache_and_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_settle_sleep", lambda _s: None)
    runner.clear_git_version_cache()
    yield
    runner.clear_git_version_cache()


def _prepare(tmp_path: Path, git: ProbeGit, lane: str = "probe") -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    lanes = tmp_path / "lanes"
    lanes.mkdir()
    return runner.prepare_worktree(
        repo_root=repo,
        lane=lane,
        lanes_parent=lanes,
        git_runner=git,
        timeout=5.0,
        checkout_timeout=9.0,
        require_clean_base=False,
    )


def test_probe_timeout_once_leaves_dispatch_alive(tmp_path: Path) -> None:
    """A GIL-starved probe succeeds on the retry; the dispatch is not GIT_TIMEOUT."""
    git = ProbeGit(timeout_remaining={"--version": 1}, branch="grok/probe")
    result = _prepare(tmp_path, git)
    assert result.get("ok") is True, result
    assert "error" not in result
    assert _count(git.calls, "--version") == 2
    assert _count(git.calls, "add") == 1


def test_probe_timeout_twice_fails_with_starvation_fields(tmp_path: Path) -> None:
    """A hung probe still fails; the error names spawn cost, not a broken git."""
    git = ProbeGit(timeout_remaining={"--version": 2}, branch="grok/probe")
    result = _prepare(tmp_path, git)
    assert result.get("ok") is False
    assert result.get("error") == "GIT_TIMEOUT"
    assert result.get("step") == "--version"
    assert result.get("message") == (
        "git --version exceeded its 5s budget (the command was not "
        "observed to fail — the wrapper stopped waiting)"
    )
    assert result.get("retried") is True
    assert result.get("known_cause") == runner.GIT_SPAWN_STARVATION_CAUSE
    assert result.get("spawn_seconds") == STARVED_SPAWN_SECONDS
    assert "3258.7" in str(result.get("spawn_measurement"))
    assert "7.1" in str(result.get("spawn_measurement"))
    assert _count(git.calls, "--version") == 2
    assert _count(git.calls, "add") == 0


def test_checkout_is_not_retried(tmp_path: Path) -> None:
    """``worktree add`` has its own budget; running it twice would double a long wait."""
    git = ProbeGit(
        timeout_remaining={"add": 1},
        branch="grok/probe",
        locked=True,
        create_target=True,
    )
    result = _prepare(tmp_path, git)
    assert _count(git.calls, "add") == 1
    # Unsettled checkout is still GIT_TIMEOUT (retryable), just not retried here.
    assert result.get("error") == "GIT_TIMEOUT"
    assert result.get("step") == "worktree add"
    assert result.get("retried") is not True


def test_version_cache_returns_without_spawning_twice(tmp_path: Path) -> None:
    """The binary does not change while the process lives; clearing forces a spawn."""
    calls: list[list[str]] = []
    binary = str(tmp_path / "fake-git.exe")

    def fake(
        args: Sequence[str], cwd: Path | None, timeout: float
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        calls.append(argv)
        return {
            "args": argv,
            "returncode": 0,
            "stdout": "git version 2.45.0\n",
            "stderr": "",
            "timedOut": False,
            "spawn_seconds": 0.0071,
        }

    first = runner.cached_git_version(fake, None, 5.0, binary=binary)
    second = runner.cached_git_version(fake, None, 5.0, binary=binary)
    assert first["stdout"] == second["stdout"] == "git version 2.45.0\n"
    assert calls == [["--version"]]
    runner.clear_git_version_cache()
    third = runner.cached_git_version(fake, None, 5.0, binary=binary)
    assert third["stdout"] == "git version 2.45.0\n"
    assert calls == [["--version"], ["--version"]]


def _ok_version() -> dict[str, Any]:
    return {
        "args": ["--version"],
        "returncode": 0,
        "stdout": "git version 2.45.0\n",
        "stderr": "",
        "timedOut": False,
        "spawn_seconds": IDLE_SPAWN_SECONDS,
    }


def _scripted_spawn(responses: list[dict[str, Any]]):
    calls: list[list[str]] = []

    def fake(
        args: Sequence[str], cwd: Path | None, timeout: float
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        calls.append(argv)
        if not responses:
            raise AssertionError(f"spawned past the scripted responses: {argv}")
        return responses.pop(0)

    return fake, calls


def test_version_cache_does_not_remember_a_failure(tmp_path: Path) -> None:
    """A long-lived server must retry a failed probe; git can appear after startup."""
    missing_bin = str(tmp_path / "missing" / "git")
    fake, calls = _scripted_spawn(
        [
            {
                "args": ["--version"],
                "returncode": 127,
                "stdout": "",
                "stderr": "git not found",
                "timedOut": False,
                "missing": True,
                "spawn_seconds": 0.001,
            },
            _ok_version(),
        ]
    )
    first = runner.cached_git_version(fake, None, 5.0, binary=missing_bin)
    second = runner.cached_git_version(fake, None, 5.0, binary=missing_bin)
    third = runner.cached_git_version(fake, None, 5.0, binary=missing_bin)
    assert first.get("missing") is True
    assert second.get("returncode") == 0
    assert third.get("stdout") == "git version 2.45.0\n"
    assert calls == [["--version"], ["--version"]]

    nonzero_bin = str(tmp_path / "broken" / "git")
    fake2, calls2 = _scripted_spawn(
        [
            {
                "args": ["--version"],
                "returncode": 1,
                "stdout": "",
                "stderr": "fatal: broken",
                "timedOut": False,
                "spawn_seconds": 0.001,
            },
            _ok_version(),
        ]
    )
    assert runner.cached_git_version(fake2, None, 5.0, binary=nonzero_bin).get(
        "returncode"
    ) == 1
    assert runner.cached_git_version(fake2, None, 5.0, binary=nonzero_bin).get(
        "returncode"
    ) == 0
    assert calls2 == [["--version"], ["--version"]]


def test_probe_timeout_twice_without_slow_spawn_does_not_claim_starvation(
    tmp_path: Path,
) -> None:
    """A hung git that spawned in milliseconds is a timeout, not GIL starvation."""
    git = ProbeGit(
        timeout_remaining={"--version": 2},
        spawn_seconds=IDLE_SPAWN_SECONDS,
        branch="grok/probe",
    )
    result = _prepare(tmp_path, git)
    assert result.get("ok") is False
    assert result.get("error") == "GIT_TIMEOUT"
    assert result.get("retried") is True
    assert result.get("spawn_seconds") == IDLE_SPAWN_SECONDS
    assert "known_cause" not in result
    assert "spawn_measurement" not in result
    assert IDLE_SPAWN_SECONDS < runner.GIT_SPAWN_STARVATION_MIN_SECONDS
    assert STARVED_SPAWN_SECONDS >= runner.GIT_SPAWN_STARVATION_MIN_SECONDS


def test_starved_first_attempt_still_names_starvation_when_retry_is_fast(
    tmp_path: Path,
) -> None:
    """First Popen starved, retry spawned in milliseconds; both timed out.

    ``_run_git_probe`` used to return only the second result, so
    ``_git_timeout_error`` read spawn_seconds=0.0071 and omitted
    known_cause — the starvation this change exists to name, and the
    antivirus turn it exists to prevent.
    """
    class MixedSpawnGit(ProbeGit):
        def __init__(self) -> None:
            super().__init__(
                timeout_remaining={"--version": 2}, branch="grok/probe"
            )
            self._timeout_spawns = [STARVED_SPAWN_SECONDS, IDLE_SPAWN_SECONDS]

        def _timeout(self, argv: list[str], timeout: float) -> dict[str, Any]:
            result = super()._timeout(argv, timeout)
            if self._timeout_spawns:
                result["spawn_seconds"] = self._timeout_spawns.pop(0)
            return result

    git = MixedSpawnGit()
    result = _prepare(tmp_path, git)
    assert result.get("ok") is False
    assert result.get("error") == "GIT_TIMEOUT"
    assert result.get("retried") is True
    assert result.get("known_cause") == runner.GIT_SPAWN_STARVATION_CAUSE
    assert result.get("spawn_seconds") == STARVED_SPAWN_SECONDS
    assert result.get("spawn_seconds") != IDLE_SPAWN_SECONDS
    assert result.get("attempt_spawn_seconds") == [
        STARVED_SPAWN_SECONDS,
        IDLE_SPAWN_SECONDS,
    ]
    assert result.get("spawn_covers") == runner.GIT_SPAWN_COVERS
    assert "3258.7" in str(result.get("spawn_measurement"))
    assert _count(git.calls, "--version") == 2
    assert _count(git.calls, "add") == 0


def _settle_poll_count() -> int:
    waited = 0.0
    polls = 0
    while True:
        polls += 1
        if waited >= runner.CHECKOUT_SETTLE_GRACE_SECONDS:
            break
        waited += runner.CHECKOUT_SETTLE_POLL_SECONDS
    return polls


def test_settle_loop_does_not_retry_probes(tmp_path: Path) -> None:
    """Settle is already a loop; retrying inside it multiplies a hung git past the job budget."""
    git = ProbeGit(
        timeout_remaining={"add": 1, "list": 10_000},
        branch="grok/probe",
        locked=True,
        create_target=True,
    )
    result = _prepare(tmp_path, git)
    assert result.get("error") == "GIT_TIMEOUT"
    assert result.get("step") == "worktree add"
    list_calls = sum(
        1 for argv in git.calls if "worktree" in argv and "list" in argv
    )
    polls = _settle_poll_count()
    assert list_calls == polls
    assert _count(git.calls, "add") == 1


@pytest.mark.parametrize("exc_type", [OSError, KeyboardInterrupt])
def test_spawn_git_kills_the_child_when_communicate_raises(
    monkeypatch: pytest.MonkeyPatch, exc_type: type[BaseException]
) -> None:
    """subprocess.run killed on any exception; TimeoutExpired was not the only path."""

    class Child:
        def __init__(self) -> None:
            self.killed = False
            self.returncode = None
            self.pid = 4242

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            raise exc_type("pipe closed" if exc_type is OSError else "")

        def kill(self) -> None:
            self.killed = True

    child = Child()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(exc_type):
        runner._spawn_git(["status"], None, 5.0)
    assert child.killed is True


def test_spawn_git_kill_failure_does_not_hide_original_or_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kill() raising used to replace KeyboardInterrupt; unbounded drain hung."""

    class Child:
        def __init__(self) -> None:
            self.kill_calls = 0
            self.communicate_calls = 0
            self.returncode = None
            self.pid = 4242

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise KeyboardInterrupt()
            if timeout is None:
                raise AssertionError(
                    "drain communicate() had no timeout; would hang"
                )
            return ("", "")

        def kill(self) -> None:
            self.kill_calls += 1
            raise OSError("kill failed")

    child = Child()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(KeyboardInterrupt):
        runner._spawn_git(["status"], None, 5.0)
    assert child.kill_calls == 1
    assert child.communicate_calls == 2


def test_default_git_runner_does_not_cache_a_failed_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """default_git_runner is the production cache path; injected ProbeGit never reaches it."""
    binary = str(tmp_path / "prod-git.exe")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: binary)
    fake, calls = _scripted_spawn(
        [
            {
                "args": ["--version"],
                "returncode": 127,
                "stdout": "",
                "stderr": "git not found",
                "timedOut": False,
                "missing": True,
                "spawn_seconds": 0.001,
            },
            _ok_version(),
        ]
    )
    monkeypatch.setattr(runner, "_spawn_git", fake)
    first = runner.default_git_runner(["--version"], None, 5.0)
    second = runner.default_git_runner(["--version"], None, 5.0)
    third = runner.default_git_runner(["--version"], None, 5.0)
    assert first.get("missing") is True
    assert second.get("returncode") == 0
    assert third.get("stdout") == "git version 2.45.0\n"
    assert calls == [["--version"], ["--version"]]


def test_agent_runtime_git_runner_does_not_cache_a_failed_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The write-role runner in agent_runtime is the other production cache path."""
    captured: dict[str, Any] = {}

    def fake_ensure(root: Path, git_runner=None, timeout: float = 30.0):
        captured["git"] = git_runner
        return {
            "ok": True,
            "ignored": True,
            "written": False,
            "reason": "ALREADY_IGNORED",
        }

    def fake_prepare(**kwargs: Any) -> dict[str, Any]:
        captured["git"] = kwargs.get("git_runner") or captured.get("git")
        return {"ok": False, "error": "STOP_FOR_TEST", "message": "stop"}

    monkeypatch.setattr(agent_runtime, "ensure_lane_dir_ignored", fake_ensure)
    monkeypatch.setattr(agent_runtime, "prepare_worktree", fake_prepare)

    root = tmp_path / "repo"
    root.mkdir()
    task = validate_task_packet(
        {
            "objective": "capture the write-role git runner",
            "role": "execute",
            "project_root": str(root),
            "permission_profile": "workspace",
            "max_turns": 5,
            "timeout_seconds": 30,
            "inputs": [],
            "constraints": [],
            "acceptance_criteria": [],
            "expected_artifacts": ["expected.txt"],
            "correlation_id": "cache-wiring",
            "test_commands": ["python -m pytest -q"],
        },
        allowed_roots=[root],
    )
    agent_runtime.run_task(
        task,
        transport="stdio",
        lane="cache-wiring",
        router=agent_runtime.TransportRouter(
            grok_bin="grok", adapters={"stdio": object()}
        ),
        cancel_event=threading.Event(),
    )
    git = captured.get("git")
    assert git is not None

    responses = [
        {
            "returncode": 127,
            "stdout": "",
            "stderr": "binary not found",
            "timedOut": False,
            "missing": True,
            "spawn_seconds": 0.001,
        },
        {
            "returncode": 0,
            "stdout": "git version 2.45.0\n",
            "stderr": "",
            "timedOut": False,
            "spawn_seconds": IDLE_SPAWN_SECONDS,
        },
    ]
    owned_calls: list[list[str]] = []

    def fake_owned(argv, cwd, timeout, cancel_event):
        owned_calls.append([str(a) for a in argv])
        if not responses:
            raise AssertionError("spawned past the scripted responses")
        return responses.pop(0)

    monkeypatch.setattr(agent_runtime, "_run_owned_process", fake_owned)
    first = git(["--version"], root, 5.0)
    second = git(["--version"], root, 5.0)
    third = git(["--version"], root, 5.0)
    assert first.get("missing") is True
    assert second.get("returncode") == 0
    assert third.get("stdout") == "git version 2.45.0\n"
    assert len(owned_calls) == 2
