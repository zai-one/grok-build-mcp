"""Two costs the bridge was charging its own users, found by using it.

Both were discovered by running real jobs through the bridge against its own
repository, not by reading the code: a skeptic that could not run the pytest it
was handed, and a poll that returned every event a job had ever produced --
twice, once at the top level and once nested in ``result`` -- while ignoring the
``limit`` its schema advertised.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from grok_delegate.acp import permission_decision
from grok_delegate.server import DEFAULT_POLL_EVENTS, _bounded_poll

OPTIONS = [
    {"kind": "allow_once", "optionId": "ALLOW"},
    {"kind": "reject_once", "optionId": "REJECT"},
]
COMMAND = "py -3 -m pytest tests -q"


def _ask(kind: str, *, profile: str, command: str = "", declared=(COMMAND,)) -> str:
    cwd = Path(tempfile.mkdtemp())
    raw = {"command": command} if kind == "execute" else {"path": str(cwd / "a.txt")}
    decision = permission_decision(
        {"options": OPTIONS, "toolCall": {"kind": kind, "rawInput": raw}},
        {"permission_profile": profile, "test_commands": list(declared)},
        cwd,
    )
    return "ALLOW" if decision.get("optionId") == "ALLOW" else "REJECT"


# --- a skeptic can now prove something, not just assert it ----------------------


def test_a_read_only_role_may_run_the_command_it_was_handed() -> None:
    """`consult` and `skeptic` are locked to read-only by the task contract, so
    before this a skeptic could not run the very suite it was asked to judge."""
    assert _ask("execute", profile="read-only", command=COMMAND) == "ALLOW"


def test_read_only_still_means_it_cannot_change_anything() -> None:
    for kind in ("edit", "write"):
        assert _ask(kind, profile="read-only") == "REJECT", kind


@pytest.mark.parametrize("profile", ["read-only", "workspace"])
def test_an_undeclared_command_is_refused_in_both_profiles(profile: str) -> None:
    """The authorisation is the operator's list, not the profile."""
    assert _ask("execute", profile=profile, command="py -3 -c \"print(1)\"") == "REJECT"


@pytest.mark.parametrize("profile", ["read-only", "workspace"])
def test_a_decorated_command_is_still_not_the_declared_one(profile: str) -> None:
    decorated = COMMAND + "; echo EXIT=$LASTEXITCODE"
    assert _ask("execute", profile=profile, command=decorated) == "REJECT"


def test_declaring_nothing_grants_nothing() -> None:
    assert _ask("execute", profile="read-only", command=COMMAND, declared=()) == "REJECT"


# --- the poll stops charging for the whole history ------------------------------


def _record(count: int) -> dict:
    events = [{"sequence": i, "kind": "notification"} for i in range(count)]
    return {"job_id": "job-x", "state": "done", "events": list(events),
            "result": {"status": "completed", "events": list(events)}}


def test_only_the_newest_events_survive_and_the_rest_are_counted() -> None:
    out = _bounded_poll(_record(64), 5)
    assert [e["sequence"] for e in out["events"]] == [59, 60, 61, 62, 63]
    assert out["events_omitted"] == 59
    assert out["events_total"] == 64


def test_the_nested_copy_is_bounded_too() -> None:
    """A finished job carried the same list twice; bounding one paid for half."""
    out = _bounded_poll(_record(64), 5)
    assert len(out["result"]["events"]) == 5
    assert out["result"]["events_omitted"] == 59


def test_a_short_job_is_left_alone_and_says_nothing_was_dropped() -> None:
    out = _bounded_poll(_record(3), DEFAULT_POLL_EVENTS)
    assert len(out["events"]) == 3
    assert "events_omitted" not in out
    assert out["events_total"] == 3


def test_truncation_is_never_silent() -> None:
    """A list that quietly ends reads like a job that quietly stopped."""
    out = _bounded_poll(_record(100), 10)
    assert out["events_omitted"] == 90, "the caller must be able to tell it is not the whole story"


def test_a_record_without_events_is_returned_unharmed() -> None:
    assert _bounded_poll({"job_id": "job-x", "state": "running"}, 5) == {
        "job_id": "job-x",
        "state": "running",
    }


def test_the_default_is_a_bound_not_the_whole_history() -> None:
    assert 0 < DEFAULT_POLL_EVENTS <= 32
