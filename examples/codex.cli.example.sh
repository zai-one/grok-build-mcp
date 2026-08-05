#!/usr/bin/env bash
# Example: register grok-delegate with Codex CLI (placeholders only).
# Replace <REPO_PATH>, <PROJECT_ROOT>, <LANES_PARENT>, <JOBS_DIR> before running.
# Do NOT pass GROK_AGENT_SECRET, API keys, or OAuth tokens.

set -euo pipefail

codex mcp add grok-delegate \
  --env "PYTHONPATH=<REPO_PATH>" \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- python -m grok_delegate.server

# Verify registration
codex mcp list

# Optional: remove later
# codex mcp remove grok-delegate
