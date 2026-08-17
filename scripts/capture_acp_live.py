#!/usr/bin/env python3
"""Capture a redacted live ACP session from whatever Grok CLI is installed.

The bridge's frame handling was written by watching Grok 0.2.118, and comments
in ``acp.py`` still cite that build.  A comment is not evidence: the only way to
know the parsing still matches the agent is to drive the same sequence the
bridge drives -- initialize, session/new, session/prompt, request_permission,
session/cancel -- against the CLI actually on this machine and keep what came
back.

The capture is deliberately hostile to itself: every permission request is
DENIED, so the agent is never allowed to touch the filesystem, and the whole run
is bounded by a wall clock.  The result is a fixture tests can replay, not a
version to pin.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grok_delegate.acp import StdioACPTransport  # noqa: E402

OUT_DIR = ROOT / "evidence" / "live-acp"

#: Total wall clock for the whole session. A capture that hangs is a failed
#: capture, never a hung terminal.
DEFAULT_BUDGET_SECONDS = 180.0

#: Frames kept. Grok streams thought chunks; the interesting shapes arrive
#: early and the file has to stay reviewable by a human.
MAX_FRAMES = 400

#: Per-kind cap on the streaming chunks. Grok sends dozens of near-identical
#: agent_thought_chunk frames; the twentieth proves nothing the first proved
#: and turns the fixture into 130KB nobody will read. What was dropped is
#: counted in session_update_kinds, so the file stays honest about it.
MAX_CHUNKS_PER_KIND = 4
_CHUNK_KINDS = ("agent_thought_chunk", "agent_message_chunk", "user_message_chunk")

#: `available_commands_update` carries Grok's whole slash-command catalogue --
#: 83KB of the first capture, for an update the bridge drops unread. Keep one,
#: with the list cut to a sample, so the shape survives and the bulk does not.
_CATALOGUE_KINDS = ("available_commands_update",)
MAX_CATALOGUE_ENTRIES = 3

#: One scenario per shape the bridge has to parse. Each names the prompt, what
#: the client does with a permission request, and whether it cancels mid-turn.
#: Splitting them keeps every fixture small enough to read.
SCENARIOS: dict[str, dict[str, Any]] = {
    "permission-cancel": {
        "prompt": (
            "Read README.md in this directory, then write a new file NOTES.md containing "
            "one line summarising it. Use your file tools directly."
        ),
        "answer": "reject_once",
        "cancel_after_first_permission": True,
    },
    "consult": {
        # Nothing to permit and nothing to cancel: this is the shape a finished
        # turn has, which is where the receipt summary comes from.
        "prompt": "Read app.py and reply with one sentence describing what add() does.",
        "answer": "reject_once",
        "cancel_after_first_permission": False,
    },
    "command": {
        # The only scenario that lets a tool run, and only the test command the
        # bridge would have allowed anyway.
        # Worded to add nothing of its own: what the bridge has to match against
        # is the command the CLI actually asks permission for, so the prompt must
        # not be the reason that command grew a suffix.
        "prompt": (
            "Run exactly this command with your shell tool and nothing else: python -m pytest -q"
        ),
        "answer": "allow_once",
        "cancel_after_first_permission": False,
    },
}

_SECRET_KEYS = ("token", "secret", "authorization", "signature", "hostname", "apikey", "api_key")
_SECRET_TEXT = re.compile(
    r"(?i)\b(authorization|bearer|api[_-]?key|token|secret|password)\b\s*[:=]?\s*\S{0,80}"
)


def _redact_text(value: str, *, cwd: str, home: str) -> str:
    out = value
    for needle, replacement in ((cwd, "<CWD>"), (home, "<HOME>")):
        if needle:
            out = out.replace(needle, replacement)
            out = out.replace(needle.replace("\\", "/"), replacement)
            out = out.replace(needle.replace("\\", "\\\\"), replacement)
    return _SECRET_TEXT.sub(r"\1 <REDACTED>", out)


def _redact(obj: Any, *, cwd: str, home: str, session_ids: dict[str, str]) -> Any:
    """Keep the shape, drop the secrets, stabilise the identifiers.

    ``toolCallId`` survives on purpose: the two-frame permission join is keyed
    on it, so a fixture that scrubbed it could not prove the join works.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            low = str(key).lower()
            if any(part in low for part in _SECRET_KEYS):
                out[key] = "<REDACTED>"
            elif low in {"cwd", "currentworkingdirectory"}:
                out[key] = "<CWD>"
            elif low == "sessionid" and isinstance(value, str):
                out[key] = session_ids.setdefault(value, "<SESSION_%d>" % len(session_ids))
            elif low in {"agentinstanceid", "agentid"}:
                out[key] = "<%s>" % str(key).upper()
            else:
                out[key] = _redact(value, cwd=cwd, home=home, session_ids=session_ids)
        return out
    if isinstance(obj, list):
        return [_redact(item, cwd=cwd, home=home, session_ids=session_ids) for item in obj[:32]]
    if isinstance(obj, str):
        return _redact_text(obj, cwd=cwd, home=home)[:2_000]
    return obj


def _trim_catalogue(message: dict[str, Any]) -> dict[str, Any]:
    """Keep the shape of a catalogue update, drop the catalogue."""
    out = json.loads(json.dumps(message))
    update = (out.get("params") or {}).get("update") or {}
    for key, value in list(update.items()):
        if isinstance(value, list) and len(value) > MAX_CATALOGUE_ENTRIES:
            update[key] = value[:MAX_CATALOGUE_ENTRIES] + [
                {"_omitted": len(value) - MAX_CATALOGUE_ENTRIES}
            ]
    return out


def _seed_repo(root: Path) -> None:
    """A small real repository, so a read/write request has something to name."""
    (root / "README.md").write_text("# capture fixture\n\nOne line.\n", encoding="utf-8")
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8"
    )
    for argv in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=capture@local", "-c", "user.name=capture", "add", "-A"],
        [
            "git", "-c", "user.email=capture@local", "-c", "user.name=capture",
            "commit", "-q", "-m", "seed",
        ],
    ):
        subprocess.run(argv, cwd=str(root), capture_output=True, text=True, timeout=30, check=False)


def _task(max_turns: int) -> dict[str, Any]:
    """The same packet shape the bridge builds argv from -- model deliberately empty."""
    return {
        "max_turns": max_turns,
        "reasoning_effort": os.environ.get("GROK_DELEGATE_REASONING_EFFORT", "low"),
        "model": os.environ.get("GROK_DELEGATE_MODEL", ""),
    }


def capture(*, budget: float, max_turns: int, scenario: str) -> dict[str, Any]:
    plan = SCENARIOS[scenario]
    transport = StdioACPTransport()
    argv = transport.build_argv(_task(max_turns))
    home = str(Path.home())
    session_ids: dict[str, str] = {}
    frames: list[dict[str, Any]] = []
    observed: dict[str, Any] = {
        "scenario": scenario,
        "permission_requests": 0,
        "permission_without_rawinput": 0,
        "permission_tool_kinds": [],
        "tool_call_ids_with_rawinput": [],
        "unknown_methods": [],
        "session_update_kinds": {},
        "cancel_sent": False,
        "stop_reason": None,
        "agent_version": None,
        "protocol_version": None,
        "notes": [],
    }

    with tempfile.TemporaryDirectory() as raw:
        cwd = Path(raw).resolve()
        _seed_repo(cwd)
        cwd_text = str(cwd)
        popen_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(  # noqa: S603 - argv comes from the bridge itself
            argv,
            cwd=cwd_text,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **popen_kwargs,
        )
        stderr_tail: list[str] = []

        def drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_tail.append(line.rstrip()[:500])
                del stderr_tail[:-20]

        threading.Thread(target=drain_stderr, daemon=True).start()

        def send(message: dict[str, Any]) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            proc.stdin.flush()
            frames.append(
                {"dir": "->", **_redact(message, cwd=cwd_text, home=home, session_ids=session_ids)}
            )

        deadline = time.monotonic() + budget
        tool_state: dict[str, dict[str, Any]] = {}
        session_id = ""

        def pump(wanted: int | None) -> dict[str, Any] | None:
            """Read until the wanted response arrives, answering permission on the way."""
            assert proc.stdout is not None
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if line == "":
                    observed["notes"].append("agent closed stdout")
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    observed["notes"].append("malformed frame")
                    continue
                if not isinstance(message, dict):
                    continue
                method = message.get("method")
                keep = len(frames) < MAX_FRAMES
                if method == "session/update":
                    update = (message.get("params") or {}).get("update") or {}
                    kind = str(update.get("sessionUpdate") or "?")
                    seen = observed["session_update_kinds"].get(kind, 0) + 1
                    observed["session_update_kinds"][kind] = seen
                    if kind in _CHUNK_KINDS and seen > MAX_CHUNKS_PER_KIND:
                        keep = False
                    if kind in _CATALOGUE_KINDS:
                        keep = keep and seen == 1
                        message = _trim_catalogue(message)
                if keep:
                    frames.append(
                        {
                            "dir": "<-",
                            **_redact(message, cwd=cwd_text, home=home, session_ids=session_ids),
                        }
                    )
                if method == "session/update":
                    update = (message.get("params") or {}).get("update") or {}
                    tool_id = str(update.get("toolCallId") or "")
                    if tool_id and kind in {"tool_call", "tool_call_update"}:
                        tool_state.setdefault(tool_id, {}).update(dict(update))
                        known = observed["tool_call_ids_with_rawinput"]
                        if update.get("rawInput") and tool_id not in known:
                            known.append(tool_id)
                    continue
                if method == "session/request_permission" and "id" in message:
                    params = message.get("params") or {}
                    tool = params.get("toolCall") or {}
                    observed["permission_requests"] += 1
                    if not tool.get("rawInput"):
                        observed["permission_without_rawinput"] += 1
                    observed["permission_tool_kinds"].append(str(tool.get("kind") or "?"))
                    # Anything the scenario did not ask for is denied, so a
                    # capture can never become a way to run arbitrary tools.
                    wanted_kind = str(plan["answer"])
                    if wanted_kind == "allow_once" and str(tool.get("kind")) != "execute":
                        wanted_kind = "reject_once"
                    choice = next(
                        (
                            option
                            for option in (params.get("options") or [])
                            if option.get("kind") == wanted_kind
                        ),
                        None,
                    )
                    outcome = (
                        {"outcome": "selected", "optionId": choice["optionId"]}
                        if choice
                        else {"outcome": "cancelled"}
                    )
                    send({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": outcome}})
                    if (
                        plan["cancel_after_first_permission"]
                        and observed["permission_requests"] == 1
                        and session_id
                    ):
                        send(
                            {
                                "jsonrpc": "2.0",
                                "method": "session/cancel",
                                "params": {"sessionId": session_id},
                            }
                        )
                        observed["cancel_sent"] = True
                    continue
                if isinstance(method, str):
                    if method not in observed["unknown_methods"]:
                        observed["unknown_methods"].append(method)
                    continue
                if wanted is not None and message.get("id") == wanted:
                    return message
            observed["notes"].append("budget exhausted")
            return None

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "grok-delegate-live-capture", "version": "probe"},
                    },
                }
            )
            init = pump(0) or {}
            body = init.get("result") or {}
            observed["protocol_version"] = body.get("protocolVersion")
            observed["agent_version"] = (body.get("_meta") or {}).get("agentVersion")

            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session/new",
                    "params": {"cwd": cwd_text, "mcpServers": []},
                }
            )
            new = pump(1) or {}
            session_id = str((new.get("result") or {}).get("sessionId") or "")
            if not session_id:
                observed["notes"].append("session/new returned no sessionId")
            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session/prompt",
                        "params": {
                            "sessionId": session_id,
                            "prompt": [{"type": "text", "text": str(plan["prompt"])}],
                        },
                    }
                )
                prompt = pump(2) or {}
                observed["stop_reason"] = (prompt.get("result") or {}).get("stopReason")
                if "error" in prompt:
                    observed["notes"].append("session/prompt returned an error frame")
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - kill already sent
                pass

    observed["stderr_tail"] = [_redact_text(line, cwd="", home=home) for line in stderr_tail[-5:]]
    observed["frame_count"] = len(frames)
    return {"frames": frames, "observed": observed}


def _cli_version() -> str:
    try:
        done = subprocess.run(
            ["grok", "--version"], capture_output=True, text=True, timeout=20, check=False
        )
        return (done.stdout or done.stderr or "").strip().splitlines()[0][:120]
    except Exception:  # pragma: no cover - reporting only
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a live redacted ACP session.")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="permission-cancel")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    result = capture(budget=args.budget, max_turns=args.max_turns, scenario=args.scenario)
    observed = result["observed"]
    observed["cli"] = _cli_version()
    observed["captured_by"] = "scripts/capture_acp_live.py"
    observed["pin_policy"] = "evidence for the installed CLI; the bridge stays unpinned"

    out_path = Path(args.out) if args.out else OUT_DIR / ("session-%s.jsonl" % args.scenario)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(frame, separators=(",", ":")) for frame in result["frames"]) + "\n",
        encoding="utf-8",
    )
    out_path.with_suffix(".observed.json").write_text(
        json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(observed, indent=2, sort_keys=True))
    return 0 if observed.get("protocol_version") == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
