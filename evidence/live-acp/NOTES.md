# Live ACP capture — what the installed CLI actually does

Observed on the operator machine. **Not a version pin**: the bridge default stays
`GROK_DELEGATE_EXPECTED_AGENT_VERSION=any` and the compatibility contract is the
ACP protocol integer `1`. These files exist so the parsing in `grok_delegate/acp.py`
is checked against traffic instead of against comments.

| Field | Value |
|---|---|
| CLI | `grok 1.0.4 (d846eb93d9)` |
| `agentVersion` | `1.0.4` |
| ACP `protocolVersion` | `1` |
| Captured with | `scripts/capture_acp_live.py` |
| Replayed by | `tests/test_live_acp_fixtures.py` |

## Scenarios

| File | Drives | `stopReason` |
|---|---|---|
| `session-permission-cancel.jsonl` | read + write tools, permission denied, then `session/cancel` | `cancelled` |
| `session-consult.jsonl` | read-only turn that finishes normally | `end_turn` |
| `session-command.jsonl` | shell tool running the declared test command | `end_turn` |

Every capture denies anything the scenario did not ask for, so a capture can
never become a way to run arbitrary tools. Paths, hostnames and auth locations
are redacted; `toolCallId` is kept because the permission join is keyed on it.

## What the capture changed in the bridge

- **`target_file`** — the 1.0.4 read tool names its path with that key, which
  `_paths_confined` did not know. An unrecognised key reads as "no path", which
  fails closed, so a legitimate read was denied for the wrong reason.
- **`rawInput` arrives in the permission request itself**, not only in the
  earlier `session/update`. The two-frame join is now a no-op here — kept
  because the decision must not depend on which frame a build fills.
- **A shell chain's exit code belongs to its last statement.** Live:
  `python -m pytest -q; echo EXIT_CODE=$LASTEXITCODE` reported `exit_code: 0`
  while pytest failed. Agent-reported test results are labelled
  `source: "agent-reported"` and yield no verdict at all when the command
  chains; only `bridge-verifier` entries count as evidence.
- **The command must reach the bridge verbatim.** Prompted neutrally, the agent
  asked for `python -m pytest -q` exactly; prompted to "report the exit code",
  it decorated the command and the exact-match permission gate denied it.
  `build_prompt` now says so to the worker.
- **Private notifications are real and numerous** — 11 distinct
  `_x.ai/*` methods on 1.0.4, including `_x.ai/session/prompt_complete`, which
  looks like a turn result and is not one.

## Still not re-observed on this CLI

- WebSocket `grok agent serve` handshake, reconnect, and `session/load`.
  The stdio path is the default and the one under test; the WS path keeps its
  own fixtures in `evidence/round8/acp-fixtures/websocket.jsonl`.

## Re-capturing after a CLI upgrade

```
py -3 scripts/capture_acp_live.py --scenario permission-cancel
py -3 scripts/capture_acp_live.py --scenario consult
py -3 scripts/capture_acp_live.py --scenario command
py -3 -m pytest tests/test_live_acp_fixtures.py -q
```

A failing replay after an upgrade is the point: it names what changed. Fix the
parsing to handle both shapes — never by pinning the version.
