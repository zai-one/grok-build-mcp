#!/usr/bin/env python3
"""Example: FastMCP-style remote proxy to grok-delegate HTTP MCP.

Placeholders only — no real credentials or personal paths.
Adapt imports/APIs to your installed FastMCP version.

Security:
  - Authorization bearer MUST be an operator-generated secret.
  - NEVER put Grok OAuth tokens or API keys in headers or env files
    that get committed.
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Placeholders — replace before use
# ---------------------------------------------------------------------------
REMOTE_MCP_URL = os.environ.get(
    "GROK_DELEGATE_REMOTE_MCP_URL",
    "https://mcp.example.invalid/mcp",
)
# Prefer reading from a file outside the repo:
#   export GROK_DELEGATE_HTTP_TOKEN_FILE=/path/to/token
TOKEN_FILE = os.environ.get("GROK_DELEGATE_HTTP_TOKEN_FILE", "<TOKEN_FILE>")
TOKEN_ENV = os.environ.get("GROK_DELEGATE_HTTP_TOKEN", "")


def _load_bearer() -> str:
    if TOKEN_ENV and TOKEN_FILE not in ("", "<TOKEN_FILE>"):
        raise SystemExit(
            "set only one of GROK_DELEGATE_HTTP_TOKEN and GROK_DELEGATE_HTTP_TOKEN_FILE"
        )
    if TOKEN_ENV:
        return TOKEN_ENV.strip()
    if TOKEN_FILE and TOKEN_FILE != "<TOKEN_FILE>":
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
        if not value:
            raise SystemExit("token file is empty")
        return value
    raise SystemExit(
        "configure GROK_DELEGATE_HTTP_TOKEN_FILE or GROK_DELEGATE_HTTP_TOKEN "
        "(operator bearer only — not Grok OAuth)"
    )


def main() -> int:
    bearer = _load_bearer()
    headers = {"Authorization": f"Bearer {bearer}"}

    # --- Conceptual FastMCP proxy wiring ---------------------------------
    # Uncomment and adjust once FastMCP is installed in your environment:
    #
    #   from fastmcp import FastMCP
    #   # Some versions expose create_proxy / Client / HTTP transport helpers:
    #   mcp = FastMCP.as_proxy(
    #       REMOTE_MCP_URL,
    #       headers=headers,
    #   )
    #   # or:
    #   # mcp = create_proxy(url=REMOTE_MCP_URL, headers=headers)
    #   mcp.run()  # stdio for local Claude/Cursor
    #
    # Without FastMCP, document the remote for any MCP host that supports
    # URL + headers:
    print("Remote MCP URL:", REMOTE_MCP_URL, file=sys.stderr)
    print("Headers: Authorization: Bearer <redacted>", file=sys.stderr)
    print(
        "Wire this URL+headers into FastMCP create_proxy / your host remote MCP.",
        file=sys.stderr,
    )
    print(
        "Unofficial community project — not an official xAI/Grok product.",
        file=sys.stderr,
    )
    _ = headers  # used when real proxy code is enabled
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
