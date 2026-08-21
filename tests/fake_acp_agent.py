"""Fixture-driven ACP v1 subprocess used only by Round 8 integration tests."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def send(value: dict) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


cwd = Path.cwd()
session_id = "fixture-session"
agent_version = os.environ.get("GROK_FAKE_AGENT_VERSION", "0.2.118")
for raw in sys.stdin:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        continue
    method = message.get("method")
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": 1,
                    "agentCapabilities": {},
                    "authMethods": [],
                    "_meta": {"agentVersion": agent_version},
                },
            }
        )
    elif method == "session/new":
        cwd = Path(message["params"]["cwd"])
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"sessionId": session_id}})
    elif method == "session/prompt":
        prompt = message["params"]["prompt"][0]["text"]
        if "OVERSIZED_FIXTURE" in prompt:
            send({
                "jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "X" * 20000}},
                },
            })
            continue
        if "RESENT_OUTPUT_FIXTURE" in prompt:
            # What Grok actually does with a running command: resend the whole
            # accumulated buffer under the same toolCallId every time it grows.
            buffer = ""
            for index in range(120):
                buffer += f"line {index:04d} of a long test run" + chr(10)
                send({
                    "jsonrpc": "2.0", "method": "session/update", "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "call-running",
                            "status": "in_progress",
                            "rawOutput": {"command": "pytest", "output_preview": buffer},
                        },
                    },
                })
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
            continue
        if "DEEP_FRAME_FIXTURE" in prompt:
            # Deep enough to break `json.loads` on CPython 3.14 (5000 parses,
            # 20000 raises), sent as one line the reader has to survive.
            depth = 20000
            sys.stdout.write("{" + chr(34) + "a" + chr(34) + ":" * 1 + ("{" + chr(34) + "a" + chr(34) + ":") * (depth - 1) + "1" + "}" * depth + chr(10))
            sys.stdout.flush()
            send({
                "jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"type": "text", "text": "survived"}},
                },
            })
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
            continue
        if "CHUNKED_FIXTURE" in prompt:
            # The shape that killed honest long answers: 200 frames of envelope
            # carrying twenty bytes of text each.
            for index in range(200):
                send({
                    "jsonrpc": "2.0", "method": "session/update", "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": f"chunk {index:05d} ..."},
                        },
                    },
                })
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
            continue
        if "OVERSIZED_ACK_FIXTURE" in prompt:
            send({
                "jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Y" * 20000}},
                },
            })
            for followup in sys.stdin:
                cancel = json.loads(followup)
                if cancel.get("method") == "session/cancel":
                    send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "cancelled"}})
                    break
            continue
        if "ENVELOPE_FLOOD_FIXTURE" in prompt:
            # No payload at all, forever: the case the wire backstop exists for.
            index = 0
            while True:
                index += 1
                send({
                    "jsonrpc": "2.0", "method": "session/update", "params": {
                        "sessionId": session_id,
                        "update": {"sessionUpdate": "agent_thought_chunk", "toolCallId": f"t{index}"},
                    },
                })
            continue
        if "SECRET_FIXTURE" in prompt:
            send({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": session_id,
                "update": {"sessionUpdate": "agent_message_chunk", "content": {
                    "type": "text", "text": "Authorization: Bearer planted-token Password: planted-pass",
                }},
            }})
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
            continue
        if "MALFORMED_FIXTURE" in prompt:
            for _ in range(4):
                sys.stdout.write("not-json\n")
            sys.stdout.flush()
            continue
        if "CANCEL_FIXTURE" in prompt:
            for followup in sys.stdin:
                cancel = json.loads(followup)
                if cancel.get("method") == "session/cancel":
                    send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "cancelled"}})
                    break
            continue
        if "CANCEL_IGNORED_FIXTURE" in prompt:
            for _followup in sys.stdin:
                pass
            continue
        if "Role: execute" in prompt or "Role: fix" in prompt:
            match = re.search(r"Expected artifacts:\n- ([^\r\n]+)", prompt)
            artifact = match.group(1).strip() if match else "fake-output.txt"
            target = cwd / artifact
            send({
                "jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call", "toolCallId": "fixture-write",
                        "title": "Write fixture", "rawInput": {"file_path": str(target)},
                    },
                },
            })
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 100,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": session_id,
                        "toolCall": {
                            "toolCallId": "fixture-write",
                            "kind": "edit",
                            "title": "Write fixture",
                        },
                        "options": [
                            {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
                            {"optionId": "reject-once", "name": "No", "kind": "reject_once"},
                        ],
                    },
                }
            )
            decision = json.loads(sys.stdin.readline())
            selected = (((decision.get("result") or {}).get("outcome") or {}).get("optionId"))
            if selected == "allow-once":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("ROUND8_FAKE_OK\n", encoding="utf-8")
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": "fixture-test",
                                "status": "completed",
                                "rawInput": {"command": "python -m pytest -q"},
                                "rawOutput": {
                                    "command": "python -m pytest -q",
                                    "exit_code": 0,
                                    "timed_out": False,
                                    "output_for_prompt": "1 passed",
                                },
                            },
                        },
                    }
                )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "ROUND8_FAKE_DONE"},
                    },
                },
            }
        )
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
