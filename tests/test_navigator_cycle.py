"""A host that executes only cards must be able to finish a write job.

The navigator exists so the host does not have to know the protocol. An audit
walked every mode executing nothing but the card it was handed, and the write
cycle could not be completed: the plan offered one poll for a job that takes
half a minute, then said done. Verify mode had nowhere to name a job. And the
refusal for a project that never opted in did not say which tool fixes it.
"""

from __future__ import annotations

from typing import Any

import pytest

from grok_delegate import jobs as jobs_mod
from grok_delegate import session as session_mod
from grok_delegate.session import _ALLOW, _ROUTES, session_begin, session_next


@pytest.fixture(autouse=True)
def _clean_sessions():
    session_mod._sessions.clear()
    yield
    session_mod._sessions.clear()


@pytest.fixture(autouse=True)
def _ready_gate(monkeypatch, tmp_path):
    """A green gate, so the mode under test is the one that compiles.

    An unready gate reroutes every intent to triage, which would make these
    tests pass or fail for a reason that has nothing to do with the cycle.
    """
    monkeypatch.setattr(session_mod, "probe_grok_version", lambda **_k: {"version": "1.0.4"})
    monkeypatch.setattr(session_mod, "probe_auth_presence", lambda **_k: {"auth_present": True})
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(tmp_path))
    return tmp_path


def _begin(**kwargs) -> dict[str, Any]:
    kwargs.setdefault("which", lambda _n: "grok")
    return session_begin(**kwargs)


# --- waiting for a job that is still running -----------------------------------


def test_the_poll_card_repeats_while_the_job_runs(monkeypatch) -> None:
    """One poll was all the plan offered, for a job measured at 32 seconds.

    A host that executes only cards therefore closed the session on a running
    job -- the exact outcome the navigator exists to prevent.
    """
    states = iter(["running", "running", "done"])
    monkeypatch.setattr(
        jobs_mod, "get_job", lambda _jid: {"job_id": _jid, "state": next(states, "done")}
    )

    begun = _begin(intent="execute", goal="fix the parser")
    sid = begun["session_id"]
    session_mod._sessions[sid]["job_id"] = "job-1"
    # Skip past the execute card to the poll step.
    session_mod._sessions[sid]["plan_step"] = 1

    first = session_next(session_id=sid)
    assert first["card"]["tool"] == "grok_agent_poll"
    assert first["done"] is False
    assert "still running" in first["host_message"].lower()

    second = session_next(session_id=sid)
    assert second["card"]["tool"] == "grok_agent_poll", "the plan must not move on yet"

    # The turn that sees a terminal job still hands back the poll -- that call is
    # what fetches the finished receipt -- and only then releases the plan.
    third = session_next(session_id=sid)
    assert third["card"]["tool"] == "grok_agent_poll"
    assert "still running" not in third["host_message"].lower()

    fourth = session_next(session_id=sid)
    assert fourth["card"]["tool"] != "grok_agent_poll", "a finished job releases the plan"


def test_an_unknown_job_does_not_hold_the_plan_forever(monkeypatch) -> None:
    """A job the registry cannot find will never become terminal."""
    monkeypatch.setattr(jobs_mod, "get_job", lambda _jid: None)
    begun = _begin(intent="execute", goal="fix the parser")
    sid = begun["session_id"]
    session_mod._sessions[sid]["job_id"] = "job-gone"
    session_mod._sessions[sid]["plan_step"] = 1

    first = session_next(session_id=sid)
    second = session_next(session_id=sid)
    assert first["card"]["tool"] == "grok_agent_poll"
    assert second["card"]["tool"] != "grok_agent_poll"


def test_waiting_still_ends_when_the_poll_budget_runs_out(monkeypatch) -> None:
    """Holding the plan must not outlast the budget the host asked for."""
    monkeypatch.setattr(jobs_mod, "get_job", lambda _jid: {"job_id": _jid, "state": "running"})
    begun = _begin(intent="execute", goal="fix the parser", host_budget="tiny")
    sid = begun["session_id"]
    session_mod._sessions[sid]["job_id"] = "job-forever"
    session_mod._sessions[sid]["plan_step"] = 1

    seen = [session_next(session_id=sid) for _ in range(8)]
    assert any(step.get("done") for step in seen), "the loop must terminate on budget"


# --- verify can be aimed at a job ---------------------------------------------


def test_verify_accepts_the_job_it_is_meant_to_verify(monkeypatch) -> None:
    """The mode named "verify" could not be pointed at anything.

    `job_id` had nowhere to enter the session, so the plan's poll card was
    skipped for want of an id, and verify degraded to a bare status call.
    """
    monkeypatch.setattr(jobs_mod, "get_job", lambda _jid: {"job_id": _jid, "state": "done"})
    begun = _begin(intent="verify", goal="check the lane", job_id="job-42")
    sid = begun["session_id"]
    assert session_mod._sessions[sid]["job_id"] == "job-42"

    card = session_next(session_id=sid)["card"]
    assert card["tool"] == "grok_agent_poll"
    assert card["args"] == {"job_id": "job-42"}


def test_verify_without_a_job_still_degrades_gracefully() -> None:
    begun = _begin(intent="verify", goal="check the lane")
    card = session_next(session_id=begun["session_id"])["card"]
    assert card["tool"] in {"grok_agent_status", "grok_agent_session_end"}


# --- the update route names the tool, not a shell script -----------------------


def test_the_update_route_points_at_the_tool_that_exists() -> None:
    text = _ROUTES["update"]["next"]
    assert "grok_agent_update" in text
    assert "update_mcp.sh" not in text
    assert "confirm" in text


def test_the_tools_the_navigator_hands_out_are_allowlisted() -> None:
    """A card naming a tool the plan compiler would refuse is a contradiction."""
    assert "grok_agent_update" in _ALLOW
    assert "grok_agent_project" in _ALLOW


# --- a refusal that names its own remedy ---------------------------------------


def test_project_not_enabled_names_the_tool_that_fixes_it(tmp_path) -> None:
    from grok_delegate.server import _apply_project_gate

    error, _task = _apply_project_gate({"project_root": str(tmp_path), "objective": "x"})
    assert error["error"] == "PROJECT_NOT_ENABLED"
    assert error["fix_with"]["tool"] == "grok_agent_project"
    assert error["fix_with"]["args"]["project_root"] == str(tmp_path)
    assert error["fix_with"]["args"]["preset"] in {"off", "cheap", "standard", "max"}
