"""One call, live progress, a finished receipt.

The operator's complaint was not about correctness. A job runs for minutes on a
thread inside this server, finishes, runs its verifier, commits its lane -- and
nothing wakes the host. `grok_agent_execute` hands back a job_id and the receipt
then sits in the registry until somebody thinks to ask. From the other side that
is indistinguishable from nothing happening, which is a fair reason to prefer a
terminal where output at least scrolls.

MCP has an answer for exactly this: the client passes a `progressToken` in
`_meta` and the server sends `notifications/progress` while it works. The bridge
implemented none of it, and every job tool was start-then-poll because a silent
call of that length gets killed by the client. So: a bounded blocking wait, with
progress on the wire, and both halves tested here.
"""

from __future__ import annotations

import time

import pytest

from grok_delegate import jobs, server


@pytest.fixture(autouse=True)
def clean_notifier():
    server.set_notifier(None)
    server._PROGRESS_TOKEN = None
    jobs._JOBS.clear()
    yield
    server.set_notifier(None)
    server._PROGRESS_TOKEN = None
    jobs._JOBS.clear()


# --- the token ------------------------------------------------------------------


def test_a_client_that_asks_for_progress_is_heard() -> None:
    assert server.progress_token_of({"_meta": {"progressToken": "abc"}}) == "abc"
    assert server.progress_token_of({"_meta": {"progressToken": 7}}) == 7


def test_a_client_that_does_not_ask_gets_nothing() -> None:
    """Sending progress to a client that never offered a token addresses nobody."""
    for params in ({}, {"_meta": {}}, {"_meta": {"progressToken": {"not": "scalar"}}}, None):
        assert server.progress_token_of(params) is None


def test_progress_without_a_token_is_not_an_error() -> None:
    sent: list = []
    server.set_notifier(sent.append)
    assert server.emit_progress(1.0) is False
    assert sent == []


def test_progress_frame_is_shaped_the_way_the_protocol_says() -> None:
    sent: list = []
    server.set_notifier(sent.append)
    server._PROGRESS_TOKEN = "tok"
    assert server.emit_progress(3.0, 10.0, "still working") is True
    frame = sent[-1]
    assert frame["jsonrpc"] == "2.0"
    assert frame["method"] == "notifications/progress"
    assert frame["params"] == {
        "progressToken": "tok", "progress": 3.0, "total": 10.0, "message": "still working",
    }


def test_a_broken_notifier_never_fails_the_call() -> None:
    """Progress is a courtesy. A client that closed its end must not lose the job."""
    def explode(_payload):
        raise OSError("pipe closed")

    server.set_notifier(explode)
    server._PROGRESS_TOKEN = "tok"
    assert server.emit_progress(1.0) is False


# --- the wait ---------------------------------------------------------------------


def _running(job_id: str = "j-wait") -> None:
    jobs._JOBS[job_id] = {
        "job_id": job_id, "state": jobs.STATE_RUNNING, "server_pid": 1,
        "lane": "grok/x", "tool": "grok_agent_execute", "phase": "executor",
        "started_at": time.time(), "result": None,
    }


def test_wait_blocks_until_terminal() -> None:
    """The point of the whole change: one call, and the answer is the receipt."""
    _running()

    def finish() -> None:
        time.sleep(0.2)
        jobs._JOBS["j-wait"]["state"] = "done"
        jobs._JOBS["j-wait"]["result"] = {"status": "completed", "summary": "did the thing"}

    import threading

    threading.Thread(target=finish, daemon=True).start()
    started = time.monotonic()
    out = server.handle_tool_call("grok_agent_poll", {"job_id": "j-wait", "wait_seconds": 10})
    assert out["ok"] is True
    assert out["state"] == "done", out
    assert time.monotonic() - started >= 0.15, "it returned before the job could finish"


def test_wait_gives_up_quietly_and_returns_what_a_poll_would() -> None:
    """A caller out of patience gets the running record, not an error."""
    _running()
    out = server.handle_tool_call("grok_agent_poll", {"job_id": "j-wait", "wait_seconds": 1})
    assert out["ok"] is True
    assert out["state"] == jobs.STATE_RUNNING


def test_wait_emits_progress() -> None:
    """Without this the call is silent for minutes, which is the whole complaint."""
    sent: list = []
    server.set_notifier(sent.append)
    server._PROGRESS_TOKEN = "tok"
    _running()
    server.handle_tool_call("grok_agent_poll", {"job_id": "j-wait", "wait_seconds": 1})
    assert sent, "a blocking wait told the client nothing"
    assert all(f["method"] == "notifications/progress" for f in sent)
    assert sent[-1]["params"]["progressToken"] == "tok"


def test_no_wait_still_returns_immediately() -> None:
    """The default has not moved: absent wait_seconds behaves exactly as before."""
    _running()
    started = time.monotonic()
    out = server.handle_tool_call("grok_agent_poll", {"job_id": "j-wait"})
    assert out["state"] == jobs.STATE_RUNNING
    assert time.monotonic() - started < 0.5


def test_a_terminal_job_does_not_wait_at_all() -> None:
    jobs._JOBS["j-done"] = {
        "job_id": "j-done", "state": "done", "server_pid": 1, "lane": "grok/x",
        "result": {"status": "completed"},
    }
    started = time.monotonic()
    out = server.handle_tool_call("grok_agent_poll", {"job_id": "j-done", "wait_seconds": 30})
    assert out["state"] == "done"
    assert time.monotonic() - started < 0.5, "it waited on a job that had already finished"


def test_wait_seconds_is_bounded_by_the_schema_and_the_handler() -> None:
    """A wait longer than the client's own patience returns nothing to anybody."""
    schema = next(
        t["inputSchema"] for t in server.list_tools() if t["name"] == "grok_agent_poll"
    )
    assert schema["properties"]["wait_seconds"]["maximum"] == server.MAX_POLL_WAIT_SECONDS


def test_junk_wait_seconds_is_refused_not_ignored() -> None:
    _running()
    out = server.handle_tool_call("grok_agent_poll", {"job_id": "j-wait", "wait_seconds": "soon"})
    assert out["ok"] is False
    assert out["error"] == "WAIT_SECONDS_INVALID"
