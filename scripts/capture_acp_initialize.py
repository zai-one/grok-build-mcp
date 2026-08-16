#!/usr/bin/env python3
"""Capture a redacted ACP initialize (+ optional session/new) from the installed CLI.

Observed agentVersion is recorded as evidence, never as a bridge pin.
No secrets, hostnames, or absolute paths are kept.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evidence" / "live-acp"
TIMEOUT = 25.0

_REDACT = [
    (re.compile(r"(?i)(authorization|bearer|api[_-]?key|token|secret)[^\s\"]{0,80}"), r"\1<REDACTED>"),
    (re.compile(r"[A-Za-z]:\\\\[^\s\"]+"), "<CWD>"),
    (re.compile(r"[A-Za-z]:/[^\s\"]+"), "<CWD>"),
    (re.compile(r"/[^\s\"]+"), "<PATH>"),
]


def redact(value: str) -> str:
    out = value
    for pattern, repl in _REDACT:
        out = pattern.sub(repl, out)
    return out


def redact_obj(obj):
    if isinstance(obj, dict):
        cleaned = {}
        for key, item in obj.items():
            low = str(key).lower()
            if any(part in low for part in ("token", "secret", "authorization", "hostname", "signature")):
                cleaned[key] = "<REDACTED>"
            elif key in {"currentWorkingDirectory", "cwd"}:
                cleaned[key] = "<CWD>"
            elif key in {"agentInstanceId", "agentId", "sessionId"}:
                cleaned[key] = f"<{key.upper()}>"
            elif key in {"id", "name"} and isinstance(item, str):
                cleaned[key] = item
            else:
                cleaned[key] = redact_obj(item)
        return cleaned
    if isinstance(obj, list):
        return [redact_obj(item) for item in obj[:32]]
    if isinstance(obj, str):
        return redact(obj)[:2_000]
    return obj


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    init = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "grok-delegate-rebaseline", "version": "probe"},
        },
    }
    argv = [
        "grok",
        "--permission-mode",
        "default",
        "--max-turns",
        "2",
        "--no-subagents",
        "--disable-web-search",
        "agent",
        "--no-leader",
        "--model",
        "grok-4.6",
        "--reasoning-effort",
        "low",
        "stdio",
    ]
    with tempfile.TemporaryDirectory() as raw:
        cwd = Path(raw)
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        frames: list[dict] = []
        notes: list[str] = []
        deadline = time.monotonic() + TIMEOUT
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(init, separators=(",", ":")) + "\n")
            proc.stdin.flush()
            stdout = proc.stdout
            assert stdout is not None
            while time.monotonic() < deadline:
                line = stdout.readline()
                if line == "":
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    notes.append("malformed_frame")
                    continue
                frames.append(redact_obj(message) if isinstance(message, dict) else {"raw": "<non-object>"})
                if isinstance(message, dict) and message.get("id") == 0 and "result" in message:
                    break
            else:
                notes.append("timeout_waiting_for_initialize")
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        stderr = ""
        try:
            if proc.stderr:
                stderr = proc.stderr.read()[:2_000]
        except OSError:
            pass

    result = next((f for f in frames if f.get("id") == 0 and "result" in f), None)
    agent_version = None
    protocol = None
    auth_methods = []
    if isinstance(result, dict):
        body = result.get("result") or {}
        protocol = body.get("protocolVersion")
        agent_version = ((body.get("_meta") or {}).get("agentVersion"))
        auth_methods = [m.get("id") for m in (body.get("authMethods") or []) if isinstance(m, dict)]
    report = {
        "ok": result is not None and protocol == 1,
        "observed_cli": os.environ.get("GROK_CAPTURE_CLI_VERSION") or "see grok --version",
        "observed_agent_version": agent_version,
        "protocol_version": protocol,
        "auth_method_ids": auth_methods,
        "pin_policy": "any installed CLI; this capture is not a pin",
        "notes": notes,
        "stderr_redacted": redact(stderr)[:500],
        "frame_count": len(frames),
    }
    lines = [json.dumps(init, separators=(",", ":"))]
    if result:
        lines.append(json.dumps(result, separators=(",", ":")))
    (OUT_DIR / "initialize.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT_DIR / "NOTES.md").write_text(
        "\n".join(
            [
                "# Live ACP initialize capture",
                "",
                "Observed, not a version pin. Typed path stays unpinned.",
                "",
                json.dumps(report, indent=2),
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
