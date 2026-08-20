"""Why a job has gone quiet, when the answer is not on the wire.

The Grok CLI swallows provider failures and retries them itself -- up to fifteen
times with backoff -- and emits nothing over ACP while it does. From the bridge
the job looks identical to one that is thinking hard: session created, prompt
delivered, silence. An operator watching that cancelled a job forty seconds in,
having no way to know the model had answered

    500 Internal Server Error: The model is currently at capacity due to high
    demand.

and the CLI was already backing off before attempt two.

The bridge cannot see the provider. It can see two things the operator was
guessing at: how long the silence has lasted, and -- best effort -- what the CLI
itself wrote in its own log about the process we spawned. `worker_pid` joins the
two, and a record we cannot read is simply no answer rather than an error.

Set ``GROK_DELEGATE_CLI_LOG=0`` to never read that file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: Only the tail is ever read: this file is shared by every Grok session on the
#: machine and grows without a bound we control.
MAX_TAIL_BYTES = 256_000
MAX_RECORDS = 400
#: Below this a quiet job is just a job thinking, and saying anything would be
#: noise on every poll.
SILENCE_HINT_SECONDS = 45.0


def cli_log_path() -> Path:
    configured = (os.environ.get("GROK_DELEGATE_CLI_LOG_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".grok" / "logs" / "unified.jsonl"


def cli_log_enabled() -> bool:
    return (os.environ.get("GROK_DELEGATE_CLI_LOG") or "1").strip() != "0"


def _tail_records(path: Path) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > MAX_TAIL_BYTES:
                handle.seek(size - MAX_TAIL_BYTES)
                handle.readline()  # discard the partial line the seek landed in
            raw = handle.read()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[-MAX_RECORDS:]:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def diagnose_worker(worker_pid: Any) -> dict[str, Any] | None:
    """What the CLI said about this process, or None when it said nothing.

    Best effort on purpose. This reads a file another program owns, in a format
    nobody promised us, so every failure here is "no answer" -- never an error
    the operator has to care about.
    """
    if not cli_log_enabled():
        return None
    try:
        pid = int(worker_pid)
    except (TypeError, ValueError):
        return None
    path = cli_log_path()
    if not path.is_file():
        return None

    latest: dict[str, Any] | None = None
    # The record that names the cause and the record that happened last are
    # usually not the same one: the CLI logs `inference_retry` with the provider
    # error, then `inference_failed` with no context at all. Taking the newest
    # threw the only sentence worth reading away.
    explained: dict[str, Any] | None = None
    retries = 0
    for record in _tail_records(path):
        if record.get("pid") != pid:
            continue
        if str(record.get("lvl") or "").lower() not in {"warn", "error", "fatal"}:
            continue
        if "retry" in str(record.get("msg") or ""):
            retries += 1
        latest = record
        context = record.get("ctx")
        if isinstance(context, dict) and str(context.get("reason") or "").strip():
            explained = record
    if latest is None:
        return None

    from .contracts import redact_text

    source = explained or latest
    context = source.get("ctx") if isinstance(source.get("ctx"), dict) else {}
    reason = redact_text(str(context.get("reason") or source.get("msg") or ""))[:400]
    out: dict[str, Any] = {
        "source": "grok-cli-log",
        "event": str(source.get("msg") or "")[:120],
        "at": str(latest.get("ts") or "")[:64],
        "detail": reason,
        "retries_seen": retries,
    }
    for key in ("attempt", "max_retries"):
        if isinstance(context.get(key), int):
            out[key] = context[key]
    lowered = reason.lower()
    if "at capacity" in lowered or "high demand" in lowered:
        # Named so a host can tell "the provider is full" from "your job is
        # broken". Waiting is the fix, and it is not the operator's mistake.
        out["reason"] = "PROVIDER_AT_CAPACITY"
        out["hint"] = (
            "The model is refusing new work, not failing on this task. The CLI "
            "retries with backoff on its own; give the job a timeout that can "
            "outlast that, or name another model in task.model."
        )
    elif retries:
        out["reason"] = "AGENT_RETRYING"
        out["hint"] = "The CLI is retrying on its own; the silence is backoff, not a hang."
    return out


__all__ = [
    "MAX_RECORDS",
    "MAX_TAIL_BYTES",
    "SILENCE_HINT_SECONDS",
    "cli_log_enabled",
    "cli_log_path",
    "diagnose_worker",
]
