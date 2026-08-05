# Contributing

Thanks for helping improve `grok-delegate`. This package is a local stdio MCP
bridge around the Grok CLI; keep changes small, testable, and free of secrets.

## Prerequisites

- Python 3.10+
- `git`
- Optional: a logged-in Grok CLI for live smoke only (unit tests mock I/O)

## Setup

```bash
cd <REPO_PATH>
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[test]"
```

## Run tests

```bash
# Full unit suite (default; no live network / no real delegate required)
pytest tests -q

# Verbose single file
pytest tests/test_grok_delegate.py -v

# Operator self-check (needs grok on PATH and local CLI session for full PASS)
python -m grok_delegate --self-test
```

Do not commit live smoke output that contains machine-specific paths or any
environment values that look like secrets.

## Code expectations

- Fail closed: empty allowlist, reserved lanes, over-cap turns, and unsafe
  permission modes must error.
- Never add `--always-approve` or `bypassPermissions` to generated argv.
- Never accept API keys / OAuth tokens as MCP tool arguments or documented
  config keys.
- Keep `SERVER_VERSION` in `grok_delegate/guard.py` and `__version__` in
  `grok_delegate/__init__.py` in sync with `pyproject.toml`.
- Prefer pure policy in `guard.py` and transport-thin code in `server.py`.
- Redact secrets in logs, receipts, and errors.

## Pull requests

1. Fork (or branch from `main`) and keep PRs focused on one concern.
2. Add or update tests for behavior changes.
3. Run `pytest tests -q` and fix failures before requesting review.
4. Update docs under `docs/` when operators or integrators are affected.
5. Describe the change, risk, and how you verified it in the PR body.
6. Do not include real credentials, home directories, or private hostnames in
   diffs, fixtures, or screenshots.

## Security issues

Report vulnerabilities via GitHub Security Advisories — see `SECURITY.md`.
Do not open public issues that include exploit details before a fix is ready.

## License

By contributing, you agree that your contributions are licensed under the MIT
License (see `LICENSE`).
