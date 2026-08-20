"""Starved git probes retry once; checkout does not; version is cached.

Drive everything with an injected fake git runner. No sleep, no real git, no
real GIL contention. The measurement this exists to honour: on this host,
``Popen(['git','--version'])`` median of 8 is 7.1ms idle vs 3258.7ms with 16
GIL-holding threads (sleeping threads 6.5ms). Spawn, not the child.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pytest

from grok_delegate import runner


STARVED_SPAWN_SECONDS = 3.2587


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
