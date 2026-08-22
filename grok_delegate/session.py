"""Adaptive Session Protocol v1.2 — plan compiler + budget guard + navigator.

Unofficial community project. No OAuth/API material in outputs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import jobs as jobs_mod
from .economy import (
    ECONOMY_DEFAULT_MAX_TURNS,
    ECONOMY_DEFAULT_TIMEOUT_SECONDS,
    compact_job_record,
    compact_poll_enabled,
    default_max_turns,
    default_reasoning_effort,
)
from .guard import SERVER_VERSION
from .status import cached_auth_presence, probe_grok_version

INTENTS = frozenset(
    {"brainstorm", "execute", "verify", "install", "update", "triage", "feedback", "auto"}
)
HOST_BUDGETS = frozenset({"tiny", "small", "normal"})
_BUDGET_PRESETS = {
    "tiny": {"max_tool_calls": 3, "max_polls": 2},
    "small": {"max_tool_calls": 6, "max_polls": 4},
    "normal": {"max_tool_calls": 12, "max_polls": 8},
}

# Real tool allowlist for plans
_ALLOW = frozenset(
    {
        "grok_agent_session_begin",
        "grok_agent_session_tick",
        "grok_agent_session_next",
        "grok_agent_session_end",
        "grok_agent_status",
        "grok_agent_economy",
        "grok_agent_consult",
        "grok_agent_review",
        "grok_agent_execute",
        "grok_agent_fix",
        "grok_agent_poll",
        "grok_agent_cancel",
        "grok_agent_start",
        "grok_delegate_doctor",
        "grok_delegate_status",
        # Both exist and both are legitimate plan steps: the update route hands
        # back a tool card, and PROJECT_NOT_ENABLED points at the project tool.
        "grok_agent_update",
        "grok_agent_project",
    }
)

_HOST_MSG_MAX = 500
_GOAL_MAX = 500
_PAYLOAD_SOFT_MAX = 4096
_WHY_MAX = 60
_SCRIPT_MAX = 240
_LESSON_MAX = 120

_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key\s*[:=]\s*\S+"
    r"|oauth\s*[:=]\s*\S+"
    r"|authorization\s*[:=]\s*\S+"
    r"|bearer\s+[a-z0-9._\-]{12,}"
    r"|sk-[a-z0-9]{10,}"
    r"|" + "gh" + "p_" + r"[a-z0-9]+"
    r"|xai-[a-z0-9]+"
    r")"
)

_ROUTES: dict[str, dict[str, Any]] = {
    "install": {
        "mode": "install",
        "skill_ref": "references/install.md",
        "tools": [],
        "prefer": "install",
        "next": "Run one-command install + grok login; re-call session_begin.",
    },
    "update": {
        "mode": "update",
        "skill_ref": "references/update.md",
        "tools": [],
        "prefer": "update",
        "next": "grok_agent_update to preview, confirm=true to apply, then restart the MCP host.",
    },
    "triage": {
        "mode": "triage",
        "skill_ref": "references/security.md",
        "tools": ["grok_agent_status", "grok_delegate_doctor"],
        "prefer": "consult",
        "next": "Fix gate; feedback after 2+ product bugs.",
    },
    "brainstorm": {
        "mode": "brainstorm",
        "skill_ref": "references/brainstorm.md",
        "tools": ["grok_agent_consult", "grok_agent_review"],
        "prefer": "consult",
        "next": "Consult/review only; no execute until user confirms.",
    },
    "execute": {
        "mode": "execute",
        "skill_ref": "references/execute.md",
        "tools": ["grok_agent_execute", "grok_agent_poll", "grok_agent_cancel"],
        "prefer": "execute",
        "next": "One tight execute → poll → session_end.",
    },
    "verify": {
        "mode": "verify",
        "skill_ref": "references/verify.md",
        "tools": ["grok_agent_poll", "grok_agent_status", "grok_agent_review"],
        "prefer": "consult",
        "next": "Poll/status only; re-execute only on fail.",
    },
    "feedback": {
        "mode": "feedback",
        "skill_ref": "references/feedback.md",
        "tools": [],
        "prefer": "consult",
        "next": "Draft scrubbed issue via templates/issue.md.",
    },
    "operate": {
        "mode": "operate",
        "skill_ref": "references/operate.md",
        "tools": ["grok_agent_status", "grok_agent_economy"],
        "prefer": "consult",
        "next": "Pick brainstorm/execute/verify by task size.",
    },
}

_DENY_BY_MODE: dict[str, list[str]] = {
    "brainstorm": ["grok_agent_execute", "grok_agent_fix", "grok_agent_start"],
    "verify": ["grok_agent_execute", "grok_agent_fix"],
    "install": ["grok_agent_execute", "grok_agent_fix", "grok_agent_consult"],
    "update": ["grok_agent_execute", "grok_agent_fix"],
    "feedback": ["grok_agent_execute", "grok_agent_fix", "grok_agent_start"],
}

_sessions: dict[str, dict[str, Any]] = {}


def session_compact_active() -> bool:
    """Process-wide compact is only what the operator set in the environment.

    session_begin used to flip a module global and setdefault
    GROK_DELEGATE_ECONOMY / GROK_DELEGATE_ECONOMY_COMPACT_POLL, so one
    navigator in one project compacted every later poll in this process —
    including a neighbour — with no way back. Compact now lives on the
    session that asked for it.
    """
    return False


def enable_session_economy() -> None:
    """No-op. Env is the operator's; compact is a field on the session.

    Writing those two GROK_DELEGATE_ECONOMY* vars here is the leak above.
    """


def _session_wants_compact(sess: Mapping[str, Any] | None) -> bool:
    return bool(sess) and bool(sess.get("compact")) and not sess.get("ended")


def scrub_secrets(text: str) -> str:
    return _SECRETISH.sub("[REDACTED]", str(text or ""))


def _clip(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _json_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def _str_list(value: Any, *, fallback: list[str] | None = None) -> list[str]:
    items: list[str] = []
    if isinstance(value, str) and value.strip():
        items.append(value.strip()[:2_000])
    elif isinstance(value, (list, tuple)):
        for item in value:
            text = str(item).strip()[:2_000]
            if text:
                items.append(text)
            if len(items) >= 64:
                break
    if items:
        return items
    return list(fallback or [])


def bind_session_job(
    job_id: str,
    *,
    session_id: str | None = None,
    correlation_id: str | None = None,
) -> str | None:
    """Remember execute/fix job_id so the next poll/cancel card can be `{job_id}` only.

    Never overwrite an unrelated live session. Consult/review jobs must not steal
    the execute session's poll slot.
    """
    jid = str(job_id or "").strip()
    if not jid:
        return None
    sid = str(session_id or "").strip() or None
    cid = str(correlation_id or "").strip() or None
    if sid and sid in _sessions:
        _sessions[sid]["job_id"] = jid
        return sid
    if cid:
        for sess in reversed(list(_sessions.values())):
            if sess.get("ended"):
                continue
            if str(sess.get("correlation_id") or "").strip() == cid:
                sess["job_id"] = jid
                return str(sess.get("session_id") or "") or None
    for sess in reversed(list(_sessions.values())):
        if sess.get("ended"):
            continue
        if sess.get("job_id"):
            continue
        if sess.get("mode") in {"execute", "verify", "operate", "fix"}:
            sess["job_id"] = jid
            return str(sess.get("session_id") or "") or None
    return None


def _job_still_running(sess: Mapping[str, Any]) -> bool:
    """Is the session's bound job in a state the host should wait on?

    Unknown reads as "not running": a job the registry cannot find will never
    become terminal, and holding the plan on it forever is worse than moving on.
    """
    jid = str((sess or {}).get("job_id") or "").strip()
    if not jid:
        return False
    record = jobs_mod.get_job(jid)
    if not isinstance(record, Mapping):
        return False
    return str(record.get("state") or "").lower() == jobs_mod.STATE_RUNNING


def _session_budget(sess: Mapping[str, Any]) -> dict[str, Any]:
    """Budget for this session's cards: the project's preset, else the operator's.

    The navigator emits a full task packet, so whatever it puts here arrives at
    the job as an *explicit* value and outranks the project preset applied later.
    Reading the preset here is therefore not a nicety -- without it a card would
    quietly override the very preset the project chose.
    """
    budget: dict[str, Any] = {
        "max_turns": default_max_turns(),
        "reasoning_effort": default_reasoning_effort(),
    }
    root = _session_project_root(sess)
    if not root:
        return budget
    try:
        from .project_config import project_gate

        gate = project_gate(root)
    except Exception:
        # A broken or unreadable config is reported by the job gate, which fails
        # closed with a usable message. A card is not the place to raise it.
        return budget
    if gate.get("enabled"):
        budget.update(gate.get("budget") or {})
    return budget


def _session_project_root(sess: Mapping[str, Any]) -> str:
    root = str(sess.get("project_root") or "").strip()
    if root:
        return root[:1_024]
    for item in sess.get("roots") or []:
        text = str(item).strip()
        if text:
            return text[:1_024]
    return ""


def _session_correlation_id(sess: Mapping[str, Any]) -> str:
    cid = str(sess.get("correlation_id") or "").strip()
    if cid:
        return cid[:128]
    return f"sess-{sess.get('session_id') or 'x'}"[:128]


def _write_task_packet(sess: Mapping[str, Any]) -> dict[str, Any]:
    from .anchors import extract_goal_anchors

    goal = str(sess.get("goal") or "Implement only the listed scope")[:_GOAL_MAX]
    anchors = [p for p in extract_goal_anchors(goal) if not p.startswith(("http://", "https://"))]
    artifacts = _str_list(sess.get("expected_artifacts"), fallback=anchors[:8] or ["src"])
    tests = _str_list(sess.get("test_commands"), fallback=["python -m pytest -q"])
    return {
        "objective": goal or "Implement only the listed scope",
        "project_root": _session_project_root(sess),
        "correlation_id": _session_correlation_id(sess),
        "expected_artifacts": artifacts,
        "test_commands": tests,
        **_session_budget(sess),
        "timeout_seconds": ECONOMY_DEFAULT_TIMEOUT_SECONDS,
    }


def _read_task_packet(sess: Mapping[str, Any], *, role: str) -> dict[str, Any]:
    goal = str(sess.get("goal") or "Answer in bounded form")[:_GOAL_MAX]
    return {
        "objective": goal or "Answer in bounded form",
        "project_root": _session_project_root(sess),
        "correlation_id": _session_correlation_id(sess),
        "role": role,
    }


def compile_card_args(tool: str, sess: Mapping[str, Any], step: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build typed MCP arguments for a navigator card. Poll never gets session_id."""
    step = step or {}
    if tool in {"grok_agent_poll", "grok_agent_cancel"}:
        jid = str(sess.get("job_id") or "").strip()
        return {"job_id": jid} if jid else {}
    if tool in {"grok_agent_execute", "grok_agent_fix"}:
        return {"task": _write_task_packet(sess)}
    if tool == "grok_agent_consult":
        return {"task": _read_task_packet(sess, role="consult")}
    if tool == "grok_agent_review":
        return {"task": _read_task_packet(sess, role="skeptic")}
    args = dict(step.get("args_hint") or {})
    if tool == "grok_agent_session_tick":
        args.setdefault("session_id", sess.get("session_id"))
        if sess.get("job_id"):
            args.setdefault("job_id", sess.get("job_id"))
        return {k: v for k, v in args.items() if v is not None}
    if tool == "grok_agent_session_end":
        out = {"session_id": sess.get("session_id")}
        if sess.get("job_id"):
            out["job_id"] = sess.get("job_id")
        return out
    return args


def _shrink(obj: dict[str, Any], soft_max: int = _PAYLOAD_SOFT_MAX) -> dict[str, Any]:
    out = dict(obj)
    card = out.get("card")
    # drop heavy optional first
    for key in ("playbook_hint", "debug", "defaults_detail", "job"):
        if _json_size(out) <= soft_max:
            break
        if key == "job" and isinstance(out.get("job"), dict):
            out["job"] = {k: out["job"].get(k) for k in ("job_id", "state", "status") if k in out["job"]}
        else:
            out.pop(key, None)
    for k, v in list(out.items()):
        if k == "card":
            continue
        if isinstance(v, str) and len(v) > 320:
            out[k] = _clip(v, 320)
    if _json_size(out) > soft_max:
        out = {
            "ok": out.get("ok", True),
            "protocol": out.get("protocol", "session/v1.2"),
            "session_id": out.get("session_id"),
            "mode": out.get("mode"),
            "done": out.get("done"),
            "card": card,
            "host_script": out.get("host_script"),
            "force_end": out.get("force_end"),
            "next_step": out.get("next_step"),
            "host_message": _clip(str(out.get("host_message") or ""), 200),
            "truncated": True,
        }
    return out


def resolve_gate(
    *,
    allowed_roots: Sequence[Path | str] | None = None,
    which=None,
    subprocess_runner=None,
) -> dict[str, Any]:
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
            auth = cached_auth_presence(subprocess_runner=subprocess_runner, which=which_fn)
            auth_present = bool(auth.get("auth_present"))
        except Exception:
            pass
    roots: list[str] = []
    roots_ok = False
    try:
        from .server import load_allowed_roots

        loaded = (
            load_allowed_roots(injected=allowed_roots)
            if allowed_roots is not None
            else load_allowed_roots()
        )
        roots = [str(p) for p in loaded[:8]]
        roots_ok = bool(loaded)
    except Exception:
        env_roots = os.environ.get("GROK_DELEGATE_ALLOWED_ROOTS", "").strip()
        roots_ok = bool(env_roots)
        if env_roots:
            roots = [p.strip() for p in env_roots.split(os.pathsep) if p.strip()][:8]
    return {
        "ready": binary_ok and auth_present and roots_ok,
        "cli": "grok",
        "binary_ok": binary_ok,
        "auth_ok": auth_present,
        "roots_ok": roots_ok,
        "roots": roots,
        "version": version,
        "server": SERVER_VERSION,
    }


_INSTALL_SH = (
    "curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh | bash"
)
_INSTALL_PS1 = (
    "irm https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.ps1 | iex"
)


def _install_command() -> str:
    """The installer the host can actually run.

    A `curl … | bash` card is not an instruction on Windows, it is a dead end,
    and the operator running this bridge is on Windows. The repository ships
    both installers; the card should name the one that exists here.
    """
    return _INSTALL_PS1 if sys.platform == "win32" else _INSTALL_SH


#: Path-ish tokens are removed before intent matching. `update tests/conftest.py`
#: is a request to change code, but the bare substring `test` routed it to
#: "verify" -- the filename decided the mode.
_PATHISH = re.compile(r"\S*[\\/]\S*|\S+\.(?:py|ts|tsx|js|md|json|toml|yaml|yml)\b")
_EXECUTE_WORDS = re.compile(
    r"(?i)\b("
    r"fix|implement|build|add|create|update|rewrite|refactor|port|migrate|write|"
    r"почин\w*|исправ\w*|пофикс\w*|напиш\w*|сдела\w*|добав\w*|реализу\w*|обнов\w*"
    r")\b"
)
_VERIFY_WORDS = re.compile(
    r"(?i)\b("
    r"review|verify|check|audit|tests?|"
    r"провер\w*|ревью\w*|отревьюй\w*|аудит\w*|проаудируй\w*|сверь\w*"
    r")\b"
)
_BRAINSTORM_WORDS = re.compile(
    r"(?i)\b(brainstorm|design|options|разбер\w*|изуч\w*|расскаж\w*)\b|\bhow should\b"
)
_SETUP_WORDS = re.compile(r"(?i)\b(install|setup)\b")

#: Matched in this order once no leading verb decided it.
_MODE_WORDS = (
    ("execute", _EXECUTE_WORDS),
    ("verify", _VERIFY_WORDS),
    ("brainstorm", _BRAINSTORM_WORDS),
    ("install", _SETUP_WORDS),
)
_FILLER = frozenset({"please", "can", "you", "could", "lets", "let", "us", "we", "i", "now", "just"})
#: Letters only, including Cyrillic. `[a-z]+` dropped "проверь" so auto
#: never saw the leading verb and those goals fell through to operate.
_WORD = re.compile(r"[^\W\d_]+")


def _leading_verb_mode(goal: str) -> str | None:
    """The mode the goal's own first verb asks for, if it has one.

    Keyword matching alone cannot tell a verb from a noun: "check whether the
    update landed" is a question about state, but it contains "update". Goals
    are written as imperatives, so the first real word is the better witness --
    and when it says nothing, the ordered scan below still gets a turn.
    `[a-z]+` is why "проверь репозиторий" did not agree with "check the
    repository": the Cyrillic verb was not a token.
    """
    for word in _WORD.findall(goal.lower()):
        if word in _FILLER:
            continue
        for mode, pattern in _MODE_WORDS:
            if pattern.fullmatch(word):
                return mode
        return None
    return None


def _auto_mode(gate: Mapping[str, Any], intent: str, goal: str) -> str:
    if not gate.get("binary_ok") or not gate.get("auth_ok"):
        return "install" if not gate.get("binary_ok") else "triage"
    if not gate.get("roots_ok"):
        return "triage"
    if intent in INTENTS and intent != "auto":
        return intent
    # Filenames are not verbs: strip them, then match whole words only.
    g = _PATHISH.sub(" ", goal.lower())
    leading = _leading_verb_mode(g)
    if leading:
        return leading
    for mode, pattern in _MODE_WORDS:
        if pattern.search(g):
            return mode
    return "operate"


def _step(i: int, tool: str, why: str, args_hint: dict[str, Any] | None = None) -> dict[str, Any]:
    if tool not in _ALLOW:
        raise ValueError(f"plan tool not allowlisted: {tool}")
    return {
        "i": i,
        "tool": tool,
        "why": _clip(why, _WHY_MAX),
        "args_hint": args_hint or {},
    }


def compile_plan(
    mode: str, goal: str, gate_ready: bool, *, max_turns: int | None = None
) -> list[dict[str, Any]]:
    """≤5 steps; tools from allowlist only."""
    g = _clip(scrub_secrets(goal), 200) if goal else ""
    steps: list[dict[str, Any]] = []
    if mode == "install" or (not gate_ready and mode not in {"triage", "update", "feedback"}):
        return [
            _step(1, "grok_agent_session_next", "Get one install command card"),
            _step(2, "grok_agent_session_next", "Confirm gate / next card"),
            _step(3, "grok_agent_session_end", "Receipt after install"),
        ]
    if mode == "update":
        return [
            _step(1, "grok_agent_session_next", "Update command card"),
            _step(2, "grok_agent_session_end", "Receipt"),
        ]
    if mode == "feedback":
        return [
            _step(1, "grok_agent_session_next", "Issue draft card"),
            _step(2, "grok_agent_session_end", "Done"),
        ]
    if mode == "triage":
        steps.append(_step(1, "grok_agent_status", "Confirm gate/runtime"))
        steps.append(_step(2, "grok_delegate_doctor", "Diagnose CLI/auth"))
        steps.append(_step(3, "grok_agent_session_end", "Receipt + next"))
        return steps[:5]
    if mode == "brainstorm":
        return [
            _step(1, "grok_agent_consult", "Answer with tight goal", {"objective": g} if g else {}),
            _step(2, "grok_agent_session_end", "Short receipt"),
        ]
    if mode == "execute":
        turns = max_turns or ECONOMY_DEFAULT_MAX_TURNS
        return [
            _step(
                1,
                "grok_agent_execute",
                "Implement only listed scope",
                # The hint must match the budget the card will actually carry,
                # otherwise a host that follows it downgrades its own preset.
                {"objective": g, "max_turns": turns} if g else {"max_turns": turns},
            ),
            _step(2, "grok_agent_poll", "Job receipt fields only"),
            _step(3, "grok_agent_session_end", "Host receipt"),
        ]
    if mode == "verify":
        return [
            _step(1, "grok_agent_poll", "Existing job status"),
            _step(2, "grok_agent_status", "Runtime check"),
            _step(3, "grok_agent_session_end", "Receipt"),
        ]
    # operate / triage already handled above for triage
    if mode == "triage":
        return [
            _step(1, "grok_agent_status", "Confirm gate/runtime"),
            _step(2, "grok_delegate_doctor", "Diagnose CLI/auth"),
            _step(3, "grok_agent_session_end", "Receipt + next"),
        ]
    return [
        _step(1, "grok_agent_status", "Once per session"),
        _step(2, "grok_agent_session_end", "Pick execute/brainstorm via new begin"),
    ]


def _budget(host_budget: str, max_tool_calls: int | None) -> dict[str, Any]:
    preset = dict(_BUDGET_PRESETS.get(host_budget, _BUDGET_PRESETS["small"]))
    if max_tool_calls is not None:
        try:
            n = int(max_tool_calls)
            if n >= 1:
                preset["max_tool_calls"] = min(n, 32)
        except (TypeError, ValueError):
            pass
    return preset


def _host_script(mode: str, plan: list[dict[str, Any]], budget: Mapping[str, Any]) -> str:
    if not plan:
        if mode == "install":
            return _clip(
                "Gate not ready: run EASY install + grok login, set roots, then session_begin again.",
                _SCRIPT_MAX,
            )
        if mode == "update":
            return _clip(
                "grok_agent_update (preview), then confirm=true, then restart the MCP host.",
                _SCRIPT_MAX,
            )
        if mode == "feedback":
            return _clip("Fill templates/issue.md; draft_issue.py; no secrets.", _SCRIPT_MAX)
    return _clip(
        f"Call grok_agent_session_next until done=true (budget tools={budget.get('max_tool_calls')} polls={budget.get('max_polls')}). Do not invent tools. Then stop.",
        _SCRIPT_MAX,
    )


def session_begin(
    intent: str = "auto",
    *,
    goal: str | None = None,
    host_budget: str = "small",
    max_tool_calls: int | None = None,
    allowed_roots: Sequence[Path | str] | None = None,
    which=None,
    subprocess_runner=None,
    project_root: str | None = None,
    expected_artifacts: Sequence[str] | None = None,
    test_commands: Sequence[str] | None = None,
    correlation_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    intent_n = (intent or "auto").strip().lower()
    if intent_n not in INTENTS:
        return {
            "ok": False,
            "error_code": "INTENT_INVALID",
            "error": f"intent must be one of: {', '.join(sorted(INTENTS))}",
            "protocol": "session/v1.2",
        }
    hb = (host_budget or "small").strip().lower()
    if hb not in HOST_BUDGETS:
        return {
            "ok": False,
            "error_code": "BUDGET_INVALID",
            "error": f"host_budget must be one of: {', '.join(sorted(HOST_BUDGETS))}",
            "protocol": "session/v1.2",
        }
    goal_s = scrub_secrets(_clip(goal or "", _GOAL_MAX))
    enable_session_economy()
    gate = resolve_gate(
        allowed_roots=allowed_roots, which=which, subprocess_runner=subprocess_runner
    )
    mode = _auto_mode(gate, intent_n, goal_s)
    if mode in {"execute", "brainstorm", "verify", "operate", "feedback"} and not gate.get("ready"):
        mode = "install" if not gate.get("binary_ok") else "triage"
    route = _ROUTES.get(mode, _ROUTES["operate"])
    tools = [t for t in route["tools"] if t in _ALLOW]
    if gate.get("ready") and mode not in {"install", "update", "feedback"}:
        if "grok_agent_status" not in tools and mode == "operate":
            tools = ["grok_agent_status", *tools]
    stored_root = str(project_root or "").strip()[:1_024]
    if not stored_root and gate.get("roots"):
        stored_root = str(gate["roots"][0])[:1_024]
    session_defaults = _session_budget({"project_root": stored_root})
    plan = compile_plan(
        mode, goal_s, bool(gate.get("ready")), max_turns=session_defaults.get("max_turns")
    )
    # recommended = unique plan tools + route tools
    rec_tools: list[str] = []
    for tname in (
        ["grok_agent_session_next"]
        + [s["tool"] for s in plan]
        + tools
        + ["grok_agent_session_tick", "grok_agent_session_end"]
    ):
        if tname in _ALLOW and tname not in rec_tools:
            rec_tools.append(tname)
    bpreset = _budget(hb, max_tool_calls)
    budget = {
        "host_budget": hb,
        "max_tool_calls": bpreset["max_tool_calls"],
        "max_polls": bpreset["max_polls"],
        "prefer": route.get("prefer", "consult"),
        "stop_when": "plan done or budget exhausted or force_end",
    }
    deny = [t for t in _DENY_BY_MODE.get(mode, []) if t in _ALLOW]
    # never deny session_end/tick
    deny = [t for t in deny if t not in {"grok_agent_session_end", "grok_agent_session_tick"}]
    sid = uuid.uuid4().hex[:12]
    stored_cid = str(correlation_id or "").strip()[:128] or f"sess-{sid}"
    _sessions[sid] = {
        "session_id": sid,
        "intent": intent_n,
        "mode": mode,
        "goal": goal_s,
        # A verify session exists to look at a job that already ran, and until
        # now there was nowhere to say which one -- the plan's poll card was
        # skipped for want of an id the host had no way to supply.
        "job_id": str(job_id or "").strip()[:128] or None,
        "plan": plan,
        "plan_step": 0,
        "budget": budget,
        "deny_tools": deny,
        "tool_calls_used": 0,
        "polls_used": 0,
        # Navigator receipts stay small for *this* session only. Putting the
        # same switch on the process env compacted every neighbour.
        "compact": True,
        "started": True,
        "project_root": stored_root,
        "roots": list(gate.get("roots") or [])[:8],
        "expected_artifacts": _str_list(expected_artifacts),
        "test_commands": _str_list(test_commands),
        "correlation_id": stored_cid,
    }
    script = _host_script(mode, plan, budget)
    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1.2",
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
        "recommended_tools": rec_tools[:12],
        "plan": plan,
        "budget": budget,
        "deny_tools": deny,
        "host_script": script,
        "defaults": {
            **session_defaults,
            "timeout_seconds": ECONOMY_DEFAULT_TIMEOUT_SECONDS,
        },
        "economy_flags": {
            "economy": True,
            "compact_poll": True,
            "verbose_default": False,
        },
        "skill_ref": route["skill_ref"],
        "next_step": route["next"],
        "disclaimer": "Unofficial community project — not xAI/Grok.",
    }
    if goal_s:
        out["goal_echo"] = goal_s
    if not gate.get("ready"):
        out["next_step"] = "Gate failed: grok CLI + login + roots. See skill_ref."
    return _shrink(out)


def session_tick(
    *,
    session_id: str | None = None,
    job_id: str | None = None,
    verbose: bool = False,
    tool_used: str | None = None,
    step_done: bool = False,
) -> dict[str, Any]:
    enable_session_economy()
    jid = (job_id or "").strip() or None
    sid = (session_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess and not jid:
        jid = sess.get("job_id")

    warns: list[str] = []
    if sess is not None:
        sess["polls_used"] = int(sess.get("polls_used") or 0) + 1
        if tool_used:
            tu = str(tool_used).strip()
            deny = set(sess.get("deny_tools") or [])
            if tu in deny:
                warns.append(f"tool_denied:{tu}")
            if tu and tu not in _ALLOW:
                warns.append(f"tool_unknown:{tu}")
            else:
                sess["tool_calls_used"] = int(sess.get("tool_calls_used") or 0) + 1
        if step_done:
            sess["plan_step"] = min(
                int(sess.get("plan_step") or 0) + 1,
                len(sess.get("plan") or []),
            )

    state = "idle"
    phase = "waiting"
    pct = 0
    changed = 0
    tests_summary = None
    blockers: list[str] = list(warns)
    suggested = "Follow plan step; session_end when done."
    host_message = "No active job."
    compact: dict[str, Any] | None = None

    if jid:
        record = jobs_mod.get_job(jid)
        if record is None:
            state = "unknown_job"
            blockers.append("JOB_UNKNOWN")
            suggested = "session_end or fix job_id"
            host_message = f"Unknown job {jid}."
        else:
            if sid and sess is not None:
                sess["job_id"] = jid
            compact = compact_job_record(record) if not verbose else dict(record)
            state = str(compact.get("state") or compact.get("status") or "running")
            st = state.lower()
            if st in {"completed", "ok", "success", "done"}:
                pct, phase, suggested = 100, "done", "session_end"
            elif st in {"failed", "error", "cancelled", "canceled"}:
                pct, phase = 100, "failed"
                blockers.append(str(compact.get("blocked_reason") or compact.get("error") or st))
                suggested = "session_end"
            else:
                pct, phase, suggested = 50, "running", "tick/poll; no full dumps"
            cf = compact.get("changed_files") or []
            if isinstance(cf, list):
                changed = len(cf)
            tests = compact.get("tests")
            if isinstance(tests, list) and tests:
                passed = sum(1 for t in tests if isinstance(t, Mapping) and t.get("passed"))
                tests_summary = f"{passed}/{len(tests)} passed"
            elif compact.get("tests_skipped_reason"):
                # An empty list and "the verifier never ran" read identically on
                # one line, and only one of them means the host must run tests.
                tests_summary = "skipped:" + str(compact.get("tests_skipped_reason"))
            host_message = _clip(
                f"{state}: files={changed}"
                + (f" tests={tests_summary}" if tests_summary else ""),
                _HOST_MSG_MAX,
            )
    else:
        host_message = "Idle — execute plan tools or session_end."

    plan = list((sess or {}).get("plan") or [])
    step_i = int((sess or {}).get("plan_step") or 0)
    steps_left = max(0, len(plan) - step_i)
    budget = dict((sess or {}).get("budget") or _BUDGET_PRESETS["small"])
    used_tools = int((sess or {}).get("tool_calls_used") or 0)
    used_polls = int((sess or {}).get("polls_used") or 0)
    max_t = int(budget.get("max_tool_calls") or 6)
    max_p = int(budget.get("max_polls") or 4)
    force_end = used_tools >= max_t or used_polls >= max_p or (bool(plan) and steps_left == 0 and state in {"idle", "done", "completed", "failed"})
    if used_tools >= max_t:
        blockers.append("BUDGET_TOOLS")
    if used_polls >= max_p:
        blockers.append("BUDGET_POLLS")
    if force_end:
        suggested = "session_end"

    out: dict[str, Any] = {
        "ok": True,
        "protocol": "session/v1.2",
        "session_id": sid,
        "job_id": jid,
        "state": state,
        "phase": phase,
        "percent": pct,
        "step": step_i,
        "steps_left": steps_left,
        "budget_remaining": {
            "tool_calls": max(0, max_t - used_tools),
            "polls": max(0, max_p - used_polls),
        },
        "force_end": force_end,
        "changed_files_count": changed,
        "tests_summary": tests_summary,
        "blockers": blockers[:8],
        "suggested_host_action": suggested,
        "host_message": scrub_secrets(host_message)[:_HOST_MSG_MAX],
        "economy_compact": not verbose and (
            _session_wants_compact(sess) or compact_poll_enabled()
        ),
    }
    if verbose and compact is not None:
        out["job"] = compact
    elif compact is not None:
        out["job"] = {
            k: compact.get(k)
            for k in ("job_id", "state", "status", "lane", "branch", "summary", "blocked_reason")
            if k in compact
        }
        if out["job"].get("summary"):
            out["job"]["summary"] = _clip(str(out["job"]["summary"]), 160)
    return _shrink(out)



def session_next(
    *,
    session_id: str | None = None,
    advance: bool = True,
    note: str | None = None,
) -> dict[str, Any]:
    """Return the single next action card — host should only call this until done."""
    enable_session_economy()
    sid = (session_id or "").strip() or None
    sess = _sessions.get(sid) if sid else None
    if sess is None:
        return _shrink(
            {
                "ok": False,
                "error_code": "SESSION_UNKNOWN",
                "error": "call session_begin first",
                "protocol": "session/v1.2",
                "done": True,
                "card": {"kind": "end", "why": "no session"},
            }
        )
    # One next is one poll. Charging tool_calls here too meant host_budget=small
    # (4 polls / 6 tools) had 3 polls and 5 tools left after the first card, so
    # the skill loop starved after three more steps. Tools are charged on
    # tool_used, which is the host reporting a tool it actually ran.
    sess["polls_used"] = int(sess.get("polls_used") or 0) + 1
    budget = dict(sess.get("budget") or _BUDGET_PRESETS["small"])
    max_t = int(budget.get("max_tool_calls") or 6)
    max_p = int(budget.get("max_polls") or 4)
    used_t = int(sess.get("tool_calls_used") or 0)
    used_p = int(sess.get("polls_used") or 0)
    plan = list(sess.get("plan") or [])
    step_i = int(sess.get("plan_step") or 0)
    mode = str(sess.get("mode") or "operate")
    goal = str(sess.get("goal") or "")

    if used_t >= max_t or used_p >= max_p:
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": True,
                "force_end": True,
                "card": {"kind": "end", "tool": "grok_agent_session_end", "args": {"session_id": sid}, "why": "budget exhausted"},
                "host_message": "Budget exhausted — call session_end now.",
                "budget_remaining": {"tool_calls": 0, "polls": 0},
            }
        )

    # Install/update host command cards before plan tools exhaust
    if mode == "install" and step_i == 0:
        card = {
            "kind": "host_cmd",
            "cmd": _install_command(),
            "why": "One-command EASY install (then grok login)",
            "args": {},
        }
        if advance:
            sess["plan_step"] = 1
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "step": 0,
                "steps_left": max(0, len(plan) - 1),
                "card": card,
                "host_message": _clip("Run the install cmd, then grok login, then session_next again.", 200),
                "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            }
        )
    if mode == "install" and step_i == 1:
        if advance:
            sess["plan_step"] = 2
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "step": 1,
                "card": {
                    "kind": "host_cmd",
                    "cmd": "grok login && grok --version",
                    "why": "Auth gate",
                },
                "host_message": "Login if needed, then session_next (or session_end).",
                "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            }
        )
    if mode == "update" and step_i == 0:
        if advance:
            sess["plan_step"] = 1
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "card": {
                    "kind": "tool",
                    "tool": "grok_agent_update",
                    "args": {},
                    "why": "Preview the update; re-issue with confirm=true to apply",
                },
                "host_message": "Preview the update, confirm it, then restart the MCP host.",
                "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            }
        )
    if mode == "feedback" and step_i == 0:
        if advance:
            sess["plan_step"] = 1
        draft_note = scrub_secrets(_clip(note or goal or "bug report", 200))
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": False,
                "card": {
                    "kind": "mcp_tool",
                    "tool": "grok_agent_session_end",
                    "args": {"session_id": sid, "suggest_issue": True, "note": draft_note},
                    "why": "Scrubbed issue draft",
                },
                "host_message": "Call session_end with suggest_issue for draft.",
                "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            }
        )

    # Generic: emit current plan step as a typed card
    if step_i >= len(plan):
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": True,
                "force_end": True,
                "card": {
                    "kind": "end",
                    "tool": "grok_agent_session_end",
                    "args": {"session_id": sid},
                    "why": "plan complete",
                },
                "host_message": "Plan complete — session_end.",
                "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            }
        )

    while step_i < len(plan):
        peek = plan[step_i].get("tool")
        if peek == "grok_agent_session_next":
            step_i += 1
            if advance:
                sess["plan_step"] = step_i
            continue
        if peek in {"grok_agent_poll", "grok_agent_cancel"} and not str(sess.get("job_id") or "").strip():
            step_i += 1
            if advance:
                sess["plan_step"] = step_i
            continue
        if peek in {
            "grok_agent_execute",
            "grok_agent_fix",
            "grok_agent_consult",
            "grok_agent_review",
        } and not _session_project_root(sess):
            step_i += 1
            if advance:
                sess["plan_step"] = step_i
            continue
        break
    if step_i >= len(plan):
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": True,
                "force_end": True,
                "card": {
                    "kind": "end",
                    "tool": "grok_agent_session_end",
                    "args": {"session_id": sid},
                    "why": "plan complete",
                },
                "host_message": "Plan complete — session_end.",
                "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            }
        )
    step = plan[step_i]
    tool = step.get("tool")

    if tool == "grok_agent_session_end":
        if advance:
            sess["plan_step"] = step_i + 1
        return _shrink(
            {
                "ok": True,
                "protocol": "session/v1.2",
                "session_id": sid,
                "done": True,
                "force_end": True,
                "card": {
                    "kind": "end",
                    "tool": "grok_agent_session_end",
                    "args": {"session_id": sid},
                    "why": step.get("why") or "end",
                },
                "host_message": "Call session_end now.",
                "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            }
        )

    card = {
        "kind": "mcp_tool",
        "tool": tool,
        "args": compile_card_args(str(tool), sess, step),
        "why": step.get("why") or "",
    }
    # A job runs in the background for as long as it takes -- the live one this
    # cycle measured took 32 seconds. The plan offered exactly one poll and then
    # said done, so a host that executes only cards closed the session on a
    # running job. Hold on the poll step until the job is terminal; max_polls
    # still bounds it, and the message says what is being waited for.
    waiting = tool == "grok_agent_poll" and _job_still_running(sess)
    if advance and not waiting:
        sess["plan_step"] = step_i + 1
    return _shrink(
        {
            "ok": True,
            "protocol": "session/v1.2",
            "session_id": sid,
            "done": False,
            "step": step_i,
            "steps_left": max(0, len(plan) - step_i - 1),
            "card": card,
            "host_message": _clip(
                f"Job still running — call {tool} again, then session_next."
                if waiting
                else f"Call {tool} with provided args, then session_next.",
                200,
            ),
            "budget_remaining": {"tool_calls": max(0, max_t - used_t), "polls": max(0, max_p - used_p)},
            "disclaimer": "Unofficial — not xAI/Grok.",
        }
    )



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
                next_step = "One tight re-execute or human."
            elif st in {"cancelled", "canceled"}:
                status = "need-human"
                next_step = "Job cancelled."
            else:
                status = "need-human"
                next_step = "Job still running — cancel or wait."
            cf = c.get("changed_files") or []
            if isinstance(cf, list) and cf:
                changed = [str(x) for x in cf[:12]]
            tlist = c.get("tests")
            if isinstance(tlist, list) and tlist:
                passed = sum(1 for t in tlist if isinstance(t, Mapping) and t.get("passed"))
                tests = f"{passed}/{len(tlist)} passed"
            elif c.get("tests_skipped_reason"):
                tests = "skipped:" + str(c.get("tests_skipped_reason"))
            if c.get("blocked_reason"):
                blockers.append(str(c.get("blocked_reason")))
    elif note:
        next_step = _clip(scrub_secrets(note), 200)

    used_tools = int((sess or {}).get("tool_calls_used") or 0)
    used_polls = int((sess or {}).get("polls_used") or 0)
    budget = dict((sess or {}).get("budget") or {})
    max_t = int(budget.get("max_tool_calls") or 6)
    max_p = int(budget.get("max_polls") or 4)
    was_capped = used_tools >= max_t or used_polls >= max_p
    lesson = _clip(
        scrub_secrets(
            f"mode={(sess or {}).get('mode')}; prefer plan tools; budget={'capped' if was_capped else 'ok'}"
        ),
        _LESSON_MAX,
    )
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
        "protocol": "session/v1.2",
        "session_id": sid,
        "receipt": receipt,
        "budget_report": {
            "tool_calls_used": used_tools,
            "polls_used": used_polls,
            "max_tool_calls": max_t,
            "max_polls": max_p,
            "was_capped": was_capped,
        },
        "lesson": lesson,
        "host_message": _clip(f"{status}: job={job_label} tests={tests}", _HOST_MSG_MAX),
        "disclaimer": "Unofficial — not xAI/Grok.",
    }
    if suggest_issue:
        draft = (
            f"## Summary\nSession {sid or '-'} status={status}\n"
            f"## Job\n{job_label}\n"
            f"## Blockers\n{', '.join(blockers) or 'none'}\n"
            f"## Env\nserver={SERVER_VERSION}\n"
            f"## Free-form\n{scrub_secrets(note or '')}\n"
        )
        out["suggest_issue"] = True
        out["issue_draft"] = scrub_secrets(_clip(draft, 1200))
        out["issue_repo"] = "zai-one/grok-mcp"
    if sid and sid in _sessions:
        _sessions[sid]["ended"] = True
        _sessions[sid]["compact"] = False
    return _shrink(out)


def reset_sessions_for_tests() -> None:
    _sessions.clear()
