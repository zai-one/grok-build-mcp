# Project development guidance

- Python 3.10+; unofficial Grok bridge.
- Preserve host roots, opt-in projects, isolated worktrees, independent verifier and bounded wire receipts; HTTP always needs auth.
- Checks: `python -m pytest tests -q`; `python scripts/check_engineering.py --check`. Run install checks before release.

Read only the task-relevant public documentation. Personal workspace notes are optional and remain outside Git.

- [README](../../README.md): supported features and installation.
- [Contributing](../../CONTRIBUTING.md): development and release checks.

- Root/job changes: preserve host-provided roots, project opt-in, immutable base SHA and isolated worktrees. Worker tools cannot grant themselves roots.
- Verification: use independent test evidence, not worker-reported summaries. Validate command paths and keep secrets out of worktrees/receipts.
- HTTP: require the configured bearer token even on loopback. This private JSON-RPC transport is not standard Streamable HTTP; initialization is not an authorization boundary.
- Process/retry changes: keep cancellations, max-turn stops, pending jobs and concurrent sessions distinct. Preserve bounded serialized wire responses and continuation/truncation indicators.
- CLI compatibility: preserve ACP v1 support without hardcoding a current model or CLI version. Defaults belong to the installed CLI unless explicitly configured.
- Releases: keep pyproject version, SERVER_VERSION in grok_delegate/guard.py, README badge and docs/CODEX-MCP-SETUP.md consistent. Preserve the unofficial-community notice.
