"""Loopback is not a security boundary, and this endpoint runs commands.

The HTTP transport exempted loopback from authentication: with no token every
request was authorized, so any process on the machine -- and any page in the
browser, since a text/plain POST needs no preflight -- could call
grok_agent_execute against whatever allowlist the server was started with.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from http import HTTPStatus

import pytest

from grok_delegate.contracts import redact_text, reset_secret_needles_for_tests
from grok_delegate.http_server import create_http_server

TOKEN = "test-local-token-not-oauth"


@pytest.fixture()
def server():
    srv = create_http_server(host="127.0.0.1", port=0, token=TOKEN)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        reset_secret_needles_for_tests()


def _json(error: urllib.error.HTTPError) -> dict:
    return json.loads(error.read().decode("utf-8"))


def _post(
    base: str,
    body: dict,
    *,
    token: str | None = None,
    content_type: str | None = "application/json",
    path: str = "/mcp",
):
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
            parsed = json.loads(payload) if payload else None
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_starting_without_a_token_is_refused_even_on_loopback() -> None:
    with pytest.raises(ValueError) as refused:
        create_http_server(host="127.0.0.1", port=0, token=None)
    assert "GROK_DELEGATE_HTTP_TOKEN" in str(refused.value)


def test_starting_without_a_token_is_refused_off_loopback() -> None:
    with pytest.raises(ValueError):
        create_http_server(host="0.0.0.0", port=0, token=None)


def test_nonloopback_bind_is_refused_without_the_explicit_flag() -> None:
    with pytest.raises(ValueError) as refused:
        create_http_server(host="0.0.0.0", port=0, token=TOKEN)
    assert "GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK" in str(refused.value)
    assert "plaintext" in str(refused.value)


def test_nonloopback_bind_is_still_refused_when_the_flag_is_not_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK", "true")
    with pytest.raises(ValueError) as refused:
        create_http_server(host="0.0.0.0", port=0, token=TOKEN)
    assert "GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK" in str(refused.value)


def test_a_tool_call_without_the_token_is_rejected(server: str) -> None:
    status, _body = _post(server, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == HTTPStatus.UNAUTHORIZED


def test_a_tool_call_with_the_wrong_token_is_rejected(server: str) -> None:
    status, _body = _post(
        server, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="not-the-token"
    )
    assert status == HTTPStatus.UNAUTHORIZED


def test_a_tool_call_with_the_token_is_served(server: str) -> None:
    status, body = _post(
        server, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=TOKEN
    )
    assert status == HTTPStatus.OK
    assert body["result"]["tools"]


def test_a_browser_simple_request_cannot_reach_the_tools(server: str) -> None:
    """text/plain is the shape that needs no CORS preflight.

    The token already stops a cross-origin page, which cannot set an
    Authorization header on a simple request. Refusing the content type as well
    means such a request never reaches the handler at all.
    """
    status, _body = _post(
        server,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        token=TOKEN,
        content_type="text/plain",
    )
    assert status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE


def test_a_post_without_content_type_cannot_reach_the_tools(server: str) -> None:
    # urllib would invent Content-Type for POST data; speak HTTP directly.
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(server)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8")
        conn.request(
            "POST",
            "/mcp",
            body=body,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Length": str(len(body)),
            },
        )
        response = conn.getresponse()
        payload = response.read().decode("utf-8")
    finally:
        conn.close()
    assert response.status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert "content_type_must_be_json" in payload


def test_healthz_stays_open_because_it_answers_nothing(server: str) -> None:
    with urllib.request.urlopen(f"{server}/healthz", timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert response.status == HTTPStatus.OK
    assert body == {
        "ok": True,
        "service": "grok-delegate",
        "transport": "http",
        "mcp_binding": "private-jsonrpc",
    }
    assert "tools" not in body
    assert "token" not in body
    assert "roots" not in body


def test_root_get_is_not_an_open_health_alias(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"{server}/", timeout=10)
    assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert _json(refused.value) == {"error": "method_not_allowed"}


def test_mcp_get_is_method_not_allowed_not_not_found(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"{server}/mcp", timeout=10)
    assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert refused.value.headers.get("Allow") == "POST"
    assert _json(refused.value) == {"error": "method_not_allowed"}


def test_readyz_still_needs_the_token(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"{server}/readyz", timeout=10)
    assert refused.value.code == HTTPStatus.UNAUTHORIZED


def test_readyz_with_the_token_means_bearer_accepted_not_grok_login(
    server: str,
) -> None:
    request = urllib.request.Request(
        f"{server}/readyz",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert response.status == HTTPStatus.OK
    assert body == {"ok": True, "ready": True}


def test_access_logs_drop_query_strings_and_never_echo_the_token(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    buf = io.StringIO()
    monkeypatch.setattr("grok_delegate.http_server.sys.stderr", buf)
    with urllib.request.urlopen(f"{server}/healthz?token={TOKEN}", timeout=10) as response:
        assert response.status == HTTPStatus.OK
    status, _body = _post(
        server, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=TOKEN
    )
    assert status == HTTPStatus.OK
    logged = buf.getvalue()
    assert TOKEN not in logged
    assert "token=" not in logged
    assert "?" not in logged


def test_registered_http_bearer_is_stripped_from_receipt_text(server: str) -> None:
    # create_http_server registers the process token with the redactor.
    assert TOKEN not in redact_text(f"the worker echoed {TOKEN} from the environment")


def test_post_root_is_not_an_mcp_alias(server: str) -> None:
    status, _body = _post(
        server,
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        token=TOKEN,
        path="/",
    )
    assert status == HTTPStatus.NOT_FOUND


def test_delete_mcp_is_method_not_allowed(server: str) -> None:
    request = urllib.request.Request(f"{server}/mcp", method="DELETE")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=10)
    assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert refused.value.headers.get("Allow") == "POST"


def test_http_initialize_echoes_an_older_handshake_version(server: str) -> None:
    status, body = _post(
        server,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        token=TOKEN,
    )
    assert status == HTTPStatus.OK
    assert body["result"]["protocolVersion"] == "2024-11-05"


def test_second_http_initialize_is_refused_as_one_process_per_client(server: str) -> None:
    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }
    first_status, first = _post(server, {**payload, "id": 1}, token=TOKEN)
    second_status, second = _post(server, {**payload, "id": 2}, token=TOKEN)
    assert first_status == HTTPStatus.OK
    assert first["result"]["protocolVersion"] == "2025-06-18"
    assert second_status == HTTPStatus.OK
    assert second["error"]["data"]["reason"] == "ONE_CLIENT_PER_PROCESS"
    assert "one process per client" in second["error"]["message"].lower()
