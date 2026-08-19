"""MCP `roots/list`: the host declares its workspace over the protocol.

Before this, a freshly installed bridge answered every job tool with
`ALLOWED_ROOTS_EMPTY` and the only cure was editing the MCP host's config and
restarting it. MCP already had the mechanism -- `roots/list` -- and the reason
it was missing was structural: the stdio loop only ever answered, it never
asked. These tests pin both halves, the asking and the not-trusting-too-much.

The env-var route (`GROK_DELEGATE_TRUST_HOST_ROOTS`, `CLAUDE_PROJECT_DIR`) is a
different source with a different default, and lives in `test_host_roots.py`.
"""

from __future__ import annotations

import json
import io
from pathlib import Path

import pytest

from grok_delegate import host_roots, server


@pytest.fixture(autouse=True)
def _clean_roots():
    host_roots.reset_for_tests()
    yield
    host_roots.reset_for_tests()


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _initialize(roots_capability: bool = True) -> dict:
    capabilities = {"roots": {"listChanged": True}} if roots_capability else {"sampling": {}}
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": capabilities,
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }


# --- asking ---------------------------------------------------------------------


def test_a_client_that_offers_roots_gets_asked(tmp_path) -> None:
    server.handle_jsonrpc(_initialize())
    request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert request is not None
    assert request["method"] == "roots/list"
    assert isinstance(request["id"], str)


def test_a_client_without_the_capability_is_never_asked() -> None:
    """No point asking, and a spurious request is noise on someone's wire."""
    server.handle_jsonrpc(_initialize(roots_capability=False))
    assert server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_a_workspace_change_asks_again() -> None:
    server.handle_jsonrpc(_initialize())
    changed = {"jsonrpc": "2.0", "method": "notifications/roots/list_changed"}
    assert server._roots_followup(changed) is not None


def test_ordinary_traffic_asks_nothing() -> None:
    server.handle_jsonrpc(_initialize())
    assert server._roots_followup({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}) is None


def test_the_switch_turns_the_asking_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_MCP_ROOTS", "0")
    server.handle_jsonrpc(_initialize())
    assert server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


# --- reading the answer ---------------------------------------------------------


def test_a_declared_root_becomes_an_allowed_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_ALLOWED_ROOTS", raising=False)
    monkeypatch.delenv("GROK_DELEGATE_REPO_ROOT", raising=False)
    server.handle_jsonrpc(_initialize())
    request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert request is not None
    host_roots.apply_roots_response(
        {"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [{"uri": _uri(tmp_path)}]}}
    )
    assert tmp_path.resolve() in server.load_allowed_roots()


def test_the_answer_is_not_mistaken_for_a_client_request() -> None:
    """It arrives on the same pipe as everything else and carries no method."""
    server.handle_jsonrpc(_initialize())
    request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert request is not None
    reply = {"jsonrpc": "2.0", "id": request["id"], "result": {"roots": []}}
    assert host_roots.is_roots_response(reply) is True
    # A client request that reuses the string is still a request.
    assert host_roots.is_roots_response({**reply, "method": "tools/list"}) is False


def test_an_unknown_id_is_not_ours() -> None:
    assert host_roots.is_roots_response({"jsonrpc": "2.0", "id": "someone-else", "result": {}}) is False


def test_a_client_that_answers_with_an_error_grants_nothing(tmp_path) -> None:
    server.handle_jsonrpc(_initialize())
    request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert request is not None
    host_roots.apply_roots_response(
        {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "no"}}
    )
    assert host_roots.host_roots() == []


def test_a_later_answer_replaces_the_earlier_one(tmp_path) -> None:
    """Closing a folder must narrow the scope, not leave the old one granted."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    server.handle_jsonrpc(_initialize())

    def answer(paths):
        request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/roots/list_changed"})
        assert request is not None
        host_roots.apply_roots_response(
            {"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [{"uri": _uri(p)} for p in paths]}}
        )

    answer([first, second])
    assert host_roots.host_roots() == [first.resolve(), second.resolve()]
    answer([second])
    assert host_roots.host_roots() == [second.resolve()]


def test_a_root_that_is_not_a_directory_is_dropped(tmp_path) -> None:
    """A file, or a folder that has since been deleted, grants nothing."""
    a_file = tmp_path / "notes.txt"
    a_file.write_text("x", encoding="utf-8")
    assert host_roots.uri_to_path(_uri(a_file)) is None
    assert host_roots.uri_to_path(_uri(tmp_path / "gone")) is None


@pytest.mark.parametrize("uri", ["", "   ", "https://example.invalid/x", "not a uri", None, 42])
def test_a_uri_we_cannot_use_is_dropped_rather_than_guessed(uri) -> None:
    assert host_roots.uri_to_path(uri) is None


def test_a_windows_drive_uri_does_not_keep_its_leading_slash(tmp_path) -> None:
    """`file:///D:/x` parses to `/D:/x`; that slash is an artifact, not a root."""
    resolved = host_roots.uri_to_path(_uri(tmp_path))
    assert resolved == tmp_path.resolve()
    assert resolved is not None and resolved.is_dir()


# --- what it must not do --------------------------------------------------------


def test_the_switch_refuses_declared_roots_entirely(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    server.handle_jsonrpc(_initialize())
    request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert request is not None
    host_roots.apply_roots_response(
        {"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [{"uri": _uri(tmp_path)}]}}
    )
    monkeypatch.setenv("GROK_DELEGATE_MCP_ROOTS", "0")
    assert host_roots.host_roots() == []


def test_a_declared_root_widens_the_explicit_list_rather_than_replacing_it(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    declared = tmp_path / "declared"
    explicit.mkdir()
    declared.mkdir()
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(explicit))
    server.handle_jsonrpc(_initialize())
    request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert request is not None
    host_roots.apply_roots_response(
        {"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [{"uri": _uri(declared)}]}}
    )
    roots = server.load_allowed_roots()
    assert explicit.resolve() in roots and declared.resolve() in roots


def test_reinitializing_forgets_the_previous_workspace(tmp_path) -> None:
    """A new client is a new scope; inheriting the old one would be a leak."""
    server.handle_jsonrpc(_initialize())
    request = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert request is not None
    host_roots.apply_roots_response(
        {"jsonrpc": "2.0", "id": request["id"], "result": {"roots": [{"uri": _uri(tmp_path)}]}}
    )
    assert host_roots.host_roots() != []
    server.handle_jsonrpc(_initialize(roots_capability=False))
    assert host_roots.host_roots() == []


# --- the loop actually sends it -------------------------------------------------


def test_the_stdio_loop_puts_the_request_on_the_wire(tmp_path) -> None:
    """The point of the whole change: the server speaks first, unprompted."""
    inn = io.StringIO(
        json.dumps(_initialize())
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
    )
    out = io.StringIO()
    server.serve_stdio(inn, out)
    written = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert any(m.get("method") == "roots/list" for m in written), written


def test_the_loop_consumes_the_answer_instead_of_erroring_on_it(tmp_path) -> None:
    """Handing our own response to the dispatcher would answer -32601."""
    server.handle_jsonrpc(_initialize())
    pending = server._roots_followup({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert pending is not None
    inn = io.StringIO(
        json.dumps(
            {"jsonrpc": "2.0", "id": pending["id"], "result": {"roots": [{"uri": _uri(tmp_path)}]}}
        )
        + "\n"
    )
    out = io.StringIO()
    server.serve_stdio(inn, out)
    assert out.getvalue().strip() == "", "a response is not a request; nothing is owed back"
    assert tmp_path.resolve() in host_roots.host_roots()


# --- the refusal explains itself ------------------------------------------------


def test_the_empty_allowlist_error_names_which_situation_this_is() -> None:
    server.handle_jsonrpc(_initialize(roots_capability=False))
    body = server.allowed_roots_empty_error()
    assert body["error"] == "ALLOWED_ROOTS_EMPTY"
    assert body["host_declares_roots"] is False
    assert "did not offer the MCP roots capability" in body["reason"]
    assert any("GROK_DELEGATE_ALLOWED_ROOTS" in step for step in body["fix_with"])


def test_a_host_that_can_declare_roots_is_told_no_restart_is_needed() -> None:
    server.handle_jsonrpc(_initialize())
    body = server.allowed_roots_empty_error()
    assert body["host_declares_roots"] is True
    assert any("no restart" in step for step in body["fix_with"]), body["fix_with"]


def test_the_error_says_a_tool_call_can_never_grant_a_root() -> None:
    """The property that makes host-declared roots safe to trust by default."""
    assert "never granted by a tool call" in server.allowed_roots_empty_error()["note"]
