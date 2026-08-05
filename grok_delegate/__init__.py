"""Dev-only grok_delegate tooling package.

Import as ``grok_delegate`` (package layout). Run the MCP stdio server with
``python -m grok_delegate.server`` or the ``grok-delegate`` console script.

Not a product surface. Does not extend tools/mcp admin-bridge trust.
"""

from __future__ import annotations

from .guard import SERVER_VERSION as __version__

__all__ = [
    "__version__",
    "audit",
    "guard",
    "runner",
    "server",
    "status",
]
