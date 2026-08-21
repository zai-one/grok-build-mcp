"""`grok_agent_session_tick` is advertised, wired into the navigator, and had
no test naming it and no line of documentation.

Found by counting: twenty-three tools are registered and twenty-two appear
either in the docs or in a test. This is the twenty-third. It is the cheap
mid-loop check -- "is my job done yet, and did the tool I just used count" --
so the properties that matter are that it costs almost nothing, that it does
not invent a session, and that it tells the truth about a tool the session was
told not to use.
"""

from __future__ import annotations

import json

import pytest

from grok_delegate import session as session_mod
from grok_delegate.session import session_begin, session_tick


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch, tmp_path):
    session_mod._sessions.clear()
    monkeypatch.setattr(session_mod, "probe_grok_version", lambda **_k: {"version": "1.0.5"})
    monkeypatch.setattr(session_mod, "cached_auth_presence", lambda **_k: {"auth_present": True})
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(tmp_path))
    yield
    session_mod._sessions.clear()


def _session(tmp_path) -> str:
    begun = session_begin(
        goal="do the thing",
        host_budget="small",
        project_root=str(tmp_path),
        which=lambda _name: "grok",
    )
    return str(begun["session_id"])


def test_a_tick_without_a_session_answers_rather_than_raises() -> None:
    """The navigator calls this before it has anything; an exception here would
    end a turn over a question that has an answer."""
    out = session_tick()
    assert isinstance(out, dict)
    assert out.get("ok") is not False or out.get("error")


def test_a_tick_does_not_invent_progress_for_a_session_it_has_never_seen() -> None:
    """It echoes the id it was given, which is fine; what it must not do is
    report a plan it does not have."""
    out = session_tick(session_id="no-such-session")
    assert out["state"] == "idle"
    assert out["steps_left"] == 0
    assert not out.get("blockers")


def test_a_tick_counts_against_the_session_budget(tmp_path) -> None:
    sid = _session(tmp_path)
    before = int(session_mod._sessions[sid].get("polls_used") or 0)
    session_tick(session_id=sid)
    session_tick(session_id=sid)
    assert int(session_mod._sessions[sid]["polls_used"]) == before + 2


def test_a_tick_warns_about_a_tool_the_session_denied(tmp_path) -> None:
    sid = _session(tmp_path)
    session_mod._sessions[sid]["deny_tools"] = ["grok_agent_execute"]
    out = session_tick(session_id=sid, tool_used="grok_agent_execute")
    assert "tool_denied:grok_agent_execute" in (out.get("blockers") or []), out


def test_a_tick_warns_about_a_tool_that_does_not_exist(tmp_path) -> None:
    sid = _session(tmp_path)
    out = session_tick(session_id=sid, tool_used="grok_agent_teleport")
    assert "tool_unknown:grok_agent_teleport" in (out.get("blockers") or []), out


def test_a_tick_stays_small(tmp_path) -> None:
    """It exists to be called in a loop; a large answer defeats the purpose."""
    sid = _session(tmp_path)
    out = session_tick(session_id=sid)
    assert len(json.dumps(out, ensure_ascii=False, default=str)) < 4_096
