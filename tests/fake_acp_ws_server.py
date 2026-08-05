"""One-shot authenticated ACP WebSocket fixture for Round 8 integration tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def recv_exact(conn: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise EOFError
        data.extend(chunk)
    return bytes(data)


def recv_text(conn: socket.socket) -> str:
    first = recv_exact(conn, 2)
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(conn, 8))[0]
    mask = recv_exact(conn, 4) if first[1] & 0x80 else b""
    payload = recv_exact(conn, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return payload.decode("utf-8")


def send_text(conn: socket.socket, value: dict) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(payload) < 126:
        header = bytes((0x81, len(payload)))
    elif len(payload) <= 0xFFFF:
        header = bytes((0x81, 126)) + struct.pack("!H", len(payload))
    else:
        header = bytes((0x81, 127)) + struct.pack("!Q", len(payload))
    conn.sendall(header + payload)


port = int(os.environ["ROUND8_FAKE_WS_PORT"])
secret = os.environ["ROUND8_FAKE_WS_SECRET"]
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    if os.environ.get("ROUND8_FAKE_WS_STDERR_FLOOD") == "1":
        threading.Thread(
            target=lambda: (sys.stderr.write("E" * 1_100_000 + "\n"), sys.stderr.flush()),
            daemon=True,
        ).start()
    conn, _address = server.accept()
    with conn:
        headers = bytearray()
        while b"\r\n\r\n" not in headers:
            headers.extend(conn.recv(4096))
        lines = headers.decode("iso-8859-1").split("\r\n")
        target = lines[0].split()[1]
        supplied = parse_qs(urlparse(target).query).get("server-key", [""])[0]
        key = next(line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("sec-websocket-key:"))
        if supplied != secret:
            conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
            raise SystemExit(2)
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("ascii"))
        cwd = Path.cwd()
        while True:
            try:
                message = json.loads(recv_text(conn))
            except EOFError:
                break
            method = message.get("method")
            if method == "initialize":
                send_text(conn, {"jsonrpc": "2.0", "id": message["id"], "result": {
                    "protocolVersion": 1, "agentCapabilities": {}, "authMethods": [],
                    "_meta": {"agentVersion": "0.2.118"},
                }})
            elif method == "session/new":
                cwd = Path(message["params"]["cwd"])
                send_text(conn, {"jsonrpc": "2.0", "id": message["id"], "result": {"sessionId": "fixture-ws-session"}})
            elif method == "session/prompt":
                prompt = "".join(
                    str(item.get("text") or "")
                    for item in (message.get("params") or {}).get("prompt", [])
                    if isinstance(item, dict)
                )
                if "CANCEL_IGNORED_WS_FIXTURE" in prompt:
                    while True:
                        try:
                            recv_text(conn)
                        except EOFError:
                            break
                    break
                target_file = cwd / "fake-ws-output.txt"
                send_text(conn, {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "fixture-ws-session", "update": {
                        "sessionUpdate": "tool_call", "toolCallId": "fixture-ws-write",
                        "title": "Write fixture", "rawInput": {"file_path": str(target_file)},
                    },
                }})
                send_text(conn, {"jsonrpc": "2.0", "id": 100, "method": "session/request_permission", "params": {
                    "sessionId": "fixture-ws-session",
                    "toolCall": {"toolCallId": "fixture-ws-write", "kind": "edit", "title": "Write fixture"},
                    "options": [
                        {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
                        {"optionId": "reject-once", "name": "No", "kind": "reject_once"},
                    ],
                }})
                decision = json.loads(recv_text(conn))
                selected = (((decision.get("result") or {}).get("outcome") or {}).get("optionId"))
                if selected == "allow-once":
                    target_file.write_text("ROUND8_FAKE_WS_OK\n", encoding="utf-8")
                send_text(conn, {"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "fixture-ws-session", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ROUND8_FAKE_WS_DONE"}},
                }})
                send_text(conn, {"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
            elif method == "session/cancel":
                continue
