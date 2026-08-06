# Update

Prefer the helper script from a checkout or install home:

```bash
bash /path/to/grok-mcp/skills/grok-mcp/scripts/update_mcp.sh
# or from install home:
bash ~/.local/share/grok-mcp/skills/grok-mcp/scripts/update_mcp.sh
```

Manual equivalent:

```bash
cd "${GROK_MCP_HOME:-$HOME/.local/share/grok-mcp}"
git pull --ff-only
# re-activate venv
source .venv/bin/activate
pip install -e ".[test]"   # or: uv pip install -e ".[test]"
source ~/.config/grok-mcp/env
python -m grok_delegate --self-test
```

Preserve `~/.config/grok-mcp/env` (roots). Re-login only if auth fails.

If pull conflicts: stash local edits or re-run installer with same `--project`.
