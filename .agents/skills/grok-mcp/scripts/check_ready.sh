#!/usr/bin/env bash
# Exit 0 if Grok CLI + grok-mcp self-test look ready (no secrets printed).
set -euo pipefail
if ! command -v grok >/dev/null 2>&1; then
  echo "FAIL: grok CLI not on PATH"
  exit 1
fi
echo "OK: grok -> $(command -v grok)"
if [[ -n "${GROK_MCP_HOME:-}" && -x "${GROK_MCP_HOME}/.venv/bin/python" ]]; then
  PY="${GROK_MCP_HOME}/.venv/bin/python"
elif [[ -x "${HOME}/.local/share/grok-mcp/.venv/bin/python" ]]; then
  PY="${HOME}/.local/share/grok-mcp/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi
# shellcheck disable=SC1090
[[ -f "${HOME}/.config/grok-mcp/env" ]] && source "${HOME}/.config/grok-mcp/env" || true
set +e
"$PY" -m grok_delegate --self-test
code=$?
set -e
exit "$code"
