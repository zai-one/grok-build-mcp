#!/usr/bin/env python3
"""Dev-only stdio MCP entry for grok_delegate (thin transport over guard/runner).

Core logic lives in guard.py / runner.py / status.py and is transport-independent.
This module is a minimal JSON-RPC 2.0 MCP-ish stdio adapter (no product
admin-bridge, no src/** imports, no trust expansion of tools/mcp/).

Tools:
  - grok_delegate              — prepare worktree + headless run + diffstat
  - grok_delegate_plan         — same with plan_only=true (read-only profile)
  - grok_delegate_status       — structured health JSON (read-only)
  - grok_delegate_doctor       — grok doctor --json only (never fix)
  - grok_delegate_models       — grok models (read-only)
  - grok_delegate_inspect      — grok inspect --json for an allowed project
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

# Allow package and flat execution.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from .audit import (  # type: ignore[no-redef]
        build_delegation_audit,
        emit as audit_emit,
    )
    from .guard import (  # type: ignore[no-redef]
        DEFAULT_EXECUTE_SANDBOX,
        DEFAULT_PLAN_SANDBOX,
        HARD_CAP_MAX_TURNS,
        SERVER_VERSION as _guard_server_version,
        GuardError,
        host_provided_roots,
        parse_allowed_roots_env,
        path_in_allowlist,
        paths_equal,
        structured_error,
        trust_host_roots_enabled,
        validate_grok_bin,
        validate_json_schema,
        validate_model,
        validate_reasoning_effort,
        validate_rules,
        validate_sandbox_profile,
        validate_session_id,
    )
    from .runner import delegate, is_path_inside  # type: ignore[no-redef]
    from .driver import is_empty_result  # type: ignore[no-redef]
    from . import jobs  # type: ignore[no-redef]
    from .status import (  # type: ignore[no-redef]
        build_status_report,
        run_doctor_json,
        run_inspect_json,
        run_models,
    )
    from .agent_runtime import (  # type: ignore[no-redef]
        cancel_agent_job,
        runtime_status,
        shutdown_runtime,
        start_agent_job,
    )
    from .session import (  # type: ignore[no-redef]
        bind_session_job,
        session_begin,
        session_end,
        session_next,
        session_tick,
    )
    from .economy import (  # type: ignore[no-redef]
        compact_job_record,
        economy_enabled,
        economy_playbook,
    )
except ImportError:  # flat import when package dir is on sys.path
    from audit import (  # noqa: E402
        build_delegation_audit,
        emit as audit_emit,
    )
    from guard import (  # noqa: E402
        DEFAULT_EXECUTE_SANDBOX,
        DEFAULT_PLAN_SANDBOX,
        HARD_CAP_MAX_TURNS,
        SERVER_VERSION as _guard_server_version,
        GuardError,
        host_provided_roots,
        parse_allowed_roots_env,
        path_in_allowlist,
        paths_equal,
        structured_error,
        trust_host_roots_enabled,
        validate_grok_bin,
        validate_json_schema,
        validate_model,
        validate_reasoning_effort,
        validate_rules,
        validate_sandbox_profile,
        validate_session_id,
    )
    from runner import delegate, is_path_inside  # noqa: E402
    from driver import is_empty_result  # noqa: E402
    import jobs  # noqa: E402
    from status import (  # noqa: E402
        build_status_report,
        run_doctor_json,
        run_inspect_json,
        run_models,
    )
    from grok_delegate.agent_runtime import (  # noqa: E402
        cancel_agent_job,
        runtime_status,
        shutdown_runtime,
        start_agent_job,
    )
    from grok_delegate.session import (  # noqa: E402
        bind_session_job,
        session_begin,
        session_end,
        session_next,
        session_tick,
    )
    from grok_delegate.economy import (  # noqa: E402
        compact_job_record,
        economy_enabled,
        economy_playbook,
    )

SERVER_NAME = "grok-delegate"
SERVER_VERSION = _guard_server_version
PROTOCOL_VERSION = "2024-11-05"

TOOL_DELEGATE = "grok_delegate"
TOOL_PLAN = "grok_delegate_plan"
TOOL_START = "grok_delegate_start"
TOOL_POLL = "grok_delegate_poll"
TOOL_STATUS = "grok_delegate_status"
TOOL_DOCTOR = "grok_delegate_doctor"
TOOL_MODELS = "grok_delegate_models"
TOOL_INSPECT = "grok_delegate_inspect"

TOOL_AGENT_STATUS = "grok_agent_status"
TOOL_AGENT_START = "grok_agent_start"
TOOL_AGENT_POLL = "grok_agent_poll"
TOOL_AGENT_CANCEL = "grok_agent_cancel"
TOOL_AGENT_CONSULT = "grok_agent_consult"
TOOL_AGENT_REVIEW = "grok_agent_review"
TOOL_AGENT_EXECUTE = "grok_agent_execute"
TOOL_AGENT_FIX = "grok_agent_fix"
TOOL_AGENT_ECONOMY = "grok_agent_economy"
TOOL_AGENT_PROJECT = "grok_agent_project"
TOOL_AGENT_UPDATE = "grok_agent_update"
TOOL_AGENT_SESSION_BEGIN = "grok_agent_session_begin"
TOOL_AGENT_SESSION_TICK = "grok_agent_session_tick"
TOOL_AGENT_SESSION_NEXT = "grok_agent_session_next"
TOOL_AGENT_SESSION_END = "grok_agent_session_end"

AGENT_ROLE_TOOLS = {
    TOOL_AGENT_CONSULT: "consult",
    TOOL_AGENT_REVIEW: "skeptic",
    TOOL_AGENT_EXECUTE: "execute",
    TOOL_AGENT_FIX: "fix",
}
AGENT_TOOLS = frozenset(
    {
        TOOL_AGENT_STATUS,
        TOOL_AGENT_START,
        TOOL_AGENT_POLL,
        TOOL_AGENT_CANCEL,
        *AGENT_ROLE_TOOLS,
    }
)

STATUS_TOOLS = frozenset({TOOL_STATUS, TOOL_DOCTOR, TOOL_MODELS, TOOL_INSPECT})
# R6: TOOL_START shares the delegate validation path; TOOL_POLL is read-only.
DELEGATE_TOOLS = frozenset({TOOL_DELEGATE, TOOL_PLAN, TOOL_START})
ALL_TOOLS = DELEGATE_TOOLS | STATUS_TOOLS | {TOOL_POLL} | AGENT_TOOLS

_TOOL_DESCRIPTIONS = {
    TOOL_DELEGATE: (
        "Delegate a coding goal to local headless grok in an isolated git worktree "
        "on a grok/* branch. Returns branch + diffstat. No push, no merge, no "
        "--always-approve. Dev-only — not the product admin-bridge."
    ),
    TOOL_PLAN: (
        "Read-only plan variant of grok_delegate (plan_only=true). Does not allow "
        "write/edit/shell mutation in the permission profile."
    ),
    TOOL_START: (
        "Start grok_delegate in the BACKGROUND and return a job_id immediately. Use "
        "this instead of grok_delegate for real lanes: a lane runs for minutes and a "
        "synchronous call is killed by the client timeout, leaving an empty worktree. "
        "Poll with grok_delegate_poll. Same guarded profile, no push, no merge."
    ),
    TOOL_POLL: (
        "Poll a background delegation started with grok_delegate_start. With job_id: "
        "that job's state and result (branch, changed_files, commits, diffstat), plus "
        "live progress while running — phase (preflight/worktree/worktree_recover/"
        "anchors/executor/collect), elapsed_s, phase_elapsed_s, last_step, and "
        "worker_pid once the executor is spawned. A dispatch spends its first minutes "
        "in git with no process and no lane directory to see: that is normal, and "
        "phase is how you tell it apart from a stuck server. Without job_id: newest "
        "job summaries. Read-only."
    ),
    TOOL_STATUS: (
        "Structured health status: grok binary/version, auth presence (without "
        "reading auth.json), git availability, allowed roots/lanes_parent, "
        "permission and sandbox profile info. Read-only."
    ),
    TOOL_DOCTOR: (
        "Run `grok doctor --json` only (never doctor fix). Read-only diagnostic JSON."
    ),
    TOOL_MODELS: (
        "List available models via `grok models`. Read-only; no secrets."
    ),
    TOOL_INSPECT: (
        "Run `grok inspect --json` for an allowlisted project root. Read-only."
    ),
}

# R5: grok_bin is intentionally absent from the client schema.
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {"type": "string", "description": "Coding goal for the executor"},
        "lane": {
            "type": "string",
            "description": "Lane slug or grok/<slug> (not dev/master/main)",
        },
        "base_ref": {
            "type": "string",
            "description": "Git base ref (default origin/dev)",
            "default": "origin/dev",
        },
        "max_turns": {
            "type": "integer",
            "description": f"Max agent turns (server hard-capped at {HARD_CAP_MAX_TURNS})",
        },
        "model": {"type": "string", "description": "Optional model id"},
        "plan_only": {
            "type": "boolean",
            "description": "If true, use read-only permission profile",
            "default": False,
        },
        "repo_root": {
            "type": "string",
            "description": (
                "Absolute path to a repo root; accepted only if it resolve()s to "
                "an entry on GROK_DELEGATE_ALLOWED_ROOTS (or single "
                "GROK_DELEGATE_REPO_ROOT pin)"
            ),
        },
        "lanes_parent": {
            "type": "string",
            "description": (
                "Optional parent dir for worktrees; rejected if inside repo_root "
                "or outside GROK_DELEGATE_LANES_PARENT when that env is set"
            ),
        },
        "sandbox": {
            "type": "string",
            "description": (
                "Optional sandbox profile override: off|workspace|devbox|"
                "read-only|strict (known built-ins only)"
            ),
        },
        "reasoning_effort": {
            "type": "string",
            "description": "Optional reasoning effort (low|medium|high|…)",
        },
        "rules": {
            "type": "string",
            "description": "Optional extra rules appended to system prompt (bounded)",
        },
        "json_schema": {
            "description": "Optional JSON Schema object/string for structured output",
        },
        "lane_verdict": {
            "type": "boolean",
            "default": True,
            "description": (
                "Attach the lane verdict schema (default true). The run ends when the "
                "executor emits that object, so on a goal it must read the codebase to "
                "start, it emits one describing intent and the lane closes empty. Set "
                "false for exploration-heavy goals: the executor then works to natural "
                "completion and reports prose in summary instead of a parsed verdict."
            ),
        },
        "no_subagents": {
            "type": "boolean",
            "description": "If true, pass --no-subagents",
            "default": False,
        },
        "disable_web_search": {
            "type": "boolean",
            "description": "If true, pass --disable-web-search",
            "default": False,
        },
        "resume": {
            "description": (
                "Resume session: true for most recent, or a session UUID string"
            ),
        },
        "continue_session": {
            "type": "boolean",
            "description": "Pass --continue (most recent for cwd)",
            "default": False,
        },
        "fork_session": {
            "type": "boolean",
            "description": "Pass --fork-session (requires resume/continue)",
            "default": False,
        },
        "session_id": {
            "type": "string",
            "description": "UUID for --session-id (new or forked session name)",
        },
    },
    "required": ["goal", "lane"],
    "additionalProperties": False,
}

_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

# R6: poll schema for background delegations.
_POLL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "Job id from grok_delegate_start; omit to list recent jobs",
        },
        "limit": {
            "type": "integer",
            "description": "Max job summaries when listing (default 20)",
        },
    },
    "additionalProperties": False,
}

_INSPECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo_root": {
            "type": "string",
            "description": "Allowlisted project root to inspect",
        },
    },
    "required": ["repo_root"],
    "additionalProperties": False,
}

_TASK_PACKET_PROPERTIES: dict[str, Any] = {
    "schema_version": {"type": "string", "const": "grok-task-packet.v1"},
    "objective": {"type": "string", "minLength": 1, "maxLength": 12000},
    "role": {"type": "string", "enum": ["consult", "execute", "skeptic", "fix"]},
    "project_root": {"type": "string", "minLength": 1, "maxLength": 1024},
    "base_ref": {"type": "string", "minLength": 1, "maxLength": 256},
    "model": {"type": "string", "minLength": 1, "maxLength": 128},
    "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh", "max"]},
    "permission_profile": {"type": "string", "enum": ["read-only", "workspace"]},
    "max_turns": {"type": "integer", "minimum": 1, "maximum": 60},
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
    "inputs": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 2000}},
    "constraints": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 2000}},
    "acceptance_criteria": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 2000}},
    "expected_artifacts": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 2000}},
    "test_commands": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 2000}},
    "correlation_id": {"type": "string", "minLength": 1, "maxLength": 128},
}


def _agent_task_schema(*, require_role: bool, write_role: bool = False) -> dict[str, Any]:
    required = ["objective", "project_root", "correlation_id"]
    if require_role:
        required.append("role")
    if write_role:
        required.extend(["expected_artifacts", "test_commands"])
    task_schema: dict[str, Any] = {
        "type": "object",
        "properties": _TASK_PACKET_PROPERTIES,
        "required": required,
        "additionalProperties": False,
    }
    if write_role:
        task_schema["properties"] = {
            **_TASK_PACKET_PROPERTIES,
            "expected_artifacts": {**_TASK_PACKET_PROPERTIES["expected_artifacts"], "minItems": 1},
            "test_commands": {**_TASK_PACKET_PROPERTIES["test_commands"], "minItems": 1},
        }
    elif require_role:
        task_schema["allOf"] = [{
            "if": {
                "properties": {"role": {"enum": ["execute", "fix"]}},
                "required": ["role"],
            },
            "then": {
                "required": ["expected_artifacts", "test_commands"],
                "properties": {
                    "expected_artifacts": {"minItems": 1},
                    "test_commands": {"minItems": 1},
                },
            },
        }]
    return {
        "type": "object",
        "properties": {
            "task": task_schema,
            "transport": {
                "type": "string",
                "enum": ["legacy", "stdio", "websocket", "auto"],
                "default": "stdio",
            },
            "lane": {"type": "string", "maxLength": 96},
        },
        "required": ["task"],
        "additionalProperties": False,
    }


_AGENT_POLL_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string", "maxLength": 128},
        "limit": {"type": "integer", "minimum": 1, "maximum": 64},
    },
    "additionalProperties": False,
}

_AGENT_CANCEL_SCHEMA = {
    "type": "object",
    "properties": {"job_id": {"type": "string", "minLength": 1, "maxLength": 128}},
    "required": ["job_id"],
    "additionalProperties": False,
}


def mark_empty_execute_lane(
    result: dict[str, Any],
    *,
    plan_only: bool,
) -> dict[str, Any]:
    """Make an empty execute lane unmistakable to an MCP caller.

    ``runner.delegate`` deliberately reports ``ok`` at the run layer and leaves
    emptiness to ``driver.is_empty_result`` — see the exit-0 chaos test. An MCP
    client is not the driver: it sees only this dict, and ``ok: true`` beside
    ``changed_files: []`` reads as success. Measured 2026-07-26: a lane that
    spent its turns reading files came back ``ok: true`` / ``state: done`` and
    was reported as done work.

    Structural signal only; the executor's prose is never pattern-matched.
    ``plan_only`` lanes are exempt — producing nothing is their job.
    """
    if plan_only or not isinstance(result, dict):
        return result
    if not result.get("ok") or not is_empty_result(result):
        return result
    marked = dict(result)
    marked["ok"] = False
    marked["status"] = "no_changes"
    marked["is_empty_result"] = True
    marked["error"] = "EXECUTE_NO_CHANGES"
    marked["message"] = (
        "execute lane finished without changing any file and without committing; "
        "read the summary for the executor's own account (a refusal or an exhausted "
        "turn budget reports itself there). Use the plan tool for work that is not "
        "supposed to write."
    )
    return marked


def load_allowed_roots(
    *,
    env: Mapping[str, str] | None = None,
    injected: Sequence[Path | str] | None = None,
) -> list[Path]:
    """Load allowlist from injection, GROK_DELEGATE_ALLOWED_ROOTS, or REPO_ROOT pin.

    Empty result means fail-closed at resolve time.
    """
    if injected is not None:
        out: list[Path] = []
        for p in injected:
            try:
                out.append(Path(p).resolve())
            except OSError:
                continue
        return out

    environ = env if env is not None else os.environ
    raw = environ.get("GROK_DELEGATE_ALLOWED_ROOTS")
    parts = parse_allowed_roots_env(raw)
    roots: list[Path] = []
    for part in parts:
        try:
            roots.append(Path(part).expanduser().resolve())
        except OSError:
            continue

    # Single-pin fallback: treat REPO_ROOT as a one-entry allowlist.
    if not roots:
        single = environ.get("GROK_DELEGATE_REPO_ROOT")
        if single and str(single).strip():
            try:
                roots.append(Path(single).expanduser().resolve())
            except OSError:
                pass

    # Host-reported project directory, added last so it widens the allowlist
    # rather than replacing it, and so the single-pin fallback above keeps its
    # meaning. Returns nothing unless the operator opted in.
    for candidate in host_provided_roots(environ):
        try:
            host_root = Path(candidate).expanduser().resolve()
        except OSError:
            continue
        if host_root not in roots:
            roots.append(host_root)
    return roots


def default_lanes_parent_for_root(repo_root: Path) -> Path:
    """Sibling ``pcp-lanes`` next to the repo root."""
    return Path(repo_root).resolve().parent / "pcp-lanes"


def resolve_trusted_repo_root(
    args: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    allowed_roots: Sequence[Path | str] | None = None,
) -> Path:
    """Resolve client repo_root against allowlist (R5 + multi-root Round4).

    - Allowlist from ``allowed_roots`` injection, else env
      ``GROK_DELEGATE_ALLOWED_ROOTS`` / single ``GROK_DELEGATE_REPO_ROOT``.
    - When ``repo_root`` injection is provided (tests/server pin) without an
      explicit allowlist, that pin becomes the sole allowlist entry.
    - Client path accepted only if ``resolve()`` equals an allowlist entry.
    - Empty allowlist → fail-closed with setup hint.
    - ``..`` / symlink escapes rejected because membership is post-resolve equality.
    """
    if allowed_roots is not None:
        allow = load_allowed_roots(injected=allowed_roots)
    elif repo_root is not None:
        # Test / explicit server pin: single-entry allowlist.
        allow = [Path(repo_root).resolve()]
    else:
        allow = load_allowed_roots()

    if not allow:
        raise GuardError(
            "ALLOWED_ROOTS_EMPTY",
            "no allowed repo roots configured; set GROK_DELEGATE_ALLOWED_ROOTS "
            "(semicolon-separated absolute paths) or GROK_DELEGATE_REPO_ROOT",
        )

    client = args.get("repo_root")
    if client is None or str(client).strip() == "":
        return allow[0]

    try:
        client_path = Path(str(client)).expanduser().resolve()
    except OSError as exc:
        raise GuardError("REPO_ROOT_INVALID", f"invalid repo_root: {exc}") from exc

    if not path_in_allowlist(client_path, allow):
        raise GuardError(
            "REPO_ROOT_UNTRUSTED",
            "client repo_root is not on GROK_DELEGATE_ALLOWED_ROOTS "
            "(must resolve to an allowlisted absolute path)",
        )
    return client_path


def resolve_trusted_lanes_parent(
    args: Mapping[str, Any],
    *,
    repo_root: Path,
) -> Path | None:
    """Resolve lanes_parent with confinement checks (R5).

    - If GROK_DELEGATE_LANES_PARENT is set, client value must resolve inside it
      (or equal it); if client omits lanes_parent, use the env path.
    - Else default sibling pcp-lanes for this root (returned as Path so worktree
      is never inside the repo).
    - Target parent must never resolve inside repo_root.
    """
    env_parent = os.environ.get("GROK_DELEGATE_LANES_PARENT")
    client = args.get("lanes_parent")

    if env_parent:
        pinned = Path(env_parent).expanduser().resolve()
        if client is None or str(client).strip() == "":
            candidate = pinned
        else:
            candidate = Path(str(client)).expanduser().resolve()
            if not paths_equal(candidate, pinned) and not is_path_inside(candidate, pinned):
                raise GuardError(
                    "LANES_PARENT_UNTRUSTED",
                    "client lanes_parent is outside GROK_DELEGATE_LANES_PARENT",
                )
    elif client is None or str(client).strip() == "":
        candidate = default_lanes_parent_for_root(repo_root)
    else:
        candidate = Path(str(client)).expanduser().resolve()

    if is_path_inside(candidate, repo_root):
        raise GuardError(
            "LANES_PARENT_INSIDE_REPO",
            "lanes_parent must not resolve inside the main repo working tree",
        )
    return candidate


def resolve_server_grok_bin(args: Mapping[str, Any]) -> str:
    """Grok binary from env/config only — never from undeclared client args (R5)."""
    if "grok_bin" in args and args.get("grok_bin") is not None:
        # Fail closed even if additionalProperties were bypassed.
        validate_grok_bin(str(args.get("grok_bin")), from_client=True)
    env_bin = os.environ.get("GROK_DELEGATE_BIN")
    return validate_grok_bin(env_bin, from_client=False)


def sandbox_enabled_from_env() -> bool:
    """Whether to apply --sandbox by default (disabled only if explicitly off)."""
    raw = (os.environ.get("GROK_DELEGATE_SANDBOX") or os.environ.get("GROK_SANDBOX") or "").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return False
    return True


def _delegate_kwargs_from_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """Validate optional CLI params from tool args (fail-closed)."""
    sandbox = None
    if args.get("sandbox") is not None:
        sandbox = validate_sandbox_profile(str(args.get("sandbox")))
    model = validate_model(args.get("model") if args.get("model") is not None else None)
    effort = None
    if args.get("reasoning_effort") is not None:
        effort = validate_reasoning_effort(str(args.get("reasoning_effort")))
    rules = None
    if args.get("rules") is not None:
        rules = validate_rules(str(args.get("rules")))
    schema = None
    if args.get("json_schema") is not None:
        schema = validate_json_schema(args.get("json_schema"))  # type: ignore[arg-type]
    session_id = None
    if args.get("session_id") is not None:
        session_id = validate_session_id(str(args.get("session_id")))

    resume: str | bool | None = None
    if "resume" in args:
        r = args.get("resume")
        if r is None or r is False:
            resume = None  # omit --resume (JSON false is not a session id)
        elif r is True:
            resume = True
        else:
            resume = str(r).strip() or None

    return {
        "model": model,
        "sandbox": sandbox,
        "sandbox_enabled": sandbox_enabled_from_env(),
        "reasoning_effort": effort,
        "rules": rules,
        "json_schema": schema,
        # The lane verdict schema is what ends a run: with --json-schema in argv
        # the executor's job is to produce that object, and on a goal that needs
        # reading first it produces one describing intent ("inspecting package
        # conventions") long before any work. Measured 2026-07-26: five lanes in
        # a row came back empty for exactly that reason, while a goal doable
        # without exploration finished in two turns. Callers with an
        # exploration-heavy goal can turn the schema off and read the prose
        # summary instead.
        "lane_verdict": bool(args.get("lane_verdict", True)),
        "no_subagents": bool(args.get("no_subagents", False)),
        "disable_web_search": bool(args.get("disable_web_search", False)),
        "resume": resume,
        "continue_session": bool(args.get("continue_session", False)),
        "fork_session": bool(args.get("fork_session", False)),
        "session_id": session_id,
    }


def handle_status_tool(
    name: str,
    arguments: Mapping[str, Any] | None,
    *,
    allowed_roots: Sequence[Path | str] | None = None,
    repo_root: Path | None = None,
    subprocess_runner=None,
    which=None,
    git_runner=None,
) -> dict[str, Any]:
    """Handle read-only status tools (no delegation, no mutations)."""
    args = dict(arguments or {})
    try:
        grok_bin = resolve_server_grok_bin(args)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    if allowed_roots is not None:
        roots = load_allowed_roots(injected=allowed_roots)
    elif repo_root is not None:
        roots = [Path(repo_root).resolve()]
    else:
        roots = load_allowed_roots()

    if name == TOOL_STATUS:
        # Same lanes resolution as delegate path (honors GROK_DELEGATE_LANES_PARENT).
        lanes_map: dict[str, str] = {}
        for r in roots:
            try:
                parent = resolve_trusted_lanes_parent({}, repo_root=r)
            except GuardError:
                parent = default_lanes_parent_for_root(r)
            lanes_map[str(r)] = str(
                parent if parent is not None else default_lanes_parent_for_root(r)
            )
        report = build_status_report(
            allowed_roots=roots,
            lanes_parent_map=lanes_map,
            grok_bin=grok_bin,
            sandbox_enabled=sandbox_enabled_from_env(),
            default_execute_sandbox=DEFAULT_EXECUTE_SANDBOX,
            default_plan_sandbox=DEFAULT_PLAN_SANDBOX,
            subprocess_runner=subprocess_runner,
            which=which,
            git_runner=git_runner,
        )
        return report

    if name == TOOL_DOCTOR:
        from .status import compatibility_report

        report = run_doctor_json(
            grok_bin=grok_bin,
            subprocess_runner=subprocess_runner,
            which=which,
        )
        if isinstance(report, dict):
            report = dict(report)
            report["compatibility"] = compatibility_report()
            if report["compatibility"].get("warning"):
                report["warnings"] = [report["compatibility"]["warning"]]
        return report

    if name == TOOL_MODELS:
        return run_models(
            grok_bin=grok_bin,
            subprocess_runner=subprocess_runner,
            which=which,
        )

    if name == TOOL_INSPECT:
        # Require allowlisted project.
        try:
            root = resolve_trusted_repo_root(
                args,
                repo_root=repo_root,
                allowed_roots=allowed_roots if allowed_roots is not None else (
                    [repo_root] if repo_root is not None else None
                ),
            )
        except GuardError as exc:
            return structured_error(exc.code, exc.message)
        return run_inspect_json(
            root,
            grok_bin=grok_bin,
            subprocess_runner=subprocess_runner,
            which=which,
        )

    return structured_error("TOOL_UNKNOWN", f"unknown status tool: {name}")


def _handle_update_tool(confirm: bool) -> dict[str, Any]:
    """Report, and on confirmation apply, an update to the bridge checkout.

    Two-step on purpose. Applying is a mutation of the operator's working copy
    and ends in a restart that drops their session, so it happens only when they
    said so -- and never over uncommitted work.
    """
    from .updater import checkout_is_dirty, default_git_runner, plan_update, update_status

    status = update_status()
    plan = plan_update(status, python_executable=sys.executable)
    out: dict[str, Any] = {"ok": True, **status, "plan": plan, "applied": False}

    if not status["checkout"]:
        out["message"] = "the bridge is not running from a git checkout, so it cannot self-update"
        return out
    if not status["available"]:
        out["message"] = (
            "the bridge is up to date"
            if status["reason"] is None
            else f"cannot tell whether an update exists ({status['reason']})"
        )
        return out

    out["message"] = (
        "an update is available; call again with confirm=true to pull and reinstall, "
        "then restart the MCP host"
    )
    if not confirm:
        return out

    dirty = checkout_is_dirty(status["checkout"], git_runner=default_git_runner)
    if dirty or dirty is None:
        return structured_error(
            "CHECKOUT_DIRTY" if dirty else "CHECKOUT_STATE_UNKNOWN",
            "refusing to update: the checkout has local changes or its state cannot be read",
            checkout=status["checkout"],
        )

    ran: list[dict[str, Any]] = []
    for step in plan["steps"]:
        try:
            result = default_git_runner(list(step["argv"]), timeout=300.0)
        except Exception as exc:
            ran.append({"what": step["what"], "ok": False, "detail": str(exc)[:300]})
            out.update({"ok": False, "steps_run": ran, "message": "update failed"})
            return out
        ok = getattr(result, "returncode", 1) == 0
        ran.append(
            {
                "what": step["what"],
                "ok": ok,
                "detail": (str(getattr(result, "stderr", "") or "")[:300] if not ok else ""),
            }
        )
        if not ok:
            out.update({"ok": False, "steps_run": ran, "message": "update failed"})
            return out

    out.update(
        {
            "applied": True,
            "steps_run": ran,
            "message": "updated; restart the MCP host so the running process picks up the new code",
            "restart_required": True,
        }
    )
    return out


def _handle_project_tool(args: Mapping[str, Any]) -> dict[str, Any]:
    """Report or set a project's preset.

    Writing is confined to the config file inside an allowlisted root: the point
    of the gate is that a project opts in deliberately, so this must not become a
    way to opt arbitrary directories in.
    """
    from .project_config import (
        CONFIG_FILENAME,
        PRESET_DESCRIPTIONS,
        config_path,
        project_gate,
        render_config,
    )

    raw_root = str(args.get("project_root") or "").strip()
    if not raw_root:
        return structured_error("PROJECT_ROOT_EMPTY", "project_root is required")
    roots = load_allowed_roots()
    if not roots:
        return structured_error(
            "ALLOWED_ROOTS_EMPTY",
            "configure GROK_DELEGATE_ALLOWED_ROOTS or GROK_DELEGATE_REPO_ROOT",
        )
    try:
        root = Path(raw_root).resolve()
    except OSError as exc:
        return structured_error("PROJECT_ROOT_INVALID", f"project_root cannot be resolved: {exc}")
    if not path_in_allowlist(root, roots):
        return structured_error(
            "PROJECT_ROOT_NOT_ALLOWED",
            "project_root is not an allowlisted root",
            project_root=str(root),
        )

    preset = str(args.get("preset") or "").strip().lower()
    if preset:
        try:
            body = render_config(preset, note=str(args.get("note") or "") or None)
        except GuardError as exc:
            return structured_error(exc.code, exc.message)
        target = config_path(root)
        try:
            target.write_text(body, encoding="utf-8")
        except OSError as exc:
            return structured_error("PROJECT_CONFIG_UNWRITABLE", f"cannot write {CONFIG_FILENAME}: {exc}")

    try:
        gate = project_gate(root)
    except GuardError as exc:
        return structured_error(exc.code, exc.message, config_path=str(config_path(root)))
    return {
        "ok": True,
        "project_root": str(root),
        "written": bool(preset),
        "presets": dict(PRESET_DESCRIPTIONS),
        **gate,
    }


def _apply_project_gate(task: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Refuse projects that never opted in; fill the budget for those that did.

    Returns ``(error_or_None, task)``. The error carries the config path and the
    preset menu, because "not configured" is only actionable if the caller is
    told where the file goes and what may be written in it.
    """
    from .project_config import CONFIG_FILENAME, project_gate

    out = dict(task)
    root = str(out.get("project_root") or "").strip()
    if not root:
        # No root to look at yet; the task contract rejects this on its own and
        # reports it better than a gate message could.
        return None, out
    try:
        gate = project_gate(root)
    except GuardError as exc:
        return structured_error(exc.code, exc.message, config_path=str(Path(root) / CONFIG_FILENAME)), out
    if not gate["enabled"]:
        detail = {
            "config_path": gate["config_path"],
            "reason": gate["reason"],
            "preset": gate["preset"],
        }
        if gate.get("presets"):
            detail["presets"] = gate["presets"]
        if gate["reason"] == "PROJECT_PRESET_OFF":
            message = (
                f"{CONFIG_FILENAME} sets preset 'off' for this project; "
                "pick another preset to delegate here"
            )
        else:
            message = (
                f"this project has no {CONFIG_FILENAME}, so the bridge will not run a job in it; "
                "write one naming a preset to opt in"
            )
        return structured_error("PROJECT_NOT_ENABLED", message, **detail), out

    # Preset budget fills what the caller left unsaid; an explicit field wins.
    for field, value in gate["budget"].items():
        out.setdefault(field, value)
    return None, out


def handle_tool_call(
    name: str,
    arguments: Mapping[str, Any] | None,
    *,
    repo_root: Path | None = None,
    allowed_roots: Sequence[Path | str] | None = None,
    git_runner=None,
    subprocess_runner=None,
    which=None,
    audit_stream: TextIO | None = None,
    principal: str = "local-dev",
) -> dict[str, Any]:
    """Transport-independent tool handler (callable without stdio)."""
    args = dict(arguments or {})

    def typed_return(result: dict[str, Any], task: Mapping[str, Any] | None = None) -> dict[str, Any]:
        packet = task if isinstance(task, Mapping) else {}
        event = build_delegation_audit(
            principal=principal,
            tool=name,
            lane=str(args.get("lane") or result.get("lane") or ""),
            base_ref=str(packet.get("base_ref") or ""),
            cwd=str(packet.get("project_root") or result.get("worktree_path") or ""),
            turns_used=None,
            outcome="ok" if result.get("ok") else "error",
            goal=str(packet.get("objective") or "") if packet else None,
            error=str(result.get("error") or "") or None,
            status=str(result.get("state") or result.get("status") or ""),
        )
        event["transport"] = str(args.get("transport") or result.get("transport") or "none")
        if packet.get("correlation_id"):
            event["correlation_id"] = str(packet["correlation_id"])
        try:
            audit_emit(event, stream=audit_stream)
        except Exception:
            pass
        return result

    # Round 8 primary typed surface.  The old grok_delegate_* names below stay
    # as compatibility aliases over the legacy backend and diagnostics.
    if name == TOOL_AGENT_ECONOMY:
        if args:
            return typed_return(
                structured_error("ARGUMENTS_UNKNOWN", "grok_agent_economy accepts no arguments")
            )
        return typed_return(economy_playbook())

    if name == TOOL_AGENT_UPDATE:
        unknown = sorted(set(args) - {"confirm"})
        if unknown:
            return typed_return(
                structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}")
            )
        return typed_return(_handle_update_tool(bool(args.get("confirm"))))

    if name == TOOL_AGENT_PROJECT:
        unknown = sorted(set(args) - {"project_root", "preset", "note"})
        if unknown:
            return typed_return(
                structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}")
            )
        return typed_return(_handle_project_tool(args))

    if name == TOOL_AGENT_SESSION_BEGIN:
        unknown = sorted(
            set(args)
            - {
                "intent",
                "goal",
                "host_budget",
                "max_tool_calls",
                "project_root",
                "expected_artifacts",
                "test_commands",
                "correlation_id",
            }
        )
        if unknown:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}"))
        intent = str(args.get("intent") or "auto")
        mtc = args.get("max_tool_calls")
        try:
            mtc_i = int(mtc) if mtc is not None and str(mtc) != "" else None
        except (TypeError, ValueError):
            return typed_return(structured_error("MAX_TOOL_CALLS_INVALID", "max_tool_calls must be int"))
        artifacts = args.get("expected_artifacts")
        tests = args.get("test_commands")
        if artifacts is not None and not isinstance(artifacts, (list, tuple, str)):
            return typed_return(structured_error("EXPECTED_ARTIFACTS_INVALID", "expected_artifacts must be a string list"))
        if tests is not None and not isinstance(tests, (list, tuple, str)):
            return typed_return(structured_error("TEST_COMMANDS_INVALID", "test_commands must be a string list"))
        return typed_return(
            session_begin(
                intent,
                goal=str(args.get("goal") or "") or None,
                host_budget=str(args.get("host_budget") or "small"),
                max_tool_calls=mtc_i,
                allowed_roots=allowed_roots,
                which=which,
                subprocess_runner=subprocess_runner,
                project_root=str(args.get("project_root") or "") or None,
                expected_artifacts=artifacts,
                test_commands=tests,
                correlation_id=str(args.get("correlation_id") or "") or None,
            )
        )

    if name == TOOL_AGENT_SESSION_TICK:
        unknown = sorted(set(args) - {"session_id", "job_id", "verbose", "tool_used", "step_done"})
        if unknown:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}"))
        return typed_return(
            session_tick(
                session_id=str(args.get("session_id") or "") or None,
                job_id=str(args.get("job_id") or "") or None,
                verbose=bool(args.get("verbose")),
                tool_used=str(args.get("tool_used") or "") or None,
                step_done=bool(args.get("step_done")),
            )
        )

    if name == TOOL_AGENT_SESSION_NEXT:
        unknown = sorted(set(args) - {"session_id", "advance", "note"})
        if unknown:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}"))
        adv = args.get("advance")
        advance = True if adv is None else bool(adv)
        return typed_return(
            session_next(
                session_id=str(args.get("session_id") or "") or None,
                advance=advance,
                note=str(args.get("note") or "") or None,
            )
        )

    if name == TOOL_AGENT_SESSION_END:
        unknown = sorted(set(args) - {"session_id", "job_id", "suggest_issue", "note"})
        if unknown:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}"))
        return typed_return(
            session_end(
                session_id=str(args.get("session_id") or "") or None,
                job_id=str(args.get("job_id") or "") or None,
                suggest_issue=bool(args.get("suggest_issue")),
                note=str(args.get("note") or "") or None,
            )
        )

    if name == TOOL_AGENT_STATUS:
        if args:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", "grok_agent_status accepts no arguments"))
        report = handle_status_tool(
            TOOL_STATUS,
            {},
            allowed_roots=allowed_roots,
            repo_root=repo_root,
            subprocess_runner=subprocess_runner,
            which=which,
            git_runner=git_runner,
        )
        return typed_return({"ok": bool(report.get("ok")), "legacy": report, "runtime": runtime_status()})

    if name == TOOL_AGENT_POLL:
        unknown = sorted(set(args) - {"job_id", "limit"})
        if unknown:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}"))
        job_id = str(args.get("job_id") or "").strip()
        if job_id:
            record = jobs.get_job(job_id)
            if record is None:
                return typed_return(structured_error("JOB_UNKNOWN", f"unknown job_id: {job_id}"))
            compact = compact_job_record(record)
            return typed_return({"ok": True, **compact})
        try:
            limit = max(1, min(int(args.get("limit", 20)), 64))
        except (TypeError, ValueError):
            return typed_return(structured_error("LIMIT_INVALID", "limit must be an integer"))
        listed = [compact_job_record(j) for j in jobs.list_jobs(limit=limit)]
        return typed_return({"ok": True, "jobs": listed, "economy": economy_enabled()})

    if name == TOOL_AGENT_CANCEL:
        unknown = sorted(set(args) - {"job_id"})
        if unknown:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}"))
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return typed_return(structured_error("JOB_ID_EMPTY", "job_id is required"))
        return typed_return(cancel_agent_job(job_id))

    if name == TOOL_AGENT_START or name in AGENT_ROLE_TOOLS:
        unknown = sorted(set(args) - {"task", "transport", "lane"})
        if unknown:
            return typed_return(structured_error("ARGUMENTS_UNKNOWN", f"unknown arguments: {', '.join(unknown)}"), args.get("task") if isinstance(args.get("task"), Mapping) else None)
        if allowed_roots is not None:
            roots = load_allowed_roots(injected=allowed_roots)
        elif repo_root is not None:
            roots = [Path(repo_root).resolve()]
        else:
            roots = load_allowed_roots()
        if not roots:
            return typed_return(structured_error(
                "ALLOWED_ROOTS_EMPTY",
                "configure GROK_DELEGATE_ALLOWED_ROOTS or GROK_DELEGATE_REPO_ROOT",
            ), args.get("task") if isinstance(args.get("task"), Mapping) else None)
        try:
            grok_bin = resolve_server_grok_bin({})
        except GuardError as exc:
            return typed_return(structured_error(exc.code, exc.message), args.get("task") if isinstance(args.get("task"), Mapping) else None)
        typed_task = args.get("task") if isinstance(args.get("task"), Mapping) else {}
        gate_error, typed_task = _apply_project_gate(typed_task)
        if gate_error is not None:
            return typed_return(gate_error, typed_task)
        result = start_agent_job(
            typed_task,
            transport=str(args.get("transport") or "stdio"),
            allowed_roots=roots,
            forced_role=AGENT_ROLE_TOOLS.get(name),
            lane=str(args.get("lane") or "") or None,
            grok_bin=grok_bin,
        )
        if result.get("job_id") and name in {TOOL_AGENT_EXECUTE, TOOL_AGENT_FIX, TOOL_AGENT_START}:
            bind_session_job(
                str(result["job_id"]),
                correlation_id=str(typed_task.get("correlation_id") or "") or None,
            )
        return typed_return(result, typed_task)

    if name in STATUS_TOOLS:
        return handle_status_tool(
            name,
            args,
            allowed_roots=allowed_roots,
            repo_root=repo_root,
            subprocess_runner=subprocess_runner,
            which=which,
            git_runner=git_runner,
        )

    if name == TOOL_POLL:
        # R6: read-only progress surface for background delegations.
        job_id = str(args.get("job_id") or "").strip()
        if job_id:
            record = jobs.get_job(job_id)
            if record is None:
                return structured_error("JOB_UNKNOWN", f"unknown job_id: {job_id}")
            return {"ok": True, **record}
        limit = args.get("limit")
        try:
            limit_v = int(limit) if limit is not None else 20
        except (TypeError, ValueError):
            limit_v = 20
        return {"ok": True, "jobs": jobs.list_jobs(limit=limit_v)}

    plan_only = bool(args.get("plan_only", False)) or name == TOOL_PLAN

    if name not in DELEGATE_TOOLS:
        err = structured_error("TOOL_UNKNOWN", f"unknown tool: {name}")
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err

    goal = args.get("goal")
    lane = args.get("lane")
    if not goal or not str(goal).strip():
        err = structured_error("GOAL_EMPTY", "goal is required")
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err
    if not lane or not str(lane).strip():
        err = structured_error("LANE_EMPTY", "lane is required")
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err

    try:
        root = resolve_trusted_repo_root(
            args,
            repo_root=repo_root,
            allowed_roots=allowed_roots,
        )
        lanes_parent = resolve_trusted_lanes_parent(args, repo_root=root)
        grok_bin = resolve_server_grok_bin(args)
        extra = _delegate_kwargs_from_args(args)
    except GuardError as exc:
        err = structured_error(exc.code, exc.message)
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err

    base_ref = str(args.get("base_ref") or "origin/dev")
    max_turns = args.get("max_turns")


    def _run_delegation() -> dict[str, Any]:
        return mark_empty_execute_lane(
            delegate(
                goal=str(goal),
                lane=str(lane),
                repo_root=root,
                base_ref=base_ref,
                max_turns=int(max_turns) if max_turns is not None else None,
                plan_only=plan_only,
                lanes_parent=lanes_parent,
                grok_bin=str(grok_bin),
                git_runner=git_runner,
                subprocess_runner=subprocess_runner,
                which=which,
                **extra,
            ),
            plan_only=plan_only,
        )

    if name == TOOL_START:
        # R6: detached lane — validation above already ran, so a bad request still
        # fails fast; only the long executor spawn moves off the request path.
        job = jobs.start_job(_run_delegation, lane=str(lane), tool=name)
        started = {
            "ok": True,
            "job_id": job.get("job_id"),
            "lane": str(lane),
            "state": job.get("state"),
            "poll_with": TOOL_POLL,
            "message": (
                "Delegation started in the background. Poll with "
                f"{TOOL_POLL} using this job_id."
            ),
        }
        audit_emit(
            build_delegation_audit(
                principal=principal,
                tool=name,
                lane=str(lane),
                base_ref=base_ref,
                cwd=None,
                turns_used=None,
                outcome="started",
                goal=str(goal),
                plan_only=plan_only,
                error=None,
                changed_file_count=0,
                branch=None,
                worktree_path=None,
                status="running",
            ),
            stream=audit_stream,
        )
        return started

    try:
        result = _run_delegation()
    except GuardError as exc:
        result = structured_error(exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 — surface as structured tool error
        result = structured_error(
            "INTERNAL_ERROR",
            f"{type(exc).__name__}: {exc}",
        )

    outcome = "ok" if result.get("ok") else "error"
    try:
        audit_emit(
            build_delegation_audit(
                principal=principal,
                tool=name,
                lane=result.get("lane") or str(lane),
                base_ref=base_ref,
                cwd=result.get("worktree_path"),
                turns_used=result.get("turns_used"),
                outcome=outcome,
                goal=str(goal),
                plan_only=plan_only,
                error=result.get("error"),
                changed_file_count=len(result.get("changed_files") or []),
                branch=result.get("branch"),
                worktree_path=result.get("worktree_path"),
                status=result.get("status"),
            ),
            stream=audit_stream,
        )
    except Exception:
        pass
    return result


def _audit_failure(
    *,
    principal: str,
    tool: str,
    args: Mapping[str, Any],
    result: Mapping[str, Any],
    stream: TextIO | None,
) -> None:
    try:
        audit_emit(
            build_delegation_audit(
                principal=principal,
                tool=tool,
                lane=str(args.get("lane") or ""),
                base_ref=str(args.get("base_ref") or ""),
                cwd=None,
                turns_used=None,
                outcome="error",
                goal=str(args.get("goal") or "") or None,
                plan_only=bool(args.get("plan_only", False)),
                error=result.get("error"),
                status="error",
            ),
            stream=stream,
        )
    except Exception:
        # Audit must not crash the tool path; best-effort only.
        pass


def list_tools() -> list[dict[str, Any]]:
    plan_schema = {
        **_INPUT_SCHEMA,
        "properties": {
            **_INPUT_SCHEMA["properties"],
            "plan_only": {
                "type": "boolean",
                "const": True,
                "default": True,
            },
        },
    }
    primary = [
        {
            "name": TOOL_AGENT_ECONOMY,
            "description": (
                "Token-economy playbook for host agents: when to consult vs execute, "
                "how to poll compact receipts, and VPS offload tips. Call once; prefer "
                "over re-planning. Read-only, no secrets."
            ),
            "inputSchema": _STATUS_SCHEMA,
        },
        {
            "name": TOOL_AGENT_UPDATE,
            "description": (
                "Check whether the running bridge is behind its git remote, and update it. "
                "Without `confirm` it only reports and returns the exact steps it would run. "
                "With confirm=true it pulls and reinstalls, then asks you to restart the MCP "
                "host -- the server cannot restart itself. Refuses if the checkout is dirty."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "Run the update. Omit to preview it.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_AGENT_PROJECT,
            "description": (
                "Read or set this project's Grok preset (.grok-mcp.json). Without `preset` "
                "it reports whether the project opted in and lists the presets: off, cheap, "
                "standard, max. With `preset` it writes the file. Job tools refuse a project "
                "that has no config, so call this first when they report PROJECT_NOT_ENABLED."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string", "minLength": 1},
                    "preset": {"enum": ["off", "cheap", "standard", "max"]},
                    "note": {"type": "string", "maxLength": 500},
                },
                "required": ["project_root"],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_AGENT_SESSION_BEGIN,
            "description": (
                "Session Protocol v1.2: begin session with plan compiler + budget guard. "
                "Pass intent, optional goal (≤500), host_budget tiny|small|normal. "
                "Returns mode, plan[≤5], budget, deny_tools, host_script, skill_ref. Call first."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": [
                            "brainstorm",
                            "execute",
                            "verify",
                            "install",
                            "update",
                            "triage",
                            "feedback",
                            "auto",
                        ],
                        "default": "auto",
                    },
                    "goal": {"type": "string", "description": "User goal ≤500 chars"},
                    "host_budget": {
                        "type": "string",
                        "enum": ["tiny", "small", "normal"],
                        "default": "small",
                    },
                    "max_tool_calls": {"type": "integer", "minimum": 1, "maximum": 32},
                    "project_root": {"type": "string", "maxLength": 1024},
                    "correlation_id": {"type": "string", "maxLength": 128},
                    "expected_artifacts": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {"type": "string", "maxLength": 2000},
                    },
                    "test_commands": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {"type": "string", "maxLength": 2000},
                    },
                },
            },
        },
        {
            "name": TOOL_AGENT_SESSION_TICK,
            "description": (
                "Session Protocol v1.2: compact progress + budget. "
                "Returns step, steps_left, budget_remaining, force_end. "
                "Pass tool_used/step_done to count budget. verbose default false."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "verbose": {"type": "boolean", "default": False},
                    "tool_used": {"type": "string"},
                    "step_done": {"type": "boolean", "default": False},
                },
            },
        },
        {
            "name": TOOL_AGENT_SESSION_NEXT,
            "description": (
                "Session Protocol v1.2 navigator: ONE next action card "
                "(host_cmd|mcp_tool|end). Host loops this until done=true."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {"type": "string"},
                    "advance": {"type": "boolean", "default": True},
                    "note": {"type": "string"},
                },
            },
        },
        {
            "name": TOOL_AGENT_SESSION_END,
            "description": (
                "Session Protocol v1.2: short receipt (status/job/changed/tests/next). "
                "Optional suggest_issue returns scrubbed issue draft (no auto-create)."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "suggest_issue": {"type": "boolean", "default": False},
                    "note": {"type": "string"},
                },
            },
        },
        {
            "name": TOOL_AGENT_STATUS,
            "description": "Version/auth-presence, exact roots, transport router, daemon and durable job status. Read-only.",
            "inputSchema": _STATUS_SCHEMA,
        },
        {
            "name": TOOL_AGENT_START,
            "description": "Asynchronously start a versioned typed task packet on an explicit legacy|stdio|websocket transport. auto means stdio only.",
            "inputSchema": _agent_task_schema(require_role=True),
        },
        {
            "name": TOOL_AGENT_POLL,
            "description": "Read bounded progress/events and the final evidence receipt for a typed job.",
            "inputSchema": _AGENT_POLL_SCHEMA,
        },
        {
            "name": TOOL_AGENT_CANCEL,
            "description": "Boundedly cancel one typed job without stopping the MCP server.",
            "inputSchema": _AGENT_CANCEL_SCHEMA,
        },
        {
            "name": TOOL_AGENT_CONSULT,
            "description": "Start an isolated read-only consult session; non-git exact allowlisted roots are supported.",
            "inputSchema": _agent_task_schema(require_role=False),
        },
        {
            "name": TOOL_AGENT_REVIEW,
            "description": "Start an independent read-only skeptic session over supplied evidence/diff inputs.",
            "inputSchema": _agent_task_schema(require_role=False),
        },
        {
            "name": TOOL_AGENT_EXECUTE,
            "description": "Start workspace execution in a git worktree. completed requires a real diff and requested evidence.",
            "inputSchema": _agent_task_schema(require_role=False, write_role=True),
        },
        {
            "name": TOOL_AGENT_FIX,
            "description": "Start a separate workspace fixer session scoped to confirmed findings in the task packet.",
            "inputSchema": _agent_task_schema(require_role=False, write_role=True),
        },
    ]
    compatibility = [
        {
            "name": TOOL_DELEGATE,
            "description": _TOOL_DESCRIPTIONS[TOOL_DELEGATE],
            "inputSchema": _INPUT_SCHEMA,
        },
        {
            "name": TOOL_PLAN,
            "description": _TOOL_DESCRIPTIONS[TOOL_PLAN],
            "inputSchema": plan_schema,
        },
        {
            "name": TOOL_START,
            "description": _TOOL_DESCRIPTIONS[TOOL_START],
            "inputSchema": _INPUT_SCHEMA,
        },
        {
            "name": TOOL_POLL,
            "description": _TOOL_DESCRIPTIONS[TOOL_POLL],
            "inputSchema": _POLL_SCHEMA,
        },
        {
            "name": TOOL_STATUS,
            "description": _TOOL_DESCRIPTIONS[TOOL_STATUS],
            "inputSchema": _STATUS_SCHEMA,
        },
        {
            "name": TOOL_DOCTOR,
            "description": _TOOL_DESCRIPTIONS[TOOL_DOCTOR],
            "inputSchema": _STATUS_SCHEMA,
        },
        {
            "name": TOOL_MODELS,
            "description": _TOOL_DESCRIPTIONS[TOOL_MODELS],
            "inputSchema": _STATUS_SCHEMA,
        },
        {
            "name": TOOL_INSPECT,
            "description": _TOOL_DESCRIPTIONS[TOOL_INSPECT],
            "inputSchema": _INSPECT_SCHEMA,
        },
    ]
    return primary + compatibility


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def handle_jsonrpc(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request; returns response or None for notifications."""
    if message.get("jsonrpc") != "2.0":
        return _jsonrpc_error(message.get("id"), -32600, "invalid jsonrpc version")

    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    is_notification = "id" not in message

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else _jsonrpc_result(req_id, result)

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return None if is_notification else _jsonrpc_result(
            req_id, {"tools": list_tools()}
        )

    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") if isinstance(params, dict) else {}
        if not name:
            return _jsonrpc_error(req_id, -32602, "tools/call requires name")
        tool_result = handle_tool_call(str(name), arguments or {})
        payload = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(tool_result, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": tool_result,
            "isError": not bool(tool_result.get("ok")),
        }
        return None if is_notification else _jsonrpc_result(req_id, payload)

    if method == "ping":
        return None if is_notification else _jsonrpc_result(req_id, {})

    if is_notification:
        return None
    return _jsonrpc_error(req_id, -32601, f"method not found: {method}")


# Bound the log so an unattended server cannot fill the disk.
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 3
_LOG_HANDLER_TAG = "grok-delegate-file"


def configure_logging(env: Mapping[str, str] | None = None) -> Path | None:
    """Attach a rotating file log; return its path, or None when disabled.

    The modules already log — nothing ever collected it, so the server ran with
    no on-disk trace at all and a stuck dispatch could only be diagnosed by
    watching the filesystem by hand. Explicit path via GROK_DELEGATE_LOG_FILE,
    otherwise alongside the durable job records (an operator who asked for those
    asked for durable state). Level via GROK_DELEGATE_LOG_LEVEL, default INFO.

    Never a stdout handler: stdout is the JSON-RPC channel, and a log line
    written there corrupts the protocol.
    """
    source = env if env is not None else os.environ
    raw = (source.get("GROK_DELEGATE_LOG_FILE") or "").strip()
    if raw:
        target = Path(raw)
    else:
        jobs_dir = (source.get("GROK_DELEGATE_JOBS_DIR") or "").strip()
        if not jobs_dir:
            return None
        target = Path(jobs_dir) / "grok-delegate.log"

    root = logging.getLogger("grok_delegate")
    for existing in list(root.handlers):
        if getattr(existing, "_gd_tag", None) == _LOG_HANDLER_TAG:
            root.removeHandler(existing)
            try:
                existing.close()
            except Exception:  # noqa: BLE001 — a stale handler must not block setup
                pass

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        # An unwritable log path degrades to no logging. It must never stop a
        # server from serving.
        return None

    handler._gd_tag = _LOG_HANDLER_TAG  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    level_name = (source.get("GROK_DELEGATE_LOG_LEVEL") or "INFO").strip().upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))
    root.addHandler(handler)
    # Do not let records escape to a root handler the host may have pointed at
    # stdout.
    root.propagate = False
    root.info("logging to %s (level=%s)", target, logging.getLevelName(root.level))
    return target


def configure_durable_jobs(env: Mapping[str, str] | None = None) -> Path | None:
    """Enable durable job records from GROK_DELEGATE_JOBS_DIR and rehydrate them.

    R7-D shipped persistence but nothing switched it on, so a live server still kept job
    state in memory only: a restart lost the status of every lane it had dispatched while
    the work itself survived on the lane branch. Called at startup; returns the directory
    in use, or None when the env var is unset (memory-only, previous behaviour).
    """
    source = env if env is not None else os.environ
    raw = (source.get("GROK_DELEGATE_JOBS_DIR") or "").strip()
    if not raw:
        return None
    target = Path(raw)
    jobs.configure_jobs_dir(target)
    jobs.rehydrate_jobs(target)
    return target


def serve_stdio(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Blocking stdio JSON-RPC loop (line-delimited or Content-Length framed)."""
    if stdin is None and stdout is None and hasattr(sys.stdin, "buffer") and hasattr(sys.stdout, "buffer"):
        configure_logging()
        configure_durable_jobs()
        _serve_binary_stdio(sys.stdin.buffer, sys.stdout.buffer)
        return
    inn = stdin or sys.stdin
    out = stdout or sys.stdout
    configure_logging()
    configure_durable_jobs()

    while True:
        chunk = inn.readline()
        if chunk == "":
            break
        line = chunk.strip()
        if not line:
            continue

        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            inn.readline()  # blank line after headers
            body = inn.read(length)
            try:
                message = json.loads(body)
            except json.JSONDecodeError:
                continue
            response = handle_jsonrpc(message)
            if response is not None:
                raw = json.dumps(response, ensure_ascii=False)
                out.write(f"Content-Length: {len(raw.encode('utf-8'))}\r\n\r\n{raw}")
                out.flush()
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            err = _jsonrpc_error(None, -32700, "parse error")
            out.write(json.dumps(err) + "\n")
            out.flush()
            continue

        try:
            response = handle_jsonrpc(message)
        except Exception as exc:  # noqa: BLE001
            response = _jsonrpc_error(
                message.get("id"),
                -32603,
                f"internal error: {type(exc).__name__}",
            )
            traceback.print_exc(file=sys.stderr)

        if response is not None:
            out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()


def _serve_binary_stdio(inn: Any, out: Any) -> None:
    """Byte-correct MCP framing; Content-Length always counts UTF-8 bytes."""
    while True:
        first = inn.readline()
        if first == b"":
            return
        if not first.strip():
            continue
        framed = first.lower().startswith(b"content-length:")
        if framed:
            try:
                length = int(first.split(b":", 1)[1].strip())
            except ValueError:
                continue
            while True:
                header = inn.readline()
                if header in {b"", b"\r\n", b"\n"}:
                    break
            body = bytearray()
            while len(body) < length:
                chunk = inn.read(length - len(body))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) != length:
                return
        else:
            body = bytearray(first.rstrip(b"\r\n"))
        try:
            message = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = _jsonrpc_error(None, -32700, "parse error")
        else:
            try:
                response = handle_jsonrpc(message)
            except Exception as exc:  # noqa: BLE001
                response = _jsonrpc_error(
                    message.get("id") if isinstance(message, dict) else None,
                    -32603,
                    f"internal error: {type(exc).__name__}",
                )
                traceback.print_exc(file=sys.stderr)
        if response is None:
            continue
        raw = json.dumps(response, ensure_ascii=False).encode("utf-8")
        out.write((f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw) if framed else raw + b"\n")
        out.flush()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        sys.stdout.write(
            "grok-delegate MCP (unofficial community bridge)\n"
            "Usage: python -m grok_delegate.server\n"
            "       python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765\n"
            "       python -m grok_delegate --self-test\n"
            "Env: GROK_DELEGATE_ALLOWED_ROOTS, GROK_DELEGATE_REPO_ROOT,\n"
            "     GROK_DELEGATE_BIN, GROK_DELEGATE_LANES_PARENT, GROK_DELEGATE_SANDBOX,\n"
            "     GROK_DELEGATE_ECONOMY=1, GROK_DELEGATE_HTTP_TOKEN\n"
            "NOT an official xAI/Grok product. NOT the product admin-bridge.\n"
        )
        return 0
    transport = "stdio"
    host = os.environ.get("GROK_DELEGATE_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("GROK_DELEGATE_HTTP_PORT", "8765") or "8765")
    i = 0
    while i < len(args):
        if args[i] == "--transport" and i + 1 < len(args):
            transport = str(args[i + 1]).strip().lower()
            i += 2
            continue
        if args[i] == "--host" and i + 1 < len(args):
            host = str(args[i + 1]).strip()
            i += 2
            continue
        if args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
            continue
        i += 1
    try:
        if transport == "http":
            from .http_server import serve_http

            serve_http(host=host, port=port)
            return 0
        if transport != "stdio":
            sys.stderr.write(f"unknown transport: {transport}\n")
            return 2
        serve_stdio()
        return 0
    finally:
        shutdown_runtime()


if __name__ == "__main__":
    raise SystemExit(main())
