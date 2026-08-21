"""Spawning a child must not wait behind the threads reading an agent stream.

The starvation was measured before the fix and after it, three runs each,
`Popen(['git','--version'])` with sixteen bytecode-hot threads:

    idle process ..............   5.7 /  5.9 /  5.6 ms
    busy, default interval .... 1280  / 1920  / 1526  ms
    busy, this fix ............   22.9 /  22.5 /  24.4 ms

and a whole lane preparation under the same load fell from 39.4-53.0 s to
22.4-27.5 s, against the 60 s budget a routine tick was losing.

Timing cannot be asserted on a shared CI box, so what is pinned here is the
mechanism: the window is entered, it is scoped, and it always closes.
"""

from __future__ import annotations

import sys
import threading

import pytest

from grok_delegate import runner as runner_mod
from grok_delegate.runner import SPAWN_SWITCH_INTERVAL_SECONDS, spawn_priority


@pytest.fixture(autouse=True)
def _interval_restored():
    before = sys.getswitchinterval()
    yield
    sys.setswitchinterval(before)


def test_the_window_lowers_the_interval_and_gives_it_back() -> None:
    before = sys.getswitchinterval()
    with spawn_priority():
        assert sys.getswitchinterval() == SPAWN_SWITCH_INTERVAL_SECONDS
    assert sys.getswitchinterval() == before


def test_a_raising_spawn_still_gives_it_back() -> None:
    before = sys.getswitchinterval()
    with pytest.raises(RuntimeError):
        with spawn_priority():
            raise RuntimeError("Popen exploded")
    assert sys.getswitchinterval() == before


def test_nested_windows_restore_once_at_the_end() -> None:
    """The inner exit must not hand the interpreter back mid-spawn."""
    before = sys.getswitchinterval()
    with spawn_priority():
        with spawn_priority():
            assert sys.getswitchinterval() == SPAWN_SWITCH_INTERVAL_SECONDS
        assert sys.getswitchinterval() == SPAWN_SWITCH_INTERVAL_SECONDS
    assert sys.getswitchinterval() == before


def test_concurrent_spawns_hold_the_window_until_the_last_one_leaves() -> None:
    before = sys.getswitchinterval()
    inside = threading.Event()
    release = threading.Event()
    seen: list[float] = []

    def hold() -> None:
        with spawn_priority():
            inside.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    assert inside.wait(5)
    with spawn_priority():
        pass
    # The other thread is still spawning: the window must not have closed.
    seen.append(sys.getswitchinterval())
    release.set()
    holder.join(timeout=5)
    assert seen == [SPAWN_SWITCH_INTERVAL_SECONDS]
    assert sys.getswitchinterval() == before


def test_the_switch_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_SPAWN_PRIORITY", "0")
    before = sys.getswitchinterval()
    with spawn_priority():
        assert sys.getswitchinterval() == before


def test_git_spawns_inside_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this the fix would be a helper nothing calls."""
    seen: list[float] = []
    import subprocess

    real_popen = subprocess.Popen

    class Recording:
        def __init__(self, *args, **kwargs):
            seen.append(sys.getswitchinterval())
            self._proc = real_popen(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._proc, name)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    runner_mod._spawn_git(["--version"], None, 30.0)
    assert seen == [SPAWN_SWITCH_INTERVAL_SECONDS]
    assert sys.getswitchinterval() != SPAWN_SWITCH_INTERVAL_SECONDS


def test_the_verifier_spawns_inside_the_window(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from grok_delegate import agent_runtime

    seen: list[float] = []
    import subprocess

    real_popen = subprocess.Popen

    class Recording:
        def __init__(self, *args, **kwargs):
            seen.append(sys.getswitchinterval())
            self._proc = real_popen(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._proc, name)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    agent_runtime._run_owned_process(
        [sys.executable, "-c", "print('ok')"],
        tmp_path,
        60.0,
        threading.Event(),
    )
    assert seen == [SPAWN_SWITCH_INTERVAL_SECONDS]
