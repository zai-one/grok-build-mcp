## Plan Compiler + Budget Guard

`session_begin` returns a ≤5-step `plan` and hard `budget` (tool_calls/polls). Hosts save tokens by executing the plan only — no re-planning essays.

## Session Protocol (source of truth)

Host agents should call `grok_agent_session_begin` first. It enables compact economy defaults and returns mode + tools + one `skill_ref`. Prefer that over re-reading this document.

# Token economy (English)

> ## ⚠️ Unofficial product disclaimer
>
> **Not** an official product of xAI, Grok, Anthropic, OpenAI, or Codex.
> Community software only. Auth remains the local Grok CLI session.

## Idea in one paragraph

The **host agent** (Claude, Cursor, …) should stay thin: short plans, tool
calls, and compact receipts. The **Grok CLI** on the same machine or a VPS
runs the expensive coding loop. You buy host tokens for *orchestration*, not
for rewriting every file in chat.

```text
Host agent  ──short MCP calls──►  grok-delegate  ──►  Grok CLI (worktrees)
     ▲                                │
     └──── compact poll / summary ────┘
```

## Enable economy defaults

| Variable | Effect |
|---|---|
| `GROK_DELEGATE_ECONOMY=1` | Default `timeout_seconds=600` when the client omits it, and compact poll. It also carries `max_turns=12` / `reasoning_effort=low`, but a project that opted in via `.grok-mcp.json` always supplies both, so on the job path the preset wins and economy never lowers the worker |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1` | Force compact job/poll payloads (summary clip, few events, capped file lists) |

If compact poll is unset, enabling economy also turns compact poll on.

## Playbook tool

Call once per session:

```text
grok_agent_economy
```

Returns `do` / `dont` lists, host prompt snippets, env hints, and VPS notes.
No arguments.

## Recommended call sequence

| Step | Tool | Host cost tip |
|---|---|---|
| 1 | `grok_agent_status` | Once per session — not every turn |
| 2 | `grok_agent_economy` | Once — load playbook |
| 3 | `grok_agent_consult` or `grok_agent_review` | Q&A / critique without worktrees |
| 4 | `grok_agent_execute` | One focused objective + artifacts + 1–3 cheap tests |
| 5 | `grok_agent_poll` | `job_id` only; read summary / files / tests / blocked_reason |
| 6 | Human | Merge `grok/*` after review |

## Anti-waste rules (for host agents)

1. **Don't** paste large source trees into `objective` — point at paths.
2. **Don't** use `execute` for questions — use `consult`.
3. **Don't** set `max_turns` near the hard cap (60) for routine work.
4. **Don't** re-send the full goal on every poll.
5. **Don't** request full event transcripts unless debugging.
6. **Don't** stack concurrent jobs; cancel stale ones.
7. **Never** put OAuth, API keys, or `GROK_AGENT_SECRET` in MCP config.

## Prompt shapes that save tokens

**Consult**

> Answer in ≤12 bullets. No code dumps unless asked.

**Execute**

> Implement only the listed artifacts. Stop when tests pass.
> Do not refactor unrelated files.

**Poll**

> Return status, blocked_reason, changed_files, test pass flags only.

## VPS angle

Run Grok CLI + this MCP on a VPS (auth on the VPS). Connect Claude/Cursor via
stdio over SSH, or **bearer HTTP JSON-RPC behind TLS** (not Streamable HTTP).
Host-side tokens buy orchestration; Grok does the long loop. See
[install/vps.md](install/vps.md).

## Related

- [install/en.md](install/en.md) — full install
- [install/fastmcp.md](install/fastmcp.md) — FastMCP proxy
- Root [README.md](../README.md)
