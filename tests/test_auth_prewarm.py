"""The session probe starts while the host is still thinking.

`notifications/initialized` is the first moment the bridge knows a host is
there, and the last quiet one before it is asked for something. Spending the
12.7 s session probe there costs nobody anything; spending it inside the first
`grok_agent_status` cost the operator a visibly stalled session.
"""

from __future__ import annotations

import pytest

from grok_delegate import server


@pytest.fixture(autouse=True)
def _no_real_probe(monkeypatch):
    calls: list = []
    monkeypatch.setattr(server, "prime_auth_probe_async", lambda: calls.append(1))
    return calls


def _initialized() -> dict:
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_the_probe_starts_when_the_host_says_it_is_ready(_no_real_probe) -> None:
    assert server.handle_jsonrpc(_initialized()) is None
    assert len(_no_real_probe) == 1


def test_the_switch_turns_the_prewarm_off(monkeypatch, _no_real_probe) -> None:
    monkeypatch.setenv("GROK_DELEGATE_PREWARM", "0")
    server.handle_jsonrpc(_initialized())
    assert _no_real_probe == []


def test_a_failing_prewarm_never_breaks_the_handshake(monkeypatch) -> None:
    """A machine without a working CLI still has to finish `initialize`."""

    def boom():
        raise RuntimeError("no grok here")

    monkeypatch.setattr(server, "prime_auth_probe_async", boom)
    assert server.handle_jsonrpc(_initialized()) is None
