from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from grok_delegate.economy import (
    apply_task_economy_defaults,
    compact_job_record,
    economy_enabled,
    economy_playbook,
)
from grok_delegate.http_server import create_http_server
from grok_delegate.server import handle_tool_call, list_tools


def test_economy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_ECONOMY", raising=False)
    assert economy_enabled() is False
    assert apply_task_economy_defaults({"objective": "x"}) == {"objective": "x"}
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY", "1")
    out = apply_task_economy_defaults({"objective": "x"})
    assert out["max_turns"] == 12
    assert out["timeout_seconds"] == 600
    assert out["reasoning_effort"] == "low"
    # client wins
    out2 = apply_task_economy_defaults({"objective": "x", "max_turns": 3})
    assert out2["max_turns"] == 3


def test_compact_job_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY", "1")
    fat = {
        "ok": True,
        "job_id": "j1",
        "status": "completed",
        "summary": "s" * 5000,
        "changed_files": [f"f{i}.py" for i in range(50)],
        "events": [{"n": i} for i in range(20)],
        "tests": [
            {
                "command": "pytest -q",
                "passed": True,
                "returncode": 0,
                "source": "bridge-verifier",
                "output_preview": "x" * 999,
            }
        ],
        "secret_field": "should-drop",
    }
    compact = compact_job_record(fat)
    assert compact["economy_compact"] is True
    assert len(compact["summary"]) <= 1501
    assert len(compact["changed_files"]) == 24
    assert len(compact["events"]) == 4
    assert "output_preview" not in compact["tests"][0]
    assert "secret_field" not in compact


def test_economy_tool_listed_and_callable() -> None:
    names = {t["name"] for t in list_tools()}
    assert "grok_agent_economy" in names
    result = handle_tool_call("grok_agent_economy", {})
    assert result.get("ok") is True
    assert "do" in result and "dont" in result
    assert "Unofficial" in result.get("disclaimer", "")
    play = economy_playbook()
    assert play["economy"] is True


def test_http_health_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_HTTP_TOKEN", "test-local-token-not-oauth")
    server = create_http_server(host="127.0.0.1", port=0, token="test-local-token-not-oauth")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        health = urlopen(f"http://{host}:{port}/healthz", timeout=2)
        assert json.loads(health.read().decode())["ok"] is True
        req = Request(
            f"http://{host}:{port}/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "grok_agent_economy", "arguments": {}},
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-local-token-not-oauth",
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
        assert body["result"]["structuredContent"]["ok"] is True
        bad = Request(
            f"http://{host}:{port}/mcp",
            data=b"{}",
            headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(bad, timeout=2)
        assert raised.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
