from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from grok_delegate.economy import (
    apply_task_economy_defaults,
    compact_job_record,
    economy_enabled,
    economy_playbook,
)
from grok_delegate.http_server import create_http_server
from grok_delegate.server import handle_tool_call, list_tools


def test_economy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_ECONOMY", raising=False)
    assert economy_enabled() is False
    assert apply_task_economy_defaults({"objective": "x"}) == {"objective": "x"}
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY", "1")
    out = apply_task_economy_defaults({"objective": "x"})
    assert out["max_turns"] == 12
    assert out["timeout_seconds"] == 600
    assert out["reasoning_effort"] == "low"
    # client wins
    out2 = apply_task_economy_defaults({"objective": "x", "max_turns": 3})
    assert out2["max_turns"] == 3


def test_compact_job_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_ECONOMY", "1")
    fat = {
        "ok": True,
        "job_id": "j1",
        "status": "completed",
        "summary": "s" * 5000,
        "changed_files": [f"f{i}.py" for i in range(50)],
        "events": [{"n": i} for i in range(20)],
        "tests": [
            {
                "command": "pytest -q",
                "passed": True,
                "returncode": 0,
                "source": "bridge-verifier",
                "output_preview": "x" * 999,
            }
        ],
        "diffstat": "1 file changed",
        "worktree_path": "/tmp/grok/lane",
        "unified_diff": "diff --git a/f.py b/f.py\n" + ("+" * 40_000),
        "secret_field": "should-drop",
    }
    compact = compact_job_record(fat)
    assert compact["economy_compact"] is True
    assert len(compact["summary"]) <= 1501
    assert len(compact["changed_files"]) == 24
    # Per-field caps keep the four newest events; the record budget then takes
    # them back first, because on the poll that is fat enough to need trimming --
    # the final one, the only one carrying a diff -- events are the field a host
    # can most afford to lose and the diff is what it came for.
    assert 1 <= len(compact["events"]) <= 4
    assert "output_preview" not in compact["tests"][0]
    assert "secret_field" not in compact
    assert compact["worktree_path"] == "/tmp/grok/lane"
    assert compact["diffstat"] == "1 file changed"
    assert compact["unified_diff"].endswith("\n…(truncated)")
    assert len(compact["unified_diff"].encode("utf-8")) <= 16_384 + 20


def test_economy_tool_listed_and_callable() -> None:
    names = {t["name"] for t in list_tools()}
    assert "grok_agent_economy" in names
    result = handle_tool_call("grok_agent_economy", {})
    assert result.get("ok") is True
    assert "do" in result and "dont" in result
    assert "Unofficial" in result.get("disclaimer", "")
    play = economy_playbook()
    assert play["economy"] is True


def test_http_health_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_DELEGATE_HTTP_TOKEN", "test-local-token-not-oauth")
    server = create_http_server(host="127.0.0.1", port=0, token="test-local-token-not-oauth")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        health = urlopen(f"http://{host}:{port}/healthz", timeout=2)
        assert json.loads(health.read().decode())["ok"] is True
        req = Request(
            f"http://{host}:{port}/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "grok_agent_economy", "arguments": {}},
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-local-token-not-oauth",
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
        assert body["result"]["structuredContent"]["ok"] is True
        bad = Request(
            f"http://{host}:{port}/mcp",
            data=b"{}",
            headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(bad, timeout=2)
        assert raised.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        from grok_delegate.contracts import reset_secret_needles_for_tests

        reset_secret_needles_for_tests()


def test_one_poll_stays_inside_one_budget_however_big_the_job(monkeypatch) -> None:
    """Per-field caps are not a budget: `ECONOMY_MAX_UNIFIED_DIFF` alone is all of it.

    A job with a large diff produced a "compact" record of 22 KB while every
    individual cap was respected, and the host paid for it on the poll that
    matters most -- the last one, the only one carrying the diff.
    """
    import json as _json

    from grok_delegate.economy import ECONOMY_MAX_RECORD, compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    record = {
        "ok": True, "job_id": "j", "state": "done",
        "result": {
            "status": "completed", "summary": "s" * 9000,
            "changed_files": [f"file{i}.py" for i in range(400)],
            "unified_diff": "+padding padding padding\n" * 9000,
            "diffstat": "d" * 4000, "artifacts": ["app.py"], "tests": [],
        },
    }
    out = compact_job_record(record)
    assert len(_json.dumps(out, ensure_ascii=False)) <= ECONOMY_MAX_RECORD


def test_a_trimmed_receipt_says_which_fields_gave_way(monkeypatch) -> None:
    """Silent truncation reads as "that is all there was", which is the worse lie."""
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record(
        {"ok": True, "job_id": "j", "state": "done",
         "result": {"status": "completed", "unified_diff": "+x\n" * 20000}}
    )
    assert "unified_diff" in out["economy_trimmed"]
    assert out["economy_budget_chars"] > 0
    assert out["unified_diff"].endswith("…(truncated)")


def test_a_small_receipt_is_left_exactly_as_it_was(monkeypatch) -> None:
    """The budget must not announce itself on a job that never approached it."""
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record(
        {"ok": True, "job_id": "j", "state": "done",
         "result": {"status": "completed", "summary": "done", "unified_diff": "+x\n"}}
    )
    assert "economy_trimmed" not in out
    assert out["unified_diff"] == "+x\n"


def test_the_budget_is_measured_in_what_the_wire_actually_carries() -> None:
    """Characters of an escaped dump are not bytes on the wire, and it matters.

    Every write in `server.py` serialises `ensure_ascii=False` and encodes UTF-8.
    A harness measuring `json.dumps(...)` with the default escaping read a
    Cyrillic receipt at roughly three times its real size and failed three live
    audits that were inside the promise; measuring characters of the unescaped
    form would undercount it instead, because Cyrillic is two bytes each.
    """
    import json as _json

    from grok_delegate.economy import ECONOMY_MAX_RECORD, fit_poll_budget, wire_size

    record = {
        "ok": True, "job_id": "j", "state": "done",
        "result": {"status": "completed",
                   "summary": "Мост отвечает reject_once, а CLI шлёт cancel сам. " * 400},
    }
    escaped = len(_json.dumps(record, default=str))
    assert escaped > wire_size(record), "escaping inflates non-ASCII; the wire does not"
    assert wire_size(record) > len(_json.dumps(record, ensure_ascii=False, default=str)), \
        "and characters undercount it, because these are two bytes each"

    out = fit_poll_budget(record)
    assert wire_size(out) <= ECONOMY_MAX_RECORD
    assert out["result"]["status"] == "completed"


def test_an_ascii_receipt_measures_the_same_either_way() -> None:
    """The distinction must not move the answer for the common case."""
    import json as _json

    from grok_delegate.economy import wire_size

    record = {"ok": True, "summary": "plain ascii summary", "state": "done"}
    assert wire_size(record) == len(_json.dumps(record, ensure_ascii=False, default=str))


def test_a_shortened_file_list_says_how_much_was_left_out(monkeypatch) -> None:
    """Eighty changed files arriving as twenty-four with no count is a lie.

    It reads as "that is all this job touched", which is the one thing a reviewer
    must not be wrong about. Found by the audit routine reading its own bridge.
    """
    from grok_delegate.economy import ECONOMY_MAX_CHANGED_FILES, compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record({
        "ok": True, "job_id": "j", "state": "done",
        "result": {"status": "completed", "summary": "ok",
                   "changed_files": [f"f{i}.py" for i in range(80)]},
    })
    assert len(out["changed_files"]) == ECONOMY_MAX_CHANGED_FILES
    assert out["changed_files_omitted"] == 80 - ECONOMY_MAX_CHANGED_FILES
    assert out["changed_files_total"] == 80


def test_a_short_list_gains_no_bookkeeping(monkeypatch) -> None:
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record({
        "ok": True, "job_id": "j", "state": "done",
        "result": {"status": "completed", "changed_files": ["a.py", "b.py"]},
    })
    assert "changed_files_omitted" not in out
    assert "changed_files_total" not in out


def test_the_lane_wide_file_list_reaches_a_compact_poll(monkeypatch) -> None:
    """`full_changed_files` sat in keep_keys and in no lift list, so a finished
    job's compact poll dropped it -- and it is what separates this run's work
    from everything already sitting in the lane."""
    from grok_delegate.economy import compact_job_record

    monkeypatch.setenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
    out = compact_job_record({
        "ok": True, "job_id": "j", "state": "done",
        "result": {"status": "completed", "changed_files": ["a.py"],
                   "full_changed_files": ["a.py", "older.py"]},
    })
    assert out["full_changed_files"] == ["a.py", "older.py"]
