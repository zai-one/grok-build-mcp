# Changelog

## Unreleased

- Skeptic follow-up on `fix/orchestrator-loop`: `grok_agent_cancel` cards compile
  `{job_id}` like poll; execute/fix bind by `correlation_id` and no longer let
  consult/review steal the poll slot. Tag `v0.9.0` stays on `ed019df` (no retag).

## 0.9.0 — Unpin, typed session_next, evidence pack
- Default Grok CLI `agentVersion` check is **off** (`any`). Opt-in pin:
  `GROK_DELEGATE_EXPECTED_AGENT_VERSION`. Mismatch is a warning event plus
  status/doctor `compatibility.warning`; the typed path is not blocked
- `session_next` execute cards compile a full `task` packet; poll cards are
  `{job_id}` only. The server binds `job_id` from execute. Contract tests
  validate cards against the real tool schemas
- Compact poll/receipt evidence: listed files, diffstat, optional **bounded**
  unified diff (16KiB cap), bridge-verifier tests, `worktree_path`
- `grok_agent_status` / doctor report `bridge_version`, `grok_delegate_version`,
  detected CLI, ACP v1, unpin status, skill protocol, `update_hint`
  (`git pull` / editable reinstall / restart MCP — no updater daemon)
- AGENTS.md, Cursor rule, GitHub issue templates, skill v1.1 on
  `.cursor` / `.claude` / `.codex` / `.agents`
- Live ACP **initialize** captured on the installed CLI (observed `1.0.4`);
  that string is not a pin. Permission/cancel/WebSocket frames remain to capture
- `GROK_DELEGATE_TRUST_HOST_ROOTS=1` lets the host's project directory
  (`CLAUDE_PROJECT_DIR`) join the allowlist, so the session's own project no
  longer has to be listed by hand. Opt-in; widens the explicit list instead of
  replacing it; exact-equality membership unchanged. `grok_agent_status` reports
  `roots.host_root_trusted` and `roots.host_root`
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

## 0.8.0 — Session Protocol v1.2 Navigator
- `*_session_next` returns one action card (host_cmd|mcp_tool|end)
- Host loop: begin → next* → end (minimal tokens)
- Skill v1.0.0 enforces navigator-only protocol
- Install/update/feedback cards without empty plans

## 0.7.0 — Session Protocol v1.1
- Plan compiler + budget guard
