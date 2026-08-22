"""Navigator budget, compact isolation, Russian auto-mode, installer lane home.

Measured: one session_begin setdefault GROK_DELEGATE_ECONOMY_COMPACT_POLL so
every later poll in the process compacted; each session_next spent both a poll
and a tool call, so host_budget=small starved after three remaining steps;
intent=auto treated Russian verbs as operate; installers wrote lanes to a
sibling .grok-mcp-lanes pytest would walk.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from grok_delegate.economy import compact_job_record, compact_poll_enabled
from grok_delegate import jobs as jobs_mod
from grok_delegate import session as session_mod
from grok_delegate.session import (
    _auto_mode,
    enable_session_economy,
    session_begin,
    session_compact_active,
    session_end,
    session_next,
    session_tick,
)


READY = {"binary_ok": True, "auth_ok": True, "roots_ok": True, "ready": True}
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch, tmp_path):
    session_mod._sessions.clear()
    monkeypatch.setattr(session_mod, "probe_grok_version", lambda **_k: {"version": "1.0.5"})
    monkeypatch.setattr(session_mod, "cached_auth_presence", lambda **_k: {"auth_present": True})
    monkeypatch.setenv("GROK_DELEGATE_ALLOWED_ROOTS", str(tmp_path))
    yield
    session_mod._sessions.clear()


def _session(tmp_path, **kwargs) -> str:
    kwargs.setdefault("goal", "fix the parser")
    kwargs.setdefault("host_budget", "small")
    kwargs.setdefault("intent", "execute")
    begun = session_begin(
        project_root=str(tmp_path),
        which=lambda _name: "grok",
        **kwargs,
    )
    return str(begun["session_id"])


def _clear_economy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_ECONOMY", raising=False)
    monkeypatch.delenv("GROK_DELEGATE_ECONOMY_COMPACT_POLL", raising=False)


# --- compact belongs to the session -------------------------------------------


def test_session_begin_does_not_write_economy_env(monkeypatch, tmp_path) -> None:
    """setdefault of those two vars is how a neighbour inherited compact polls."""
    _clear_economy_env(monkeypatch)
    _session(tmp_path)
    assert "GROK_DELEGATE_ECONOMY" not in os.environ
    assert "GROK_DELEGATE_ECONOMY_COMPACT_POLL" not in os.environ


def test_enable_session_economy_does_not_write_economy_env(monkeypatch) -> None:
    _clear_economy_env(monkeypatch)
    enable_session_economy()
    assert "GROK_DELEGATE_ECONOMY" not in os.environ
    assert "GROK_DELEGATE_ECONOMY_COMPACT_POLL" not in os.environ


def test_a_session_does_not_leave_process_wide_compact_on(monkeypatch, tmp_path) -> None:
    """After begin returns, grok_agent_poll for any project must follow env."""
    _clear_economy_env(monkeypatch)
    _session(tmp_path)
    assert session_compact_active() is False
    assert compact_poll_enabled() is False


def test_a_sessions_compact_does_not_clip_a_neighbour_job(monkeypatch, tmp_path) -> None:
    _clear_economy_env(monkeypatch)
    _session(tmp_path, job_id="sess-job")
    fat = {
        "ok": True,
        "job_id": "neighbour",
        "status": "completed",
        "summary": "s" * 5000,
    }
    record = compact_job_record(fat)
    assert record["summary"] == fat["summary"]
    assert record.get("economy_compact") is not True


def _fat_job(job_id: str) -> dict:
    return {
        "ok": True,
        "job_id": job_id,
        "status": "completed",
        "summary": "s" * 5000,
    }


def test_this_sessions_poll_is_compact(monkeypatch, tmp_path) -> None:
    """The host pays for grok_agent_poll (execute plan step 2), not a tick flag."""
    _clear_economy_env(monkeypatch)
    _session(tmp_path, job_id="sess-job")
    record = compact_job_record(_fat_job("sess-job"))
    assert record.get("economy_compact") is True
    assert len(record["summary"]) < 5000


def test_this_session_still_reports_compact_on_its_own_tick(monkeypatch, tmp_path) -> None:
    _clear_economy_env(monkeypatch)
    sid = _session(tmp_path, job_id="sess-job")
    record = compact_job_record(_fat_job("sess-job"))
    assert record.get("economy_compact") is True
    assert len(record["summary"]) < 5000
    out = session_tick(session_id=sid)
    assert out["economy_compact"] is True


def test_compact_does_not_outlive_the_session(monkeypatch, tmp_path) -> None:
    _clear_economy_env(monkeypatch)
    sid = _session(tmp_path, job_id="sess-job")
    assert session_mod._sessions[sid]["compact"] is True
    assert compact_job_record(_fat_job("sess-job")).get("economy_compact") is True
    session_end(session_id=sid)
    assert session_mod._sessions[sid].get("compact") is False
    assert compact_job_record(_fat_job("sess-job")).get("economy_compact") is not True
    out = session_tick(session_id=sid)
    assert out["economy_compact"] is False


# --- one navigator step spends one budget -------------------------------------


def test_a_navigator_step_spends_a_poll_not_a_tool_call(tmp_path) -> None:
    """small is 4 polls / 6 tools; charging both left 3 and 5, then three steps."""
    sid = _session(tmp_path, host_budget="small")
    out = session_next(session_id=sid)
    remaining = out["budget_remaining"]
    assert remaining["polls"] == 3
    assert remaining["tool_calls"] == 6
    sess = session_mod._sessions[sid]
    assert sess["polls_used"] == 1
    assert sess["tool_calls_used"] == 0


def test_tool_used_is_what_spends_the_tool_budget(tmp_path) -> None:
    sid = _session(tmp_path, host_budget="small")
    session_next(session_id=sid)
    session_tick(session_id=sid, tool_used="grok_agent_status")
    sess = session_mod._sessions[sid]
    assert sess["tool_calls_used"] == 1
    assert sess["polls_used"] == 2


def test_small_budget_is_not_exhausted_after_three_next_cards(monkeypatch, tmp_path) -> None:
    """The skill loop is session_next until done; dual charge killed it at 3+1."""
    monkeypatch.setattr(
        jobs_mod, "get_job", lambda _jid: {"job_id": _jid, "state": "running"}
    )
    sid = _session(tmp_path, host_budget="small")
    session_mod._sessions[sid]["job_id"] = "job-forever"
    session_mod._sessions[sid]["plan_step"] = 1
    seen = [session_next(session_id=sid) for _ in range(3)]
    assert all(not step.get("force_end") for step in seen)
    remaining = seen[-1]["budget_remaining"]
    assert remaining["polls"] == 1
    assert remaining["tool_calls"] == 6


# --- intent=auto reads Russian ------------------------------------------------


@pytest.mark.parametrize(
    ("english", "russian", "mode"),
    [
        ("check the repository", "проверь репозиторий", "verify"),
        ("review this diff", "отревьюй этот диф", "verify"),
        ("fix the failing test", "почини падающий тест", "execute"),
    ],
)
def test_auto_mode_agrees_for_the_measured_english_russian_pairs(
    english: str, russian: str, mode: str
) -> None:
    assert _auto_mode(READY, "auto", english) == mode
    assert _auto_mode(READY, "auto", russian) == mode


@pytest.mark.parametrize(
    ("goal", "mode"),
    [
        ("проверить репозиторий", "verify"),
        ("Проверь репозиторий", "verify"),
        ("ревью этого дифа", "verify"),
        ("аудит репозитория", "verify"),
        ("проаудируй этот диф", "verify"),
        ("сверь receipt", "verify"),
        ("исправь падающий тест", "execute"),
        ("пофикси падающий тест", "execute"),
        ("напиши тест", "execute"),
        ("сделай фикс", "execute"),
        ("добавь колонку", "execute"),
        ("реализуй приёмку", "execute"),
        ("обнови конфиг", "execute"),
        ("разбери варианты", "brainstorm"),
        ("изучи схему", "brainstorm"),
        ("расскажи как устроен мост", "brainstorm"),
    ],
)
def test_auto_mode_russian_vocabulary(goal: str, mode: str) -> None:
    assert _auto_mode(READY, "auto", goal) == mode


def test_cyrillic_mode_stems_need_a_word_boundary() -> None:
    assert _auto_mode(READY, "auto", "непроверь репозиторий") == "operate"


# --- installer lanes home -----------------------------------------------------


def test_installers_do_not_park_lanes_in_a_sibling_directory() -> None:
    """Fresh install wrote <parent>/.grok-mcp-lanes; pytest walks a sibling."""
    ps1 = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert ".grok-mcp-lanes" not in ps1
    assert ".grok-mcp-lanes" not in sh
    assert ".grok/lanes" in ps1
    assert ".grok/lanes" in sh
    # The override must stay unset unless the operator passed --lanes.
    # Banning the old sibling name is not the invariant: a renamed sibling
    # would still park unmerged work outside <project>/.grok/lanes.
    assert "GROK_DELEGATE_LANES_PARENT" not in ps1
    assert re.search(r'(?m)^LANES_PARENT=""$', sh)
    assert re.search(r'(?m)^LANES_EXPORT=""$', sh)
    assert 'if [ -n "$LANES_PARENT" ]' in sh
    env_body = re.search(r'cat > "\$ENV_FILE" <<ENV\n(.*?)\nENV', sh, re.S)
    assert env_body is not None
    assert "${LANES_EXPORT}" in env_body.group(1)
    assert "GROK_DELEGATE_LANES_PARENT" not in env_body.group(1)

