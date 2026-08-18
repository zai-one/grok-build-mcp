"""Handshake-era MCP version negotiation.

The 2026-08-18 transport report said to advertise 2025-03-26 or 2025-06-18
and to read params.protocolVersion, instead of always answering 2024-11-05.
We refused spec Streamable HTTP; this still applies to stdio.
"""

from __future__ import annotations

import pytest

from grok_delegate.server import (
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    handle_jsonrpc,
    negotiate_protocol_version,
)


def _initialize(requested: object | None) -> dict:
    params: dict = {"capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}}
    if requested is not None:
        params["protocolVersion"] = requested
    response = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
    )
    assert response is not None
    return response


def test_latest_advertised_version_is_the_named_handshake_revision() -> None:
    assert PROTOCOL_VERSION == "2025-06-18"
    assert PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS


def test_first_public_revision_stays_supported() -> None:
    assert "2024-11-05" in SUPPORTED_PROTOCOL_VERSIONS


def test_modern_era_that_dropped_initialize_is_not_claimed() -> None:
    assert "2026-07-28" not in SUPPORTED_PROTOCOL_VERSIONS


@pytest.mark.parametrize(
    ("requested", "echoed"),
    [
        ("2024-11-05", "2024-11-05"),
        ("2025-03-26", "2025-03-26"),
        ("2025-06-18", "2025-06-18"),
        ("2025-11-25", "2025-06-18"),
        ("2026-07-28", "2025-06-18"),
        ("", "2025-06-18"),
        (None, "2025-06-18"),
        (1, "2025-06-18"),
    ],
)
def test_initialize_echoes_a_supported_handshake_version(requested: object, echoed: str) -> None:
    body = _initialize(requested)
    assert body["result"]["protocolVersion"] == echoed
    assert body["result"]["serverInfo"]["name"] == "grok-delegate"
    assert body["result"]["capabilities"] == {"tools": {}}


def test_negotiate_protocol_version_matches_initialize() -> None:
    assert negotiate_protocol_version("2024-11-05") == "2024-11-05"
    assert negotiate_protocol_version("not-a-revision") == PROTOCOL_VERSION
    assert negotiate_protocol_version(None) == PROTOCOL_VERSION
