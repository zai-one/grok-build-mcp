#!/usr/bin/env bash
set -euo pipefail
HOME_DIR="${GROK_MCP_HOME:-$HOME/.local/share/grok-mcp}"
if [[ ! -d "$HOME_DIR/.git" ]]; then
  echo "No install at $HOME_DIR — run install.sh first"
  exit 1
fi
cd "$HOME_DIR"
git pull --ff-only
if command -v uv >/dev/null 2>&1; then
  uv pip install -e ".[test]"
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -e ".[test]"
fi
# shellcheck disable=SC1090
[[ -f "$HOME/.config/grok-mcp/env" ]] && source "$HOME/.config/grok-mcp/env" || true
.venv/bin/python -m grok_delegate --self-test || true
echo "Update attempted. Fix auth with: grok login"
