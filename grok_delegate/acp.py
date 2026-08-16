"""ACP v1 clients for the Grok Build CLI.

The stdio implementation is deliberately small and fixture-driven: UTF-8
newline-delimited JSON-RPC, one session per task, explicit permission decisions,
bounded events, bounded cancellation and process-tree cleanup.  WebSocket lives
behind the same result contract and is added after its live protocol spike.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote, urlparse

from .contracts import EVENT_SCHEMA_ID, MAX_EVENTS, bounded_event, build_prompt, redact_text as _redact_text
from .guard import ALWAYS_APPROVE_FLAG, GuardError, SERVER_VERSION, validate_grok_bin

ACP_PROTOCOL_VERSION = 1

# Agent version this bridge *optionally* compares against.
#
# Default is unpinned: any installed Grok CLI that speaks ACP v1 is accepted.
# A hardcoded pin silently kills the typed dispatch path the moment the CLI is
# upgraded, while `grok doctor` stays green because it never opens an ACP session.
# Operators who still want a pin set GROK_DELEGATE_EXPECTED_AGENT_VERSION; a
# mismatch is a warning event, never a handshake failure.
#
# GROK_DELEGATE_EXPECTED_AGENT_VERSION="" / "any" / "*" / "off" also means unpinned.
def expected_agent_version() -> str | None:
    configured = os.environ.get("GROK_DELEGATE_EXPECTED_AGENT_VERSION")
    if configured is None:
        return DEFAULT_EXPECTED_AGENT_VERSION
    configured = configured.strip()
    if configured == "" or configured.lower() in {"any", "*", "off", "none"}:
        return None
    return configured


DEFAULT_EXPECTED_AGENT_VERSION: str | None = None
_expected_agent_version = expected_agent_version

# Distinguishes "caller did not pass the argument" (resolve from env at construction time)
# from an explicit `None` (caller deliberately disabled the check).
_EXPECTED_SENTINEL: Any = object()
DEFAULT_OUTPUT_BYTES = 1_000_000
MAX_MALFORMED_FRAMES = 3
MAX_WS_FRAME_BYTES = 2_000_000
CANCEL_GRACE_SECONDS = 5.0

def _model_argv(task: Mapping[str, Any]) -> list[str]:
    """``--model <id>`` when the task names one, nothing when it does not.

    An absent model is a deliberate deferral to the CLI's own default, so the
    flag has to disappear from argv entirely; forwarding ``str(None)`` would ask
    the CLI for a model literally called "None".
    """
    model = str(task.get("model") or "").strip()
    return ["--model", model] if model else []


EventSink = Callable[[dict[str, Any]], None]


class TransportAdapter(Protocol):
    name: str

    def run(
        self,
        task: Mapping[str, Any],
        *,
        cwd: Path,
        cancel_event: threading.Event,
        event_sink: EventSink | None = None,
    ) -> dict[str, Any]: ...


class ACPError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class StdioACPTransport:
    name = "stdio"

    def __init__(
        self,
        *,
        grok_bin: str = "grok",
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        expected_agent_version: Any = _EXPECTED_SENTINEL,
        output_byte_cap: int = DEFAULT_OUTPUT_BYTES,
    ) -> None:
        self.grok_bin = validate_grok_bin(grok_bin, from_client=False)
        self.popen_factory = popen_factory
        self.expected_agent_version = (
            _expected_agent_version()
            if expected_agent_version is _EXPECTED_SENTINEL
            else expected_agent_version
        )
        self.output_byte_cap = max(16_384, min(int(output_byte_cap), 8_000_000))

    def build_argv(self, task: Mapping[str, Any]) -> list[str]:
        resolved = shutil.which(self.grok_bin) or self.grok_bin
        argv = [
            resolved,
            "--permission-mode",
            "default",
            "--max-turns",
            str(task["max_turns"]),
            "--no-subagents",
            "--disable-web-search",
            "agent",
            "--no-leader",
        ]
        # No model means "whatever the CLI defaults to", so say nothing rather
        # than forwarding the string "None" as a model id.
        argv += _model_argv(task)
        argv += [
            "--reasoning-effort",
            str(task["reasoning_effort"]),
            "stdio",
        ]
        assert_safe_acp_argv(argv)
        return argv

    def run(
        self,
        task: Mapping[str, Any],
        *,
        cwd: Path,
        cancel_event: threading.Event,
        event_sink: EventSink | None = None,
    ) -> dict[str, Any]:
        cwd = Path(cwd).resolve(strict=True)
        argv = self.build_argv(task)
        started = _utc_now()
        flags = 0
        popen_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            popen_kwargs["creationflags"] = flags
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = self.popen_factory(
                argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **popen_kwargs,
            )
        except FileNotFoundError as exc:
            raise ACPError("GROK_MISSING", f"grok binary not found: {self.grok_bin}") from exc
        except OSError as exc:
            raise ACPError("ACP_SPAWN_FAILED", str(exc)) from exc

        process_job = _WindowsKillJob(proc)
        inbound: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=32)
        reader_overflow = threading.Event()
        reader_stop = threading.Event()
        reader_budget = {"remaining": self.output_byte_cap}
        reader_budget_lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_line_reader,
                args=(
                    proc.stdout, "stdout", inbound, self.output_byte_cap,
                    reader_overflow, reader_budget, reader_budget_lock, reader_stop,
                ),
                daemon=True,
            ),
            threading.Thread(
                target=_line_reader,
                args=(
                    proc.stderr, "stderr", inbound, min(self.output_byte_cap, 64_000),
                    reader_overflow, reader_budget, reader_budget_lock, reader_stop,
                ),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()

        events: list[dict[str, Any]] = []
        text_chunks: list[str] = []
        tests: list[dict[str, Any]] = []
        tool_state: dict[str, dict[str, Any]] = {}
        session_id: str | None = None
        output_bytes = 0
        malformed = 0
        request_id = 0
        cancelled = False
        cancel_deadline: float | None = None
        timed_out = False
        deadline = time.monotonic() + float(task["timeout_seconds"])
        stderr_tail: list[str] = []

        def emit(kind: str, payload: Mapping[str, Any]) -> None:
            event = {
                "schema_version": EVENT_SCHEMA_ID,
                "sequence": len(events),
                "kind": kind[:128],
                "at": _utc_now(),
                "payload": bounded_event(payload),
            }
            if len(events) >= MAX_EVENTS:
                events.pop(0)
                for index, existing in enumerate(events):
                    existing["sequence"] = index
                event["sequence"] = len(events)
            events.append(event)
            if event_sink is not None:
                try:
                    event_sink(event)
                except Exception:
                    pass

        def send(message: Mapping[str, Any]) -> None:
            raw = json.dumps(dict(message), ensure_ascii=False, separators=(",", ":"))
            if proc.stdin is None:
                raise ACPError("ACP_DISCONNECTED", "stdin closed")
            try:
                proc.stdin.write(raw + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ACPError("ACP_DISCONNECTED", "agent stdin closed") from exc

        def await_response(wanted: int, phase: str) -> dict[str, Any]:
            nonlocal output_bytes, malformed, session_id, cancelled, timed_out, cancel_deadline
            while True:
                if reader_overflow.is_set():
                    raise ACPError("ACP_OUTPUT_LIMIT", "agent output exceeded reader or queue cap")
                if cancel_deadline is not None and time.monotonic() >= cancel_deadline:
                    raise ACPError("ACP_CANCEL_TIMEOUT", "agent did not acknowledge cancellation")
                if time.monotonic() >= deadline:
                    timed_out = True
                    if session_id and not cancelled:
                        send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
                        cancelled = True
                    raise ACPError("ACP_TIMEOUT", f"timeout during {phase}")
                if cancel_event.is_set() and session_id and not cancelled:
                    send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
                    emit("cancel_sent", {"sessionId": session_id})
                    cancelled = True
                    cancel_deadline = time.monotonic() + CANCEL_GRACE_SECONDS
                if cancel_event.is_set() and session_id is None:
                    raise ACPError("ACP_CANCELLED", f"cancelled during {phase}")
                if proc.poll() is not None and inbound.empty():
                    raise ACPError("ACP_PROCESS_EXITED", f"agent exited with code {proc.returncode} during {phase}")
                try:
                    channel, raw = inbound.get(timeout=0.1)
                except queue.Empty:
                    continue
                output_bytes += len(raw.encode("utf-8", errors="replace"))
                if output_bytes > self.output_byte_cap:
                    raise ACPError("ACP_OUTPUT_LIMIT", "agent output exceeded configured cap")
                if channel == "stderr":
                    if raw:
                        stderr_tail.append(_redact_text(raw)[:500])
                        del stderr_tail[:-20]
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    malformed += 1
                    if malformed > MAX_MALFORMED_FRAMES:
                        raise ACPError("ACP_MALFORMED_JSON", "too many malformed JSON-RPC frames")
                    continue
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    malformed += 1
                    if malformed > MAX_MALFORMED_FRAMES:
                        raise ACPError("ACP_MALFORMED_JSONRPC", "too many invalid JSON-RPC frames")
                    continue

                method = message.get("method")
                if method == "session/request_permission" and "id" in message:
                    permission_params = _permission_params_with_tool_state(
                        message.get("params") or {}, tool_state
                    )
                    decision = permission_decision(permission_params, task, cwd)
                    send({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": decision}})
                    tool = ((message.get("params") or {}).get("toolCall") or {})
                    emit(
                        "permission",
                        {
                            "decision": decision,
                            "tool": {
                                "kind": tool.get("kind"),
                                "title": tool.get("title"),
                                "toolCallId": tool.get("toolCallId"),
                            },
                            "optionKinds": [
                                option.get("kind")
                                for option in ((message.get("params") or {}).get("options") or [])
                            ],
                        },
                    )
                    continue
                if method == "session/update":
                    update = (message.get("params") or {}).get("update") or {}
                    _consume_update(update, text_chunks=text_chunks, tests=tests, tool_state=tool_state)
                    compact = _compact_session_update(update)
                    if compact is not None:
                        emit("session_update", compact)
                    continue
                if isinstance(method, str):
                    # Grok 0.2.118 emits private notifications. Preserve only
                    # bounded/redacted event metadata; never treat them as success.
                    emit("notification", {"method": method})
                    continue
                if message.get("id") == wanted:
                    if "error" in message:
                        error = message.get("error") or {}
                        raise ACPError("ACP_REMOTE_ERROR", str(error.get("message") or error))
                    return message

        result: dict[str, Any]
        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": ACP_PROTOCOL_VERSION,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "grok-delegate", "version": SERVER_VERSION},
                    },
                }
            )
            init = await_response(request_id, "initialize")
            init_result = init.get("result") or {}
            if init_result.get("protocolVersion") != ACP_PROTOCOL_VERSION:
                raise ACPError("ACP_VERSION_MISMATCH", "agent did not negotiate ACP protocol version 1")
            agent_version = ((init_result.get("_meta") or {}).get("agentVersion"))
            if self.expected_agent_version and agent_version != self.expected_agent_version:
                emit(
                    "version_mismatch",
                    {
                        "expected": self.expected_agent_version,
                        "got": agent_version,
                        "blocking": False,
                    },
                )
            emit("initialized", {"protocolVersion": 1, "agentVersion": agent_version})

            request_id += 1
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "session/new",
                    "params": {"cwd": str(cwd), "mcpServers": []},
                }
            )
            session = await_response(request_id, "session/new")
            session_id = str((session.get("result") or {}).get("sessionId") or "")
            if not session_id:
                raise ACPError("ACP_SESSION_INVALID", "session/new returned no sessionId")
            emit("session_created", {"sessionId": session_id})

            request_id += 1
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": build_prompt(task)}],
                    },
                }
            )
            prompt = await_response(request_id, "session/prompt")
            stop_reason = str((prompt.get("result") or {}).get("stopReason") or "")
            status = "completed" if stop_reason == "end_turn" else (
                "cancelled" if stop_reason == "cancelled" or cancelled else "failed"
            )
            result = {
                "status": status,
                "session_id": session_id,
                "stop_reason": stop_reason,
                "summary": _redact_text("".join(text_chunks))[:16_000],
                "tests": tests,
                "events": events,
                "agent_version": agent_version,
                "worker_pid": proc.pid,
                "agent_pid": proc.pid,
                "started_at": started,
                "finished_at": _utc_now(),
                "blocked_reason": None if status == "completed" else f"ACP_STOP_{stop_reason or 'UNKNOWN'}",
            }
        except ACPError as exc:
            result = {
                "status": "cancelled" if exc.code == "ACP_CANCELLED" or cancel_event.is_set() else "failed",
                "session_id": session_id,
                "summary": _redact_text("".join(text_chunks))[:16_000],
                "tests": tests,
                "events": events,
                "worker_pid": proc.pid,
                "agent_pid": proc.pid,
                "started_at": started,
                "finished_at": _utc_now(),
                "blocked_reason": exc.code,
                "error": _redact_text(exc.message),
                "timed_out": timed_out,
                "stderr_preview": "\n".join(stderr_tail[-5:])[:2_000],
            }
        finally:
            reader_stop.set()
            _graceful_stop(proc, process_job)
        result["worker_alive_after_shutdown"] = proc.poll() is None
        return result


class WebSocketACPTransport:
    """ACP v1 over RFC 6455, with an optional managed loopback daemon.

    With no configured endpoint a short-lived ``grok agent serve`` process is
    started in the task cwd.  This is intentional: Grok 0.2.118 can stall
    ``session/new`` when the daemon cwd and requested cwd differ.  A permanent
    daemon may instead be selected with ``GROK_DELEGATE_WS_ENDPOINT`` plus
    ``GROK_AGENT_SECRET``; neither value is included in receipts or errors.
    """

    name = "websocket"

    def __init__(
        self,
        *,
        grok_bin: str = "grok",
        endpoint: str | None = None,
        secret: str | None = None,
        expected_agent_version: Any = _EXPECTED_SENTINEL,
        output_byte_cap: int = DEFAULT_OUTPUT_BYTES,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.grok_bin = validate_grok_bin(grok_bin, from_client=False)
        self.endpoint = endpoint
        self.secret = secret
        self.expected_agent_version = (
            _expected_agent_version()
            if expected_agent_version is _EXPECTED_SENTINEL
            else expected_agent_version
        )
        self.output_byte_cap = max(16_384, min(int(output_byte_cap), 8_000_000))
        self.popen_factory = popen_factory

    def run(
        self,
        task: Mapping[str, Any],
        *,
        cwd: Path,
        cancel_event: threading.Event,
        event_sink: EventSink | None = None,
    ) -> dict[str, Any]:
        cwd = Path(cwd).resolve(strict=True)
        started = _utc_now()
        configured = (self.endpoint or os.environ.get("GROK_DELEGATE_WS_ENDPOINT") or "").strip()
        managed = not bool(configured)
        proc: subprocess.Popen[str] | None = None
        process_job: _WindowsKillJob | None = None
        ws: _WebSocketConnection | None = None
        # Raw bytes are retained only in bounded process memory and are redacted
        # once as a joined stream. Chunk-local redaction can miss a marker split
        # across the 4096-byte reader boundary.
        stderr_tail: list[str] = []
        stderr_overflow = threading.Event()

        if managed:
            host, port, path = "127.0.0.1", _free_loopback_port(), "/ws"
            server_secret = secrets.token_urlsafe(32)
            argv = _managed_ws_argv(self.grok_bin, task, port)
            env = os.environ.copy()
            env["GROK_AGENT_SECRET"] = server_secret
            popen_kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                popen_kwargs["start_new_session"] = True
            try:
                proc = self.popen_factory(
                    argv,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    **popen_kwargs,
                )
            except (FileNotFoundError, OSError) as exc:
                return _acp_failure("ACP_WS_SPAWN_FAILED", str(exc), started=started)
            process_job = _WindowsKillJob(proc)
            threading.Thread(
                target=_bounded_tail_reader,
                args=(
                    proc.stderr, stderr_tail, self.output_byte_cap, stderr_overflow,
                ),
                daemon=True,
            ).start()
        else:
            try:
                host, port, path = _parse_loopback_ws_endpoint(configured)
            except ACPError as exc:
                return _acp_failure(exc.code, exc.message, started=started)
            server_secret = self.secret or os.environ.get("GROK_AGENT_SECRET") or ""
            if not server_secret:
                return _acp_failure(
                    "ACP_WS_SECRET_MISSING",
                    "GROK_AGENT_SECRET is required for a configured WebSocket endpoint",
                    started=started,
                )

        deadline = time.monotonic() + float(task["timeout_seconds"])
        result: dict[str, Any]
        try:
            ws = _connect_ws_with_retry(
                host,
                port,
                path,
                server_secret,
                deadline=deadline,
                managed_proc=proc,
                cancel_event=cancel_event,
            )
            result = self._conversation(
                ws,
                task,
                cwd=cwd,
                cancel_event=cancel_event,
                event_sink=event_sink,
                started=started,
                deadline=deadline,
                worker_pid=proc.pid if proc else None,
                managed=managed,
                reconnect_factory=lambda: _connect_ws_with_retry(
                    host,
                    port,
                    path,
                    server_secret,
                    deadline=min(deadline, time.monotonic() + 5.0),
                    managed_proc=proc,
                    cancel_event=cancel_event,
                ),
                external_overflow=stderr_overflow,
            )
        except ACPError as exc:
            result = _acp_failure(
                exc.code,
                exc.message,
                started=started,
                worker_pid=proc.pid if proc else None,
                stderr_preview=_redact_text("".join(stderr_tail))[-2_000:],
            )
            if exc.code == "ACP_CANCELLED" or cancel_event.is_set():
                result["status"] = "cancelled"
        finally:
            if ws is not None:
                ws.close()
            if proc is not None and process_job is not None:
                _graceful_stop(proc, process_job)
        if managed and proc is not None:
            result["worker_alive_after_shutdown"] = proc.poll() is None
        else:
            result["worker_alive_after_shutdown"] = None
        return result

    def _conversation(
        self,
        ws: "_WebSocketConnection",
        task: Mapping[str, Any],
        *,
        cwd: Path,
        cancel_event: threading.Event,
        event_sink: EventSink | None,
        started: str,
        deadline: float,
        worker_pid: int | None,
        managed: bool,
        reconnect_factory: Callable[[], "_WebSocketConnection"] | None = None,
        external_overflow: threading.Event | None = None,
    ) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        text_chunks: list[str] = []
        tests: list[dict[str, Any]] = []
        tool_state: dict[str, dict[str, Any]] = {}
        session_id: str | None = None
        output_bytes = 0
        malformed = 0
        cancelled = False
        cancel_deadline: float | None = None
        reconnect_used = False
        timed_out = False

        def emit(kind: str, payload: Mapping[str, Any]) -> None:
            event = {
                "schema_version": EVENT_SCHEMA_ID,
                "sequence": len(events),
                "kind": kind[:128],
                "at": _utc_now(),
                "payload": bounded_event(payload),
            }
            if len(events) >= MAX_EVENTS:
                events.pop(0)
                for index, existing in enumerate(events):
                    existing["sequence"] = index
                event["sequence"] = len(events)
            events.append(event)
            if event_sink is not None:
                try:
                    event_sink(event)
                except Exception:
                    pass

        def send(message: Mapping[str, Any]) -> None:
            ws.send_text(json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")))

        def await_response(wanted: int, phase: str) -> dict[str, Any]:
            nonlocal output_bytes, malformed, session_id, cancelled, timed_out, cancel_deadline, reconnect_used, ws
            while True:
                if external_overflow is not None and external_overflow.is_set():
                    raise ACPError("ACP_OUTPUT_LIMIT", "managed daemon stderr exceeded configured cap")
                if cancel_deadline is not None and time.monotonic() >= cancel_deadline:
                    raise ACPError("ACP_CANCEL_TIMEOUT", "agent did not acknowledge cancellation")
                if time.monotonic() >= deadline:
                    timed_out = True
                    if session_id and not cancelled:
                        send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
                        cancelled = True
                    raise ACPError("ACP_TIMEOUT", f"timeout during {phase}")
                if cancel_event.is_set() and session_id and not cancelled:
                    send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
                    emit("cancel_sent", {"sessionId": session_id})
                    cancelled = True
                    cancel_deadline = time.monotonic() + CANCEL_GRACE_SECONDS
                if cancel_event.is_set() and session_id is None:
                    raise ACPError("ACP_CANCELLED", f"cancelled during {phase}")
                try:
                    raw = ws.receive_text(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                except ACPError as exc:
                    if (
                        exc.code != "ACP_DISCONNECTED"
                        or reconnect_used
                        or reconnect_factory is None
                        or not session_id
                        or cancel_event.is_set()
                    ):
                        raise
                    reconnect_used = True
                    replacement = reconnect_factory()
                    ws = replacement
                    try:
                        send({"jsonrpc": "2.0", "id": 10_000, "method": "initialize", "params": {
                            "protocolVersion": ACP_PROTOCOL_VERSION,
                            "clientCapabilities": {},
                            "clientInfo": {"name": "grok-delegate", "version": SERVER_VERSION},
                        }})
                        reinit = await_response(10_000, "reconnect/initialize")
                        if (reinit.get("result") or {}).get("protocolVersion") != ACP_PROTOCOL_VERSION:
                            raise ACPError("ACP_VERSION_MISMATCH", "reconnect did not negotiate ACP v1")
                        send({"jsonrpc": "2.0", "id": 10_001, "method": "session/load", "params": {
                            "sessionId": session_id, "cwd": str(cwd), "mcpServers": [],
                        }})
                        await_response(10_001, "reconnect/session/load")
                        emit("session_reconnected", {"sessionId": session_id, "resume": "loaded"})
                    finally:
                        replacement.close()
                    # Resending an in-flight write prompt could duplicate side
                    # effects. Prove reconnect/load, then fail closed and require
                    # a new correlation id for an operator-approved retry.
                    raise ACPError(
                        "ACP_RETRY_REQUIRED",
                        "session reconnected and loaded after disconnect; prompt was not replayed",
                    )
                if raw is None:
                    continue
                output_bytes += len(raw.encode("utf-8", errors="replace"))
                if output_bytes > self.output_byte_cap:
                    raise ACPError("ACP_OUTPUT_LIMIT", "agent output exceeded configured cap")
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    malformed += 1
                    if malformed > MAX_MALFORMED_FRAMES:
                        raise ACPError("ACP_MALFORMED_JSON", "too many malformed JSON-RPC frames")
                    continue
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    malformed += 1
                    if malformed > MAX_MALFORMED_FRAMES:
                        raise ACPError("ACP_MALFORMED_JSONRPC", "too many invalid JSON-RPC frames")
                    continue
                method = message.get("method")
                if method == "session/request_permission" and "id" in message:
                    permission_params = _permission_params_with_tool_state(
                        message.get("params") or {}, tool_state
                    )
                    decision = permission_decision(permission_params, task, cwd)
                    send({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": decision}})
                    tool = ((message.get("params") or {}).get("toolCall") or {})
                    emit("permission", {"decision": decision, "tool": {
                        "kind": tool.get("kind"), "title": tool.get("title"),
                        "toolCallId": tool.get("toolCallId"),
                    }})
                    continue
                if method == "session/update":
                    update = (message.get("params") or {}).get("update") or {}
                    _consume_update(update, text_chunks=text_chunks, tests=tests, tool_state=tool_state)
                    compact = _compact_session_update(update)
                    if compact is not None:
                        emit("session_update", compact)
                    continue
                if isinstance(method, str):
                    emit("notification", {"method": method})
                    continue
                if message.get("id") == wanted:
                    if "error" in message:
                        error = message.get("error") or {}
                        raise ACPError("ACP_REMOTE_ERROR", str(error.get("message") or error))
                    return message

        try:
            send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                "clientCapabilities": {},
                "clientInfo": {"name": "grok-delegate", "version": SERVER_VERSION},
            }})
            init = await_response(0, "initialize")
            init_result = init.get("result") or {}
            if init_result.get("protocolVersion") != ACP_PROTOCOL_VERSION:
                raise ACPError("ACP_VERSION_MISMATCH", "agent did not negotiate ACP protocol version 1")
            agent_version = ((init_result.get("_meta") or {}).get("agentVersion"))
            if self.expected_agent_version and agent_version != self.expected_agent_version:
                emit(
                    "version_mismatch",
                    {
                        "expected": self.expected_agent_version,
                        "got": agent_version,
                        "blocking": False,
                    },
                )
            emit("initialized", {"protocolVersion": 1, "agentVersion": agent_version})
            send({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {
                "cwd": str(cwd), "mcpServers": [],
            }})
            session = await_response(1, "session/new")
            session_id = str((session.get("result") or {}).get("sessionId") or "")
            if not session_id:
                raise ACPError("ACP_SESSION_INVALID", "session/new returned no sessionId")
            emit("session_created", {"sessionId": session_id})
            send({"jsonrpc": "2.0", "id": 2, "method": "session/prompt", "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": build_prompt(task)}],
            }})
            prompt = await_response(2, "session/prompt")
            stop_reason = str((prompt.get("result") or {}).get("stopReason") or "")
            status = "completed" if stop_reason == "end_turn" else (
                "cancelled" if stop_reason == "cancelled" or cancelled else "failed"
            )
            return {
                "status": status, "session_id": session_id, "stop_reason": stop_reason,
                "summary": _redact_text("".join(text_chunks))[:16_000], "tests": tests, "events": events,
                "agent_version": agent_version, "worker_pid": worker_pid, "agent_pid": worker_pid,
                "started_at": started, "finished_at": _utc_now(),
                "blocked_reason": None if status == "completed" else f"ACP_STOP_{stop_reason or 'UNKNOWN'}",
                "worker_alive_after_shutdown": False if managed else None,
            }
        except ACPError as exc:
            return {
                "status": "cancelled" if exc.code == "ACP_CANCELLED" or cancel_event.is_set() else "failed",
                "session_id": session_id, "summary": _redact_text("".join(text_chunks))[:16_000],
                "tests": tests, "events": events, "worker_pid": worker_pid, "agent_pid": worker_pid,
                "started_at": started, "finished_at": _utc_now(), "blocked_reason": exc.code,
                "error": _redact_text(exc.message), "timed_out": timed_out,
                "worker_alive_after_shutdown": False if managed else None,
            }


class _WebSocketConnection:
    """Minimal synchronous RFC 6455 client for ACP text frames."""

    def __init__(self, stream: socket.socket, prebuffer: bytes = b"") -> None:
        self.stream = stream
        self.prebuffer = bytearray(prebuffer)
        self.closed = False

    def send_text(self, text: str) -> None:
        if self.closed:
            raise ACPError("ACP_DISCONNECTED", "WebSocket is closed")
        payload = text.encode("utf-8")
        self._send_frame(0x1, payload)

    def receive_text(self, *, timeout: float) -> str | None:
        if self.closed:
            raise ACPError("ACP_DISCONNECTED", "WebSocket is closed")
        self.stream.settimeout(max(0.01, timeout))
        chunks: list[bytes] = []
        text_started = False
        while True:
            try:
                first = self._recv_exact(2)
            except socket.timeout:
                return None
            except OSError as exc:
                raise ACPError("ACP_DISCONNECTED", "WebSocket receive failed") from exc
            fin = bool(first[0] & 0x80)
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            if length > MAX_WS_FRAME_BYTES:
                raise ACPError("ACP_OUTPUT_LIMIT", "WebSocket frame exceeded configured cap")
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 0x8:
                self.closed = True
                raise ACPError("ACP_DISCONNECTED", "WebSocket peer closed the connection")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                chunks = [payload]
                text_started = True
            elif opcode == 0x0 and text_started:
                chunks.append(payload)
            else:
                raise ACPError("ACP_WS_PROTOCOL", f"unsupported WebSocket opcode {opcode}")
            if sum(len(chunk) for chunk in chunks) > MAX_WS_FRAME_BYTES:
                raise ACPError("ACP_OUTPUT_LIMIT", "fragmented WebSocket message exceeded configured cap")
            if fin:
                try:
                    return b"".join(chunks).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ACPError("ACP_MALFORMED_JSON", "WebSocket text was not UTF-8") from exc

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._send_frame(0x8, struct.pack("!H", 1000))
        except Exception:
            pass
        self.closed = True
        try:
            self.stream.close()
        except OSError:
            pass

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        try:
            self.stream.sendall(header + mask + masked)
        except OSError as exc:
            raise ACPError("ACP_DISCONNECTED", "WebSocket send failed") from exc

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        if self.prebuffer:
            take = min(size, len(self.prebuffer))
            data.extend(self.prebuffer[:take])
            del self.prebuffer[:take]
        while len(data) < size:
            chunk = self.stream.recv(size - len(data))
            if not chunk:
                raise ACPError("ACP_DISCONNECTED", "WebSocket peer closed the connection")
            data.extend(chunk)
        return bytes(data)


def _managed_ws_argv(grok_bin: str, task: Mapping[str, Any], port: int) -> list[str]:
    resolved = shutil.which(grok_bin) or grok_bin
    argv = [
        resolved,
        "--permission-mode", "default",
        "--max-turns", str(task["max_turns"]),
        "--no-subagents", "--disable-web-search",
        "agent", "--no-leader",
        *_model_argv(task),
        "--reasoning-effort", str(task["reasoning_effort"]),
        "serve", "--bind", f"127.0.0.1:{port}",
    ]
    if ALWAYS_APPROVE_FLAG in argv or "bypassPermissions" in argv:
        raise GuardError("ACP_WS_ARGV_UNSAFE", "managed WebSocket daemon argv is unsafe")
    return argv


def _parse_loopback_ws_endpoint(value: str) -> tuple[str, int, str]:
    candidate = value.strip()
    if "://" not in candidate:
        candidate = "ws://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme != "ws" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ACPError("ACP_WS_ENDPOINT_INVALID", "endpoint must be a secret-free ws:// loopback URL")
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ACPError("ACP_WS_NON_LOOPBACK", "WebSocket endpoint must be loopback-only")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ACPError("ACP_WS_ENDPOINT_INVALID", "WebSocket endpoint port is invalid") from exc
    if port is None or not (1 <= port <= 65535):
        raise ACPError("ACP_WS_ENDPOINT_INVALID", "WebSocket endpoint requires an explicit port")
    path = parsed.path or "/ws"
    if path != "/ws":
        raise ACPError("ACP_WS_ENDPOINT_INVALID", "WebSocket endpoint path must be /ws")
    return ("127.0.0.1" if host == "localhost" else host), port, path


def _connect_ws_with_retry(
    host: str,
    port: int,
    path: str,
    secret: str,
    *,
    deadline: float,
    managed_proc: subprocess.Popen[str] | None,
    cancel_event: threading.Event | None = None,
) -> _WebSocketConnection:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise ACPError("ACP_CANCELLED", "cancelled before WebSocket session creation")
        if managed_proc is not None and managed_proc.poll() is not None:
            raise ACPError("ACP_WS_PROCESS_EXITED", f"managed daemon exited with code {managed_proc.returncode}")
        try:
            return _connect_ws(host, port, path, secret, timeout=min(2.0, max(0.1, deadline - time.monotonic())))
        except (ACPError, OSError) as exc:
            last_error = exc
            if cancel_event is not None:
                cancel_event.wait(0.1)
            else:
                time.sleep(0.1)
    if isinstance(last_error, ACPError) and last_error.code != "ACP_WS_CONNECT_FAILED":
        raise last_error
    raise ACPError("ACP_WS_CONNECT_FAILED", "could not connect to the loopback WebSocket daemon")


def _connect_ws(host: str, port: int, path: str, secret: str, *, timeout: float) -> _WebSocketConnection:
    try:
        stream = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise ACPError("ACP_WS_CONNECT_FAILED", "could not connect to the loopback WebSocket daemon") from exc
    try:
        peer = str(stream.getpeername()[0]).split("%", 1)[0]
        if not ipaddress.ip_address(peer).is_loopback:
            raise ACPError("ACP_WS_NON_LOOPBACK", "connected WebSocket peer is not loopback")
    except ValueError as exc:
        stream.close()
        raise ACPError("ACP_WS_NON_LOOPBACK", "could not verify WebSocket peer as loopback") from exc
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request_path = f"{path}?server-key={quote(secret, safe='')}"
    host_header = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    request = (
        f"GET {request_path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode("ascii")
    try:
        stream.sendall(request)
        response, prebuffer = _recv_http_headers(stream)
        lines = response.decode("iso-8859-1", errors="replace").split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            raise ACPError("ACP_WS_HANDSHAKE_FAILED", "WebSocket server rejected the upgrade")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise ACPError("ACP_WS_HANDSHAKE_FAILED", "WebSocket accept proof did not match")
        if headers.get("upgrade", "").lower() != "websocket" or "upgrade" not in {
            token.strip().lower() for token in headers.get("connection", "").split(",")
        }:
            raise ACPError("ACP_WS_HANDSHAKE_FAILED", "WebSocket upgrade headers were incomplete")
        return _WebSocketConnection(stream, prebuffer)
    except Exception:
        stream.close()
        raise


def _recv_http_headers(stream: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = stream.recv(4096)
        if not chunk:
            raise ACPError("ACP_WS_HANDSHAKE_FAILED", "WebSocket server closed during upgrade")
        data.extend(chunk)
        if len(data) > 32_768:
            raise ACPError("ACP_WS_HANDSHAKE_FAILED", "WebSocket upgrade response was too large")
    header, remainder = bytes(data).split(b"\r\n\r\n", 1)
    return header, remainder


def _recv_exact(stream: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ACPError("ACP_DISCONNECTED", "WebSocket peer closed the connection")
        data.extend(chunk)
    return bytes(data)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _acp_failure(
    code: str,
    message: str,
    *,
    started: str,
    worker_pid: int | None = None,
    stderr_preview: str = "",
) -> dict[str, Any]:
    return {
        "status": "failed", "session_id": None, "summary": "", "tests": [], "events": [],
        "worker_pid": worker_pid, "agent_pid": worker_pid, "started_at": started,
        "finished_at": _utc_now(), "blocked_reason": code, "error": _redact_text(message),
        "stderr_preview": stderr_preview, "worker_alive_after_shutdown": False,
    }


def permission_decision(
    params: Mapping[str, Any],
    task: Mapping[str, Any],
    cwd: Path,
) -> dict[str, Any]:
    options = list(params.get("options") or [])
    reject = next((o for o in options if o.get("kind") == "reject_once"), None)
    allow = next((o for o in options if o.get("kind") == "allow_once"), None)
    tool = params.get("toolCall") or {}
    kind = str(tool.get("kind") or "other").lower()
    raw = tool.get("rawInput") or {}

    permitted = False
    if kind == "think":
        permitted = True
    elif task.get("permission_profile") == "read-only":
        permitted = kind in {"read", "search"} and _paths_confined(raw, cwd)
    elif kind in {"edit", "write"}:
        permitted = _paths_confined(raw, cwd)
    elif kind == "execute":
        command = str(raw.get("command") or "").strip()
        permitted = (
            command in {str(item).strip() for item in task.get("test_commands", [])}
            and _command_allowed(command, cwd)
        )
    elif kind in {"read", "search"}:
        permitted = _paths_confined(raw, cwd)

    choice = allow if permitted else reject
    if choice is None:
        return {"outcome": "cancelled"}
    return {"outcome": "selected", "optionId": choice["optionId"]}


def _permission_params_with_tool_state(
    params: Mapping[str, Any],
    tool_state: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Join ACP's real two-frame tool call before evaluating permission.

    Grok 0.2.118 sends rawInput in an earlier session/update tool_call and only
    the kind/title/id in session/request_permission.  Permission decisions must
    use both frames; absence from both remains a deny-by-default decision.
    """
    merged = dict(params)
    tool = dict(params.get("toolCall") or {})
    tool_id = str(tool.get("toolCallId") or "")
    previous = dict(tool_state.get(tool_id) or {})
    if not tool.get("rawInput") and isinstance(previous.get("rawInput"), Mapping):
        tool["rawInput"] = dict(previous["rawInput"])
    merged["toolCall"] = tool
    return merged


def assert_safe_acp_argv(argv: Sequence[str]) -> None:
    parts = [str(value) for value in argv]
    if ALWAYS_APPROVE_FLAG in parts:
        raise GuardError("ALWAYS_APPROVE_FORBIDDEN", "ACP argv must never include --always-approve")
    if "bypassPermissions" in parts:
        raise GuardError("PERMISSION_MODE_FORBIDDEN", "ACP argv must never bypass permissions")
    if "--permission-mode" not in parts or "default" not in parts:
        raise GuardError("ACP_PERMISSION_MODE_MISSING", "ACP argv must use explicit default permissions")
    if parts[-1:] != ["stdio"]:
        raise GuardError("ACP_STDIO_ARGV_INVALID", "stdio adapter must end in `agent ... stdio`")


_FORBIDDEN_COMMAND = re.compile(
    r"(?i)(?:^|[;&|]\s*)git\s+(?:push|merge|pull|rebase|reset|clean|checkout)|"
    r"(?:remove-item|del|erase|rmdir|rm)\b|"
    r"(?:auth\.json|credentials?|oauth|api[_-]?key)|"
    r"(?:invoke-webrequest|curl|wget|ssh|scp)\b"
)
_ALLOWED_COMMAND = re.compile(
    r"(?i)^\s*(?:"
    # Absolute or bare Python launcher + module runner (posix + Windows).
    r"(?:\S*[\\/])?(?:py(?:thon\d*)?(?:\.exe)?(?:\s+-\d+)?)\s+-m\s+(?:pytest|unittest)\b|"
    r"pytest\b|npm(?:\.cmd)?\s+(?:test|run\s+test)\b|pnpm\s+test\b|"
    r"cargo\s+test\b|go\s+test\b|dotnet\s+test\b|"
    r"git\s+(?:status|diff|log|show|rev-parse)\b|"
    r"rg\b|Get-Content\b|Get-ChildItem\b|Test-Path\b"
    r")"
)


def _command_allowed(command: str, cwd: Path) -> bool:
    text = command.strip()
    if (
        not text
        or len(text) > 2_000
        or any(c in text for c in ("\x00", "\n", "\r", ";", "&", "|", ">", "<", "`", "(", ")"))
        or "$(" in text
    ):
        return False
    if _FORBIDDEN_COMMAND.search(text):
        return False
    return bool(_ALLOWED_COMMAND.match(text)) and _command_paths_confined(text, cwd)


def _command_paths_confined(command: str, cwd: Path) -> bool:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return False
    root = cwd.resolve()
    for raw in tokens[1:]:
        token = raw.strip("\"'")
        if "=" in token:
            token = token.split("=", 1)[1].strip("\"'")
        token = token.split("::", 1)[0]
        if not token or token.startswith("-"):
            continue
        # Dotted unittest module names are not filesystem paths. Any explicit
        # slash, drive, UNC prefix or parent segment is resolved and confined.
        looks_like_path = (
            "/" in token
            or "\\" in token
            or token in {".", ".."}
            or token.startswith("../")
            or token.startswith("..\\")
            or bool(re.match(r"^[A-Za-z]:", token))
        )
        if not looks_like_path:
            continue
        candidate = Path(token)
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return False
    return True


def validated_test_argv(command: str, cwd: Path) -> list[str]:
    text = str(command).strip()
    root = Path(cwd).resolve(strict=True)
    if not _command_allowed(text, root):
        raise ACPError("TEST_COMMAND_UNSAFE", "test command is outside the bounded allowlist")
    try:
        argv = [token.strip("\"'") for token in shlex.split(text, posix=False)]
    except ValueError as exc:
        raise ACPError("TEST_COMMAND_UNSAFE", "test command quoting is invalid") from exc
    if not argv:
        raise ACPError("TEST_COMMAND_UNSAFE", "test command is empty")
    return argv


def _paths_confined(raw: Mapping[str, Any], cwd: Path) -> bool:
    candidates = [raw.get(key) for key in ("file_path", "path", "target", "destination") if raw.get(key)]
    if not candidates:
        return False
    for value in candidates:
        path = Path(str(value))
        candidate = path.resolve() if path.is_absolute() else (cwd / path).resolve()
        try:
            candidate.relative_to(cwd)
        except ValueError:
            return False
        lowered_parts = {part.casefold() for part in candidate.parts}
        if (
            lowered_parts & {"auth.json", "credentials.json", ".env", ".npmrc", ".pypirc"}
            or candidate.name.casefold().startswith(".env.")
            or candidate.suffix.casefold() in {".pem", ".p12", ".pfx", ".key"}
        ):
            return False
    return True


def _consume_update(
    update: Mapping[str, Any],
    *,
    text_chunks: list[str],
    tests: list[dict[str, Any]],
    tool_state: dict[str, dict[str, Any]],
) -> None:
    kind = str(update.get("sessionUpdate") or "")
    if kind == "agent_message_chunk":
        content = update.get("content") or {}
        if content.get("type") == "text":
            text_chunks.append(str(content.get("text") or "")[:8_000])
        return
    tool_id = str(update.get("toolCallId") or "")
    if kind == "tool_call" and tool_id:
        tool_state[tool_id] = dict(update)
        return
    if kind != "tool_call_update" or not tool_id:
        return
    previous = tool_state.setdefault(tool_id, {})
    previous.update(dict(update))
    raw_output = update.get("rawOutput") or {}
    command = str(raw_output.get("command") or (previous.get("rawInput") or {}).get("command") or "")
    if update.get("status") == "completed" and _looks_like_test(command):
        exit_code = raw_output.get("exit_code")
        tests.append(
            {
                "command": command[:1_000],
                "passed": exit_code == 0 and not bool(raw_output.get("timed_out")),
                "returncode": exit_code,
                "output_preview": _redact_text(str(raw_output.get("output_for_prompt") or ""))[:2_000],
            }
        )


def _compact_session_update(update: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep operational evidence, not Grok's large UI/config notification payloads."""
    kind = str(update.get("sessionUpdate") or "")
    if kind == "agent_message_chunk":
        content = update.get("content") or {}
        return {
            "sessionUpdate": kind,
            "content": {
                "type": content.get("type"),
                # Chunks can split a credential marker/value boundary. The
                # joined summary is redacted after reassembly; per-chunk event
                # text is intentionally not persisted.
                "text_redacted": True,
                "text_bytes": min(
                    len(str(content.get("text") or "").encode("utf-8", errors="replace")),
                    8_000,
                ),
            },
        }
    if kind == "tool_call":
        raw = update.get("rawInput") or {}
        return {
            "sessionUpdate": kind,
            "toolCallId": update.get("toolCallId"),
            "title": update.get("title"),
            "rawInput": {
                key: _redact_text(str(raw.get(key)))[:1_000]
                for key in ("command", "file_path", "path")
                if raw.get(key) is not None
            },
        }
    if kind == "tool_call_update":
        raw_output = update.get("rawOutput") or {}
        return {
            "sessionUpdate": kind,
            "toolCallId": update.get("toolCallId"),
            "kind": update.get("kind"),
            "title": update.get("title"),
            "status": update.get("status"),
            "rawOutput": {
                "command": _redact_text(str(raw_output.get("command") or ""))[:1_000],
                "exit_code": raw_output.get("exit_code"),
                "timed_out": raw_output.get("timed_out"),
                "output_preview": _redact_text(str(raw_output.get("output_for_prompt") or ""))[:2_000],
            },
        }
    if kind == "plan":
        return {"sessionUpdate": kind, "entries": list(update.get("entries") or [])[:32]}
    # available_commands_update and other UI/config payloads can exceed hundreds
    # of kilobytes and are not execution evidence.
    return None


def _looks_like_test(command: str) -> bool:
    return bool(re.search(r"(?i)(pytest|unittest|npm\s+test|cargo\s+test|go\s+test|dotnet\s+test)", command))


def _line_reader(
    stream: Any,
    channel: str,
    target: queue.Queue[tuple[str, str]],
    line_cap: int = DEFAULT_OUTPUT_BYTES,
    overflow: threading.Event | None = None,
    byte_budget: dict[str, int] | None = None,
    budget_lock: threading.Lock | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    if stream is None:
        return
    try:
        while stop_event is None or not stop_event.is_set():
            line = stream.readline(max(2, int(line_cap)) + 1)
            if line == "":
                break
            if len(line) > line_cap or (len(line) == line_cap + 1 and not line.endswith("\n")):
                if overflow is not None:
                    overflow.set()
                return
            if byte_budget is not None and budget_lock is not None:
                size = len(line.encode("utf-8", errors="replace"))
                with budget_lock:
                    if size > byte_budget.get("remaining", 0):
                        if overflow is not None:
                            overflow.set()
                        return
                    byte_budget["remaining"] = byte_budget.get("remaining", 0) - size
            payload = (channel, line.rstrip("\r\n"))
            while stop_event is None or not stop_event.is_set():
                try:
                    # The aggregate byte budget already bounds memory.  Apply
                    # backpressure during short Grok bursts instead of turning
                    # a healthy but briefly full queue into ACP_OUTPUT_LIMIT.
                    target.put(payload, timeout=0.1)
                    break
                except queue.Full:
                    continue
            else:
                return
    except Exception:
        return


def _bounded_tail_reader(
    stream: Any,
    tail: list[str],
    byte_cap: int,
    overflow: threading.Event,
) -> None:
    """Continuously drain daemon stderr while retaining only a redacted tail."""
    if stream is None:
        return
    remaining = max(1, int(byte_cap))
    try:
        while True:
            chunk = stream.read(min(4096, remaining + 1))
            if chunk == "":
                return
            size = len(chunk.encode("utf-8", errors="replace"))
            if size > remaining:
                overflow.set()
                return
            remaining -= size
            tail.append(chunk)
            if remaining <= 0:
                overflow.set()
                return
    except Exception:
        return


def _graceful_stop(proc: subprocess.Popen[str], process_job: "_WindowsKillJob") -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            _kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
    finally:
        process_job.close()


def _kill_process_tree(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(int(pid), 9)
    except Exception:
        pass


class _WindowsKillJob:
    """Best-effort Job Object; closing it kills the ACP process tree."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.handle: int | None = None
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )]

            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC_LIMIT),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                kernel32.CloseHandle(handle)
                return
            if not kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(int(proc._handle))):  # type: ignore[attr-defined]
                kernel32.CloseHandle(handle)
                return
            self.handle = int(handle)
        except Exception:
            self.handle = None

    def close(self) -> None:
        if self.handle is None:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self.handle)  # type: ignore[attr-defined]
        except Exception:
            pass
        self.handle = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ACPError",
    "ACP_PROTOCOL_VERSION",
    "DEFAULT_EXPECTED_AGENT_VERSION",
    "StdioACPTransport",
    "TransportAdapter",
    "WebSocketACPTransport",
    "assert_safe_acp_argv",
    "expected_agent_version",
    "permission_decision",
    "validated_test_argv",
]
