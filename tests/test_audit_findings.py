"""What eight Grok audits found in this bridge, pinned so it cannot come back.

Each audit read one dimension of the project with its own sixty turns and had to
cite `read: file:line` for every claim, with the citation re-checked against the
tree. Every test here corresponds to a finding that was then reproduced by hand
before anything was changed -- the reproduction is quoted in the docstring, and
the claims that did not reproduce are not here at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from grok_delegate import agent_runtime, server
from grok_delegate.acp import permission_decision
from grok_delegate.contracts import finalize_receipt, redact_text, validate_task_packet
from grok_delegate.economy import ECONOMY_MAX_RECORD, fit_poll_budget, wire_size

ROOT = Path(__file__).resolve().parent.parent


# --- the search gate --------------------------------------------------------------


def _search(**raw) -> dict:
    return {
        "toolCall": {"kind": "search", "title": "search", "toolCallId": "t1", "rawInput": dict(raw)},
        "options": [
            {"optionId": "allow", "kind": "allow_once"},
            {"optionId": "reject", "kind": "reject_once"},
        ],
    }


READ_ONLY = {"role": "skeptic", "permission_profile": "read-only", "test_commands": ["py -3 -m pytest tests -q"]}


def _allowed(params: dict, task: dict | None = None, cwd: Path | None = None) -> bool:
    decision = permission_decision(params, task or READ_ONLY, cwd or ROOT)
    return str(decision.get("optionId")) == "allow"


def test_naming_a_path_does_not_excuse_the_pattern(tmp_path: Path) -> None:
    """Measured: `glob **/.env*` refused, the same glob with `path: "."` allowed.

    The gate returned `_paths_confined(...)` the moment any path key was
    present, so every pattern check was skipped for a request that carried both.
    """
    assert not _allowed(_search(glob="**/.env*"), cwd=tmp_path)
    assert not _allowed(_search(path=".", glob="**/.env*"), cwd=tmp_path)


def test_a_pattern_is_judged_by_the_same_rule_as_a_path(tmp_path: Path) -> None:
    """`**/*.pem`, `**/*.key`, `id_rsa/**` and `.env.local/settings.json` were
    all allowed: the search gate read only the last component and knew nothing
    about suffixes."""
    for pattern in ("**/*.pem", "**/*.key", "id_rsa/**", ".env.local/settings.json", "**/.envrc"):
        assert not _allowed(_search(glob=pattern), cwd=tmp_path), pattern


def test_an_ordinary_search_still_runs(tmp_path: Path) -> None:
    """The gate that refuses everything is the gate people turn off."""
    assert _allowed(_search(glob="**/*.py"), cwd=tmp_path)
    assert _allowed(_search(pattern="def build_prompt"), cwd=tmp_path)


def test_a_pattern_inside_a_list_is_still_a_pattern(tmp_path: Path) -> None:
    """`str(['../outside/**'])` wraps the `../` in list punctuation, and the
    escape pattern -- anchored on a separator or the start -- stopped matching."""
    assert not _allowed(_search(glob=["../outside/**"]), cwd=tmp_path)
    assert not _allowed(_search(glob=["ok/**", "**/.env"]), cwd=tmp_path)
    assert _allowed(_search(glob=["src/**", "tests/**"]), cwd=tmp_path)


def test_a_source_file_named_after_a_secret_is_not_one(tmp_path: Path) -> None:
    """A live false refusal: the declared command `py -3 -m pytest
    tests/api_key.py -q` could not run, because `api_key` matched anywhere."""
    command = "py -3 -m pytest tests/api_key.py -q"
    task = {"role": "execute", "permission_profile": "workspace", "test_commands": [command]}
    params = {
        "toolCall": {"kind": "execute", "title": "run", "toolCallId": "t", "rawInput": {"command": command}},
        "options": [
            {"optionId": "allow", "kind": "allow_once"},
            {"optionId": "reject", "kind": "reject_once"},
        ],
    }
    assert _allowed(params, task, tmp_path)


def test_a_credential_named_command_is_still_refused(tmp_path: Path) -> None:
    """The other half of the same rule, so the loosening did not go too far."""
    for command in ("git show HEAD:.env", "cat id_rsa", "py -3 -m pytest api_key -q"):
        task = {"role": "execute", "permission_profile": "workspace", "test_commands": [command]}
        params = {
            "toolCall": {"kind": "execute", "title": "run", "toolCallId": "t", "rawInput": {"command": command}},
            "options": [
                {"optionId": "allow", "kind": "allow_once"},
                {"optionId": "reject", "kind": "reject_once"},
            ],
        }
        assert not _allowed(params, task, tmp_path), command


# --- the poll budget ---------------------------------------------------------------


def test_many_verifier_rows_still_fit_one_poll() -> None:
    """Measured before the fix: 64 rows of 2000-character preview produced a
    138174-byte record against a 16384-byte budget, with `economy_trimmed`
    None. `_TRIM_ORDER` named six fields and `tests` was not one of them."""
    tests = [
        {
            "command": f"py -3 -m pytest tests/test_{index}.py -q",
            "returncode": 0,
            "source": "bridge-verifier",
            "outcome": "passed",
            "passed": True,
            "output_preview": "." * 2_000,
        }
        for index in range(64)
    ]
    record = {
        "ok": True,
        "job_id": "j",
        "state": "done",
        "result": {"status": "completed", "tests": tests, "summary": "done"},
    }
    fitted = fit_poll_budget(record)
    assert wire_size(fitted) <= ECONOMY_MAX_RECORD
    assert "tests" in (fitted.get("economy_trimmed") or []), "a silent trim is the worse failure"
    # Trimmed, not dropped: which commands ran and how they ended is what
    # acceptance reads, and the coarse fallback that empties the record would
    # take it away. This is what separates the two.
    rows = (fitted.get("result") or {}).get("tests") or fitted.get("tests") or []
    assert rows, "the graceful trim must keep the rows and shorten the previews"
    assert all(len(str(row.get("output_preview") or "")) < 2_000 for row in rows)


def test_many_artifacts_still_fit_one_poll() -> None:
    record = {
        "ok": True,
        "job_id": "j",
        "state": "done",
        "result": {"status": "completed", "artifacts": [f"out/{i}/file.txt" for i in range(4000)]},
    }
    fitted = fit_poll_budget(record)
    assert wire_size(fitted) <= ECONOMY_MAX_RECORD
    assert "artifacts" in (fitted.get("economy_trimmed") or [])
    kept = (fitted.get("result") or {}).get("artifacts") or fitted.get("artifacts") or []
    assert kept, "a shortened list is more use than no list"


@pytest.mark.parametrize("summary_chars", [16_000, 16_200, 16_320, 16_360, 16_384, 20_000, 60_000])
def test_the_budget_holds_at_every_size_near_the_boundary(summary_chars: int) -> None:
    """The markers are part of the record, and they are added after it is
    measured. A single sample cannot show that: the failure is a boundary, so
    the test walks the boundary."""
    record = {
        "ok": True,
        "job_id": "j",
        "state": "done",
        "result": {"status": "completed", "summary": "s" * summary_chars},
    }
    fitted = fit_poll_budget(record)
    assert wire_size(fitted) <= ECONOMY_MAX_RECORD, fitted.get("economy_trimmed")


def test_the_markers_are_inside_the_budget_they_report() -> None:
    """Adding `economy_trimmed` after measuring is how a record came back nine
    bytes over the cap it had just been fitted to."""
    record = {
        "ok": True,
        "job_id": "j",
        "state": "done",
        "result": {"status": "completed", "summary": "s" * 40_000, "unified_diff": "+x\n" * 5_000},
    }
    fitted = fit_poll_budget(record)
    assert wire_size(fitted) <= ECONOMY_MAX_RECORD
    # The nested receipt is budgeted against what the envelope leaves it, so the
    # number reported is that share rather than the whole cap.
    assert 0 < fitted.get("economy_budget_chars", 0) <= ECONOMY_MAX_RECORD


# --- acceptance --------------------------------------------------------------------


def _task(**overrides) -> dict:
    task = {
        "objective": "do the thing",
        "role": "execute",
        "project_root": str(ROOT),
        "expected_artifacts": ["out.txt"],
        "test_commands": ["py -3 -m pytest -q"],
    }
    task.update(overrides)
    return task


def _receipt(**overrides) -> dict:
    receipt = {
        "status": "completed",
        "changed_files": ["out.txt"],
        "full_changed_files": ["out.txt"],
        "artifacts": ["out.txt"],
        "worker_written_files": ["out.txt"],
        "tests": [
            {
                "command": "py -3 -m pytest -q",
                "returncode": 0,
                "source": "bridge-verifier",
                "outcome": "passed",
                "passed": True,
            }
        ],
        "lane_commit": {"ok": True, "committed": True, "reason": None, "sha": "abc123"},
    }
    receipt.update(overrides)
    return receipt


def test_a_write_job_cannot_claim_the_read_only_commit_exemption() -> None:
    """Reproduced: the same receipt with `reason: NOT_A_WRITE_ROLE` and no sha
    came back `completed`. The block only runs for execute and fix, so the
    exemption could only ever be claimed by a role it does not apply to."""
    out = finalize_receipt(
        _receipt(lane_commit={"ok": True, "committed": False, "reason": "NOT_A_WRITE_ROLE", "sha": None}),
        _task(),
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"].startswith("LANE_COMMIT_MISSING")


def test_a_run_that_never_happened_is_not_evidence() -> None:
    """Reproduced: `outcome: not_run` with `passed: True` and
    `source: bridge-verifier` was accepted as the verifier's proof."""
    out = finalize_receipt(
        _receipt(
            tests=[
                {
                    "command": "py -3 -m pytest -q",
                    "returncode": 0,
                    "source": "bridge-verifier",
                    "outcome": "not_run",
                    "not_run_reason": "cancelled",
                    "passed": True,
                }
            ]
        ),
        _task(),
    )
    assert out["status"] == "blocked"
    assert out["blocked_reason"] == "TEST_EVIDENCE_MISSING"


def test_an_ordinary_receipt_is_still_accepted() -> None:
    assert finalize_receipt(_receipt(), _task())["status"] == "completed"


# --- redaction ----------------------------------------------------------------------


def test_a_short_password_is_still_a_password() -> None:
    """`password=hunter2` and `SECRET_KEY=secret` reached receipts intact: the
    bare-value rule requires eight characters."""
    assert "hunter2" not in redact_text("password=hunter2")
    assert "secret" not in redact_text("SECRET_KEY=secret").replace("SECRET_KEY", "")
    assert "s3cr3t" not in redact_text("client_secret: s3cr3t")


def test_the_redactor_still_does_not_eat_code() -> None:
    """The eight-character floor exists because a lower one turned
    `token: str = ""` into `token: <REDACTED> = ""` and lost the call in
    `password_hash = bcrypt(x)`."""
    assert redact_text('token: str = ""') == 'token: str = ""'
    assert redact_text("password_hash = bcrypt(x)") == "password_hash = bcrypt(x)"
    assert redact_text("password=None") == "password=None"


def test_a_value_is_not_redacted_twice() -> None:
    assert redact_text("password=hunter2hunter2").count("<REDACTED>") == 1


# --- the wire ------------------------------------------------------------------------


def _spawn_server() -> subprocess.Popen:
    env = dict(os.environ)
    env["GROK_DELEGATE_ALLOWED_ROOTS"] = str(ROOT)
    env["GROK_DELEGATE_PREWARM"] = "0"
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, r'{ROOT}'); from grok_delegate.server import main; sys.exit(main([]))",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


@pytest.mark.parametrize("header", [b"Content-Length: -5\r\n\r\n{}", b"Content-Length: 999999999\r\n\r\n{}"])
def test_a_malformed_frame_header_does_not_end_the_session(header: bytes) -> None:
    """Reproduced: after a healthy initialize, one `Content-Length: -5` frame
    made the process exit with code 0 -- `read(-5)` drains the pipe to EOF and
    the loop returns. From the host's side the bridge simply vanished."""
    proc = _spawn_server()
    try:
        time.sleep(1.5)
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
                }
            )
        )
        proc.stdin.flush()
        time.sleep(0.5)
        proc.stdin.write(header)
        proc.stdin.flush()
        time.sleep(1.0)
        assert proc.poll() is None, "the server exited on a malformed frame header"
    finally:
        proc.kill()


def test_the_text_framing_loop_survives_a_bad_header() -> None:
    """`serve_stdio` has two loops -- byte-correct and line-delimited -- and the
    tests only ever drove one of them. Mutation testing found the other: the
    negative-length guard could be deleted there with the suite still green."""
    import io

    request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    script = (
        "Content-Length: -5\r\n\r\n{}\n"
        f"Content-Length: {len(request)}\r\n\r\n{request}\n"
    )
    out = io.StringIO()
    server.serve_stdio(stdin=io.StringIO(script), stdout=out)
    written = out.getvalue()
    assert '"tools"' in written, "the loop stopped at the bad header instead of answering past it"


def test_a_container_where_a_string_was_declared_is_refused() -> None:
    """Probed live: `job_id: {"a": 1}` answered `JOB_UNKNOWN: unknown job_id:
    {'a': 1}` -- it stringified the dict and looked it up -- and
    `lane: {"a": 1}` started a job on a lane called `grok/a-1`."""
    out = server.handle_tool_call("grok_agent_poll", {"job_id": {"a": 1}}, allowed_roots=[ROOT])
    assert out["ok"] is False
    assert out["error"] == "ARGUMENTS_INVALID"
    assert "job_id" in out["message"]


def test_a_declared_argument_is_still_accepted() -> None:
    out = server.handle_tool_call("grok_agent_poll", {"job_id": "nope"}, allowed_roots=[ROOT])
    assert out["error"] == "JOB_UNKNOWN"


def test_cwd_is_advertised_because_the_handler_honours_it() -> None:
    """A client reading `tools/list` could not discover an argument the compat
    handlers have always used."""
    schemas = {tool["name"]: tool.get("inputSchema") or {} for tool in server.list_tools()}
    for name in ("grok_delegate", "grok_delegate_start"):
        assert "cwd" in (schemas[name].get("properties") or {}), name


# --- the lane lock -------------------------------------------------------------------


def test_a_review_lane_is_covered_by_the_lane_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`lane_is_busy` keys the lane a job owns. A read-only job standing in
    another lane through `review_lane` owned none, so with concurrency 2 two
    workers could hold one worktree."""
    root = tmp_path / "repo"
    root.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True, capture_output=True)
    (root / ".grok-mcp.json").write_text('{"preset": "cheap"}', encoding="utf-8")

    monkeypatch.setitem(agent_runtime._REVIEW_OCCUPANCY, "grok/held", "job-someone-else")
    out = agent_runtime.start_agent_job(
        {
            "objective": "write something",
            "role": "execute",
            "project_root": str(root),
            "correlation_id": "lock-probe",
            "expected_artifacts": ["out.txt"],
            "test_commands": ["py -3 -c pass"],
        },
        transport="stdio",
        allowed_roots=[root],
        lane="held",
    )
    assert out.get("ok") is False
    assert out.get("error") == "LANE_BUSY"
