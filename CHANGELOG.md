# Changelog

## Unreleased
- `.mcp.json` wired a third-party `grok-cli-mcp` server instead of this one; its
  draft-04 schemas made hosts fail every request after loading a tool
- Project-scoped entry now resolves the package via `CLAUDE_PROJECT_DIR` (no
  working-directory assumption) and takes the interpreter from `${GROK_MCP_PYTHON:-py}`
- `tests/test_tool_schemas.py` guards draft 2020-12 conformance of every tool schema
- `install.ps1` writes Claude/Cursor snippets, matching `install.sh`, and carries
  the environment inside them. `install.sh` points its snippets at a wrapper that
  sources the env file first; Windows has no wrapper, so a snippet with `"env": {}`
  produced a server with an empty allowlist and every repo-touching tool failed
  closed
- Corrected stale server version and tool counts in README and Codex setup readback
- `docs/EASY.md` stacked three contradictory session protocols (v0.8, v1.1, v1.2);
  collapsed to the current navigator loop
- README documents the allowlist the project-scoped path needs, since that path
  never runs the installer that would have written it
- Verified against Grok Build CLI `1.0.0 (3cd0d0cbce) [stable]`: no bridge change
  needed. Version/auth/doctor/models probes pass, `channel` parses from the new
  `[stable]` suffix, all 21 flags `build_grok_argv` can emit still exist, and a
  live plan-only delegate run returns `SMOKE PASS`. ACP transports were not
  re-observed on 1.0.0 and stay pinned to the `0.2.118` baseline

## 0.8.0 — Session Protocol v1.2 Navigator
- `*_session_next` returns one action card (host_cmd|mcp_tool|end)
- Host loop: begin → next* → end (minimal tokens)
- Skill v1.0.0 enforces navigator-only protocol
- Install/update/feedback cards without empty plans

## 0.7.0 — Session Protocol v1.1
- Plan compiler + budget guard
