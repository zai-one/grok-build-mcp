"""CLI agentVersion is unpinned by default; a pin is opt-in and warn-only."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from grok_delegate.acp import (
    DEFAULT_EXPECTED_AGENT_VERSION,
    StdioACPTransport,
    expected_agent_version,
)
from grok_delegate.contracts import validate_task_packet
from grok_delegate.guard import SERVER_VERSION
from grok_delegate.server import handle_tool_call
from grok_delegate.status import compatibility_report

FAKE = Path(__file__).resolve().parent / "fake_acp_agent.py"


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
        "correlation_id": "unpin-test",
    }
    value.update(overrides)
    return value


def _fake_transport() -> StdioACPTransport:
    def factory(_argv, **kwargs):
        return subprocess.Popen([sys.executable, str(FAKE)], **kwargs)

    return StdioACPTransport(grok_bin="grok", popen_factory=factory)


def _git_ok(args, cwd, timeout):
    argv = [str(a) for a in args]
    stdout = "git version 2.45.0\n" if "--version" in argv else ""
    return {
        "args": argv,
        "returncode": 0,
        "stdout": stdout,
        "stderr": "",
        "timedOut": False,
    }


def test_default_expected_agent_version_is_unpinned(monkeypatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", raising=False)
    assert DEFAULT_EXPECTED_AGENT_VERSION is None
    assert expected_agent_version() is None


def test_env_any_and_off_disable_pin(monkeypatch) -> None:
    for value in ("", "any", "*", "off", "none", "ANY"):
        monkeypatch.setenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", value)
        assert expected_agent_version() is None


def test_env_pin_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", "9.9.9")
    assert expected_agent_version() == "9.9.9"


def test_alien_agent_version_does_not_block_typed_stdio(monkeypatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", raising=False)
    monkeypatch.setenv("GROK_FAKE_AGENT_VERSION", "not-a-pin")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root), allowed_roots=[root])
        result = _fake_transport().run(task, cwd=root, cancel_event=threading.Event())
    assert result["status"] == "completed"
    assert result.get("blocked_reason") != "GROK_VERSION_MISMATCH"
    assert result.get("agent_version") == "not-a-pin"


def test_opt_in_pin_mismatch_warns_and_continues(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", "1.2.3")
    monkeypatch.setenv("GROK_FAKE_AGENT_VERSION", "9.9.9")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(_packet(root), allowed_roots=[root])
        result = _fake_transport().run(task, cwd=root, cancel_event=threading.Event())
    assert result["status"] == "completed"
    kinds = [event.get("kind") for event in result.get("events") or []]
    assert "version_mismatch" in kinds
    mismatch = next(e for e in result["events"] if e.get("kind") == "version_mismatch")
    assert mismatch["payload"]["blocking"] is False
    assert mismatch["payload"]["expected"] == "1.2.3"
    assert mismatch["payload"]["got"] == "9.9.9"


def test_status_and_doctor_report_unpin(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", raising=False)

    class _Cli:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def __call__(self, args, cwd, timeout):
            argv = [str(a) for a in args]
            self.calls.append(argv)
            if "version" in argv:
                stdout = json.dumps({"currentVersion": "9.9.9", "channel": "stable"})
            elif "models" in argv:
                stdout = "You are logged in with grok.com.\nDefault model: grok\n"
            elif "doctor" in argv:
                stdout = json.dumps({"schemaVersion": "1", "counts": {"issues": 0}})
            else:
                stdout = ""
            return {
                "args": argv,
                "returncode": 0,
                "stdout": stdout,
                "stderr": "",
                "timedOut": False,
            }

    sp = _Cli()
    status = handle_tool_call(
        "grok_delegate_status",
        {},
        allowed_roots=[tmp_path],
        subprocess_runner=sp,
        which=lambda n: n,
        git_runner=_git_ok,
    )
    compat = status["compatibility"]
    assert compat["expected_agent_version"] == "any"
    assert compat["pin_enabled"] is False
    assert compat["mismatch"] is False
    assert compat["mismatch_blocks_typed_path"] is False
    assert compat["bridge_version"] == SERVER_VERSION
    assert compat["grok_delegate_version"] == SERVER_VERSION
    assert compat["detected_cli_version"] == "9.9.9"
    assert "git pull" in compat["update_hint"]

    doctor_sp = _Cli()
    doctor = handle_tool_call(
        "grok_delegate_doctor",
        {},
        subprocess_runner=doctor_sp,
        which=lambda n: n,
    )
    assert doctor["compatibility"]["expected_agent_version"] == "any"
    assert doctor["compatibility"]["detected_cli_version"] == "9.9.9"
    assert any("doctor" in call for call in doctor_sp.calls)
    assert not any("fix" in a.lower() for call in doctor_sp.calls for a in call)


def test_compatibility_report_warns_when_pin_mismatches(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", "1.0.0")
    report = compatibility_report(detected_cli_version="9.9.9")
    assert report["mismatch"] is True
    assert report["mismatch_blocks_typed_path"] is False
    assert report["warning"]
    assert "9.9.9" in report["warning"]
