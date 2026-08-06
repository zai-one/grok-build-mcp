# Feedback → GitHub Issue

## When to open

- Same MCP failure twice+ in a session after correct install/login
- Clear product gap (docs, tool UX, install)
- User asks to report / improve MCP

## When not

- User error (no `grok login`, wrong roots) — fix with install mode
- Secrets or private code in the body

## How

1. Fill [`templates/issue-bug.md`](../templates/issue-bug.md) or [`issue-improvement.md`](../templates/issue-improvement.md)
2. Optionally: `python scripts/draft_issue.py --repo zai-one/grok-mcp --type bug --title "..." --body-file /tmp/body.md`
3. If `gh` authenticated: script can create; else give markdown to the user

## Rules

- Free-form OK; structure helps
- Redact tokens, cookies, home paths with usernames if sensitive
- Unofficial project — be polite and factual
