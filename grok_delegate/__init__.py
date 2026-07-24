"""Dev-only grok_delegate tooling package.

Import as ``grok_delegate`` (package layout). Run the MCP stdio server with
``python -m grok_delegate.server`` or ``python grok_delegate/server.py``.

Not a product surface. Does not extend tools/mcp admin-bridge trust.
"""

from __future__ import annotations

__all__ = [
    "audit",
    "guard",
    "runner",
    "server",
    "status",
]
