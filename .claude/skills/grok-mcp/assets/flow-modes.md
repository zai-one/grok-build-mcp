# Host ↔ MCP ↔ CLI

```text
User / Host agent (Claude, Cursor, …)
        │  short prompts + tool calls
        ▼
   grok-mcp (stdio or HTTP)
        │  spawn / protocol
        ▼
   Grok CLI (local login session)
        │
        ▼
   worktree / code / tests → receipts back up
```

Modes: brainstorm (consult) · executor (execute+poll) · verifier (status/tests) · feedback (GitHub Issue).
