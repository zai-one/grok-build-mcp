"""The session probe is a network call, so it must be paid for once.

`grok models` measured 12.72 / 12.76 / 12.80 s on the development machine, and
it was the whole cost of `grok_agent_status` and of `session_begin`. These tests
pin the three properties that make caching it safe: a present session is reused,
an absent one is never remembered, and concurrent callers share one probe.
"""
from __future__ import annotations

import threading
import time

import pytest

from grok_delegate import status as status_mod
from grok_delegate.status import (
    build_status_report,
    cached_auth_presence,
    clear_auth_probe_cache,
    prime_auth_probe_async,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_auth_probe_cache()
    yield
    clear_auth_probe_cache()


def _counting_probe(answer: dict, calls: list, delay: float = 0.0):
    def probe(**_kwargs):
        calls.append(1)
        if delay:
            time.sleep(delay)
        return dict(answer)

    return probe


def test_present_session_is_probed_once(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": True}, calls)
    )
    first = cached_auth_presence()
    second = cached_auth_presence()
    assert first["auth_present"] is True
    assert second["auth_present"] is True
    assert len(calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True


def test_first_answer_says_what_it_cost(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        status_mod,
        "probe_auth_presence",
        _counting_probe({"ok": True, "auth_present": True}, calls, delay=0.05),
    )
    out = cached_auth_presence()
    assert out["probe_seconds"] >= 0.05


def test_absent_session_is_never_cached(monkeypatch):
    """The operator is about to fix this with `grok login`.

    A cached "no" would keep saying no for the whole TTL after the login
    succeeded, which is worse than the slow probe it replaced.
    """
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": False}, calls)
    )
    cached_auth_presence()
    cached_auth_presence()
    cached_auth_presence()
    assert len(calls) == 3


def test_login_after_a_refusal_is_seen_immediately(monkeypatch):
    answers = [{"ok": True, "auth_present": False}, {"ok": True, "auth_present": True}]

    def probe(**_kwargs):
        return answers.pop(0) if answers else {"ok": True, "auth_present": True}

    monkeypatch.setattr(status_mod, "probe_auth_presence", probe)
    assert cached_auth_presence()["auth_present"] is False
    assert cached_auth_presence()["auth_present"] is True


def test_cache_expires(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": True}, calls)
    )
    cached_auth_presence(ttl=0.05)
    time.sleep(0.08)
    cached_auth_presence(ttl=0.05)
    assert len(calls) == 2


def test_zero_ttl_disables_the_cache(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": True}, calls)
    )
    cached_auth_presence(ttl=0)
    cached_auth_presence(ttl=0)
    assert len(calls) == 2


def test_clear_forces_a_fresh_probe(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": True}, calls)
    )
    cached_auth_presence()
    clear_auth_probe_cache()
    cached_auth_presence()
    assert len(calls) == 2


def test_concurrent_callers_share_one_probe(monkeypatch):
    """Two tools asking at once cost 12.7 s, not 25.4 s."""
    calls: list = []
    monkeypatch.setattr(
        status_mod,
        "probe_auth_presence",
        _counting_probe({"ok": True, "auth_present": True}, calls, delay=0.2),
    )
    results: list = []
    threads = [
        threading.Thread(target=lambda: results.append(cached_auth_presence())) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 4
    assert all(r["auth_present"] for r in results)
    assert len(calls) == 1


def test_a_raising_probe_frees_its_waiters(monkeypatch):
    """A probe that dies must not park everyone else until the timeout."""

    started = threading.Event()

    def probe(**_kwargs):
        started.set()
        time.sleep(0.1)
        raise RuntimeError("probe died")

    monkeypatch.setattr(status_mod, "probe_auth_presence", probe)
    outcome: list = []

    def first():
        try:
            cached_auth_presence()
        except RuntimeError:
            outcome.append("raised")

    thread = threading.Thread(target=first)
    thread.start()
    started.wait(5)
    began = time.perf_counter()
    with pytest.raises(RuntimeError):
        cached_auth_presence()
    assert time.perf_counter() - began < 5.0
    thread.join(timeout=5)
    assert outcome == ["raised"]


def test_injected_runner_bypasses_the_cache(monkeypatch):
    """One caller's stub must not answer for the machine.

    Every test that hands `build_status_report` a fake runner would otherwise
    poison the shared cache for the next one.
    """
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": True}, calls)
    )
    cached_auth_presence(subprocess_runner=lambda *a, **k: {"returncode": 0})
    cached_auth_presence(subprocess_runner=lambda *a, **k: {"returncode": 0})
    assert len(calls) == 2


def test_status_says_whether_the_answer_was_cached(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": True}, calls)
    )
    first = build_status_report(allowed_roots=[], which=lambda _n: "grok")
    second = build_status_report(allowed_roots=[], which=lambda _n: "grok")
    assert first["auth"]["present"] is True
    assert first["auth"]["cached"] is False
    assert first["auth"]["probe_seconds"] is not None
    assert second["auth"]["cached"] is True
    assert len(calls) == 1


def test_prime_is_silent_when_there_is_no_binary():
    assert prime_auth_probe_async(which=lambda _n: None, grok_bin="grok") is None


def test_prime_fills_the_cache_before_anyone_asks(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        status_mod, "probe_auth_presence", _counting_probe({"ok": True, "auth_present": True}, calls)
    )
    thread = prime_auth_probe_async(which=lambda _n: "grok", grok_bin="grok")
    assert thread is not None
    thread.join(timeout=10)
    out = cached_auth_presence()
    assert out["cached"] is True
    assert len(calls) == 1
