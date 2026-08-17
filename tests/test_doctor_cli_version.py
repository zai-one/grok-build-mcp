"""Doctor must see the same CLI version as status so an opt-in pin can warn."""

from __future__ import annotations

import json

from grok_delegate.server import handle_tool_call


def _doctor_cli(_args, _cwd, _timeout):
    """Answer only `grok doctor --json`. Version comes from the injected probe."""
    return {
        "args": [str(a) for a in _args],
        "returncode": 0,
        "stdout": json.dumps({"schemaVersion": "1", "counts": {"issues": 0}}),
        "stderr": "",
        "timedOut": False,
    }


def _doctor(monkeypatch, probe):
    monkeypatch.setattr("grok_delegate.status.probe_grok_version", probe)
    return handle_tool_call(
        "grok_delegate_doctor",
        {},
        subprocess_runner=_doctor_cli,
        which=lambda n: n,
    )


def test_doctor_reports_detected_cli_version_when_probe_succeeds(monkeypatch) -> None:
    """An operator comparing doctor to status must see the same CLI version."""

    def probe(**_kwargs):
        return {"ok": True, "version": "9.9.9"}

    report = _doctor(monkeypatch, probe)
    assert report.get("ok") is True
    assert report["compatibility"]["detected_cli_version"] == "9.9.9"


def test_doctor_stays_usable_when_version_probe_fails(monkeypatch) -> None:
    """A dead version probe must not hide the doctor report itself."""

    def probe(**_kwargs):
        raise RuntimeError("cli version probe failed")

    report = _doctor(monkeypatch, probe)
    assert report.get("ok") is True
    assert "doctor" in report
    assert report["compatibility"]["detected_cli_version"] is None
    assert report["compatibility"]["mismatch"] is False
    assert report["compatibility"]["mismatch_blocks_typed_path"] is False


def test_doctor_shows_opt_in_pin_mismatch_without_blocking(monkeypatch) -> None:
    """The pin is a warning on doctor too; it must never block the typed path."""
    monkeypatch.setenv("GROK_DELEGATE_EXPECTED_AGENT_VERSION", "1.0.0")

    def probe(**_kwargs):
        return {"ok": True, "version": "9.9.9"}

    report = _doctor(monkeypatch, probe)
    compat = report["compatibility"]
    assert compat["detected_cli_version"] == "9.9.9"
    assert compat["expected_agent_version"] == "1.0.0"
    assert compat["pin_enabled"] is True
    assert compat["mismatch"] is True
    assert compat["mismatch_blocks_typed_path"] is False
    assert compat["warning"]
