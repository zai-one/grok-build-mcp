"""The output budget must measure the agent, not the protocol around it.

Measured on a live job: the median `agent_message_chunk` frame is 456 bytes and
carries 2-8 bytes of text. Counting frames therefore charged a 25 KB answer
1.5-5.8 MB against a 1-2.4 MB cap, and the overrun did not bound the answer --
it raised, discarding every edit the agent had already made and reporting the
job as failed. Five jobs with an explicit length limit passed; three without it
died at `ACP_OUTPUT_LIMIT`.

Two properties, and they pull in opposite directions on purpose: an honest long
answer has to arrive whole, and an agent emitting nothing but envelopes still
has to be stopped.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from grok_delegate.acp import StdioACPTransport, payload_bytes
from grok_delegate.contracts import validate_task_packet

HERE = Path(__file__).resolve().parent
FAKE = HERE / "fake_acp_agent.py"


def _packet(root: Path, **overrides) -> dict:
    value = {
        "objective": "Return a bounded result",
        "role": "consult",
        "project_root": str(root),
        "permission_profile": "read-only",
        "max_turns": 5,
        "timeout_seconds": 20,
        "inputs": [],
        "constraints": [],
        "acceptance_criteria": [],
        "expected_artifacts": [],
        "correlation_id": "output-budget",
    }
    value.update(overrides)
    return value


def _run(root: Path, objective: str, cap: int, timeout_seconds: int = 20) -> dict:
    task = validate_task_packet(
        _packet(root, objective=objective, timeout_seconds=timeout_seconds), allowed_roots=[root]
    )
    transport = StdioACPTransport(
        grok_bin="grok",
        popen_factory=lambda _argv, **kwargs: subprocess.Popen([sys.executable, str(FAKE)], **kwargs),
        output_byte_cap=cap,
    )
    return transport.run(task, cwd=root, cancel_event=threading.Event())


# --- what counts -----------------------------------------------------------------


def test_the_envelope_is_not_the_agent_s_output() -> None:
    frame = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "01a0252a-fe3d-7670-8fcf-7357c08a3220",
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "hello"},
            },
        },
    }
    # Five bytes of text in a frame that is hundreds of bytes on the wire. Keys
    # are counted too -- a megabyte-long key is agent-produced bytes like any
    # other -- so the charge is not exactly five, but it is nowhere near the
    # frame.
    import json as _json

    wire = len(_json.dumps(frame, ensure_ascii=False).encode("utf-8"))
    charged = payload_bytes(frame)
    assert len("hello") < charged < wire / 4
    assert wire > 150


def test_an_identifier_key_stops_being_free_when_it_is_not_an_identifier() -> None:
    """`status` is a word. A hundred kilobytes under that key is not."""
    small = {"params": {"update": {"status": "in_progress"}}}
    huge = {"params": {"update": {"status": "X" * 100_000}}}
    assert payload_bytes(small) < 100
    assert payload_bytes(huge) > 100_000


def test_a_giant_key_is_counted() -> None:
    assert payload_bytes({"params": {"K" * 50_000: 1}}) > 50_000


def test_a_frame_with_no_text_costs_nothing() -> None:
    frame = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "01a0252a",
            "update": {"sessionUpdate": "agent_thought_chunk", "toolCallId": "t1"},
        },
    }
    # Only the two structural keys remain chargeable, so the cost is a
    # handful of bytes rather than the frame's hundreds.
    assert payload_bytes(frame) < 64


def test_non_ascii_is_measured_in_bytes() -> None:
    """Twelve bytes of Cyrillic must cost twelve, not six."""

    def frame(text: str) -> dict:
        return {"params": {"update": {"content": {"type": "text", "text": text}}}}

    empty = payload_bytes(frame(""))
    assert payload_bytes(frame("привет")) - empty == len("привет".encode("utf-8"))


def test_nested_lists_are_counted() -> None:
    def frame(first: str, second: str) -> dict:
        return {
            "params": {
                "prompt": [{"type": "text", "text": first}, {"type": "text", "text": second}]
            }
        }

    empty = payload_bytes(frame("", ""))
    assert payload_bytes(frame("ab", "cde")) - empty == 5


# --- the answer that used to die --------------------------------------------------


def test_a_long_answer_in_small_chunks_arrives_whole() -> None:
    """200 frames, ~91 KB of wire, ~4 KB of text, against the 16 KB floor.

    Counted as frames this trips the cap around frame 36. Counted as output it
    is nowhere near it, which is the whole point.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = _run(root, "CHUNKED_FIXTURE", cap=16_384)
    assert result["status"] == "completed"
    assert result["blocked_reason"] is None
    assert result["output_truncated"] is False
    assert result["summary"].startswith("chunk 00000")
    assert "chunk 00199" in result["summary"]
    assert result["output_payload_bytes"] < 16_384


# --- the runaway that still has to stop -------------------------------------------


def test_an_overrun_is_bounded_not_crashed() -> None:
    """The agent that answers our cancel ends as a cancel, with its work intact."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = _run(root, "OVERSIZED_ACK_FIXTURE", cap=16_384)
    assert result["status"] == "cancelled"
    assert result["blocked_reason"] == "ACP_OUTPUT_LIMIT"
    assert result["output_truncated"] is True
    assert result["output_cap_bytes"] == 16_384
    # The text collected before the cap is still there: a truncated answer is
    # worth more than a discarded one.
    assert result["summary"].startswith("Y")


def test_an_ignored_cancel_still_reports_the_real_cause() -> None:
    """Not `ACP_CANCEL_TIMEOUT`: we are the ones who asked it to stop."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = _run(root, "OVERSIZED_FIXTURE", cap=16_384, timeout_seconds=30)
    assert result["blocked_reason"] == "ACP_OUTPUT_LIMIT"
    assert result["status"] == "cancelled"
    assert result["output_truncated"] is True


def test_an_agent_that_sends_only_envelopes_is_still_stopped() -> None:
    """Payload accounting must not become a way to stream forever for free."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = _run(root, "ENVELOPE_FLOOD_FIXTURE", cap=16_384, timeout_seconds=25)
    assert result["status"] in {"cancelled", "failed"}
    assert result["blocked_reason"] in {"ACP_OUTPUT_LIMIT", "ACP_TIMEOUT"}


def test_a_resent_tool_output_is_charged_once(monkeypatch) -> None:
    """A running command's buffer is resent whole every time it grows.

    120 updates carrying a buffer that reaches ~3.4 KB cost ~205 KB if each
    frame is charged in full -- twelve times a 16 KB budget for one test run.
    This is the shape that killed the first live skeptic pass.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = _run(root, "RESENT_OUTPUT_FIXTURE", cap=16_384)
    assert result["status"] == "completed"
    assert result["output_truncated"] is False
    assert result["output_payload_bytes"] < 16_384


def test_two_tool_calls_are_charged_separately() -> None:
    from grok_delegate.acp import charge_payload

    seen: dict[str, int] = {}

    def frame(call_id: str, size: int) -> dict:
        return {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "s",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": call_id,
                    "rawOutput": {"output_preview": "." * size},
                },
            },
        }

    scaffold = payload_bytes(frame("a", 0))
    assert charge_payload(frame("a", 100), seen) == scaffold + 100
    assert charge_payload(frame("b", 50), seen) == scaffold + 50
    # Growth of a known call is charged as growth, scaffold included once.
    assert charge_payload(frame("a", 300), seen) == 200
    assert charge_payload(frame("a", 300), seen) == 0


def test_a_shrinking_buffer_is_not_a_refund() -> None:
    """A tool that truncates its own preview must not hand back budget."""
    from grok_delegate.acp import charge_payload

    seen: dict[str, int] = {}
    frame = lambda size: {
        "params": {"update": {"toolCallId": "a", "rawOutput": {"output_preview": "." * size}}}
    }
    scaffold = payload_bytes(frame(0))
    assert charge_payload(frame(400), seen) == scaffold + 400
    assert charge_payload(frame(10), seen) == 0
    assert charge_payload(frame(500), seen) == 100


def test_a_deeply_nested_frame_is_counted_not_crashed() -> None:
    """Recursing to Python's own limit would turn a bad frame into a dead job."""
    frame: dict = {"text": "bottom"}
    for _ in range(5000):
        frame = {"params": frame}
    counted = payload_bytes(frame)
    assert counted > 0


def test_the_receipt_carries_the_truncation_flags(tmp_path) -> None:
    """The adapter knowing is not the same as the caller being told.

    Found live: the skeptic's job reported `output_truncated: None` in the
    receipt while the adapter had set it, because `run_task` rebuilt the receipt
    from a fixed key list.
    """
    import subprocess
    import threading

    from grok_delegate import agent_runtime
    from grok_delegate.runner import ensure_lane_dir_ignored

    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@e.invalid"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True, capture_output=True)
    ensure_lane_dir_ignored(root)

    class _Truncating:
        name = "stdio"

        def run(self, task, *, cwd, cancel_event, event_sink=None):
            return {
                "status": "cancelled",
                "blocked_reason": "ACP_OUTPUT_LIMIT",
                "summary": "as far as it got",
                "tests": [],
                "events": [],
                "worker_written_files": [],
                "output_truncated": True,
                "output_payload_bytes": 20_000,
                "output_cap_bytes": 16_384,
            }

    task = validate_task_packet(
        _packet(root, objective="anything", role="skeptic"), allowed_roots=[root]
    )
    receipt = agent_runtime.run_task(
        task,
        transport="stdio",
        lane="truncation",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": _Truncating()}),
        cancel_event=threading.Event(),
    )
    assert receipt["output_truncated"] is True
    assert receipt["output_payload_bytes"] == 20_000
    assert receipt["output_cap_bytes"] == 16_384


def test_a_compact_poll_still_says_the_answer_was_cut(monkeypatch) -> None:
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record(
        {
            "ok": True,
            "job_id": "j",
            "state": "done",
            "result": {"status": "cancelled", "summary": "cut here", "output_truncated": True},
        }
    )
    assert out["output_truncated"] is True


def test_the_growth_ledger_is_bounded() -> None:
    """A CLI inventing a toolCallId per frame must not grow a dict forever.

    Past the bound new ids are charged in full, which is the old safe
    behaviour; ids already known keep being charged only for their growth.
    """
    from grok_delegate.acp import _TOOL_LEDGER_MAX, charge_payload

    seen: dict[str, int] = {}

    def frame(call_id: str, size: int) -> dict:
        return {"params": {"update": {"toolCallId": call_id, "rawOutput": {"output_preview": "." * size}}}}

    charge_payload(frame("first", 10), seen)
    for index in range(_TOOL_LEDGER_MAX + 50):
        charge_payload(frame(f"c{index}", 10), seen)
    assert len(seen) == _TOOL_LEDGER_MAX
    # The one we started with is still tracked, so its growth is still a delta,
    # even after the ledger filled up behind it.
    assert charge_payload(frame("first", 30), seen) == 20
