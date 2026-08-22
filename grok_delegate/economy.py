"""Token-economy helpers for host agents (Claude, Cursor, etc.).

Design goal: the *host* model should send short instructions and receive
compact receipts, while the heavy coding work runs on the local/VPS Grok CLI.
No OAuth or API keys are stored here.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

# Defaults applied when GROK_DELEGATE_ECONOMY is on and the client omits fields.
ECONOMY_DEFAULT_MAX_TURNS = 12
ECONOMY_DEFAULT_TIMEOUT_SECONDS = 600
ECONOMY_DEFAULT_REASONING = "low"
ECONOMY_MAX_SUMMARY = 1_500
ECONOMY_MAX_EVENTS = 4
ECONOMY_MAX_CHANGED_FILES = 24
ECONOMY_MAX_UNIFIED_DIFF = 16_384
#: What one tools/call answer may cost the host, whole. That is the assembled
#: MCP result -- structuredContent plus the compatibility content.text copy --
#: not the inner record. Measured on this tree: a 14 923 B fitted poll left
#: the server as a 32 345 B JSON-RPC result (2.17x) because the budget was
#: enforced on the inner object while the client paid for both copies.
#: indent=2 on the text copy was a second tax (1.72x on tools/list-shaped
#: payloads: 25 849 B compact vs 44 341 B). The duplicate stays -- old clients
#: read only content.text -- but it is compact, and the record is fitted so
#: the assembled result itself sits under this cap.
ECONOMY_MAX_RECORD = 16_384
_COMPACT_SEPARATORS = (",", ":")

_TRUE = frozenset({"1", "true", "yes", "on"})


def economy_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("GROK_DELEGATE_ECONOMY", "")).strip().lower() in _TRUE


def compact_poll_enabled(
    env: Mapping[str, str] | None = None, *, job_id: str | None = None
) -> bool:
    """Whether this record should be compacted.

    ``job_id`` is what makes the session-scoped answer possible: a navigator
    compacts its own job's polls and must not compact a neighbouring project's,
    and without the id the only honest answers were "everyone" or "no one" --
    the first leaked across projects, the second made session_begin's promise
    of a small receipt empty.
    """
    source = env if env is not None else os.environ
    raw = str(source.get("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "")).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in _TRUE:
        return True
    try:
        from .session import session_compact_active

        if session_compact_active(job_id):
            return True
    except Exception:
        pass
    return economy_enabled(source)


def default_reasoning_effort(env: Mapping[str, str] | None = None) -> str:
    """Effort the bridge picks when the caller named none.

    ``low`` suits the cheap-worker loop this module was written for, but it is a
    default, not a policy: an operator paying for a strong model wants that model
    thinking, and had no way to say so before.
    """
    from .guard import configured_reasoning_effort

    source = env if env is not None else os.environ
    return configured_reasoning_effort(source) or ECONOMY_DEFAULT_REASONING


def default_max_turns(env: Mapping[str, str] | None = None) -> int:
    """Turn budget the bridge picks when the caller named none.

    Raising effort without raising this trades one ceiling for another: the model
    reasons harder and still runs out of turns mid-task.
    """
    from .guard import configured_max_turns

    source = env if env is not None else os.environ
    return configured_max_turns(source) or ECONOMY_DEFAULT_MAX_TURNS


def apply_task_economy_defaults(task: dict[str, Any]) -> dict[str, Any]:
    """Fill missing budget fields with economy defaults (never override client)."""
    if not economy_enabled():
        return task
    out = dict(task)
    if "max_turns" not in out:
        out["max_turns"] = default_max_turns()
    if "timeout_seconds" not in out:
        out["timeout_seconds"] = ECONOMY_DEFAULT_TIMEOUT_SECONDS
    if "reasoning_effort" not in out:
        out["reasoning_effort"] = default_reasoning_effort()
    return out


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def _clip_bytes(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    clipped = encoded[: max(0, limit)].decode("utf-8", errors="replace")
    return clipped + "\n…(truncated)"


def compact_job_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Shrink a job/receipt for host-agent context windows."""
    if not compact_poll_enabled(job_id=str(record.get("job_id") or "").strip() or None):
        return dict(record)
    keep_keys = (
        "ok",
        "job_id",
        "state",
        "status",
        "transport",
        "lane",
        "correlation_id",
        "blocked_reason",
        "error",
        "error_code",
        "worktree_path",
        "branch",
        "summary",
        "changed_files",
        "full_changed_files",
        "artifacts",
        "tests",
        "tests_skipped_reason",
        "denied_tool_calls",
        "output_truncated",
        "lane_commit",
        "diffstat",
        "unified_diff",
        "started_at",
        "finished_at",
        "events",
    )
    # A finished job keeps its verdict inside `result`, while the envelope only
    # carries state. Compacting the envelope alone produced the worst possible
    # receipt -- state "error" next to error null -- so lift the fields that say
    # *why* before shrinking, without letting them overwrite an envelope value.
    record = dict(record)
    nested = record.get("result")
    if isinstance(nested, Mapping):
        # `full_changed_files` was in keep_keys and not in this list, so a
        # finished job's compact poll dropped it entirely -- and it is the field
        # that separates "what this run did" from "what is sitting in the lane".
        for key in ("status", "blocked_reason", "summary", "worktree_path", "branch",
                    "changed_files", "full_changed_files", "artifacts", "tests",
                    "tests_skipped_reason", "denied_tool_calls", "lane_commit",
                    "output_truncated", "diffstat", "unified_diff"):
            if record.get(key) in (None, "", [], {}) and nested.get(key) not in (None, "", [], {}):
                record[key] = nested[key]

    out: dict[str, Any] = {}
    for key in keep_keys:
        if key not in record:
            continue
        value = record[key]
        if key == "summary":
            out[key] = _clip(value, ECONOMY_MAX_SUMMARY)
        elif key in {"changed_files", "full_changed_files", "artifacts"} and isinstance(value, list):
            # Say what was left out. Eighty changed files arriving as twenty-four
            # with no count reads as "that is all this job touched", which is the
            # one thing a reviewer must not be wrong about.
            out[key] = value[:ECONOMY_MAX_CHANGED_FILES]
            if len(value) > ECONOMY_MAX_CHANGED_FILES:
                out[key + "_omitted"] = len(value) - ECONOMY_MAX_CHANGED_FILES
                out[key + "_total"] = len(value)
        elif key == "events" and isinstance(value, list):
            # Same bookkeeping as changed_files next to it: 64 events arriving
            # as the last four with events_total and events_omitted both None
            # reads as "that is all there was", which is the one thing a reader
            # must not be wrong about.
            out[key] = value[-ECONOMY_MAX_EVENTS:]
            if len(value) > ECONOMY_MAX_EVENTS:
                out["events_omitted"] = len(value) - ECONOMY_MAX_EVENTS
                out["events_total"] = len(value)
        elif key == "tests" and isinstance(value, list):
            slim = []
            for item in value[:16]:
                if not isinstance(item, Mapping):
                    continue
                # `outcome` travels or the distinction it exists for dies here:
                # a compact poll that kept only `passed` would hand the host
                # `passed: None` for a run that never happened, which is the
                # same ambiguity in a new place. `passed` is copied only when
                # the row has one.
                row = {
                    "command": _clip(item.get("command"), 200),
                    "returncode": item.get("returncode"),
                    "source": item.get("source"),
                }
                for field in ("outcome", "not_run_reason"):
                    if item.get(field) is not None:
                        row[field] = item[field]
                if "passed" in item:
                    row["passed"] = item["passed"]
                slim.append(row)
            out[key] = slim
        elif key == "diffstat":
            out[key] = _clip(value, 800)
        elif key == "unified_diff":
            out[key] = _clip_bytes(value, ECONOMY_MAX_UNIFIED_DIFF)
        else:
            out[key] = value
    out["economy_compact"] = True
    return _fit_record_budget(out, ECONOMY_MAX_RECORD, measure=tool_result_wire_size)


#: field -> the smallest length worth keeping, in the order a reader can most
#: afford to lose them. Events are already bounded and purely informational, a
#: summary is the agent's prose about work the diff shows anyway, and the diff
#: is the last thing to give because it is what the host came for.
#:
#: `tests` and `artifacts` are in the list because leaving them out did not make
#: them small: a job declaring many test commands carried a 2000-character
#: preview for each, and a job producing many files carried every path, and
#: neither could be cut -- so the record came back at 138 KB against a 16 KiB
#: budget with nothing said about it.
#: What gives way first when a poll will not fit, and how little of it may
#: remain. The diff moved ahead of the summary once the budget started counting
#: the compatibility copy: the cap then buys about half as much text, and the
#: old order spent it on 4 010 characters of raw diff while cutting the worker's
#: answer to 313. The diff is the one field a reader can always get in full --
#: it is on the lane branch, and the receipt says where.
_TRIM_ORDER: tuple[tuple[str, int], ...] = (
    ("events", 1), ("unified_diff", 512), ("diffstat", 200),
    ("summary", 1_200), ("full_changed_files", 4), ("changed_files", 4),
    ("artifacts", 4), ("tests", 1),
)

#: How much of one verifier run's output survives when the budget is tight. The
#: exit status and the command are what acceptance reads; the preview is for a
#: human, and a human can ask for the lane.
_TEST_PREVIEW_FLOOR = 200



def wire_size(value: Any) -> int:
    """What this payload actually costs on the wire, in bytes.

    Every stdio and HTTP write in `server.py` serialises with
    `ensure_ascii=False` and encodes UTF-8, so that is the only measurement the
    budget may use. Counting characters of the escaped form inflates Cyrillic
    roughly threefold and fails a poll that was inside the promise; counting
    characters of the unescaped form undercounts it, because Cyrillic is two
    bytes each. A live audit run tripped over both directions of that in one
    afternoon.
    """
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def assemble_tool_result(tool_result: Any) -> dict[str, Any]:
    """The MCP tools/call `result` the client is billed for.

    Old clients read only content[].text, so the duplicate of structuredContent
    stays. It does not have to be pretty: indent=2 cost 1.72x on tools/list-
    shaped payloads (25 849 B compact vs 44 341 B).
    """
    body = tool_result if isinstance(tool_result, Mapping) else {"ok": False}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    body, ensure_ascii=False, default=str, separators=_COMPACT_SEPARATORS
                ),
            }
        ],
        "structuredContent": body,
        "isError": not bool(body.get("ok")),
    }


def tool_result_wire_size(value: Any) -> int:
    """Bytes of the assembled tools/call result, both copies included.

    Dual copy does not make a 16 KiB envelope impossible -- it halves the
    inner record. Fitting the inner object to 16 KiB and then sending 32 KB
    was the lie; the 2.17x measured before this existed (14 923 B record -> 32 345 B
    assembled result) is what this counts. The JSON-RPC frame around it
    is a few dozen bytes more; the doubling is the compatibility copy,
    not the envelope.
    """
    return wire_size(assemble_tool_result(value))


def fit_poll_budget(record: dict[str, Any], cap: int = 0) -> dict[str, Any]:
    """Hold one poll under the budget whether or not compaction is on.

    `compact_job_record` only runs for hosts that asked for it, so the typed
    path had no ceiling at all: a live audit came back at 17,725 characters in a
    single poll against a promise of 16 KiB. The fat fields sit under `result`
    there and at the top level in compact mode, so both are fitted.

    The cap is the assembled tools/call result. A 14 923 B inner record was
    32 345 B on the wire (2.17x) when the budget ignored the compatibility
    copy; dual copy halves the inner object rather than doubling the bill.

    One pass, not two. Budgeting the nested receipt first and then the whole
    record again cut the same fields twice against different priority orders:
    the inner pass shrank a 6 000-char summary to 313 to protect the diff, the
    outer pass then dropped the diff anyway, and the result was 2 947 B against
    a 16 384 B budget.
    """
    cap = cap or ECONOMY_MAX_RECORD
    if tool_result_wire_size(record) <= cap:
        return record
    _fit_record_budget(record, cap, measure=tool_result_wire_size)
    # A trim inside `result` is a trim the reader has to know about, and the
    # reader is looking at the envelope: without this, a poll came back at
    # exactly the budget with nothing saying what had been cut to get there.
    nested = record.get("result")
    if isinstance(nested, dict):
        for key in ("economy_trimmed", "economy_dropped", "economy_budget_chars"):
            if key in nested and key not in record:
                record[key] = nested[key]
    return record


def _fit_record_budget(
    out: dict[str, Any],
    cap: int,
    *,
    measure: Any | None = None,
) -> dict[str, Any]:
    """Hold the whole record under one budget, not each field under its own.

    Per-field caps do not add up to a promise. ``ECONOMY_MAX_UNIFIED_DIFF`` alone
    is the entire poll budget, so a job with a large diff produced a "compact"
    record of 22 KB while every individual cap was respected -- and the host paid
    for it on the poll that mattered most, the last one.

    Trimming is ordered by what a reader can most afford to lose, and it is never
    silent: ``economy_trimmed`` names each field that gave way, because a receipt
    that quietly drops half a diff is worse than one that admits it.

    ``measure`` defaults to the inner record. Polls pass ``tool_result_wire_size``
    so the cap is the assembled tools/call result the client actually receives.
    """
    size_of = measure or wire_size

    def size() -> int:
        return size_of(out)

    if size() <= cap:
        return out

    def holders() -> "list[dict[str, Any]]":
        """Where the fat fields live: at the top level, or under `result`.

        Compaction lifts them up; the typed path leaves them nested. Only the
        top level was scanned, so an uncompacted poll skipped the ordered trim
        entirely and went straight to dropping whole fields by weight.
        """
        nested = out.get("result")
        return [out, nested] if isinstance(nested, dict) else [out]

    def bytes_per_char() -> float:
        """How much one cut character is worth against `measure`.

        The assembled result carries the record twice, so removing n characters
        removes about 2n bytes from it. Charging the full overage to a single
        copy overshot by that factor on every step and threw away content the
        budget could have paid for.
        """
        inner = wire_size(out)
        return max(1.0, size() / inner) if inner else 1.0

    trimmed: list[str] = []

    def note(key: str, owner: "dict[str, Any] | None" = None) -> None:
        if key not in trimmed:
            trimmed.append(key)
        # Markers are part of the record they describe. Adding them after the
        # last measurement is how a receipt came back nine bytes over the cap
        # it had just been fitted to -- and on the assembled result they cost
        # twice, once per copy.
        out["economy_trimmed"] = trimmed
        out["economy_budget_chars"] = cap
        # And on the record that actually lost the field: a host reading the
        # receipt alone must not see a shortened diff with nothing saying so.
        if owner is not None and owner is not out:
            owner["economy_trimmed"] = list(trimmed)
            owner["economy_budget_chars"] = cap

    # Test rows are mostly preview text, and the preview is the part a reader can
    # lose. Shrink it before dropping whole rows, so a job with many declared
    # commands still shows which ones ran and how they ended.
    for holder in holders():
        if size() <= cap or not isinstance(holder.get("tests"), list):
            continue
        for row in holder["tests"]:
            if isinstance(row, dict) and isinstance(row.get("output_preview"), str):
                preview = row["output_preview"]
                if len(preview) > _TEST_PREVIEW_FLOOR:
                    row["output_preview"] = preview[:_TEST_PREVIEW_FLOOR] + "…(truncated)"
                    note("tests", holder)

    for key, floor in _TRIM_ORDER:
        for _ in range(8):  # halving converges; the bound stops a pathological value
            over = size() - cap
            if over <= 0:
                break
            owner = next(
                (holder for holder in holders() if key in holder and holder[key]), None
            )
            if owner is None:
                break
            value = owner[key]
            need = max(1, int(over / bytes_per_char()))
            if isinstance(value, str) and len(value) > floor:
                # Same marker the per-field clip uses: one truncation convention
                # on the wire, and `economy_trimmed` to say the budget caused it.
                keep = max(floor, len(value) - need - 48)
                owner[key] = value[:keep].removesuffix("\n…(truncated)") + "\n…(truncated)"
            elif isinstance(value, list) and len(value) > floor:
                owner[key] = value[: max(floor, len(value) // 2)]
            else:
                break
            note(key, owner)
        if size() <= cap:
            break

    if size() > cap:
        # Everything the order allows has been cut and the record is still too
        # large. Names cannot help here -- each remaining field has a floor, and
        # `result` has to stay because the verdict lives in it -- so this pass
        # measures instead: drop the heaviest thing that is not an identifier,
        # and repeat. It converges because every step removes bytes.
        essential = {
            "ok", "job_id", "state", "status", "lane", "branch", "correlation_id",
            "blocked_reason", "error", "error_code", "worktree_path", "economy_compact",
            "lane_commit", "output_truncated", "tests_skipped_reason", "result",
        }
        dropped: list[str] = []
        for _ in range(64):
            if size() <= cap:
                break
            nested = out.get("result") if isinstance(out.get("result"), dict) else {}
            weights = [
                (wire_size(value), key, out)
                for key, value in out.items()
                if key not in essential
            ] + [
                (wire_size(value), key, nested)
                for key, value in nested.items()
                if key not in essential
            ]
            if not weights:
                break
            _, key, owner = max(weights)
            owner.pop(key, None)
            dropped.append(key)
            note(key)
        if dropped:
            out["economy_dropped"] = dropped

    if trimmed:
        out["economy_trimmed"] = trimmed
        out["economy_budget_chars"] = cap
    return out


def economy_playbook() -> dict[str, Any]:
    """Short host-agent playbook: save Claude/Cursor tokens by offloading work."""
    return {
        "ok": True,
        "economy": True,
        "prerequisite": (
            "Grok CLI must be installed and `grok login` completed on this host "
            "before any tool can do useful work. This MCP is only a bridge."
        ),
        "goal": (
            "Keep the host model thin: plan briefly, delegate coding to Grok on "
            "this machine/VPS, poll compact receipts, merge as a human."
        ),
        "do": [
            "Call grok_agent_status once per session (not every turn); it also "
            "reports whether the CLI is installed and logged in.",
            "Then grok_agent_session_begin, and execute only the card that "
            "grok_agent_session_next hands back, until it returns kind=end.",
            "Give session_begin project_root, expected_artifacts and "
            "test_commands: an execute card built without them guesses.",
            "Typed tools (consult / execute / poll / review) are the fallback "
            "for a host without the navigator, or when a card is rejected.",
            "Poll with grok_agent_poll using job_id; do not re-send the full goal.",
            "Read summary + changed_files + diffstat + bounded unified_diff + tests + worktree_path.",
            "Never request full event transcripts or raw tool dumps unless debugging.",
            "One job at a time; cancel stale jobs instead of stacking.",
        ],
        "dont": [
            "Don't paste large source trees into the objective — point at paths.",
            "Don't use execute for questions (use consult).",
            "Don't lower the worker's max_turns or reasoning_effort to save "
            "money: economy here means the host reads a compact receipt, not "
            "that Grok thinks less. Those two are the operator's to set.",
            "Don't put OAuth/API keys or GROK_AGENT_SECRET in MCP config.",
            "Don't push/merge from the agent — human reviews the grok/* branch.",
        ],
        "host_prompts": {
            "consult": "Answer in ≤12 bullets. No code dumps unless asked.",
            "execute": (
                "Implement only the listed artifacts. Stop when tests pass. "
                "Do not refactor unrelated files."
            ),
            "poll": "Return status, blocked_reason, changed_files, test pass flags only.",
        },
        "env": {
            "GROK_DELEGATE_ECONOMY": "1 enables lower default max_turns/timeout/reasoning",
            "GROK_DELEGATE_ECONOMY_COMPACT_POLL": "1 forces compact poll payloads",
        },
        "vps": (
            "Run this MCP on a VPS with Grok CLI logged in; connect Claude via "
            "stdio over SSH or bearer HTTP JSON-RPC behind TLS (not Streamable HTTP). "
            "Host tokens buy orchestration only; "
            "Grok does the long coding loop."
        ),
        "disclaimer": (
            "Unofficial community project. Not affiliated with, endorsed by, or "
            "supported by xAI, Grok, Anthropic, OpenAI, or Codex."
        ),
    }
