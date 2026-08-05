# Grok Build CLI 0.2.118 ACP golden fixtures

These redacted JSONL fixtures were captured from the installed
`grok 0.2.118 (1e1687c1cf) [stable]` on Windows on 2026-08-05. The process was
started as:

```powershell
grok --permission-mode default agent --no-leader stdio
```

Each line is one newline-delimited ACP JSON-RPC frame. Dynamic session, tool,
request and filesystem identifiers are replaced with angle-bracket markers.
No token, credential file, WebSocket secret, response signature, hostname or
account detail is retained.

Observed wire invariants used by the implementation:

- protocol version is the integer `1`;
- stdio framing is one UTF-8 JSON object per line;
- `session/new` returns `sessionId`;
- prompt completion is the response to the original `session/prompt` request;
- `session/cancel` is a notification, not a request;
- tool permission is an agent-to-client `session/request_permission` request;
- permission rejection and explicit cancellation both complete the prompt with
  `stopReason: "cancelled"`;
- write/execute permission prompts appear only when the parent CLI is launched
  with explicit `--permission-mode default` in this version.

The fake ACP agents in the test suite replay these shapes. Live acceptance is
separate and must not be replaced by fixture tests.

`websocket.jsonl` records the same live ACP lifecycle over authenticated RFC
6455 text frames. The HTTP query secret is deliberately not recorded.
