"""The bridge does not pin a model, and its chosen budgets are operator-tunable.

Naming a model default in code ages the same way pinning ``agentVersion`` did:
the CLI ships a better one and the bridge quietly holds callers on the old one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from grok_delegate.acp import _managed_ws_argv, _model_argv
from grok_delegate.contracts import validate_task_packet
from grok_delegate.economy import (
    ECONOMY_DEFAULT_MAX_TURNS,
    ECONOMY_DEFAULT_REASONING,
    default_max_turns,
    default_reasoning_effort,
)
from grok_delegate.guard import (
    GuardError,
    configured_model,
    configured_reasoning_effort,
)


def _packet(root: Path, **overrides):
    value = {
        "objective": "Return a bounded result",
        "role": "consult",
        "project_root": str(root),
        "permission_profile": "read-only",
        "max_turns": 5,
        "timeout_seconds": 10,
        "inputs": [],
        "constraints": [],
        "acceptance_criteria": [],
        "expected_artifacts": [],
        "correlation_id": "model-default-test",
    }
    value.update(overrides)
    return value


def _packet_without_budget(root: Path, **overrides):
    """A packet that names no budget, so the contract default is what we observe."""
    value = _packet(root, **overrides)
    value.pop("max_turns", None)
    value.pop("reasoning_effort", None)
    return value


# --- policy is pure over an explicit env mapping ---------------------------


def test_model_env_absent_means_no_opinion() -> None:
    assert configured_model({}) is None
    assert configured_model({"GROK_DELEGATE_MODEL": ""}) is None
    assert configured_model({"GROK_DELEGATE_MODEL": "   "}) is None


def test_model_env_is_forwarded_and_validated() -> None:
    assert configured_model({"GROK_DELEGATE_MODEL": "grok-4.6"}) == "grok-4.6"
    try:
        configured_model({"GROK_DELEGATE_MODEL": "bad\nmodel"})
    except GuardError as exc:
        assert exc.code == "MODEL_INVALID"
    else:  # pragma: no cover - guard must reject control characters
        raise AssertionError("a model id with a newline must be rejected")


def test_effort_env_absent_means_no_opinion() -> None:
    assert configured_reasoning_effort({}) is None


def test_effort_env_rejects_values_outside_the_contract() -> None:
    assert configured_reasoning_effort({"GROK_DELEGATE_REASONING_EFFORT": "xhigh"}) == "xhigh"
    try:
        configured_reasoning_effort({"GROK_DELEGATE_REASONING_EFFORT": "turbo"})
    except GuardError as exc:
        assert exc.code == "REASONING_EFFORT_INVALID"
    else:  # pragma: no cover - unknown efforts must not reach the CLI
        raise AssertionError("an unknown reasoning effort must be rejected")


# --- an unset model must vanish from argv, not become the string "None" ----


def test_model_argv_is_empty_when_the_task_names_no_model() -> None:
    assert _model_argv({}) == []
    assert _model_argv({"model": None}) == []
    assert _model_argv({"model": "  "}) == []


def test_model_argv_forwards_an_explicit_model() -> None:
    assert _model_argv({"model": "grok-4.6"}) == ["--model", "grok-4.6"]


def test_ws_daemon_argv_omits_model_rather_than_sending_none() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root), allowed_roots=[root])
        assert task["model"] is None
        argv = _managed_ws_argv("grok", task, 45999)
        assert "--model" not in argv
        assert "None" not in argv


def test_ws_daemon_argv_keeps_an_explicit_model() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root, model="grok-4.6"), allowed_roots=[root])
        argv = _managed_ws_argv("grok", task, 45999)
        assert argv[argv.index("--model") + 1] == "grok-4.6"


# --- the task contract no longer invents a model ---------------------------


def test_task_packet_leaves_model_unset_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_MODEL", raising=False)
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root), allowed_roots=[root])
        assert task["model"] is None


def test_task_packet_takes_the_operator_model(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_MODEL", "grok-4.6")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root), allowed_roots=[root])
        assert task["model"] == "grok-4.6"


def test_explicit_model_beats_the_operator_default(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_MODEL", "grok-4.6")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root, model="grok-4.5"), allowed_roots=[root])
        assert task["model"] == "grok-4.5"


# --- bridge-chosen budgets follow the operator -----------------------------


def test_budget_defaults_are_unchanged_without_env() -> None:
    assert default_reasoning_effort({}) == ECONOMY_DEFAULT_REASONING
    assert default_max_turns({}) == ECONOMY_DEFAULT_MAX_TURNS


def test_budget_defaults_follow_the_operator() -> None:
    assert default_reasoning_effort({"GROK_DELEGATE_REASONING_EFFORT": "max"}) == "max"
    assert default_max_turns({"GROK_DELEGATE_MAX_TURNS": "40"}) == 40


def test_out_of_range_turn_budget_falls_back_rather_than_failing_every_job() -> None:
    for bogus in ("0", "61", "-5", "twelve", ""):
        assert default_max_turns({"GROK_DELEGATE_MAX_TURNS": bogus}) == ECONOMY_DEFAULT_MAX_TURNS


def test_navigator_execute_card_carries_the_operator_budget(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_REASONING_EFFORT", "xhigh")
    monkeypatch.setenv("GROK_DELEGATE_MAX_TURNS", "40")
    from grok_delegate import session

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        card = session._write_task_packet({"goal": "Ship the thing", "project_root": str(root)})
        assert card["reasoning_effort"] == "xhigh"
        assert card["max_turns"] == 40


def test_contract_effort_follows_the_operator_without_economy(monkeypatch) -> None:
    """The budget must not depend on GROK_DELEGATE_ECONOMY also being on.

    Economy and worker budget are separate concerns: economy exists to keep the
    *host's* context small, and tying the model's effort to it meant paying for a
    strong model that was told to think as little as possible.
    """
    monkeypatch.delenv("GROK_DELEGATE_ECONOMY", raising=False)
    monkeypatch.setenv("GROK_DELEGATE_REASONING_EFFORT", "xhigh")
    monkeypatch.setenv("GROK_DELEGATE_MAX_TURNS", "40")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet_without_budget(root), allowed_roots=[root])
        assert task["reasoning_effort"] == "xhigh"
        assert task["max_turns"] == 40


def test_economy_no_longer_forces_effort_down(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY", "1")
    monkeypatch.setenv("GROK_DELEGATE_REASONING_EFFORT", "max")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root), allowed_roots=[root])
        assert task["reasoning_effort"] == "max"


def test_contract_defaults_are_unchanged_when_the_operator_says_nothing(monkeypatch) -> None:
    for name in ("GROK_DELEGATE_ECONOMY", "GROK_DELEGATE_REASONING_EFFORT", "GROK_DELEGATE_MAX_TURNS"):
        monkeypatch.delenv(name, raising=False)
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet_without_budget(root), allowed_roots=[root])
        assert task["reasoning_effort"] == "high"
        assert task["max_turns"] == 40


def test_explicit_task_values_beat_the_operator_default(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_REASONING_EFFORT", "max")
    monkeypatch.setenv("GROK_DELEGATE_MAX_TURNS", "40")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            _packet(root, reasoning_effort="low", max_turns=3), allowed_roots=[root]
        )
        assert task["reasoning_effort"] == "low"
        assert task["max_turns"] == 3
