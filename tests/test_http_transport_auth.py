"""Loopback is not a security boundary, and this endpoint runs commands.

The HTTP transport exempted loopback from authentication: with no token every
request was authorized, so any process on the machine -- and any page in the
browser, since a text/plain POST needs no preflight -- could call
grok_agent_execute against whatever allowlist the server was started with.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http import HTTPStatus

import pytest

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


def _post(base: str, body: dict, *, token: str | None = None, content_type: str = "application/json"):
    request = urllib.request.Request(
        f"{base}/mcp",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": content_type},
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_starting_without_a_token_is_refused_even_on_loopback() -> None:
    with pytest.raises(ValueError) as refused:
        create_http_server(host="127.0.0.1", port=0, token=None)
    assert "GROK_DELEGATE_HTTP_TOKEN" in str(refused.value)


def test_starting_without_a_token_is_refused_off_loopback() -> None:
    with pytest.raises(ValueError):
        create_http_server(host="0.0.0.0", port=0, token=None)


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


def test_healthz_stays_open_because_it_answers_nothing(server: str) -> None:
    with urllib.request.urlopen(f"{server}/healthz", timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert response.status == HTTPStatus.OK
    assert body["ok"] is True
    assert "tools" not in body


def test_readyz_still_needs_the_token(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"{server}/readyz", timeout=10)
    assert refused.value.code == HTTPStatus.UNAUTHORIZED
