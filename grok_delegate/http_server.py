"""Bearer-protected Streamable-ish JSON-RPC HTTP for VPS / remote MCP hosts.

This is intentionally stdlib-only. **Every** bind requires
``GROK_DELEGATE_HTTP_TOKEN`` or ``GROK_DELEGATE_HTTP_TOKEN_FILE``, loopback
included: loopback is shared with every other process on the machine and with
the browser, and the tools behind this endpoint create worktrees and run
commands. ``/healthz`` is the only unauthenticated route, and it answers nothing.
Never place Grok OAuth tokens in the HTTP bearer field.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from .server import handle_jsonrpc

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
MAX_BODY = 2_000_000


def _configured_token(explicit: Optional[str] = None) -> Optional[str]:
    if explicit is not None:
        value = str(explicit).strip()
        return value or None
    value = os.environ.get("GROK_DELEGATE_HTTP_TOKEN", "").strip()
    token_file = os.environ.get("GROK_DELEGATE_HTTP_TOKEN_FILE", "").strip()
    if value and token_file:
        raise ValueError(
            "set only one of GROK_DELEGATE_HTTP_TOKEN and GROK_DELEGATE_HTTP_TOKEN_FILE"
        )
    if not token_file:
        return value or None
    path = Path(token_file).expanduser().resolve()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("cannot read GROK_DELEGATE_HTTP_TOKEN_FILE") from exc
    if not text:
        raise ValueError("GROK_DELEGATE_HTTP_TOKEN_FILE is empty")
    return text


class DelegateHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        token: Optional[str],
    ) -> None:
        self.token = token
        self.inflight = threading.BoundedSemaphore(
            max(1, min(int(os.environ.get("GROK_DELEGATE_HTTP_MAX_INFLIGHT", "16") or "16"), 256))
        )
        super().__init__(server_address, DelegateHTTPRequestHandler)


class DelegateHTTPRequestHandler(BaseHTTPRequestHandler):
    server: DelegateHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Avoid writing secrets from query strings into default stderr access logs.
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _unauthorized(self) -> None:
        self._write(
            HTTPStatus.UNAUTHORIZED,
            {"error": "unauthorized"},
            extra_headers={"WWW-Authenticate": "Bearer"},
        )

    def _authorized(self) -> bool:
        token = self.server.token
        if token is None:
            # Unreachable via create_http_server, which refuses to bind without
            # a token. Kept as a deny so a directly-constructed server cannot
            # inherit the old behaviour, where "loopback may run without a
            # token" meant every local process -- and every web page, since a
            # text/plain POST needs no preflight -- could call grok_agent_execute.
            return False
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        supplied = header[len(prefix) :].strip()
        return bool(supplied) and hmac.compare_digest(supplied, token)

    def _write(
        self,
        status: int,
        payload: Any,
        *,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/healthz", "/"}:
            self._write(HTTPStatus.OK, {"ok": True, "service": "grok-delegate", "transport": "http"})
            return
        if path == "/readyz":
            if not self._authorized():
                self._unauthorized()
                return
            self._write(HTTPStatus.OK, {"ok": True, "ready": True})
            return
        self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/mcp", "/"}:
            self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            self._unauthorized()
            return
        # A browser can send text/plain without a preflight. Requiring JSON means
        # a cross-origin page cannot reach this handler at all, independently of
        # whether it somehow obtained the token.
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type and content_type != "application/json":
            self._write(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "content_type_must_be_json"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > MAX_BODY:
            self._write(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"})
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        if not isinstance(message, dict):
            self._write(HTTPStatus.BAD_REQUEST, {"error": "invalid_jsonrpc"})
            return
        if not self.server.inflight.acquire(blocking=False):
            self._write(HTTPStatus.TOO_MANY_REQUESTS, {"error": "too_many_requests"})
            return
        try:
            response = handle_jsonrpc(message)
        finally:
            self.server.inflight.release()
        if response is None:
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._write(HTTPStatus.OK, response)


def create_http_server(
    *,
    host: str,
    port: int,
    token: Optional[str] = None,
) -> DelegateHTTPServer:
    host = str(host).strip()
    if not host:
        raise ValueError("HTTP host must not be empty")
    if port < 0 or port > 65_535:
        raise ValueError("HTTP port must be between 0 and 65535")
    configured = _configured_token(token)
    if not configured:
        # Loopback used to be exempt. It is not a boundary: every process on the
        # machine shares it, and a page in the browser can POST to it. The tools
        # behind this endpoint create worktrees and run commands, so the rule is
        # now the same wherever it binds.
        raise ValueError(
            "HTTP transport requires GROK_DELEGATE_HTTP_TOKEN, including on loopback"
        )
    return DelegateHTTPServer((host, port), token=configured)


def serve_http(*, host: str, port: int) -> None:
    server = create_http_server(host=host, port=port)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
