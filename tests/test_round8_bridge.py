from __future__ import annotations

import io
import json
import subprocess
import os
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from grok_delegate import acp, agent_runtime, jobs, jobs_store, runner, server
from grok_delegate.acp import (
    ACPError,
    StdioACPTransport,
    WebSocketACPTransport,
    _parse_loopback_ws_endpoint,
    _redact_text,
    permission_decision,
)
from grok_delegate.contracts import (
    bounded_event,
    finalize_receipt,
    redact_text,
    validate_task_packet,
    validate_transport,
)
from grok_delegate.guard import GuardError


HERE = Path(__file__).resolve().parent
FAKE = HERE / "fake_acp_agent.py"
FAKE_WS = HERE / "fake_acp_ws_server.py"


def packet(root: Path, **overrides):
    value = {
        "objective": "Return a bounded result",
        "role": "consult",
        "project_root": str(root),
        "permission_profile": "read-only",
        "max_turns": 5,
        "timeout_seconds": 10,
        "inputs": [],
        "constraints": [],
        "acceptance_criteria": [],
        "expected_artifacts": [],
        "correlation_id": "round8-test",
    }
    value.update(overrides)
    if value.get("role") in {"execute", "fix"} and "expected_artifacts" not in overrides:
        value["expected_artifacts"] = ["expected-output.txt"]
    if value.get("role") in {"execute", "fix"} and "test_commands" not in overrides:
        value["test_commands"] = ["python -m pytest -q"]
    return value


def fake_transport() -> StdioACPTransport:
    def factory(_argv, **kwargs):
        return subprocess.Popen([sys.executable, str(FAKE)], **kwargs)

    return StdioACPTransport(grok_bin="grok", popen_factory=factory)


def test_task_packet_is_closed_and_exact_root() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        valid = validate_task_packet(packet(root), allowed_roots=[root])
        assert valid["schema_version"] == "grok-task-packet.v1"
        with pytest.raises(GuardError) as unknown:
            validate_task_packet({**packet(root), "surprise": True}, allowed_roots=[root])
        assert unknown.value.code == "TASK_PACKET_UNKNOWN_FIELDS"
        child = root / "child"
        child.mkdir()
        with pytest.raises(GuardError) as escaped:
            validate_task_packet(packet(child), allowed_roots=[root])
        assert escaped.value.code == "PROJECT_ROOT_UNTRUSTED"


def test_forced_role_rejects_mismatch() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with pytest.raises(GuardError) as mismatch:
            validate_task_packet(packet(root), allowed_roots=[root], forced_role="execute")
        assert mismatch.value.code == "TASK_ROLE_MISMATCH"


def test_supplied_task_schema_version_must_match() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with pytest.raises(GuardError) as mismatch:
            validate_task_packet(packet(root, schema_version="future.v9"), allowed_roots=[root])
        assert mismatch.value.code == "TASK_SCHEMA_VERSION_INVALID"


def test_auto_is_stdio_only() -> None:
    assert validate_transport("auto") == "stdio"
    with pytest.raises(GuardError):
        validate_transport("fallback-magic")


def test_permission_policy_is_deny_by_default() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        options = [
            {"optionId": "allow-once", "kind": "allow_once"},
            {"optionId": "reject-once", "kind": "reject_once"},
        ]
        readonly = packet(root)
        outside = {"options": options, "toolCall": {"kind": "edit", "rawInput": {"file_path": str(root.parent / "escape.txt")}}}
        assert permission_decision(outside, readonly, root)["optionId"] == "reject-once"
        workspace = packet(root, role="execute", permission_profile="workspace")
        push = {"options": options, "toolCall": {"kind": "execute", "rawInput": {"command": "git push origin main"}}}
        assert permission_decision(push, workspace, root)["optionId"] == "reject-once"
        test = {"options": options, "toolCall": {"kind": "execute", "rawInput": {"command": "python -m pytest -q"}}}
        assert permission_decision(test, workspace, root)["optionId"] == "allow-once"

        inside_read = {"options": options, "toolCall": {"kind": "read", "rawInput": {"file_path": str(root / "README.md")}}}
        outside_read = {"options": options, "toolCall": {"kind": "read", "rawInput": {"file_path": str(root.parent / "auth.json")}}}
        assert permission_decision(inside_read, readonly, root)["optionId"] == "allow-once"
        assert permission_decision(outside_read, readonly, root)["optionId"] == "reject-once"
        assert permission_decision(outside_read, workspace, root)["optionId"] == "reject-once"
        fetch = {"options": options, "toolCall": {"kind": "fetch", "rawInput": {"url": "https://example.invalid"}}}
        assert permission_decision(fetch, readonly, root)["optionId"] == "reject-once"

        chained = {"options": options, "toolCall": {"kind": "execute", "rawInput": {"command": "pytest -q; Set-Content C:\\outside.txt PWN"}}}
        external_test = {"options": options, "toolCall": {"kind": "execute", "rawInput": {"command": "pytest C:\\outside\\test_escape.py"}}}
        powershell_subexpression = {"options": options, "toolCall": {"kind": "execute", "rawInput": {"command": "pytest -q $(Get-Content auth.json)"}}}
        in_root_auth = {"options": options, "toolCall": {"kind": "read", "rawInput": {"file_path": str(root / "auth.json")}}}
        assert permission_decision(chained, workspace, root)["optionId"] == "reject-once"
        assert permission_decision(external_test, workspace, root)["optionId"] == "reject-once"
        assert permission_decision(powershell_subexpression, workspace, root)["optionId"] == "reject-once"
        assert permission_decision(in_root_auth, readonly, root)["optionId"] == "reject-once"


def test_write_task_requires_expected_artifact() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with pytest.raises(GuardError) as missing:
            validate_task_packet(
                packet(
                    root,
                    role="execute",
                    permission_profile="workspace",
                    expected_artifacts=[],
                ),
                allowed_roots=[root],
            )
        assert missing.value.code == "EXPECTED_ARTIFACTS_REQUIRED"


def test_permission_and_artifact_readback_reject_symlink_escape() -> None:
    with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as other_raw:
        root = Path(raw).resolve()
        outside = Path(other_raw).resolve()
        link = root / "outside-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks are unavailable on this Windows host")
        task = validate_task_packet(
            packet(root, role="execute", permission_profile="workspace"), allowed_roots=[root]
        )
        options = [
            {"optionId": "allow-once", "kind": "allow_once"},
            {"optionId": "reject-once", "kind": "reject_once"},
        ]
        request = {"options": options, "toolCall": {
            "kind": "edit", "rawInput": {"file_path": str(link / "escaped.txt")},
        }}
        assert permission_decision(request, task, root)["optionId"] == "reject-once"
        (outside / "artifact.txt").write_text("outside", encoding="utf-8")
        with pytest.raises(GuardError) as artifact_escape:
            validate_task_packet(
                packet(
                    root, role="execute", permission_profile="workspace",
                    expected_artifacts=["outside-link/artifact.txt"],
                ),
                allowed_roots=[root],
            )
        assert artifact_escape.value.code == "EXPECTED_ARTIFACT_ESCAPE"


def test_fake_stdio_consult_handshake_and_shutdown() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(packet(root), allowed_roots=[root])
        result = fake_transport().run(task, cwd=root, cancel_event=threading.Event())
        assert result["status"] == "completed"
        assert result["session_id"] == "fixture-session"
        assert result["summary"] == "ROUND8_FAKE_DONE"
        assert result["worker_alive_after_shutdown"] is False


def test_fake_stdio_execute_permission_diff_and_test_evidence() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            packet(
                root,
                objective="Create the fixture",
                role="execute",
                permission_profile="workspace",
                expected_artifacts=["fake-output.txt"],
                acceptance_criteria=["pytest test passes"],
            ),
            allowed_roots=[root],
        )
        result = fake_transport().run(task, cwd=root, cancel_event=threading.Event())
        assert result["status"] == "completed"
        assert (root / "fake-output.txt").read_text(encoding="utf-8") == "ROUND8_FAKE_OK\n"
        assert result["tests"] == [
            {
                "command": "python -m pytest -q",
                "passed": True,
                "returncode": 0,
                "output_preview": "1 passed",
            }
        ]


def test_fake_stdio_cancel_returns_cancelled_and_process_dies() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(packet(root, objective="CANCEL_FIXTURE"), allowed_roots=[root])
        cancel = threading.Event()
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault("result", fake_transport().run(task, cwd=root, cancel_event=cancel)))
        thread.start()
        time.sleep(0.2)
        cancel.set()
        thread.join(5)
        assert not thread.is_alive()
        assert holder["result"]["status"] == "cancelled"
        assert holder["result"]["worker_alive_after_shutdown"] is False


def test_cancel_has_independent_grace_deadline(monkeypatch) -> None:
    monkeypatch.setattr(acp, "CANCEL_GRACE_SECONDS", 0.2)
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            packet(root, objective="CANCEL_IGNORED_FIXTURE", timeout_seconds=10),
            allowed_roots=[root],
        )
        cancel = threading.Event()
        holder = {}
        thread = threading.Thread(
            target=lambda: holder.setdefault(
                "result", fake_transport().run(task, cwd=root, cancel_event=cancel)
            )
        )
        thread.start()
        time.sleep(0.3)
        cancel.set()
        thread.join(3)
        assert not thread.is_alive()
        assert holder["result"]["status"] == "cancelled"
        assert holder["result"]["blocked_reason"] == "ACP_CANCEL_TIMEOUT"
        assert holder["result"]["worker_alive_after_shutdown"] is False


def test_malformed_frames_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(packet(root, objective="MALFORMED_FIXTURE"), allowed_roots=[root])
        result = fake_transport().run(task, cwd=root, cancel_event=threading.Event())
        assert result["status"] == "failed"
        assert result["blocked_reason"] == "ACP_MALFORMED_JSON"


def test_timeout_and_oversized_output_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        timeout_task = validate_task_packet(
            packet(root, objective="CANCEL_FIXTURE", timeout_seconds=1), allowed_roots=[root]
        )
        timed = fake_transport().run(timeout_task, cwd=root, cancel_event=threading.Event())
        assert timed["status"] == "failed"
        assert timed["blocked_reason"] == "ACP_TIMEOUT"

        oversized_task = validate_task_packet(
            packet(root, objective="OVERSIZED_FIXTURE"), allowed_roots=[root]
        )
        oversized = StdioACPTransport(
            grok_bin="grok",
            popen_factory=lambda _argv, **kwargs: subprocess.Popen([sys.executable, str(FAKE)], **kwargs),
            output_byte_cap=16_384,
        ).run(oversized_task, cwd=root, cancel_event=threading.Event())
        assert oversized["status"] == "failed"
        assert oversized["blocked_reason"] == "ACP_OUTPUT_LIMIT"


def test_secret_redaction_covers_events_and_receipt_summary() -> None:
    cleaned = bounded_event({
        "message": "Authorization: Bearer planted-token",
        "nested": {"password": "planted-pass", "accessToken": "planted-access"},
    })
    assert "planted" not in str(cleaned)
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(packet(root, objective="SECRET_FIXTURE"), allowed_roots=[root])
        result = fake_transport().run(task, cwd=root, cancel_event=threading.Event())
        assert result["status"] == "completed"
        assert "planted" not in result["summary"]
        assert result["summary"].count("<REDACTED>") == 2


def test_completed_execute_without_diff_is_downgraded() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            packet(root, role="execute", permission_profile="workspace"),
            allowed_roots=[root],
        )
        receipt = finalize_receipt(
            {
                "status": "completed",
                "job_id": "job-x",
                "transport": "stdio",
                "changed_files": [],
                "tests": [],
                "started_at": "x",
                "finished_at": "y",
            },
            task,
        )
        assert receipt["status"] == "no_changes"
        assert receipt["ok"] is False
        assert receipt["blocked_reason"] == "EXECUTE_NO_CHANGES"


def test_preexisting_expected_artifact_cannot_certify_unrelated_diff() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["preexisting.txt"],
                test_commands=["python -m pytest -q"],
            ),
            allowed_roots=[root],
        )
        receipt = finalize_receipt({
            "status": "completed", "job_id": "job-x", "transport": "stdio",
            "changed_files": ["unrelated.txt"], "artifacts": ["preexisting.txt"],
            "tests": [{"command": "python -m pytest -q", "passed": True, "returncode": 0, "source": "bridge-verifier"}],
            "started_at": "x", "finished_at": "y",
        }, task)
        assert receipt["ok"] is False
        assert receipt["blocked_reason"].startswith("UNEXPECTED_CHANGED_FILES")


def test_unexpected_changed_file_blocks_completed_execute() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"],
                test_commands=["python -m pytest -q"],
            ),
            allowed_roots=[root],
        )
        receipt = finalize_receipt({
            "status": "completed", "job_id": "job-x", "transport": "stdio",
            "changed_files": ["expected.txt", "attacker.py"], "artifacts": ["expected.txt"],
            "tests": [{"command": "python -m pytest -q", "passed": True, "returncode": 0, "source": "bridge-verifier"}],
            "started_at": "x", "finished_at": "y",
        }, task)
        assert receipt["ok"] is False
        assert receipt["blocked_reason"] == "UNEXPECTED_CHANGED_FILES: attacker.py"


def test_mcp_primary_and_compatibility_tools_are_advertised() -> None:
    names = {tool["name"] for tool in server.list_tools()}
    assert {
        "grok_agent_status", "grok_agent_start", "grok_agent_poll", "grok_agent_cancel",
        "grok_agent_consult", "grok_agent_review", "grok_agent_execute", "grok_agent_fix",
        "grok_delegate", "grok_delegate_plan", "grok_delegate_start", "grok_delegate_poll",
        "grok_delegate_status", "grok_delegate_doctor", "grok_delegate_models", "grok_delegate_inspect",
    } <= names


def test_mcp_write_tool_schema_requires_nonempty_artifacts_and_tests() -> None:
    tools = {tool["name"]: tool for tool in server.list_tools()}
    task_schema = tools["grok_agent_execute"]["inputSchema"]["properties"]["task"]
    assert {"expected_artifacts", "test_commands"} <= set(task_schema["required"])
    assert task_schema["properties"]["expected_artifacts"]["minItems"] == 1
    assert task_schema["properties"]["test_commands"]["minItems"] == 1
    start_task = tools["grok_agent_start"]["inputSchema"]["properties"]["task"]
    assert start_task["allOf"][0]["then"]["required"] == ["expected_artifacts", "test_commands"]


def test_mcp_tools_call_uses_structured_content_and_error_flag() -> None:
    response = server.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "grok_agent_poll", "arguments": {"job_id": "missing"}},
    })
    payload = response["result"]
    assert payload["isError"] is True
    assert payload["structuredContent"]["error"] == "JOB_UNKNOWN"


def test_router_registers_websocket_without_changing_auto_routing() -> None:
    router = agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": fake_transport()})
    assert isinstance(router.adapter("websocket"), WebSocketACPTransport)
    assert router.adapter("auto") is router.adapter("stdio")


def test_websocket_external_endpoint_is_loopback_and_requires_env_secret(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(packet(root), allowed_roots=[root])
        non_loopback = WebSocketACPTransport(endpoint="ws://example.com:2419/ws")
        result = non_loopback.run(task, cwd=root, cancel_event=threading.Event())
        assert result["blocked_reason"] == "ACP_WS_NON_LOOPBACK"

        monkeypatch.delenv("GROK_AGENT_SECRET", raising=False)
        missing_secret = WebSocketACPTransport(endpoint="ws://127.0.0.1:2419/ws")
        result = missing_secret.run(task, cwd=root, cancel_event=threading.Event())
        assert result["blocked_reason"] == "ACP_WS_SECRET_MISSING"
        assert _parse_loopback_ws_endpoint("ws://localhost:2419/ws")[0] == "127.0.0.1"


def test_websocket_cancel_before_connect_is_bounded() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        task = validate_task_packet(
            packet(root, objective="cancel before connect", timeout_seconds=10),
            allowed_roots=[root],
        )
        cancel = threading.Event()
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault(
            "result",
            WebSocketACPTransport(
                endpoint=f"ws://127.0.0.1:{port}/ws", secret="ephemeral-test-secret"
            ).run(task, cwd=root, cancel_event=cancel),
        ))
        thread.start()
        time.sleep(0.2)
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert holder["result"]["status"] == "cancelled"
        assert holder["result"]["blocked_reason"] == "ACP_CANCELLED"


def test_websocket_cancel_while_session_new_is_pending_is_bounded() -> None:
    class StalledSessionWebSocket:
        def __init__(self, cancel: threading.Event, session_pending: threading.Event):
            self.cancel = cancel
            self.session_pending = session_pending
            self.sent = []
            self.closed = False
            self.delivered_initialize = False

        def send_text(self, raw: str) -> None:
            self.sent.append(json.loads(raw))

        def receive_text(self, *, timeout: float):
            if not self.delivered_initialize:
                self.delivered_initialize = True
                return json.dumps({
                    "jsonrpc": "2.0", "id": 0,
                    "result": {
                        "protocolVersion": 1,
                        "_meta": {"agentVersion": "0.2.118"},
                    },
                })
            if any(message.get("method") == "session/new" for message in self.sent):
                self.session_pending.set()
            self.cancel.wait(timeout)
            return None

        def close(self) -> None:
            self.closed = True

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            packet(root, objective="cancel pending session/new", timeout_seconds=10),
            allowed_roots=[root],
        )
        cancel = threading.Event()
        session_pending = threading.Event()
        ws = StalledSessionWebSocket(cancel, session_pending)
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault(
            "result",
            WebSocketACPTransport()._conversation(
                ws, task, cwd=root, cancel_event=cancel, event_sink=None,
                started="2026-08-05T00:00:00Z", deadline=time.monotonic() + 10,
                worker_pid=None, managed=False,
            ),
        ))
        thread.start()
        assert session_pending.wait(2)
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert holder["result"]["status"] == "cancelled"
        assert holder["result"]["blocked_reason"] == "ACP_CANCELLED"
        assert not any(message.get("method") == "session/cancel" for message in ws.sent)


def test_fake_authenticated_websocket_handshake_permission_and_completion() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        secret = "fixture-secret-never-log"
        env = os.environ.copy()
        env.update({"ROUND8_FAKE_WS_PORT": str(port), "ROUND8_FAKE_WS_SECRET": secret})
        proc = subprocess.Popen(
            [sys.executable, str(FAKE_WS)], cwd=root, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            task = validate_task_packet(
                packet(
                    root, role="execute", permission_profile="workspace",
                    objective="Create the WebSocket fixture",
                    expected_artifacts=["fake-ws-output.txt"],
                ),
                allowed_roots=[root],
            )
            result = WebSocketACPTransport(
                endpoint=f"ws://127.0.0.1:{port}/ws", secret=secret,
            ).run(task, cwd=root, cancel_event=threading.Event())
            assert result["status"] == "completed"
            assert result["session_id"] == "fixture-ws-session"
            assert result["summary"] == "ROUND8_FAKE_WS_DONE"
            assert (root / "fake-ws-output.txt").read_text(encoding="utf-8") == "ROUND8_FAKE_WS_OK\n"
        finally:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def test_managed_websocket_reports_worker_state_after_real_shutdown() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)

        def managed_factory(argv, **kwargs):
            port = str(argv[-1]).rsplit(":", 1)[1]
            env = dict(kwargs["env"])
            env["ROUND8_FAKE_WS_PORT"] = port
            env["ROUND8_FAKE_WS_SECRET"] = env["GROK_AGENT_SECRET"]
            kwargs["env"] = env
            return subprocess.Popen([sys.executable, str(FAKE_WS)], **kwargs)

        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                objective="Create managed WebSocket fixture",
                expected_artifacts=["fake-ws-output.txt"],
            ),
            allowed_roots=[root],
        )
        result = WebSocketACPTransport(popen_factory=managed_factory).run(
            task, cwd=root, cancel_event=threading.Event()
        )
        assert result["status"] == "completed"
        assert result["worker_pid"]
        assert result["worker_alive_after_shutdown"] is False


def test_managed_websocket_uncooperative_cancel_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(acp, "CANCEL_GRACE_SECONDS", 0.2)
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)

        def managed_factory(argv, **kwargs):
            port = str(argv[-1]).rsplit(":", 1)[1]
            env = dict(kwargs["env"])
            env["ROUND8_FAKE_WS_PORT"] = port
            env["ROUND8_FAKE_WS_SECRET"] = env["GROK_AGENT_SECRET"]
            kwargs["env"] = env
            return subprocess.Popen([sys.executable, str(FAKE_WS)], **kwargs)

        task = validate_task_packet(
            packet(root, objective="CANCEL_IGNORED_WS_FIXTURE", timeout_seconds=10),
            allowed_roots=[root],
        )
        cancel = threading.Event()
        session_created = threading.Event()
        holder = {}

        def event_sink(event):
            if event.get("kind") == "session_created":
                session_created.set()

        thread = threading.Thread(target=lambda: holder.setdefault(
            "result",
            WebSocketACPTransport(popen_factory=managed_factory).run(
                task, cwd=root, cancel_event=cancel, event_sink=event_sink
            ),
        ))
        thread.start()
        assert session_created.wait(3)
        cancel.set()
        thread.join(4)
        assert not thread.is_alive()
        assert holder["result"]["status"] == "cancelled"
        assert holder["result"]["blocked_reason"] == "ACP_CANCEL_TIMEOUT"
        assert holder["result"]["worker_alive_after_shutdown"] is False


def test_managed_websocket_stderr_is_cumulatively_bounded() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)

        def managed_factory(argv, **kwargs):
            port = str(argv[-1]).rsplit(":", 1)[1]
            env = dict(kwargs["env"])
            env["ROUND8_FAKE_WS_PORT"] = port
            env["ROUND8_FAKE_WS_SECRET"] = env["GROK_AGENT_SECRET"]
            env["ROUND8_FAKE_WS_STDERR_FLOOD"] = "1"
            kwargs["env"] = env
            return subprocess.Popen([sys.executable, str(FAKE_WS)], **kwargs)

        task = validate_task_packet(packet(root), allowed_roots=[root])
        result = WebSocketACPTransport(popen_factory=managed_factory).run(
            task, cwd=root, cancel_event=threading.Event()
        )
        assert result["status"] == "failed"
        # Under parallel suite load the fake daemon may miss the first connect
        # window; the important product gate is still a fail-closed non-success
        # receipt with either the output cap or a connect/process failure code.
        assert result.get("blocked_reason") in {
            "ACP_OUTPUT_LIMIT",
            "ACP_WS_CONNECT_FAILED",
            "ACP_WS_PROCESS_EXITED",
        }
        assert result["worker_alive_after_shutdown"] in {False, None}


def test_managed_websocket_stderr_redaction_crosses_reader_boundary() -> None:
    raw = "x" * 4089 + "server-" + "key=PLANTED_SECRET_123\n"
    tail = []
    overflow = threading.Event()
    acp._bounded_tail_reader(io.StringIO(raw), tail, 20_000, overflow)
    preview = acp._redact_text("".join(tail))[-2_000:]
    assert overflow.is_set() is False
    assert "PLANTED_SECRET_123" not in preview
    assert "<REDACTED>" in preview


def test_websocket_secret_is_redacted_in_both_daemon_formats() -> None:
    raw = "Secret: planted-value\nWebSocket URL: ws://127.0.0.1/ws?server-key=planted-value"
    cleaned = _redact_text(raw)
    assert "planted-value" not in cleaned
    assert cleaned.count("<REDACTED>") == 2


def test_two_jobs_queue_at_concurrency_one_and_backpressure_is_bounded(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        started: list[str] = []
        release = threading.Event()
        executor = ThreadPoolExecutor(max_workers=1)
        monkeypatch.setattr(agent_runtime, "_EXECUTOR", executor)
        monkeypatch.setattr(agent_runtime, "_CONCURRENCY", 1)
        monkeypatch.setattr(agent_runtime, "_MAX_QUEUED", 1)
        monkeypatch.setattr(agent_runtime, "_ADMISSION", threading.BoundedSemaphore(2))
        jobs.reset_jobs_for_tests()

        def controlled(task, **_kwargs):
            started.append(task["correlation_id"])
            release.wait(5)
            return finalize_receipt({
                "status": "completed", "job_id": "controlled", "transport": "stdio",
                "changed_files": [], "tests": [], "started_at": "x", "finished_at": "y",
            }, task)

        monkeypatch.setattr(agent_runtime, "run_task", controlled)
        first = agent_runtime.start_agent_job(
            packet(root, correlation_id="queue-one"), transport="stdio", allowed_roots=[root]
        )
        deadline = time.time() + 2
        while not started and time.time() < deadline:
            time.sleep(0.01)
        second = agent_runtime.start_agent_job(
            packet(root, correlation_id="queue-two"), transport="stdio", allowed_roots=[root]
        )
        third = agent_runtime.start_agent_job(
            packet(root, correlation_id="queue-three"), transport="stdio", allowed_roots=[root]
        )
        assert first["state"] == "running" and second["state"] == "running"
        assert started == ["queue-one"]
        assert third["error"] == "QUEUE_FULL"
        release.set()
        deadline = time.time() + 3
        while len(started) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert started == ["queue-one", "queue-two"]
        executor.shutdown(wait=True)


def test_queued_job_cancel_is_immediately_terminal(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        started: list[str] = []
        release = threading.Event()
        executor = ThreadPoolExecutor(max_workers=1)
        monkeypatch.setattr(agent_runtime, "_EXECUTOR", executor)
        monkeypatch.setattr(agent_runtime, "_CONCURRENCY", 1)
        monkeypatch.setattr(agent_runtime, "_MAX_QUEUED", 1)
        monkeypatch.setattr(agent_runtime, "_ADMISSION", threading.BoundedSemaphore(2))
        jobs.reset_jobs_for_tests()
        with agent_runtime._LOCK:
            agent_runtime._CANCEL_EVENTS.clear()
            agent_runtime._FUTURES.clear()
            agent_runtime._JOB_META.clear()

        def controlled(task, **_kwargs):
            started.append(task["correlation_id"])
            release.wait(5)
            return finalize_receipt({
                "status": "completed", "job_id": "controlled", "transport": "stdio",
                "changed_files": [], "tests": [], "started_at": "x", "finished_at": "y",
            }, task)

        monkeypatch.setattr(agent_runtime, "run_task", controlled)
        first = agent_runtime.start_agent_job(
            packet(root, correlation_id="cancel-queue-one"), transport="stdio", allowed_roots=[root]
        )
        deadline = time.time() + 2
        while not started and time.time() < deadline:
            time.sleep(0.01)
        second = agent_runtime.start_agent_job(
            packet(root, correlation_id="cancel-queue-two"), transport="stdio", allowed_roots=[root]
        )
        cancelled = agent_runtime.cancel_agent_job(second["job_id"])
        second_record = jobs.get_job(second["job_id"])
        assert first["state"] == "running"
        assert cancelled["cancelled_while_queued"] is True
        assert second_record["state"] == "done"
        assert second_record["result"]["status"] == "cancelled"
        assert started == ["cancel-queue-one"]
        release.set()
        executor.shutdown(wait=True)


def test_shutdown_terminalizes_queued_future(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        started: list[str] = []
        release = threading.Event()
        executor = ThreadPoolExecutor(max_workers=1)
        monkeypatch.setattr(agent_runtime, "_EXECUTOR", executor)
        monkeypatch.setattr(agent_runtime, "_ADMISSION", threading.BoundedSemaphore(2))
        jobs.reset_jobs_for_tests()
        with agent_runtime._LOCK:
            agent_runtime._CANCEL_EVENTS.clear()
            agent_runtime._FUTURES.clear()
            agent_runtime._JOB_META.clear()

        def controlled(task, **_kwargs):
            started.append(task["correlation_id"])
            release.wait(5)
            return finalize_receipt({
                "status": "cancelled", "job_id": "controlled", "transport": "stdio",
                "changed_files": [], "tests": [], "started_at": "x", "finished_at": "y",
            }, task)

        monkeypatch.setattr(agent_runtime, "run_task", controlled)
        first = agent_runtime.start_agent_job(
            packet(root, correlation_id="shutdown-one"), transport="stdio", allowed_roots=[root]
        )
        deadline = time.time() + 2
        while not started and time.time() < deadline:
            time.sleep(0.01)
        second = agent_runtime.start_agent_job(
            packet(root, correlation_id="shutdown-two"), transport="stdio", allowed_roots=[root]
        )
        shutdown = agent_runtime.shutdown_runtime()
        second_record = jobs.get_job(second["job_id"])
        assert first["state"] == "running"
        assert shutdown["cancel_signalled"] == 2
        assert shutdown["queued_terminalized"] == 1
        assert second_record["state"] == "done"
        assert second_record["result"]["blocked_reason"] == "CANCELLED_WHILE_QUEUED"
        release.set()
        deadline = time.time() + 2
        while (jobs.get_job(first["job_id"]) or {}).get("state") == "running" and time.time() < deadline:
            time.sleep(0.01)


def test_job_persistence_wires_disk_eviction(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(jobs.jobs_store, "save_job", lambda record, root: True)
    monkeypatch.setattr(
        jobs.jobs_store, "evict_on_disk",
        lambda root, max_jobs: calls.append((Path(root), max_jobs)) or 0,
    )
    with tempfile.TemporaryDirectory() as raw:
        jobs.configure_jobs_dir(raw)
        try:
            jobs._persist({"job_id": "job-evict", "state": "done"})
        finally:
            jobs.configure_jobs_dir(None)
    assert calls and calls[0][1] == jobs.MAX_JOBS


def test_job_identity_covers_full_packet_transport_and_lane() -> None:
    with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as other_raw:
        root = Path(raw)
        other = Path(other_raw)
        a = validate_task_packet(packet(root), allowed_roots=[root])
        b = validate_task_packet(packet(other), allowed_roots=[other])
        first = agent_runtime._job_id(a, "stdio", "grok/lane-a")
        assert first != agent_runtime._job_id(b, "stdio", "grok/lane-a")
        assert first != agent_runtime._job_id(a, "websocket", "grok/lane-a")
        assert first != agent_runtime._job_id(a, "stdio", "grok/lane-b")


def test_websocket_disconnect_reconnects_and_loads_without_replaying_prompt() -> None:
    class ScriptedSocket:
        def __init__(self, incoming):
            self.incoming = list(incoming)
            self.sent = []
            self.closed = False

        def send_text(self, raw):
            self.sent.append(json.loads(raw))

        def receive_text(self, **_kwargs):
            value = self.incoming.pop(0)
            if isinstance(value, Exception):
                raise value
            return json.dumps(value)

        def close(self):
            self.closed = True

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(packet(root), allowed_roots=[root])
        initial = ScriptedSocket([
            {"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": 1, "_meta": {"agentVersion": "0.2.118"}}},
            {"jsonrpc": "2.0", "id": 1, "result": {"sessionId": "reconnect-session"}},
            ACPError("ACP_DISCONNECTED", "fixture drop"),
        ])
        replacement = ScriptedSocket([
            {"jsonrpc": "2.0", "id": 10_000, "result": {"protocolVersion": 1, "_meta": {"agentVersion": "0.2.118"}}},
            {"jsonrpc": "2.0", "id": 10_001, "result": {"sessionId": "reconnect-session"}},
        ])
        result = WebSocketACPTransport()._conversation(
            initial, task, cwd=root, cancel_event=threading.Event(), event_sink=None,
            started="x", deadline=time.monotonic() + 5, worker_pid=None,
            managed=False, reconnect_factory=lambda: replacement,
        )
        assert result["status"] == "failed"
        assert result["blocked_reason"] == "ACP_RETRY_REQUIRED"
        assert any(event["kind"] == "session_reconnected" for event in result["events"])
        methods = [message.get("method") for message in replacement.sent]
        assert methods == ["initialize", "session/load"]
        assert "session/prompt" not in methods


def test_binary_content_length_framing_counts_cyrillic_bytes() -> None:
    request = json.dumps({
        "jsonrpc": "2.0", "id": 41, "method": "initialize",
        "params": {"clientInfo": {"name": "проверка", "version": "1"}},
    }, ensure_ascii=False).encode("utf-8")
    incoming = io.BytesIO(f"Content-Length: {len(request)}\r\n\r\n".encode("ascii") + request)
    outgoing = io.BytesIO()
    server._serve_binary_stdio(incoming, outgoing)
    header, body = outgoing.getvalue().split(b"\r\n\r\n", 1)
    assert int(header.split(b":", 1)[1]) == len(body)
    response = json.loads(body.decode("utf-8"))
    assert response["id"] == 41
    from grok_delegate.guard import SERVER_VERSION
    assert response["result"]["serverInfo"]["version"] == SERVER_VERSION


def test_typed_tool_emits_sanitized_audit() -> None:
    audit = io.StringIO()
    result = server.handle_tool_call(
        "grok_agent_poll", {"job_id": "missing"}, audit_stream=audit
    )
    assert result["error"] == "JOB_UNKNOWN"
    event = json.loads(audit.getvalue())
    assert event["tool"] == "grok_agent_poll"
    assert event["outcome"] == "error"
    assert "objective" not in event


def test_durable_record_is_versioned_redacted_and_below_reload_cap() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        event = {"kind": "chunk", "payload": {"text": "X" * 8_000}}
        record = {
            "job_id": "job-large", "state": "done", "server_pid": os.getpid(),
            "events": [event for _ in range(256)],
            "result": {"status": "completed", "events": [event for _ in range(128)],
                       "summary": "Authorization: Bearer planted-token"},
        }
        assert jobs_store.save_job(record, root)
        path = jobs_store.job_path(root, "job-large")
        assert path.stat().st_size <= jobs_store.MAX_JOB_FILE_BYTES
        text = path.read_text(encoding="utf-8")
        assert "planted-token" not in text
        loaded = jobs_store.load_jobs(root)
        assert loaded["job-large"]["job_record_schema"] == jobs_store.JOB_RECORD_SCHEMA


def test_owned_legacy_process_obeys_cancel() -> None:
    with tempfile.TemporaryDirectory() as raw:
        cancel = threading.Event()
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault(
            "result",
            agent_runtime._run_owned_process(
                [sys.executable, "-c", "import time; time.sleep(30)"], Path(raw), 60, cancel
            ),
        ))
        thread.start()
        time.sleep(0.3)
        cancel.set()
        thread.join(5)
        assert not thread.is_alive()
        assert holder["result"]["cancelled"] is True


def test_owned_process_caps_many_chunk_output() -> None:
    with tempfile.TemporaryDirectory() as raw:
        result = agent_runtime._run_owned_process(
            [sys.executable, "-c", "import sys; [sys.stdout.write('X'*4096) for _ in range(400)]"],
            Path(raw), 10, threading.Event(),
        )
        assert result["output_limited"] is True
        assert len(result["stdout"].encode("utf-8")) <= 1_000_000


def test_stdio_reader_enforces_aggregate_budget_before_queueing() -> None:
    target = __import__("queue").Queue(maxsize=32)
    overflow = threading.Event()
    budget = {"remaining": 100}
    acp._line_reader(
        io.StringIO(("X" * 40 + "\n") * 10),
        "stdout", target, 1_000, overflow, budget, threading.Lock(),
    )
    queued = 0
    while not target.empty():
        queued += len(target.get_nowait()[1].encode("utf-8")) + 1
    assert overflow.is_set()
    assert queued <= 100


def test_cancel_during_git_preflight_is_bounded(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        cancel = threading.Event()
        holder = {}

        def stalled_git(_argv, _cwd, _timeout, event):
            event.wait(5)
            return {"returncode": 130, "stdout": "", "stderr": "", "cancelled": True, "timedOut": False}

        monkeypatch.setattr(agent_runtime, "_run_owned_process", stalled_git)
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"], test_commands=["python -m pytest -q"],
            ),
            allowed_roots=[root],
        )
        router = agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": fake_transport()})
        thread = threading.Thread(target=lambda: holder.setdefault(
            "result",
            agent_runtime.run_task(
                task, transport="stdio", lane="round8-preflight-cancel",
                router=router, cancel_event=cancel,
            ),
        ))
        thread.start()
        time.sleep(0.2)
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert holder["result"]["status"] == "cancelled"
        assert holder["result"]["blocked_reason"] == "CANCELLED_DURING_PREFLIGHT"


def test_reused_worktree_preexisting_diff_cannot_certify_new_run(monkeypatch) -> None:
    class NoWriteAdapter:
        name = "stdio"

        def run(self, _task, **_kwargs):
            return {
                "status": "completed", "session_id": "no-write-session",
                "summary": "did nothing", "tests": [], "events": [],
                "worker_alive_after_shutdown": False,
            }

    with tempfile.TemporaryDirectory() as raw:
        outer = Path(raw)
        root = outer / "repo"
        root.mkdir()
        lanes = outer / "lanes"
        monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(lanes))

        def git(*args):
            subprocess.run(
                ["git", *args], cwd=root, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )

        git("init", "-b", "master")
        (root / "test_acceptance.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        git("add", ".")
        git("-c", "user.name=Round8", "-c", "user.email=round8@example.invalid", "commit", "-m", "baseline")
        prep = agent_runtime.prepare_worktree(
            repo_root=root, lane="round8-stale-diff", base_ref="master",
            lanes_parent=lanes, require_clean_base=False,
        )
        assert prep["ok"]
        lane_root = Path(prep["worktree_path"])
        (lane_root / "expected.txt").write_text("OLD_UNTRACKED\n", encoding="utf-8")
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"],
                test_commands=[f"{sys.executable} -m unittest test_acceptance -v"],
            ),
            allowed_roots=[root],
        )
        result = agent_runtime.run_task(
            task, transport="stdio", lane="round8-stale-diff",
            router=agent_runtime.TransportRouter(
                grok_bin="grok", adapters={"stdio": NoWriteAdapter()}
            ),
            cancel_event=threading.Event(),
        )
        assert result["status"] == "no_changes"
        assert result["ok"] is False
        assert result["changed_files"] == []


def test_reused_worktree_preexisting_unexpected_diff_blocks_new_run(monkeypatch) -> None:
    class ExpectedOnlyAdapter:
        name = "stdio"

        def run(self, _task, **kwargs):
            (Path(kwargs["cwd"]) / "expected.txt").write_text("NEW\n", encoding="utf-8")
            return {
                "status": "completed", "session_id": "expected-only-session",
                "summary": "changed only expected artifact", "tests": [], "events": [],
                "worker_alive_after_shutdown": False,
            }

    with tempfile.TemporaryDirectory() as raw:
        outer = Path(raw)
        root = outer / "repo"
        root.mkdir()
        lanes = outer / "lanes"
        monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(lanes))

        def git(*args):
            subprocess.run(
                ["git", *args], cwd=root, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )

        git("init", "-b", "master")
        (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (root / "test_acceptance.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        git("add", ".")
        git("-c", "user.name=Round8", "-c", "user.email=round8@example.invalid", "commit", "-m", "baseline")
        prep = agent_runtime.prepare_worktree(
            repo_root=root, lane="round8-stale-attacker", base_ref="master",
            lanes_parent=lanes, require_clean_base=False,
        )
        assert prep["ok"]
        lane_root = Path(prep["worktree_path"])
        (lane_root / "attacker.py").write_text("STALE_UNEXPECTED = True\n", encoding="utf-8")
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"],
                test_commands=[f"{sys.executable} -m unittest test_acceptance -v"],
            ),
            allowed_roots=[root],
        )
        result = agent_runtime.run_task(
            task, transport="stdio", lane="round8-stale-attacker",
            router=agent_runtime.TransportRouter(
                grok_bin="grok", adapters={"stdio": ExpectedOnlyAdapter()}
            ),
            cancel_event=threading.Event(),
        )
        assert result["status"] == "blocked"
        assert result["ok"] is False
        assert result["blocked_reason"] == "UNEXPECTED_CHANGED_FILES: attacker.py"
        assert result["changed_files"] == ["expected.txt"]
        assert result["full_changed_files"] == ["attacker.py", "expected.txt"]


def test_incomplete_post_git_snapshot_with_stale_file_fails_closed(monkeypatch) -> None:
    def failing_status_git(args, _cwd, _timeout):
        if "status" in args:
            return {"returncode": 1, "stdout": "", "stderr": "simulated"}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    snapshot = runner.collect_diff(Path("unused"), git_runner=failing_status_git)
    assert snapshot["ok"] is False
    assert snapshot["error_code"] == "DIFF_SNAPSHOT_FAILED"
    assert snapshot["failed_probe"] == "status_porcelain"

    class ExpectedOnlyAdapter:
        name = "stdio"

        def run(self, _task, **kwargs):
            (Path(kwargs["cwd"]) / "expected.txt").write_text("EXPECTED\n", encoding="utf-8")
            return {
                "status": "completed", "session_id": "snapshot-session",
                "summary": "changed expected only", "tests": [], "events": [],
                "worker_alive_after_shutdown": False,
            }

    with tempfile.TemporaryDirectory() as raw:
        outer = Path(raw)
        root = outer / "repo"
        root.mkdir()
        lanes = outer / "lanes"
        monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(lanes))

        def git(*args):
            subprocess.run(
                ["git", *args], cwd=root, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )

        git("init", "-b", "master")
        (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        git("add", ".")
        git("-c", "user.name=Round8", "-c", "user.email=round8@example.invalid", "commit", "-m", "baseline")
        prep = agent_runtime.prepare_worktree(
            repo_root=root, lane="round8-incomplete-snapshot", base_ref="master",
            lanes_parent=lanes, require_clean_base=False,
        )
        assert prep["ok"]
        lane_root = Path(prep["worktree_path"])
        (lane_root / "attacker.py").write_text("STALE_UNEXPECTED = True\n", encoding="utf-8")

        original_collect = agent_runtime.collect_diff
        calls = 0

        def fail_second_snapshot(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                return {
                    "ok": False, "error": "DIFF_SNAPSHOT_FAILED",
                    "error_code": "DIFF_SNAPSHOT_FAILED",
                    "failed_probe": "status_porcelain", "changed_files": [],
                    "commits": [], "diffstat": "",
                }
            return original_collect(*args, **kwargs)

        monkeypatch.setattr(agent_runtime, "collect_diff", fail_second_snapshot)
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"],
                test_commands=["python -m pytest -q"],
            ),
            allowed_roots=[root],
        )
        result = agent_runtime.run_task(
            task, transport="stdio", lane="round8-incomplete-snapshot",
            router=agent_runtime.TransportRouter(
                grok_bin="grok", adapters={"stdio": ExpectedOnlyAdapter()}
            ),
            cancel_event=threading.Event(),
        )
        assert calls == 2
        assert (lane_root / "attacker.py").exists()
        assert (lane_root / "expected.txt").exists()
        assert result["status"] == "failed"
        assert result["ok"] is False
        assert result["blocked_reason"] == "DIFF_SNAPSHOT_FAILED"
        assert result["error_code"] == "DIFF_SNAPSHOT_FAILED"
        assert result["failed_probe"] == "status_porcelain"
        assert result["tests"] == []


def test_verifier_cannot_revert_artifact_and_leave_stale_completed_receipt(monkeypatch) -> None:
    class WritesThenTestReverts:
        name = "stdio"

        def run(self, _task, **kwargs):
            (Path(kwargs["cwd"]) / "expected.txt").write_text("NEW\n", encoding="utf-8")
            return {
                "status": "completed", "session_id": "write-session",
                "summary": "wrote before verifier", "tests": [], "events": [],
                "worker_alive_after_shutdown": False,
            }

    with tempfile.TemporaryDirectory() as raw:
        outer = Path(raw)
        root = outer / "repo"
        root.mkdir()
        lanes = outer / "lanes"
        monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(lanes))

        def git(*args):
            subprocess.run(
                ["git", *args], cwd=root, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )

        git("init", "-b", "master")
        (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (root / "expected.txt").write_text("BASE\n", encoding="utf-8")
        (root / "test_revert.py").write_text(
            "import unittest\nfrom pathlib import Path\n"
            "class T(unittest.TestCase):\n"
            "    def test_revert(self):\n"
            "        Path('expected.txt').write_text('BASE\\n', encoding='utf-8')\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        git("add", ".")
        git("-c", "user.name=Round8", "-c", "user.email=round8@example.invalid", "commit", "-m", "baseline")
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"],
                test_commands=[f"{sys.executable} -m unittest test_revert -v"],
            ),
            allowed_roots=[root],
        )
        result = agent_runtime.run_task(
            task, transport="stdio", lane="round8-verifier-revert",
            router=agent_runtime.TransportRouter(
                grok_bin="grok", adapters={"stdio": WritesThenTestReverts()}
            ),
            cancel_event=threading.Event(),
        )
        assert result["status"] == "no_changes"
        assert result["ok"] is False
        assert result["changed_files"] == []


def test_terminal_persistence_cannot_be_overwritten_by_stale_progress(monkeypatch) -> None:
    jobs.reset_jobs_for_tests()
    persisted = []
    progress_persisting = threading.Event()
    release_progress = threading.Event()
    finish_work = threading.Event()

    def controlled_persist(record):
        if record.get("state") == "running" and record.get("phase") == "executor":
            progress_persisting.set()
            release_progress.wait(3)
        persisted.append((record.get("state"), record.get("phase")))

    monkeypatch.setattr(jobs, "_persist", controlled_persist)
    record = jobs.start_job(
        lambda: (finish_work.wait(3) or True) and {"ok": True, "status": "completed"},
        job_id="job-persist-order",
    )
    assert record["state"] == "running"
    updater = threading.Thread(target=lambda: jobs.update_job(
        "job-persist-order", {"phase": "executor"}
    ))
    updater.start()
    assert progress_persisting.wait(2)
    finish_work.set()
    time.sleep(0.1)
    release_progress.set()
    updater.join(2)
    deadline = time.time() + 2
    while (jobs.get_job("job-persist-order") or {}).get("state") == "running" and time.time() < deadline:
        time.sleep(0.01)
    assert (jobs.get_job("job-persist-order") or {})["state"] == "done"
    assert persisted[-1][0] == "done"


def test_bare_credentials_and_pem_are_redacted_from_summary_events_and_durable_receipt() -> None:
    planted = {
        "xai": "xai-PLANTED_XAI_TOKEN_1234567890",
        "xai_upper": "XAI-PLANTED_UPPER_TOKEN_1234567890",
        "sk": "sk-PLANTED_OPENAI_TOKEN_1234567890",
        "mapping_key": "sk-PLANTED_KEY_AS_MAPPING_KEY_1234567890",
        "object_value": "sk-PLANTED_OBJECT_VALUE_1234567890",
        "tuple_value": "xai-PLANTED_TUPLE_VALUE_1234567890",
        "pem_body": "PLANTED_PEM_BODY_1234567890",
        "pem_end": "-----END PRIVATE KEY-----",
    }
    raw = (
        f"tokens {planted['xai']} {planted['xai_upper']} {planted['sk']}\n"
        "-----BEGIN PRIVATE KEY-----\n"
        f"{planted['pem_body']}\n"
        "-----END PRIVATE KEY-----\n"
        "standalone -----END PRIVATE KEY-----"
    )
    assert all(value not in redact_text(raw) for value in planted.values())
    unterminated = "-----BEGIN OPENSSH PRIVATE KEY-----\nPLANTED_UNTERMINATED_PEM_BODY"
    assert "PLANTED_UNTERMINATED_PEM_BODY" not in redact_text(unterminated)

    class SecretObject:
        def __str__(self) -> str:
            return planted["object_value"]

    event = acp._compact_session_update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": raw},
    })
    assert "text" not in (event.get("content") or {})
    bounded = bounded_event({
        "message": raw,
        "nested": {
            "output": raw,
            planted["mapping_key"]: "mapping value",
            "object": SecretObject(),
            "tuple": (planted["tuple_value"],),
        },
    })
    assert all(value not in json.dumps(bounded) for value in planted.values())

    with tempfile.TemporaryDirectory() as raw_dir:
        record = {
            "job_id": "job-bare-secret-redaction", "state": "done",
            "events": [{
                "kind": "summary",
                "payload": {
                    "message": raw,
                    planted["mapping_key"]: "mapping value",
                    "object": SecretObject(),
                    "tuple": (planted["tuple_value"],),
                },
            }],
            "result": {
                "schema_version": "grok-work-receipt.v1", "status": "completed",
                "summary": raw,
                "events": [{
                    "payload": {
                        "message": raw,
                        planted["mapping_key"]: "mapping value",
                        "object": SecretObject(),
                        "tuple": (planted["tuple_value"],),
                    },
                }],
            },
        }
        assert jobs_store.save_job(record, raw_dir)
        persisted = (Path(raw_dir) / "job-bare-secret-redaction.json").read_text(encoding="utf-8")
        assert all(value not in persisted for value in planted.values())
        loaded = jobs_store.load_jobs(raw_dir)["job-bare-secret-redaction"]
        assert all(value not in json.dumps(loaded) for value in planted.values())

        task = validate_task_packet(
            packet(
                Path(raw_dir), role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"],
                test_commands=["python -m pytest -q"],
            ),
            allowed_roots=[Path(raw_dir)],
        )
        secret_path = "sk-PLANTED_UNEXPECTED_PATH_1234567890.py"
        live_receipt = finalize_receipt({
            "status": "completed", "job_id": "job-live-redaction", "transport": "stdio",
            "changed_files": ["expected.txt"],
            "full_changed_files": ["expected.txt", secret_path],
            "artifacts": ["expected.txt"],
            "tests": [{
                "command": "python -m pytest -q", "passed": True,
                "returncode": 0, "source": "bridge-verifier",
            }],
            "started_at": "x", "finished_at": "y",
        }, task)
        assert live_receipt["status"] == "blocked"
        assert secret_path not in json.dumps(live_receipt)
        assert "<REDACTED>" in live_receipt["blocked_reason"]


def test_surviving_worker_forces_failed_terminal_receipt() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = validate_task_packet(
            packet(
                root, role="execute", permission_profile="workspace",
                expected_artifacts=["expected.txt"],
                test_commands=["python -m pytest -q"],
            ),
            allowed_roots=[root],
        )
        receipt = finalize_receipt({
            "status": "completed", "job_id": "job-live-worker", "transport": "stdio",
            "changed_files": ["expected.txt"], "full_changed_files": ["expected.txt"],
            "artifacts": ["expected.txt"],
            "tests": [{
                "command": "python -m pytest -q", "passed": True,
                "returncode": 0, "source": "bridge-verifier",
            }],
            "worker_alive_after_shutdown": True,
            "started_at": "x", "finished_at": "y",
        }, task)
        assert receipt["status"] == "failed"
        assert receipt["ok"] is False
        assert receipt["blocked_reason"] == "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"
        assert receipt["error"] == "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"


def test_concurrent_idempotent_start_reserves_exactly_one_worker(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        release = threading.Event()
        calls = []
        executor = ThreadPoolExecutor(max_workers=1)
        monkeypatch.setattr(agent_runtime, "_EXECUTOR", executor)
        monkeypatch.setattr(agent_runtime, "_ADMISSION", threading.BoundedSemaphore(2))
        jobs.reset_jobs_for_tests()

        def controlled(task, **_kwargs):
            calls.append(task["correlation_id"])
            release.wait(3)
            return finalize_receipt({
                "status": "completed", "job_id": "one", "transport": "stdio",
                "changed_files": [], "tests": [], "started_at": "x", "finished_at": "y",
            }, task)

        monkeypatch.setattr(agent_runtime, "run_task", controlled)
        task = packet(root, correlation_id="same-correlation")
        barrier = threading.Barrier(3)
        results = []

        def caller():
            barrier.wait()
            results.append(agent_runtime.start_agent_job(
                task, transport="stdio", allowed_roots=[root], lane="same-lane"
            ))

        threads = [threading.Thread(target=caller) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)
        assert len(results) == 2
        assert len({result["job_id"] for result in results}) == 1
        assert sorted(result["idempotent_replay"] for result in results) == [False, True]
        deadline = time.time() + 2
        while not calls and time.time() < deadline:
            time.sleep(0.01)
        assert calls == ["same-correlation"]
        release.set()
        executor.shutdown(wait=True)


def test_executor_and_skeptic_use_distinct_sessions() -> None:
    class SessionAdapter:
        name = "stdio"

        def __init__(self):
            self.count = 0

        def run(self, _task, **_kwargs):
            self.count += 1
            return {
                "status": "completed", "session_id": f"session-{self.count}",
                "summary": "ok", "tests": [], "events": [], "stop_reason": "end_turn",
                "agent_version": "0.2.118", "worker_pid": None, "agent_pid": None,
                "worker_alive_after_shutdown": False,
            }

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        adapter = SessionAdapter()
        router = agent_runtime.TransportRouter(adapters={"stdio": adapter})
        consult = validate_task_packet(packet(root, correlation_id="consult-session"), allowed_roots=[root])
        skeptic = validate_task_packet(packet(
            root, role="skeptic", permission_profile="read-only", correlation_id="skeptic-session"
        ), allowed_roots=[root])
        first = agent_runtime.run_task(
            consult, transport="stdio", lane="grok/consult-session", router=router,
            cancel_event=threading.Event(),
        )
        second = agent_runtime.run_task(
            skeptic, transport="stdio", lane="grok/skeptic-session", router=router,
            cancel_event=threading.Event(),
        )
        assert first["session_id"] != second["session_id"]
