# Feedback → Issue

When: 2+ MCP fails after good gate, or user asks.

1. `.github/ISSUE_TEMPLATE/` or `templates/issue.md` (scrub secrets)
2. `python scripts/draft_issue.py --repo zai-one/grok-mcp --title "…" --body-file body.md`
3. `--create` only with user OK + `gh`

Required fields: symptom, host, grok CLI version, bridge version,
`grok_agent_status` / doctor excerpt, expected vs actual, redacted fixture/logs.

Repo: `zai-one/grok-mcp`.
