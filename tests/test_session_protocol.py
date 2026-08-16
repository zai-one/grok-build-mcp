from __future__ import annotations

import json

from grok_delegate.server import handle_tool_call, list_tools
from grok_delegate.session import bind_session_job, reset_sessions_for_tests, scrub_secrets

_PAYLOAD_CAP = 4096


def setup_function():
    reset_sessions_for_tests()


def _tool_schema(name: str) -> dict:
    tool = next(t for t in list_tools() if t["name"] == name)
    return tool["inputSchema"]


def assert_valid_against_schema(schema: dict, instance, path: str = "$") -> None:
    """Stdlib subset of JSON Schema: required, additionalProperties, types, bounds."""
    declared = schema.get("type")
    if declared:
        types = declared if isinstance(declared, list) else [declared]
        ok = False
        for item in types:
            if item == "object" and isinstance(instance, dict):
                ok = True
            elif item == "array" and isinstance(instance, list):
                ok = True
            elif item == "string" and isinstance(instance, str):
                ok = True
            elif item == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
                ok = True
            elif item == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
                ok = True
            elif item == "boolean" and isinstance(instance, bool):
                ok = True
            elif item == "null" and instance is None:
                ok = True
        assert ok, f"{path}: expected {types}, got {type(instance).__name__}"
    if "const" in schema:
        assert instance == schema["const"], f"{path}: const"
    if "enum" in schema and instance is not None:
        assert instance in schema["enum"], f"{path}: enum {schema['enum']}"
    if isinstance(instance, str):
        if "minLength" in schema:
            assert len(instance) >= schema["minLength"], path
        if "maxLength" in schema:
            assert len(instance) <= schema["maxLength"], path
    if isinstance(instance, list):
        if "minItems" in schema:
            assert len(instance) >= schema["minItems"], path
        if "maxItems" in schema:
            assert len(instance) <= schema["maxItems"], path
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                assert_valid_against_schema(item_schema, item, f"{path}[{index}]")
    if isinstance(instance, dict):
        props = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            extra = sorted(set(instance) - set(props))
            assert not extra, f"{path}: extra properties {extra}"
        for key in schema.get("required") or []:
            assert key in instance, f"{path}: missing required {key}"
        for key, value in instance.items():
            if key in props:
                assert_valid_against_schema(props[key], value, f"{path}.{key}")


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


def _begin_execute(tmp_path, **extra):
    args = {
        "intent": "execute",
        "goal": extra.pop("goal", "change tests/sample.py"),
        "host_budget": "small",
        "project_root": str(tmp_path),
        "expected_artifacts": extra.pop("expected_artifacts", ["tests/sample.py"]),
        "test_commands": extra.pop("test_commands", ["python -m pytest -q"]),
        "correlation_id": extra.pop("correlation_id", "corr-session"),
    }
    args.update(extra)
    return handle_tool_call(
        "grok_agent_session_begin",
        args,
        allowed_roots=[tmp_path],
        which=lambda n: "grok" if n == "grok" else None,
        subprocess_runner=_ready_runner,
    )


def test_session_next_listed():
    assert "grok_agent_session_next" in {t["name"] for t in list_tools()}


def test_navigator_install_loop():
    b = handle_tool_call(
        "grok_agent_session_begin",
        {"intent": "auto", "goal": "fix x", "host_budget": "small"},
        which=lambda _n: None,
    )
    assert b["protocol"] == "session/v1.2"
    assert b["plan"]
    sid = b["session_id"]
    cards = []
    for _ in range(6):
        n = handle_tool_call("grok_agent_session_next", {"session_id": sid})
        assert len(json.dumps(n)) < _PAYLOAD_CAP
        cards.append(n.get("card", {}).get("kind"))
        if n.get("done"):
            break
    assert "host_cmd" in cards or "end" in cards
    assert cards[-1] in {"end", "mcp_tool", "host_cmd"}
    e = handle_tool_call("grok_agent_session_end", {"session_id": sid})
    assert e.get("budget_report")


def test_begin_scrubs_goal():
    b = handle_tool_call(
        "grok_agent_session_begin",
        {"goal": "x api_key=sk-leaktest99", "host_budget": "tiny"},
        which=lambda _n: None,
    )
    assert "sk-leaktest99" not in json.dumps(b)


def test_scrub():
    assert "REDACTED" in scrub_secrets("oauth=abcsecretvalue")


def test_execute_card_is_full_task_packet(tmp_path):
    begin = _begin_execute(tmp_path)
    assert begin["ok"] is True
    assert begin["mode"] == "execute"
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": begin["session_id"]})
    card = nxt["card"]
    assert card["kind"] == "mcp_tool"
    assert card["tool"] == "grok_agent_execute"
    assert "session_id" not in card["args"]
    assert set(card["args"]) <= {"task", "transport", "lane"}
    task = card["args"]["task"]
    for key in ("objective", "project_root", "correlation_id", "expected_artifacts", "test_commands"):
        assert key in task
    assert task["expected_artifacts"] == ["tests/sample.py"]
    assert task["test_commands"] == ["python -m pytest -q"]
    assert_valid_against_schema(_tool_schema("grok_agent_execute"), card["args"])


def test_poll_card_is_job_id_only_after_bind(tmp_path):
    begin = _begin_execute(tmp_path)
    sid = begin["session_id"]
    first = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    assert first["card"]["tool"] == "grok_agent_execute"
    bind_session_job("job-abc123def456", session_id=sid)
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    card = nxt["card"]
    assert card["tool"] == "grok_agent_poll"
    assert card["args"] == {"job_id": "job-abc123def456"}
    assert "session_id" not in card["args"]
    assert_valid_against_schema(_tool_schema("grok_agent_poll"), card["args"])


def test_poll_without_job_id_is_skipped_not_injected(tmp_path):
    begin = _begin_execute(tmp_path)
    sid = begin["session_id"]
    handle_tool_call("grok_agent_session_next", {"session_id": sid})
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    card = nxt.get("card") or {}
    assert card.get("tool") != "grok_agent_poll"
    assert card.get("kind") == "end" or card.get("tool") == "grok_agent_session_end"


def test_execute_tool_result_binds_poll_job_id(tmp_path, monkeypatch):
    begin = _begin_execute(tmp_path)
    sid = begin["session_id"]
    handle_tool_call("grok_agent_session_next", {"session_id": sid})

    def fake_start(*_args, **_kwargs):
        return {"ok": True, "job_id": "job-from-execute", "state": "running"}

    monkeypatch.setattr("grok_delegate.server.start_agent_job", fake_start)
    started = handle_tool_call(
        "grok_agent_execute",
        {
            "task": {
                "objective": "change tests/sample.py",
                "project_root": str(tmp_path),
                "correlation_id": "corr-session",
                "expected_artifacts": ["tests/sample.py"],
                "test_commands": ["python -m pytest -q"],
            }
        },
        allowed_roots=[tmp_path],
    )
    assert started["job_id"] == "job-from-execute"
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": sid})
    assert nxt["card"]["tool"] == "grok_agent_poll"
    assert nxt["card"]["args"] == {"job_id": "job-from-execute"}
    assert_valid_against_schema(_tool_schema("grok_agent_poll"), nxt["card"]["args"])


def test_consult_card_matches_schema(tmp_path):
    begin = handle_tool_call(
        "grok_agent_session_begin",
        {
            "intent": "brainstorm",
            "goal": "how should auth work",
            "host_budget": "small",
            "project_root": str(tmp_path),
        },
        allowed_roots=[tmp_path],
        which=lambda n: "grok" if n == "grok" else None,
        subprocess_runner=_ready_runner,
    )
    assert begin["mode"] == "brainstorm"
    nxt = handle_tool_call("grok_agent_session_next", {"session_id": begin["session_id"]})
    card = nxt["card"]
    assert card["tool"] == "grok_agent_consult"
    assert_valid_against_schema(_tool_schema("grok_agent_consult"), card["args"])
