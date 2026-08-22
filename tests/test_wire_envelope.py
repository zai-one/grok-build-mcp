"""Three wire defects: pretty-printed dual copy, silent event drop, unbound consult.

Measured on this tree. Each test fails on the unfixed code.
"""

from __future__ import annotations

import json

from grok_delegate import jobs as jobs_mod
from grok_delegate.economy import (
    ECONOMY_MAX_EVENTS,
    ECONOMY_MAX_RECORD,
    assemble_tool_result,
    compact_job_record,
    fit_poll_budget,
    tool_result_wire_size,
    wire_size,
)
from grok_delegate.project_config import CONFIG_FILENAME, render_config
from grok_delegate.server import handle_jsonrpc, handle_tool_call
from grok_delegate.session import reset_sessions_for_tests


def setup_function():
    reset_sessions_for_tests()
    jobs_mod.reset_jobs_for_tests()


def _ready_runner(argv, cwd, timeout):
    argv = [str(a) for a in argv]
    if "version" in argv:
        stdout = json.dumps({"currentVersion": "9.9.9", "channel": "stable"})
    elif "models" in argv:
        stdout = "You are logged in with grok.com.\nAvailable models\nDefault model: grok\n"
    else:
        stdout = ""
    return {
        "ok": True,
        "returncode": 0,
        "stdout": stdout,
        "stderr": "",
        "timedOut": False,
    }


def _enable_project(tmp_path, preset="standard"):
    (tmp_path / CONFIG_FILENAME).write_text(render_config(preset), encoding="utf-8")
    return tmp_path


def _tools_call(name: str, arguments: dict | None = None, req_id: int = 1) -> dict:
    response = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )
    assert response is not None
    return response


def _fat_poll_record() -> dict:
    return {
        "ok": True,
        "job_id": "j",
        "state": "done",
        "result": {
            "status": "completed",
            "summary": "s" * 9000,
            "changed_files": [f"file{i}.py" for i in range(400)],
            "unified_diff": "+padding padding padding\n" * 9000,
            "diffstat": "d" * 4000,
            "artifacts": ["app.py"],
            "tests": [],
        },
    }


# --- 1. dual copy, compact text, budget on the assembled result ---------------


def test_content_text_is_compact_not_pretty() -> None:
    """indent=2 cost 1.72x on tools/list-shaped payloads (25 849 B vs 44 341 B)."""
    response = _tools_call("grok_agent_economy")
    payload = response["result"]
    body = payload["structuredContent"]
    text = payload["content"][0]["text"]
    compact = json.dumps(body, ensure_ascii=False, default=str, separators=(",", ":"))
    pretty = json.dumps(body, ensure_ascii=False, indent=2)
    assert text == compact
    assert len(text.encode("utf-8")) < len(pretty.encode("utf-8"))
    assert json.loads(text) == body


def test_the_budget_is_the_assembled_result_the_client_receives(monkeypatch) -> None:
    """A 14 923 B fitted poll left as 32 345 B JSON-RPC (2.17x) while the cap
    was enforced on the inner record. Dual copy halves the inner object; it
    does not double the bill.
    """
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    fitted = fit_poll_budget(_fat_poll_record())
    assembled = assemble_tool_result(fitted)
    assert tool_result_wire_size(fitted) <= ECONOMY_MAX_RECORD
    assert wire_size(assembled) <= ECONOMY_MAX_RECORD
    # Inner size is no longer the promise: the unfixed code already held the
    # inner record under 16 KiB while sending twice that.
    assert fitted.get("status") == "completed" or (fitted.get("result") or {}).get("status") == "completed"


def test_a_poll_on_the_wire_fits_the_cap(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    jobs_mod._JOBS["j"] = _fat_poll_record()
    response = _tools_call("grok_agent_poll", {"job_id": "j"})
    payload = response["result"]
    assert wire_size(payload) <= ECONOMY_MAX_RECORD
    text = payload["content"][0]["text"]
    assert text == json.dumps(
        payload["structuredContent"], ensure_ascii=False, default=str, separators=(",", ":")
    )
    assert json.loads(text) == payload["structuredContent"]


def test_compact_job_record_fits_the_assembled_result(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record(_fat_poll_record())
    assert tool_result_wire_size(out) <= ECONOMY_MAX_RECORD


# --- 2. events drop is counted the same way as changed_files ------------------


def test_a_compact_poll_counts_the_events_it_dropped(monkeypatch) -> None:
    """64 events in, 4 out, events_total and events_omitted both None."""
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record(
        {
            "ok": True,
            "job_id": "j",
            "state": "done",
            "events": [{"n": i} for i in range(64)],
            "result": {"status": "completed", "summary": "ok"},
        }
    )
    assert [row["n"] for row in out["events"]] == [60, 61, 62, 63]
    assert len(out["events"]) == ECONOMY_MAX_EVENTS
    assert out["events_omitted"] == 64 - ECONOMY_MAX_EVENTS
    assert out["events_total"] == 64


def test_a_short_event_list_gains_no_bookkeeping(monkeypatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record(
        {
            "ok": True,
            "job_id": "j",
            "state": "done",
            "events": [{"n": 1}, {"n": 2}],
            "result": {"status": "completed"},
        }
    )
    assert "events_omitted" not in out
    assert "events_total" not in out


# --- 3. consult/review bind to their session, never steal execute -------------


def _begin(tmp_path, *, intent: str, goal: str, correlation_id: str, **extra):
    args = {
        "intent": intent,
        "goal": goal,
        "host_budget": "small",
        "project_root": str(tmp_path),
        "correlation_id": correlation_id,
    }
    args.update(extra)
    return handle_tool_call(
        "grok_agent_session_begin",
        args,
        allowed_roots=[tmp_path],
        which=lambda n: "grok" if n == "grok" else None,
        subprocess_runner=_ready_runner,
    )


def test_a_consult_job_reaches_session_end(tmp_path, monkeypatch) -> None:
    """Observed live: consult reached completed and session_end said job none."""
    _enable_project(tmp_path)
    begin = _begin(
        tmp_path, intent="brainstorm", goal="how should auth work",
        correlation_id="corr-brainstorm",
    )
    assert begin["ok"] is True
    sid = begin["session_id"]
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    card = nxt["card"]
    assert card["tool"] == "grok_agent_consult"

    monkeypatch.setattr(
        "grok_delegate.server.start_agent_job",
        lambda *_a, **_k: {"ok": True, "job_id": "job-from-consult", "state": "running"},
    )
    jobs_mod._JOBS["job-from-consult"] = {
        "job_id": "job-from-consult",
        "state": "done",
        "result": {"status": "completed", "summary": "use a local login"},
    }
    started = handle_tool_call(
        "grok_agent_consult", card["args"], allowed_roots=[tmp_path],
    )
    assert started["job_id"] == "job-from-consult"

    ended = handle_tool_call("grok_agent_session_end", {"session_id": sid})
    assert ended["receipt"]["job"] == "job-from-consult"
    assert ended["receipt"]["job"] != "none"


def test_a_review_job_is_bound_to_its_session(tmp_path, monkeypatch) -> None:
    _enable_project(tmp_path)
    begin = _begin(
        tmp_path, intent="brainstorm", goal="review the auth design",
        correlation_id="corr-review",
    )
    sid = begin["session_id"]
    monkeypatch.setattr(
        "grok_delegate.server.start_agent_job",
        lambda *_a, **_k: {"ok": True, "job_id": "job-from-review", "state": "running"},
    )
    jobs_mod._JOBS["job-from-review"] = {
        "job_id": "job-from-review",
        "state": "done",
        "result": {"status": "completed", "summary": "looks fine"},
    }
    started = handle_tool_call(
        "grok_agent_review",
        {
            "task": {
                "objective": "review the auth design",
                "project_root": str(tmp_path),
                "correlation_id": "corr-review",
                "role": "skeptic",
            }
        },
        allowed_roots=[tmp_path],
    )
    assert started["job_id"] == "job-from-review"
    ended = handle_tool_call("grok_agent_session_end", {"session_id": sid})
    assert ended["receipt"]["job"] == "job-from-review"


def test_consult_does_not_steal_an_execute_session_poll_slot(tmp_path, monkeypatch) -> None:
    """Widening the bind set without changing the fallback parks consult on
    the execute session that is waiting for its own job.
    """
    _enable_project(tmp_path)
    begin = _begin(
        tmp_path, intent="execute", goal="change tests/sample.py",
        correlation_id="corr-session",
        expected_artifacts=["tests/sample.py"],
        test_commands=["python -m pytest -q"],
    )
    sid = begin["session_id"]
    handle_tool_call("grok_agent_session_next", {"session_id": sid})

    monkeypatch.setattr(
        "grok_delegate.server.start_agent_job",
        lambda *_a, **_k: {"ok": True, "job_id": "job-from-consult", "state": "running"},
    )
    consult = handle_tool_call(
        "grok_agent_consult",
        {
            "task": {
                "objective": "how should auth work",
                "project_root": str(tmp_path),
                "correlation_id": "other-corr",
                "role": "consult",
            }
        },
        allowed_roots=[tmp_path],
    )
    assert consult["job_id"] == "job-from-consult"
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    card = nxt.get("card") or {}
    assert card.get("args", {}).get("job_id") != "job-from-consult"
    assert card.get("tool") != "grok_agent_poll"


def test_consult_with_the_execute_cid_still_does_not_steal(tmp_path, monkeypatch) -> None:
    """Matching correlation_id is how write jobs bind; a read-only job with
    the same cid must not overwrite an execute session's poll slot.
    """
    _enable_project(tmp_path)
    begin = _begin(
        tmp_path, intent="execute", goal="change tests/sample.py",
        correlation_id="corr-shared",
        expected_artifacts=["tests/sample.py"],
        test_commands=["python -m pytest -q"],
    )
    sid = begin["session_id"]
    handle_tool_call("grok_agent_session_next", {"session_id": sid})
    monkeypatch.setattr(
        "grok_delegate.server.start_agent_job",
        lambda *_a, **_k: {"ok": True, "job_id": "job-from-consult", "state": "running"},
    )
    handle_tool_call(
        "grok_agent_consult",
        {
            "task": {
                "objective": "how should auth work",
                "project_root": str(tmp_path),
                "correlation_id": "corr-shared",
                "role": "consult",
            }
        },
        allowed_roots=[tmp_path],
    )
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    card = nxt.get("card") or {}
    assert card.get("args", {}).get("job_id") != "job-from-consult"
    assert card.get("tool") != "grok_agent_poll"
