"""Roots the host already knows about, so nobody declares them twice.

MCP answers the question this bridge was answering with an environment variable:
the client tells the server which directories the user is working in. The reason
it was missing here is structural rather than accidental -- ``roots/list`` is a
request the *server* sends to the *client*, and the stdio loop only ever
answered. It never asked.

This does not widen what an agent can reach on its own. A root arrives because a
person opened that directory in their editor; no tool call can invent one, and
the agent never gets to name a root. That is the whole reason to prefer this
over letting a tool grant itself access.

Set ``GROK_DELEGATE_MCP_ROOTS=0`` to refuse host-declared roots and keep the
explicit allowlist as the only answer.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

ROOTS_LIST_METHOD = "roots/list"
ROOTS_CHANGED_NOTIFICATION = "notifications/roots/list_changed"
REQUEST_ID_PREFIX = "grok-delegate-roots-"

_LOCK = threading.Lock()
_client_supports_roots = False
_roots: list[Path] = []
_pending_ids: set[str] = set()
_request_counter = 0


def mcp_roots_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Host-declared roots are on unless the operator turns them off.

    On by default because the alternative is what shipped before: a server that
    starts, answers every job tool with ``ALLOWED_ROOTS_EMPTY``, and can only be
    fixed by editing the host's config and restarting it.
    """
    environ = env if env is not None else os.environ
    return (environ.get("GROK_DELEGATE_MCP_ROOTS") or "1").strip() != "0"


def remember_client_capabilities(params: Any) -> bool:
    """Record whether the client offered ``roots``. Returns that answer."""
    global _client_supports_roots
    supported = False
    if isinstance(params, Mapping):
        capabilities = params.get("capabilities")
        if isinstance(capabilities, Mapping):
            supported = isinstance(capabilities.get("roots"), Mapping)
    with _LOCK:
        _client_supports_roots = supported
        if not supported:
            _roots.clear()
    return supported


def client_supports_roots() -> bool:
    with _LOCK:
        return _client_supports_roots


def build_roots_request() -> dict[str, Any] | None:
    """The ``roots/list`` request to send, or None when it would be pointless.

    The id is remembered so the loop can tell our own answer apart from a client
    request and not hand it to the tool dispatcher.
    """
    global _request_counter
    if not mcp_roots_enabled():
        return None
    with _LOCK:
        if not _client_supports_roots:
            return None
        _request_counter += 1
        request_id = f"{REQUEST_ID_PREFIX}{_request_counter}"
        _pending_ids.add(request_id)
    return {"jsonrpc": "2.0", "id": request_id, "method": ROOTS_LIST_METHOD}


def is_roots_response(message: Any) -> bool:
    """True for a reply to a ``roots/list`` we sent.

    A response carries no ``method``. Checking the id alone would misread a
    client *request* that happened to reuse the string.
    """
    if not isinstance(message, Mapping) or message.get("method") is not None:
        return False
    request_id = message.get("id")
    if not isinstance(request_id, str):
        return False
    with _LOCK:
        return request_id in _pending_ids


def apply_roots_response(message: Mapping[str, Any]) -> list[Path]:
    """Take the roots out of a reply and make them the host-declared set.

    A client that answers with an error, or with nothing usable, leaves the set
    empty rather than keeping a stale one: the honest reading of "I cannot tell
    you my roots" is that we do not know them.
    """
    request_id = message.get("id")
    with _LOCK:
        _pending_ids.discard(request_id if isinstance(request_id, str) else "")
    result = message.get("result")
    entries: Sequence[Any] = ()
    if isinstance(result, Mapping) and isinstance(result.get("roots"), Sequence):
        entries = result["roots"]  # type: ignore[assignment]
    resolved: list[Path] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        path = uri_to_path(entry.get("uri"))
        if path is not None and path not in resolved:
            resolved.append(path)
    with _LOCK:
        _roots[:] = resolved
    return list(resolved)


def host_roots() -> list[Path]:
    """Directories the host declared. Empty until it answers, and that is fine."""
    if not mcp_roots_enabled():
        return []
    with _LOCK:
        return list(_roots)


def uri_to_path(uri: Any) -> Path | None:
    """`file://` URI to a real directory, or None.

    Windows is the reason this is not one line: `file:///D:/x` parses to the
    path `/D:/x`, and a leading slash before a drive letter is not a root, it is
    a parsing artifact.
    """
    if not isinstance(uri, str) or not uri.strip():
        return None
    text = uri.strip()
    parsed = urlparse(text)
    if parsed.scheme and parsed.scheme != "file":
        return None
    raw = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc.lower() not in {"", "localhost"}:
        # A UNC path: file://server/share -> \\server\share
        raw = f"//{parsed.netloc}{raw}"
    elif len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser().resolve()
    except OSError:
        return None
    return candidate if candidate.is_dir() else None


def reset_for_tests() -> None:
    global _client_supports_roots, _request_counter
    with _LOCK:
        _client_supports_roots = False
        _roots.clear()
        _pending_ids.clear()
        _request_counter = 0


__all__ = [
    "ROOTS_CHANGED_NOTIFICATION",
    "ROOTS_LIST_METHOD",
    "apply_roots_response",
    "build_roots_request",
    "client_supports_roots",
    "host_roots",
    "is_roots_response",
    "mcp_roots_enabled",
    "remember_client_capabilities",
    "reset_for_tests",
    "uri_to_path",
]
