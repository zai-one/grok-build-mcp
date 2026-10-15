"""GROK_DELEGATE_TOOL_LOADING=lazy: small start surface, runtime toolset enabling, list_changed."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from grok_delegate import lazy_loading, server
from grok_delegate.lazy_loading import ENABLE_TOOL, TOOLSETS, estimate_tokens

ALL = {tool["name"] for tool in server.list_tools()}
CORE = lazy_loading.core_names() | {ENABLE_TOOL}
LAZY_BUDGET_TOKENS = 3_500


@pytest.fixture(autouse=True)
def _fresh(monkeypatch) -> Iterator[list[dict]]:
    sent: list[dict] = []
    lazy_loading.reset()
    server.set_notifier(sent.append)
    yield sent
    server.set_notifier(None)
    lazy_loading.reset()


def rpc(method: str, params: dict | None = None) -> dict:
    response = server.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response is not None and "result" in response, response
    return response["result"]


def initialize() -> dict:
    return rpc("initialize", {"capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}})


def listed() -> set[str]:
    return {tool["name"] for tool in rpc("tools/list")["tools"]}


def call(name: str, arguments: dict | None = None) -> dict:
    return rpc("tools/call", {"name": name, "arguments": arguments or {}})["structuredContent"]


def startup_tokens() -> int:
    return estimate_tokens(rpc("tools/list")["tools"]) + estimate_tokens(initialize()["instructions"])


def test_toolsets_cover_every_tool_once() -> None:
    grouped = [name for spec in TOOLSETS.values() for name in spec["tools"]]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) == ALL


def test_eager_is_default_and_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_TOOL_LOADING", raising=False)
    assert initialize()["capabilities"] == {"tools": {}, "prompts": {}, "resources": {}}
    assert "Lazy tools" not in initialize()["instructions"]
    assert listed() == ALL
    assert rpc("tools/list")["tools"] == server.list_tools()


def test_lazy_start_surface(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", "lazy")
    result = initialize()
    assert result["capabilities"]["tools"] == {"listChanged": True}
    assert f"{ENABLE_TOOL}()" in result["instructions"]
    assert listed() == CORE
    listing = call(ENABLE_TOOL)
    assert {row["toolset"] for row in listing["toolsets"]} == {"jobs", "maintenance", "compat"}
    assert all(row["description"] and row["token_cost"] > 0 for row in listing["toolsets"])


def test_enable_registers_tools_and_notifies(monkeypatch, _fresh) -> None:
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", "lazy")
    result = call(ENABLE_TOOL, {"toolsets": ["jobs"]})
    assert set(result["added_tools"]) == set(TOOLSETS["jobs"]["tools"])
    assert listed() == CORE | set(TOOLSETS["jobs"]["tools"])
    assert _fresh == [{"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}]
    assert "GROK_DELEGATE_TOOL_LOADING=eager" in result["hint"]


def test_enable_is_idempotent(monkeypatch, _fresh) -> None:
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", "lazy")
    call(ENABLE_TOOL, {"toolsets": ["compat"]})
    again = call(ENABLE_TOOL, {"toolsets": ["compat"]})
    call(ENABLE_TOOL, {"toolsets": ["all"]})
    twice = call(ENABLE_TOOL, {"toolsets": ["all"]})
    assert again["added_tools"] == [] and again["already_enabled"] is True
    assert twice["added_tools"] == []
    assert listed() == ALL | {ENABLE_TOOL}
    assert len(_fresh) == 2


def test_hidden_tool_call_points_to_enable_and_unknown_toolset(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", "lazy")
    refused = call("grok_agent_update", {})
    assert refused["error"] == "TOOL_NOT_ENABLED" and "maintenance" in refused["hint"]
    unknown = call(ENABLE_TOOL, {"toolsets": ["nope"]})
    assert unknown["error"] == "unknown_toolset" and "jobs" in unknown["available"]


def test_enabled_tools_keep_their_own_validation_and_gates(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", "lazy")
    call(ENABLE_TOOL, {"toolsets": ["jobs"]})
    result = call("grok_agent_execute", {})  # missing required fields: the normal schema gate answers
    assert result["ok"] is False and result.get("error") != "TOOL_NOT_ENABLED"


def test_invalid_mode_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", "sometimes")
    with pytest.raises(ValueError):
        lazy_loading.mode()


def test_lazy_startup_budget(monkeypatch, capsys) -> None:
    costs = {}
    for mode in ("eager", "lazy"):
        monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", mode)
        costs[mode] = startup_tokens()
    assert costs["lazy"] <= LAZY_BUDGET_TOKENS
    assert costs["lazy"] < costs["eager"]
    with capsys.disabled():
        print(f"\nstartup tokens {json.dumps(costs)}")


def _self_test_rows(monkeypatch, capsys, tmp_path, mode: str) -> dict[str, str]:
    from grok_delegate.__main__ import _run_self_test
    from grok_delegate.update_check import DISABLE_ENV

    monkeypatch.setenv(DISABLE_ENV, "1")  # the self-test sets it too; monkeypatch restores it
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", mode)
    monkeypatch.setenv("GROK_DELEGATE_BIN", str(tmp_path / "grok"))  # absent: no live CLI calls
    _run_self_test()
    rows = {}
    for line in capsys.readouterr().out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] in {"PASS", "FAIL"}:
            rows[parts[1]] = parts[0]
    return rows


@pytest.mark.parametrize("mode", ["eager", "lazy"])
def test_self_test_tools_list_passes_in_both_modes(monkeypatch, capsys, tmp_path, mode) -> None:
    # install.sh exports the chosen mode before --self-test; lazy used to fail rpc_tools_list
    # (compat tools hidden) and the installer exited 2 suggesting `grok login`.
    rows = _self_test_rows(monkeypatch, capsys, tmp_path, mode)
    assert rows.get("rpc_tools_list") == "PASS", rows
    if mode == "lazy":
        assert rows.get("rpc_enable_toolsets") == "PASS", rows
        assert listed() == CORE  # the self-test does not leave toolsets enabled


def test_hint_does_not_promise_reconnect_restores_toolsets(monkeypatch) -> None:
    # Reconnecting a stdio server starts a new process with only the core listed, so the
    # hint must not offer it as recovery; eager + restart is the working fallback.
    monkeypatch.setenv("GROK_DELEGATE_TOOL_LOADING", "lazy")
    hint = call(ENABLE_TOOL, {"toolsets": ["jobs"]})["hint"]
    assert "reconnect the MCP server" not in hint
    assert "GROK_DELEGATE_TOOL_LOADING=eager" in hint and "restart" in hint
