"""Adaptive Session Protocol v1 — compact host↔MCP contract.

Unofficial community project. No OAuth/API material in outputs.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from .economy import (
    ECONOMY_DEFAULT_MAX_TURNS,
    ECONOMY_DEFAULT_TIMEOUT_SECONDS,
    compact_job_record,
    compact_poll_enabled,
    economy_enabled,
)
from . import jobs as jobs_mod
from .guard import SERVER_VERSION
from .status import probe_auth_presence, probe_grok_version

INTENTS = frozenset(
    {
        "brainstorm",
        "execute",
        "verify",
        "install",
        "update",
        "triage",
        "feedback",
        "auto",
    }
)

_HOST_MSG_MAX = 500
_PAYLOAD_SOFT_MAX = 1800  # chars JSON target ≤2KB
_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|oauth|authorization|bearer\s+[a-z0-9._\-]{12,}|sk-[a-z0-9]{10,}|"
    + "gh"
    + "p_"
    + r"[a-z0-9]+|xai-[a-z0-9]+)"
)

# intent → mode + skill_ref + tools
_ROUTES: dict[str, dict[str, Any]] = {
    "install": {
        "mode": "install",
        "skill_ref": "references/install.md",
        "tools": [],
        "next": "Run one-command install + grok login; re-call session_begin.",
    },
    "update": {
        "mode": "update",
        "skill_ref": "references/update.md",
        "tools": [],
        "next": "Run skills/grok-mcp/scripts/update_mcp.sh then session_begin.",
    },
    "triage": {
        "mode": "triage",
        "skill_ref": "references/security.md",
        "tools": ["grok_agent_status", "grok_delegate_doctor"],
        "next": "Fix gate failures; after 2+ product bugs use feedback mode.",
    },
    "brainstorm": {
        "mode": "brainstorm",
        "skill_ref": "references/brainstorm.md",
        "tools": ["grok_agent_consult", "grok_agent_review"],
        "next": "Consult/review only; no execute until user confirms.",
    },
    "execute": {
        "mode": "execute",
        "skill_ref": "references/execute.md",
        "tools": ["grok_agent_execute", "grok_agent_poll", "grok_agent_cancel"],
        "next": "One tight execute → poll job_id → session_end.",
    },
    "verify": {
        "mode": "verify",
        "skill_ref": "references/verify.md",
        "tools": ["grok_agent_poll", "grok_agent_status", "grok_agent_review"],
        "next": "Poll/status only; re-execute only on confirmed fail.",
    },
    "feedback": {
        "mode": "feedback",
        "skill_ref": "references/feedback.md",
        "tools": [],
        "next": "Draft scrubbed GitHub issue via templates/issue.md.",
    },
    "operate": {
        "mode": "operate",
        "skill_ref": "references/operate.md",
        "tools": ["grok_agent_status", "grok_agent_economy"],
        "next": "Pick brainstorm/execute/verify from task size.",
    },
}

_sessions: dict[str, dict[str, Any]] = {}
_compact_session = False


def session_compact_active() -> bool:
    return _compact_session or compact_poll_enabled()


def enable_session_economy() -> None:
    """Turn on compact economy for this process (session_begin)."""
    global _compact_session
    _compact_session = True
    os.environ.setdefault("GROK_DELEGATE_ECONOMY", "1")
    os.environ.setdefault("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")


def _clip(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def scrub_secrets(text: str) -> str:
    return _SECRETISH.sub("[REDACTED]", text)


def _json_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def _shrink(obj: dict[str, Any], soft_max: int = _PAYLOAD_SOFT_MAX) -> dict[str, Any]:
    """Drop optional verbose keys until under soft max."""
    out = dict(obj)
    for key in ("playbook_hint", "defaults_detail", "debug"):
        if _json_size(out) <= soft_max:
            break
        out.pop(key, None)
    # clip long strings
    for k, v in list(out.items()):
        if isinstance(v, str) and len(v) > 400:
            out[k] = _clip(v, 400)
    if _json_size(out) > soft_max:
        out = {
            "ok": out.get("ok", True),
            "protocol": out.get("protocol", "session/v1"),
            "session_id": out.get("session_id"),
            "mode": out.get("mode"),
            "next_step": out.get("next_step"),
            "truncated": True,
        }
    return out


def resolve_gate(
    *,
    allowed_roots: Sequence[Path | str] | None = None,
    which=None,
    subprocess_runner=None,
) -> dict[str, Any]:
    """Cheap gate: binary / auth presence / roots — no secrets."""
    import shutil

    which_fn = which or shutil.which
    bin_path = which_fn("grok")
    binary_ok = bool(bin_path)
    auth_present = False
    version = None
    if binary_ok:
        try:
            ver = probe_grok_version(subprocess_runner=subprocess_runner, which=which_fn)
            version = ver.get("version")
            auth = probe_auth_presence(subprocess_runner=subprocess_runner, which=which_fn)
            auth_present = bool(auth.get("auth_present"))
        except Exception:
            pass
    roots: list[str] = []
    roots_ok = False
    try:
        from .server import load_allowed_roots

        loaded = load_allowed_roots(injected=allowed_roots) if allowed_roots is not None else load_allowed_roots()
        roots = [str(p) for p in loaded[:8]]
        roots_ok = bool(loaded)
    except Exception:
        env_roots = os.environ.get("GROK_DELEGATE_ALLOWED_ROOTS", "").strip()
        roots_ok = bool(env_roots)
        if env_roots:
            roots = [p.strip() for p in env_roots.split(os.pathsep) if p.strip()][:8]
    ready = binary_ok and auth_present and roots_ok
    return {
        "ready": ready,
        "cli": "grok",
        "binary_ok": binary_ok,
        "auth_ok": auth_present,
        "roots_ok": roots_ok,
        "roots": roots,
        "version": version,
        "server": SERVER_VERSION,
    }


def _auto_mode(gate: Mapping[str, Any], intent: str) -> str:
    if not gate.get("binary_ok") or not gate.get("auth_ok"):
        return "install" if not gate.get("binary_ok") else "triage"
    if not gate.get("roots_ok"):
        return "triage"
    if intent in INTENTS and intent != "auto":
        return intent
    return "operate"


def session_begin(
    intent: str = "auto",
    *,
    allowed_roots: Sequence[Path | str] | None = None,
    which=None,
    subprocess_runner=None,
) -> dict[str, Any]:
    intent_n = (intent or "auto").strip().lower()
    if intent_n not in INTENTS:
        return {
            "ok": False,
            "error_code": "INTENT_INVALID",
            "error": f"intent must be one of: {', '.join(sorted(INTENTS))}",
            "protocol": "session/v1",
        }
    enable_session_economy()
    gate = resolve_gate(
        allowed_roots=allowed_roots, which=which, subprocess_runner=subprocess_runner
    )
    mode = _auto_mode(gate, intent_n)
    # If gate broken, force install/triage even when user asked execute
    if mode in {"execute", "brainstorm", "verify", "operate", "feedback"} and not gate.get("ready"):
        mode = "install" if not gate.get("binary_ok") else "triage"
    route = _ROUTES.get(mode, _ROUTES["operate"])
    tools = list(route["tools"])
    if gate.get("ready") and mode not in {"install", "update"}:
        # always allow status first
        if "grok_agent_status" not in tools:
            tools = ["grok_agent_status", *tools]
        if "grok_agent_economy" not in tools and mode == "operate":
            tools = ["grok_agent_economy", *tools]
    sid = uuid.uuid4().hex[:12]
    rec = {
        "session_id": sid,
        "intent": intent_n,
        "mode": mode,
        "job_id": None,
        "started": True,
    }
    _sessions[sid] = rec
    out = {
        "ok": True,
        "protocol": "session/v1",
        "session_id": sid,
        "mode": mode,
        "gate_status": {
            "ready": gate["ready"],
            "binary_ok": gate["binary_ok"],
            "auth_ok": gate["auth_ok"],
            "roots_ok": gate["roots_ok"],
            "cli": "grok",
            "server": gate["server"],
        },
        "recommended_tools": tools,
        "defaults": {
            "max_turns": ECONOMY_DEFAULT_MAX_TURNS,
            "timeout_seconds": ECONOMY_DEFAULT_TIMEOUT_SECONDS,
            "reasoning_effort": "low",
        },
        "economy_flags": {
            "economy": True,
            "compact_poll": True,
            "verbose_default": False,
        },
        "skill_ref": route["skill_ref"],
        "next_step": route["next"] if gate.get("ready") or mode in {"install", "triage", "update"} else "Fix gate (CLI+login+roots), then session_begin again.",
        "disclaimer": "Unofficial community project — not xAI/Grok.",
    }
    if not gate.get("ready"):
        out["next_step"] = (
            "Gate failed: need grok on PATH, `grok login`, and GROK_DELEGATE_ALLOWED_ROOTS. "
            "See skill_ref install/security."
        )
    return _shrink(out)


def session_tick(
    *,
    session_id: str | None = None,
    job_id: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    enable_session_economy()
    jid = (job_id or "").strip() or None
    sid = (session_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess and not jid:
        jid = sess.get("job_id")
    state = "idle"
    phase = "waiting"
    pct = 0
    changed = 0
    tests_summary = None
    blockers: list[str] = []
    suggested = "Call session_begin if new work; else execute/poll."
    host_message = "No active job."
    compact: dict[str, Any] | None = None

    if jid:
        record = jobs_mod.get_job(jid)
        if record is None:
            state = "unknown_job"
            blockers.append("JOB_UNKNOWN")
            suggested = "Check job_id or list via grok_agent_poll without id."
            host_message = f"Unknown job {jid}."
        else:
            if sid and sess is not None:
                sess["job_id"] = jid
            compact = compact_job_record(record) if not verbose else dict(record)
            state = str(compact.get("state") or compact.get("status") or "running")
            phase = state
            st = state.lower()
            if st in {"completed", "ok", "success", "done"}:
                pct = 100
                phase = "done"
                suggested = "session_end for receipt."
            elif st in {"failed", "error", "cancelled", "canceled"}:
                pct = 100
                phase = "failed"
                blockers.append(str(compact.get("blocked_reason") or compact.get("error") or st))
                suggested = "session_end; maybe one tight re-execute."
            else:
                pct = 50
                phase = "running"
                suggested = "Poll again or wait; avoid re-sending full objective."
            cf = compact.get("changed_files") or compact.get("full_changed_files") or []
            if isinstance(cf, list):
                changed = len(cf)
            tests = compact.get("tests")
            if isinstance(tests, list):
                passed = sum(1 for t in tests if isinstance(t, Mapping) and t.get("passed"))
                tests_summary = f"{passed}/{len(tests)} passed"
            host_message = _clip(
                f"{state}: files={changed}"
                + (f" tests={tests_summary}" if tests_summary else "")
                + (f" block={blockers[0]}" if blockers else ""),
                _HOST_MSG_MAX,
            )
    else:
        host_message = "Idle session — no job_id yet."
        suggested = "Start work with recommended_tools from session_begin."

    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1",
        "session_id": sid,
        "job_id": jid,
        "state": state,
        "phase": phase,
        "percent": pct,
        "changed_files_count": changed,
        "tests_summary": tests_summary,
        "blockers": blockers,
        "suggested_host_action": suggested,
        "host_message": scrub_secrets(host_message)[:_HOST_MSG_MAX],
        "economy_compact": not verbose and session_compact_active(),
    }
    if verbose and compact is not None:
        out["job"] = compact
    elif compact is not None:
        # ultra-compact job snapshot
        out["job"] = {
            k: compact.get(k)
            for k in ("job_id", "state", "status", "lane", "branch", "summary", "blocked_reason")
            if k in compact
        }
        if out["job"].get("summary"):
            out["job"]["summary"] = _clip(str(out["job"]["summary"]), 240)
    return _shrink(out)


def session_end(
    *,
    session_id: str | None = None,
    job_id: str | None = None,
    suggest_issue: bool = False,
    note: str | None = None,
) -> dict[str, Any]:
    enable_session_economy()
    jid = (job_id or "").strip() or None
    sid = (session_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess and not jid:
        jid = sess.get("job_id")
    status = "ok"
    changed: list[str] | str = "none"
    tests = "n/a"
    job_label = jid or "none"
    next_step = "Done."
    blockers: list[str] = []

    if jid:
        record = jobs_mod.get_job(jid)
        if record is None:
            status = "blocked"
            blockers.append("JOB_UNKNOWN")
            next_step = "Invalid job_id."
        else:
            c = compact_job_record(record)
            st = str(c.get("state") or c.get("status") or "").lower()
            if st in {"completed", "ok", "success", "done"}:
                status = "ok"
                next_step = "Human reviews/merges grok/* if needed."
            elif st in {"failed", "error"}:
                status = "blocked"
                next_step = "Inspect blockers; one tight re-execute or human."
            elif st in {"cancelled", "canceled"}:
                status = "need-human"
                next_step = "Job cancelled."
            else:
                status = "need-human"
                next_step = "Job still running — poll or cancel first."
            cf = c.get("changed_files") or []
            if isinstance(cf, list) and cf:
                changed = [str(x) for x in cf[:12]]
            summary = c.get("summary")
            if summary:
                next_step = _clip(f"{next_step} {_clip(summary, 120)}", 200)
            tlist = c.get("tests")
            if isinstance(tlist, list) and tlist:
                passed = sum(1 for t in tlist if isinstance(t, Mapping) and t.get("passed"))
                tests = f"{passed}/{len(tlist)} passed"
            if c.get("blocked_reason"):
                blockers.append(str(c.get("blocked_reason")))
    elif note:
        next_step = _clip(note, 200)

    receipt = {
        "status": status,
        "job": job_label,
        "changed": changed,
        "tests": tests,
        "next": next_step,
        "blockers": blockers,
    }
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1",
        "session_id": sid,
        "receipt": receipt,
        "host_message": _clip(
            f"{status}: job={job_label} tests={tests}",
            _HOST_MSG_MAX,
        ),
        "disclaimer": "Unofficial — not xAI/Grok.",
    }
    if suggest_issue:
        draft = (
            f"## Summary\nSession {sid or '-'} ended status={status}\n"
            f"## Job\n{job_label}\n"
            f"## Blockers\n{', '.join(blockers) or 'none'}\n"
            f"## Env\nserver={SERVER_VERSION} (no secrets)\n"
            f"## Free-form\n{scrub_secrets(note or '')}\n"
        )
        out["suggest_issue"] = True
        out["issue_draft"] = scrub_secrets(_clip(draft, 1200))
        out["issue_repo"] = "zai-one/grok-mcp"
    if sid and sid in _sessions:
        _sessions[sid]["ended"] = True
    return _shrink(out)


def reset_sessions_for_tests() -> None:
    global _compact_session
    _sessions.clear()
    _compact_session = False
