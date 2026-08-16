# Live ACP initialize capture

Observed on the operator machine, **not a version pin**. The bridge default remains
`GROK_DELEGATE_EXPECTED_AGENT_VERSION=any`.

| Field | Value |
|---|---|
| CLI | `grok 1.0.4 (d846eb93d9) [stable]` |
| `agentVersion` | `1.0.4` |
| ACP `protocolVersion` | `1` |
| `authenticate` RPC | not required for initialize |
| `clientCapabilities` | `{}` accepted |
| `loadSession` | `true` |
| `authMethods` | `cached_token`, `grok.com` (locations redacted) |

Captured with `scripts/capture_acp_initialize.py`. The raw initialize result also
contained large UI/config (`availableCommands`, `modelState`); those are omitted
here the same way the transport drops non-evidence session updates.

## Still not re-observed on this CLI

- `session/new` cwd-stall vs worktree cwd
- `session/request_permission` two-frame `rawInput` join
- `session/cancel`
- WebSocket `grok agent serve` handshake / reconnect + `session/load`

Do not treat `1.0.4` as a hardcoded expected version.
