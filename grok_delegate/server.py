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
        GuardError,
        parse_allowed_roots_env,
        path_in_allowlist,
        paths_equal,
        structured_error,
        validate_grok_bin,
        validate_json_schema,
        validate_model,
        validate_reasoning_effort,
        validate_rules,
        validate_sandbox_profile,
        validate_session_id,
    )
    from .runner import delegate, is_path_inside  # type: ignore[no-redef]
    from . import jobs  # type: ignore[no-redef]
    from .status import (  # type: ignore[no-redef]
        build_status_report,
        run_doctor_json,
        run_inspect_json,
        run_models,
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
        GuardError,
        parse_allowed_roots_env,
        path_in_allowlist,
        paths_equal,
        structured_error,
        validate_grok_bin,
        validate_json_schema,
        validate_model,
        validate_reasoning_effort,
        validate_rules,
        validate_sandbox_profile,
        validate_session_id,
    )
    from runner import delegate, is_path_inside  # noqa: E402
    import jobs  # noqa: E402
    from status import (  # noqa: E402
        build_status_report,
        run_doctor_json,
        run_inspect_json,
        run_models,
    )

SERVER_NAME = "grok-delegate"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"

TOOL_DELEGATE = "grok_delegate"
TOOL_PLAN = "grok_delegate_plan"
TOOL_START = "grok_delegate_start"
TOOL_POLL = "grok_delegate_poll"
TOOL_STATUS = "grok_delegate_status"
TOOL_DOCTOR = "grok_delegate_doctor"
TOOL_MODELS = "grok_delegate_models"
TOOL_INSPECT = "grok_delegate_inspect"

STATUS_TOOLS = frozenset({TOOL_STATUS, TOOL_DOCTOR, TOOL_MODELS, TOOL_INSPECT})
# R6: TOOL_START shares the delegate validation path; TOOL_POLL is read-only.
DELEGATE_TOOLS = frozenset({TOOL_DELEGATE, TOOL_PLAN, TOOL_START})
ALL_TOOLS = DELEGATE_TOOLS | STATUS_TOOLS | {TOOL_POLL}

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
        "that job's state and result (branch, changed_files, commits, diffstat). "
        "Without job_id: newest job summaries. Read-only."
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
        return run_doctor_json(
            grok_bin=grok_bin,
            subprocess_runner=subprocess_runner,
            which=which,
        )

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
        return delegate(
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
    return [
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
    inn = stdin or sys.stdin
    out = stdout or sys.stdout
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        sys.stdout.write(
            "grok-delegate MCP (dev-only)\n"
            "Usage: python -m grok_delegate.server\n"
            "       python -m grok_delegate --self-test\n"
            "       python -m grok_delegate --smoke-delegate\n"
            "Env: GROK_DELEGATE_ALLOWED_ROOTS, GROK_DELEGATE_REPO_ROOT,\n"
            "     GROK_DELEGATE_BIN, GROK_DELEGATE_LANES_PARENT, GROK_DELEGATE_SANDBOX\n"
            "NOT the product admin-bridge (tools/mcp/).\n"
        )
        return 0
    serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
