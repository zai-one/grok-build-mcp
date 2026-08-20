"""Pure policy/bounds/argv construction for grok_delegate (no I/O).

Unit-testable. Never emits --always-approve. Fail-closed on reserved lanes
and over-cap max_turns.

Headless launch uses documented ``-p/--single`` (or ``--prompt-file``) — never a
bare interactive positional prompt. Permission mode is never bypassPermissions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

# Single source of truth for the version this server reports. It lives here,
# not in server.py, because status.py reports it too and cannot import server
# (server imports status). Duplicating the literal is exactly how the two
# drifted: status kept answering 0.2.0 after the server moved to 0.3.0, so the
# one call an operator would make to check "did my restart take effect?"
# answered with the old number.
SERVER_VERSION = "0.23.0"

# Server-side hard cap for --max-turns (B5).
HARD_CAP_MAX_TURNS = 60

# Lane names that must never be used as delegation targets (B1).
RESERVED_LANE_NAMES = frozenset({"dev", "master", "main"})

# Slug after optional "grok/" prefix.
_LANE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ALWAYS_APPROVE_FLAG = "--always-approve"
BYPASS_PERMISSIONS_MODE = "bypassPermissions"

# Documented headless interfaces from live ``grok --help`` (R1).
HEADLESS_SINGLE_LONG = "--single"
HEADLESS_SINGLE_SHORT = "-p"
HEADLESS_PROMPT_FILE = "--prompt-file"
HEADLESS_PROMPT_JSON = "--prompt-json"
HEADLESS_FLAGS = frozenset(
    {
        HEADLESS_SINGLE_LONG,
        HEADLESS_SINGLE_SHORT,
        HEADLESS_PROMPT_FILE,
        HEADLESS_PROMPT_JSON,
    }
)

# Safe permission modes from live ``grok --help`` (R3). Never bypassPermissions.
PERMISSION_MODE_EXECUTE = "dontAsk"
PERMISSION_MODE_PLAN = "plan"
ALLOWED_PERMISSION_MODES = frozenset(
    {"default", "acceptEdits", "auto", "dontAsk", "plan"}
)

# Default binary name; path resolution is runner concern.
DEFAULT_GROK_BIN = "grok"

# Built-in --sandbox profiles from live docs (18-sandbox.md / GROK_SANDBOX).
# Do not invent names beyond this set for defaults.
KNOWN_SANDBOX_PROFILES = frozenset(
    {"off", "workspace", "devbox", "read-only", "strict"}
)
# Default for execute path when sandbox is enabled (discovered: workspace).
DEFAULT_EXECUTE_SANDBOX = "workspace"
# Default for plan-only path (discovered: read-only).
DEFAULT_PLAN_SANDBOX = "read-only"

# Built-in tool allowlists for --tools (comma-separated CLI).
_EXECUTE_TOOLS: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Glob",
    "Grep",
)
_PLAN_TOOLS: tuple[str, ...] = (
    "Read",
    "Glob",
    "Grep",
)

# Allowed reasoning-effort tokens (bounded; empty → omit flag).
ALLOWED_REASONING_EFFORTS = frozenset(
    {"low", "medium", "high", "xhigh", "none", "minimal", "max"}
)

# UUID for --session-id (CLI requires valid UUID).
_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# R6: path policy always handed to the executor.
#
# Absolute Windows-drive Write/Edit stay denied (containment), and the executor
# naturally reaches for absolute paths because --cwd is absolute. Each such attempt
# costs a denial round, and the model does not always recover from it — measured:
# a lane announced its write, was denied, and ended the turn with an empty worktree.
# Stating the policy up front removes the denial round entirely.
PATH_POLICY_RULE = (
    "Path policy: always use paths RELATIVE to the worktree root for Read, Write, "
    "Edit, Grep and Glob (e.g. src/services/x.ts). Absolute paths (C:/..., //...) "
    "are denied by the permission profile — if a tool call is denied, retry the same "
    "call with a relative path instead of stopping."
)

# Max length bounds for free-text CLI params.
MAX_RULES_CHARS = 8_000
MAX_JSON_SCHEMA_CHARS = 16_000
MAX_MODEL_CHARS = 128

# Deny rules applied to every execute profile (B2).
_DENY_PUSH = "Bash(git push*)"
_DENY_MERGE = "Bash(git merge*)"
# UNC absolute-path patterns (legacy / non-Windows-drive).
_DENY_CWD_ESCAPE_WRITE = "Write(//**)"
_DENY_CWD_ESCAPE_EDIT = "Edit(//**)"
_DENY_CWD_ESCAPE_READ_ABS = "Read(//**)"
# Windows drive absolute paths (R3) — best-effort globs for the permission engine.
_DENY_WIN_WRITE = "Write([A-Za-z]:/**)"
_DENY_WIN_EDIT = "Edit([A-Za-z]:/**)"
_DENY_WIN_READ = "Read([A-Za-z]:/**)"
_DENY_HOME_GROK = "Read(~/.grok/**)"
_DENY_HOME_GROK_WRITE = "Write(~/.grok/**)"
_DENY_SECRETS = "Read(**/*secret*)"
_DENY_AUTH = "Read(**/auth.json)"
# R6: narrowed from "Bash(*XAI*)" — the broad form also denied any allowed git
# command whose message merely mentioned xai.
_DENY_ENV_KEYS = "Bash(*XAI_API_KEY*)"
_DENY_DESTRUCTIVE = "Bash(rm -rf*)"
_DENY_FORCE_PUSH = "Bash(git push*--force*)"

# R6: sensitive-CONTENT read denies, applied to Read and Grep alike.
#
# These replace the former blanket Windows-absolute read deny
# ("Read([A-Za-z]:/**)"). That rule also denied legitimate reads INSIDE the lane
# worktree — the worktree is itself an absolute Windows path — so a delegation
# that had to read any file died after 1-2 turns without touching one, while a
# write-only delegation survived (the model retries writes with a relative path,
# but does not recover from a denied read). Containment for reads stays: --cwd is
# pinned to the worktree, --sandbox is applied, and the patterns below keep secret
# material unreadable regardless of path form.
_SENSITIVE_READ_PATTERNS: tuple[str, ...] = (
    "~/.grok/**",
    "**/auth.json",
    "**/*secret*",
    "**/.env",
    "**/.env.*",
    "**/.ssh/**",
    "**/id_rsa*",
    "**/*.pem",
    "**/*.key",
)


def _sensitive_read_denies() -> tuple[str, ...]:
    """Deny rules blocking secret material for both Read and Grep tools."""
    out: list[str] = []
    for pattern in _SENSITIVE_READ_PATTERNS:
        out.append(f"Read({pattern})")
        out.append(f"Grep({pattern})")
    return tuple(out)


# R6: dangerous NON-GIT shell surfaces, matched on the command itself.
#
# Replaces the substring forms "Bash(*prod*)", "Bash(*root*)" and
# "Bash(*live*device*)", which denied allowed git commands whose message or path
# merely contained those words — e.g. `git commit -m "prod readiness"` and
# `git add src/services/prod-readiness.ts` were both rejected, so a lane could
# not commit its own work. The execute allow list is git-only, so these are
# defense-in-depth against a broader allow list ever being introduced.
_DENY_SHELL_COMMANDS: tuple[str, ...] = (
    "Bash(ssh*)",
    "Bash(scp*)",
    "Bash(sftp*)",
    "Bash(curl*)",
    "Bash(wget*)",
    "Bash(adb*)",
    "Bash(docker*)",
    "Bash(kubectl*)",
    "Bash(psql*)",
    "Bash(sudo*)",
    "Bash(su -*)",
    "Bash(runas*)",
    "Bash(reg *)",
    "Bash(schtasks*)",
    "Bash(shutdown*)",
    "Bash(format*)",
    "Bash(del /*)",
    "Bash(rmdir /s*)",
)

_BASE_DENY: tuple[str, ...] = (
    _DENY_PUSH,
    _DENY_MERGE,
    _DENY_FORCE_PUSH,
    _DENY_CWD_ESCAPE_WRITE,
    _DENY_CWD_ESCAPE_EDIT,
    _DENY_WIN_WRITE,
    _DENY_WIN_EDIT,
    _DENY_HOME_GROK,
    _DENY_HOME_GROK_WRITE,
    _DENY_SECRETS,
    _DENY_AUTH,
    _DENY_ENV_KEYS,
    _DENY_DESTRUCTIVE,
    *_DENY_SHELL_COMMANDS,
    *_sensitive_read_denies(),
)

_BASE_DISALLOWED_TOOLS: tuple[str, ...] = (
    "git_push",
    "git_merge",
    "mcp",
)

# Plan-only: no write/edit/shell mutation.
_PLAN_DENY_WRITE = "Write(**)"
_PLAN_DENY_EDIT = "Edit(**)"
_PLAN_DENY_BASH = "Bash(*)"
_PLAN_DISALLOWED = (
    "Write",
    "Edit",
    "Bash",
    "Shell",
    "git_push",
    "git_merge",
    "mcp",
)

# R2: no interpreter wildcards (python*/pytest*/npm*). Those would nullify
# every shell deny via arbitrary code execution. Shell allow is git-only.
_EXECUTE_ALLOW: tuple[str, ...] = (
    "Read(**)",
    # R6: discovery tools were exposed via --tools but had no allow rule, so the
    # execute profile was inconsistent with the plan profile (which allows both).
    # Sensitive material stays unreadable through Grep via _sensitive_read_denies().
    "Grep(**)",
    "Glob(**)",
    "Write(**)",
    "Edit(**)",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(git log*)",
    "Bash(git add*)",
    "Bash(git commit*)",
)

# Interpreters that must never appear as execute allow rules (R2).
_FORBIDDEN_EXECUTE_ALLOW_PREFIXES: tuple[str, ...] = (
    "Bash(python",
    "Bash(pytest",
    "Bash(npm",
    "Bash(node",
    "Bash(npx",
    "Bash(sh",
    "Bash(bash",
    "Bash(cmd",
    "Bash(powershell",
    "Bash(pwsh",
    "Bash(*)",
)

_PLAN_ALLOW: tuple[str, ...] = (
    "Read(**)",
    "Glob(**)",
    "Grep(**)",
)


class GuardError(Exception):
    """Structured fail-closed error from pure guard logic."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"ok": "false", "error": self.code, "message": self.message}


def normalize_lane(name: str | None) -> str:
    """Normalize lane to ``grok/<slug>``; reject reserved / empty / invalid.

    Accepts ``slug``, ``grok/slug``, or ``GROK/slug`` (case-folded prefix only).
    """
    if name is None:
        raise GuardError("LANE_EMPTY", "lane is required")
    raw = str(name).strip()
    if not raw:
        raise GuardError("LANE_EMPTY", "lane is required")

    slug = raw
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) != 2 or parts[0].lower() != "grok" or not parts[1]:
            raise GuardError(
                "LANE_INVALID",
                f"lane must be 'grok/<slug>' or '<slug>', got {raw!r}",
            )
        slug = parts[1]

    lowered = slug.lower()
    if lowered in RESERVED_LANE_NAMES:
        raise GuardError(
            "LANE_RESERVED",
            f"lane {slug!r} is reserved; use a grok/* work lane",
        )
    # Also reject if full name without prefix is reserved (already covered)
    # or if someone passes "dev" as slug.

    if not _LANE_SLUG_RE.match(slug):
        raise GuardError(
            "LANE_INVALID",
            "lane slug must match ^[a-z0-9][a-z0-9-]*$ "
            f"(got {slug!r})",
        )

    return f"grok/{slug}"


def enforce_bounds(max_turns: int | None, *, hard_cap: int = HARD_CAP_MAX_TURNS) -> int:
    """Clamp/validate max_turns against server hard cap. Fail-closed if over cap.

    ``None`` or missing → hard_cap. ``<= 0`` → error. ``> hard_cap`` → error
    (not silent clamp of attacker-supplied over-cap values).
    """
    if hard_cap < 1:
        raise GuardError("HARD_CAP_INVALID", "hard_cap must be >= 1")

    if max_turns is None:
        return hard_cap

    try:
        n = int(max_turns)
    except (TypeError, ValueError) as exc:
        raise GuardError("MAX_TURNS_INVALID", "max_turns must be an integer") from exc

    if n < 1:
        raise GuardError("MAX_TURNS_INVALID", "max_turns must be >= 1")
    if n > hard_cap:
        raise GuardError(
            "MAX_TURNS_CAP",
            f"max_turns {n} exceeds hard cap {hard_cap}",
        )
    return n


def build_permission_profile(plan_only: bool = False) -> dict[str, Any]:
    """Build guarded allow/deny/disallowed_tools/tools profile (B2 + R4).

    Execute: read/write/edit inside cwd + git-only shell; deny push/merge,
    absolute-path write/edit (UNC + Windows drive globs), live/device/prod/root,
    secrets, ~/.grok. Interpreters are not allowlisted (R2). Also exposes a
    ``tools`` allowlist for CLI ``--tools`` (narrower than deny-only).

    Plan-only: read-only (deny all write/edit/shell mutation).

    Note (R3/R4): pattern deny is best-effort for the permission engine.
    Relative ``../`` via agent tools is closed only when OS ``--sandbox`` is
    actually enforced (not claimed on Windows by docs); server path
    normalization covers server-controlled paths only — see Service/Archive/EVIDENCE-ROUND4.md.
    """
    if plan_only:
        deny = list(_BASE_DENY) + [_PLAN_DENY_WRITE, _PLAN_DENY_EDIT, _PLAN_DENY_BASH]
        return {
            "allow": list(_PLAN_ALLOW),
            "deny": deny,
            "disallowed_tools": list(_PLAN_DISALLOWED),
            "tools": list(_PLAN_TOOLS),
            "permission_mode": PERMISSION_MODE_PLAN,
            "sandbox": DEFAULT_PLAN_SANDBOX,
        }

    return {
        "allow": list(_EXECUTE_ALLOW),
        # R6: UNC read escape stays denied; the Windows-drive read deny is gone —
        # it blocked in-worktree reads (see _SENSITIVE_READ_PATTERNS rationale).
        "deny": list(_BASE_DENY) + [_DENY_CWD_ESCAPE_READ_ABS],
        "disallowed_tools": list(_BASE_DISALLOWED_TOOLS),
        "tools": list(_EXECUTE_TOOLS),
        "permission_mode": PERMISSION_MODE_EXECUTE,
        "sandbox": DEFAULT_EXECUTE_SANDBOX,
    }


def permission_mode_for_profile(
    profile: Mapping[str, Sequence[str]] | Mapping[str, Any],
    *,
    plan_only: bool = False,
) -> str:
    """Resolve permission mode from profile or plan_only flag; never bypass."""
    raw = profile.get("permission_mode") if isinstance(profile, Mapping) else None
    if raw is None:
        mode = PERMISSION_MODE_PLAN if plan_only else PERMISSION_MODE_EXECUTE
    else:
        mode = str(raw)
    if mode == BYPASS_PERMISSIONS_MODE or mode not in ALLOWED_PERMISSION_MODES:
        raise GuardError(
            "PERMISSION_MODE_FORBIDDEN",
            f"permission mode {mode!r} is not allowed",
        )
    return mode


def validate_sandbox_profile(value: str | None) -> str | None:
    """Validate sandbox profile name; ``None``/empty → omit (caller default).

    ``off`` means explicitly disabled (no ``--sandbox`` flag). Unknown names
    fail closed — never invent profile strings.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw not in KNOWN_SANDBOX_PROFILES:
        raise GuardError(
            "SANDBOX_INVALID",
            f"sandbox profile must be one of {sorted(KNOWN_SANDBOX_PROFILES)}, got {raw!r}",
        )
    return raw


def resolve_sandbox_profile(
    profile: Mapping[str, Any],
    *,
    plan_only: bool = False,
    override: str | None = None,
    enabled: bool = True,
) -> str | None:
    """Pick sandbox profile for argv.

    Returns ``None`` when sandbox should not be passed (disabled / off).
    """
    if not enabled:
        return None
    if override is not None:
        chosen = validate_sandbox_profile(override)
    else:
        raw = profile.get("sandbox") if isinstance(profile, Mapping) else None
        if raw is None:
            chosen = DEFAULT_PLAN_SANDBOX if plan_only else DEFAULT_EXECUTE_SANDBOX
        else:
            chosen = validate_sandbox_profile(str(raw))
    if chosen is None or chosen == "off":
        return None
    return chosen


def validate_model(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) > MAX_MODEL_CHARS:
        raise GuardError("MODEL_INVALID", f"model id exceeds {MAX_MODEL_CHARS} chars")
    if any(c in raw for c in ("\n", "\r", "\x00")):
        raise GuardError("MODEL_INVALID", "model id contains control characters")
    return raw


def validate_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw not in ALLOWED_REASONING_EFFORTS:
        raise GuardError(
            "REASONING_EFFORT_INVALID",
            f"reasoning_effort must be one of {sorted(ALLOWED_REASONING_EFFORTS)}, got {raw!r}",
        )
    return raw


def validate_rules(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value)
    if not raw.strip():
        return None
    if len(raw) > MAX_RULES_CHARS:
        raise GuardError(
            "RULES_TOO_LONG",
            f"rules exceeds {MAX_RULES_CHARS} characters",
        )
    return raw


def validate_json_schema(value: str | Mapping[str, Any] | None) -> str | None:
    """Accept JSON object or string; return compact JSON string for CLI."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        import json

        raw = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    else:
        raw = str(value).strip()
        if not raw:
            return None
        import json

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GuardError("JSON_SCHEMA_INVALID", f"json_schema is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise GuardError("JSON_SCHEMA_INVALID", "json_schema must be a JSON object")
        raw = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    if len(raw) > MAX_JSON_SCHEMA_CHARS:
        raise GuardError(
            "JSON_SCHEMA_TOO_LONG",
            f"json_schema exceeds {MAX_JSON_SCHEMA_CHARS} characters",
        )
    return raw


def validate_session_id(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if not _SESSION_ID_RE.match(raw):
        raise GuardError(
            "SESSION_ID_INVALID",
            "session_id must be a UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)",
        )
    return raw


def build_grok_argv(
    goal: str,
    worktree: str,
    profile: Mapping[str, Any],
    max_turns: int,
    *,
    model: str | None = None,
    plan_only: bool = False,
    grok_bin: str = DEFAULT_GROK_BIN,
    hard_cap: int = HARD_CAP_MAX_TURNS,
    sandbox: str | None = None,
    sandbox_enabled: bool = True,
    reasoning_effort: str | None = None,
    rules: str | None = None,
    json_schema: str | Mapping[str, Any] | None = None,
    no_subagents: bool = False,
    disable_web_search: bool = False,
    resume: str | bool | None = None,
    continue_session: bool = False,
    fork_session: bool = False,
    session_id: str | None = None,
    use_tools_allowlist: bool = True,
) -> list[str]:
    """Assemble argv for local grok **headless** executor (R1 + R4).

    Uses documented ``--single`` (alias ``-p``) so the process is non-TUI.
    Never includes ``--always-approve``. Always pins ``--cwd`` to worktree,
    sets ``--permission-mode`` (never bypassPermissions), uses JSON output,
    enforces max_turns hard cap, applies profile --allow/--deny/
    --disallowed-tools and optional --tools allowlist, and --sandbox when a
    known profile is selected. ``--no-plan`` only when not plan_only.
    Does **not** emit CLI ``-w/--worktree`` (own prepare_worktree remains).
    """
    if not goal or not str(goal).strip():
        raise GuardError("GOAL_EMPTY", "goal is required")
    if not worktree or not str(worktree).strip():
        raise GuardError("WORKTREE_EMPTY", "worktree path is required")

    turns = enforce_bounds(max_turns, hard_cap=hard_cap)
    allow = list(profile.get("allow") or ())
    deny = list(profile.get("deny") or ())
    disallowed = list(profile.get("disallowed_tools") or ())
    tools = list(profile.get("tools") or ())
    mode = permission_mode_for_profile(profile, plan_only=plan_only)
    model_v = validate_model(model)
    effort = validate_reasoning_effort(reasoning_effort)
    rules_v = validate_rules(rules)
    schema_v = validate_json_schema(json_schema)
    session_v = validate_session_id(session_id)
    sandbox_v = resolve_sandbox_profile(
        profile,
        plan_only=plan_only,
        override=sandbox,
        enabled=sandbox_enabled,
    )

    _assert_execute_allow_safe(allow)

    argv: list[str] = [
        grok_bin,
        "--cwd",
        str(worktree),
        "--output-format",
        "json",
        "--max-turns",
        str(turns),
        "--permission-mode",
        mode,
    ]

    if sandbox_v:
        argv.extend(["--sandbox", sandbox_v])

    if model_v:
        argv.extend(["--model", model_v])
    if effort:
        argv.extend(["--reasoning-effort", effort])
    # R6: the path policy is unconditional — callers may omit rules entirely.
    combined_rules = f"{rules_v}\n{PATH_POLICY_RULE}" if rules_v else PATH_POLICY_RULE
    argv.extend(["--rules", combined_rules])
    if schema_v:
        argv.extend(["--json-schema", schema_v])
    if no_subagents:
        argv.append("--no-subagents")
    if disable_web_search:
        argv.append("--disable-web-search")

    # Session resume/continue/fork — mutually constrained.
    # bool False / None → omit --resume (JSON false must not become the string "False").
    resume_active = False
    if continue_session and resume not in (None, False):
        raise GuardError(
            "SESSION_FLAGS_CONFLICT",
            "continue_session and resume cannot both be set",
        )
    if continue_session:
        argv.append("--continue")
        resume_active = True
    elif resume is True:
        argv.append("--resume")
        resume_active = True
    elif isinstance(resume, str) and resume.strip():
        rid = resume.strip()
        if not _SESSION_ID_RE.match(rid):
            raise GuardError(
                "SESSION_ID_INVALID",
                "resume session id must be a UUID when provided",
            )
        argv.extend(["--resume", rid])
        resume_active = True
    elif resume not in (None, False) and not isinstance(resume, (str, bool)):
        raise GuardError(
            "SESSION_ID_INVALID",
            "resume must be true, a UUID string, or omitted",
        )
    if fork_session:
        if not resume_active:
            raise GuardError(
                "FORK_REQUIRES_RESUME",
                "fork_session requires resume or continue_session",
            )
        argv.append("--fork-session")
    if session_v:
        argv.extend(["--session-id", session_v])

    for rule in allow:
        argv.extend(["--allow", str(rule)])
    for rule in deny:
        argv.extend(["--deny", str(rule)])
    if disallowed:
        # Comma-separated tool names per grok CLI convention.
        argv.extend(["--disallowed-tools", ",".join(str(t) for t in disallowed)])
    if use_tools_allowlist and tools:
        argv.extend(["--tools", ",".join(str(t) for t in tools)])

    if not plan_only:
        argv.append("--no-plan")

    # R1: headless single-prompt interface — not bare interactive positional.
    argv.extend([HEADLESS_SINGLE_LONG, str(goal).strip()])

    _assert_no_always_approve(argv)
    assert_argv_safe(argv)
    return argv


def _assert_execute_allow_safe(allow: Sequence[str]) -> None:
    """Reject broad interpreter / shell allows that nullify deny rules (R2)."""
    for rule in allow:
        s = str(rule)
        for prefix in _FORBIDDEN_EXECUTE_ALLOW_PREFIXES:
            if s.startswith(prefix) or s == prefix.rstrip("(") + "(*)":
                raise GuardError(
                    "ALLOW_INTERPRETER_FORBIDDEN",
                    f"execute allow must not include interpreter/shell rule {s!r}",
                )


def _assert_no_always_approve(argv: Sequence[str]) -> None:
    for part in argv:
        if part == ALWAYS_APPROVE_FLAG or str(part).startswith(f"{ALWAYS_APPROVE_FLAG}="):
            raise GuardError(
                "ALWAYS_APPROVE_FORBIDDEN",
                "argv must never include --always-approve",
            )


def argv_has_headless_interface(argv: Sequence[str]) -> bool:
    """True if argv uses a documented non-interactive prompt interface (R1)."""
    parts = [str(a) for a in argv]
    for i, part in enumerate(parts):
        if part in HEADLESS_FLAGS:
            # Flag must carry a value (next argv token) except we only require presence.
            if part in {HEADLESS_SINGLE_LONG, HEADLESS_SINGLE_SHORT, HEADLESS_PROMPT_FILE, HEADLESS_PROMPT_JSON}:
                if i + 1 >= len(parts):
                    return False
                # Value must not look like another flag.
                if parts[i + 1].startswith("-") and parts[i + 1] not in {"-"}:
                    # allow negative-looking content? treat leading -- as missing value
                    if parts[i + 1].startswith("--"):
                        return False
            return True
        if part.startswith(f"{HEADLESS_SINGLE_LONG}="):
            return True
        if part.startswith(f"{HEADLESS_PROMPT_FILE}="):
            return True
        if part.startswith(f"{HEADLESS_PROMPT_JSON}="):
            return True
    return False


def profile_denies_push(profile: Mapping[str, Sequence[str]]) -> bool:
    """True if profile deny/disallowed structurally covers git push (R4)."""
    deny = [str(d) for d in (profile.get("deny") or ())]
    tools = [str(t).lower() for t in (profile.get("disallowed_tools") or ())]
    if "git_push" in tools:
        return True
    for rule in deny:
        # Exact shipped rule or git-push bash deny — not a bare "push" substring hunt.
        if rule == _DENY_PUSH or rule == _DENY_FORCE_PUSH:
            return True
        if rule.startswith("Bash(git push"):
            return True
    return False


def profile_denies_merge(profile: Mapping[str, Sequence[str]]) -> bool:
    """True if profile deny/disallowed structurally covers git merge (R4)."""
    deny = [str(d) for d in (profile.get("deny") or ())]
    tools = [str(t).lower() for t in (profile.get("disallowed_tools") or ())]
    if "git_merge" in tools:
        return True
    for rule in deny:
        if rule == _DENY_MERGE or rule.startswith("Bash(git merge"):
            return True
    return False


def profile_denies_cwd_escape(profile: Mapping[str, Sequence[str]]) -> bool:
    """True if profile includes absolute-path deny rules (UNC and/or Windows).

    This is a structural check on shipped deny rules — not a claim of OS-level
    confinement. Relative ``../`` escape remains a documented non-guarantee.
    """
    deny = [str(d) for d in (profile.get("deny") or ())]
    has_unc_write = _DENY_CWD_ESCAPE_WRITE in deny or any(
        d.startswith("Write(//") for d in deny
    )
    has_unc_edit = _DENY_CWD_ESCAPE_EDIT in deny or any(
        d.startswith("Edit(//") for d in deny
    )
    has_win_write = _DENY_WIN_WRITE in deny or any(
        "Write([A-Za-z]:" in d for d in deny
    )
    has_win_edit = _DENY_WIN_EDIT in deny or any(
        "Edit([A-Za-z]:" in d for d in deny
    )
    has_home = _DENY_HOME_GROK in deny or any("~/.grok" in d for d in deny)
    # Require Windows drive denies (R3) plus home protect; UNC alone is insufficient on Windows.
    return (has_win_write and has_win_edit) and has_home and (has_unc_write or has_unc_edit)


def profile_allows_interpreters(profile: Mapping[str, Sequence[str]]) -> bool:
    """True if execute allow list includes interpreter wildcards (R2 fail signal)."""
    for rule in profile.get("allow") or ():
        s = str(rule)
        for prefix in _FORBIDDEN_EXECUTE_ALLOW_PREFIXES:
            if s.startswith(prefix):
                return True
    return False


def assert_argv_safe(argv: Sequence[str]) -> None:
    """Public re-check used by runner before spawn (defense in depth)."""
    _assert_no_always_approve(argv)
    if "--cwd" not in argv:
        raise GuardError("ARGV_MISSING_CWD", "argv must pin --cwd to worktree")
    if "--output-format" not in argv or "json" not in argv:
        raise GuardError("ARGV_MISSING_JSON", "argv must use --output-format json")
    if not argv_has_headless_interface(argv):
        raise GuardError(
            "ARGV_NOT_HEADLESS",
            "argv must use -p/--single, --prompt-file, or --prompt-json (not bare interactive prompt)",
        )
    # Permission mode required and never bypass.
    if "--permission-mode" not in argv:
        raise GuardError(
            "ARGV_MISSING_PERMISSION_MODE",
            "argv must set --permission-mode",
        )
    try:
        idx = list(argv).index("--permission-mode")
        mode = str(argv[idx + 1]) if idx + 1 < len(argv) else ""
    except (ValueError, IndexError):
        mode = ""
    if mode == BYPASS_PERMISSIONS_MODE or mode not in ALLOWED_PERMISSION_MODES:
        raise GuardError(
            "PERMISSION_MODE_FORBIDDEN",
            f"permission mode {mode!r} is not allowed",
        )


def structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured fail-closed error dict for MCP/tool responses."""
    out: MutableMapping[str, Any] = {
        "ok": False,
        "error": code,
        "message": message,
    }
    out.update(extra)
    return dict(out)


def validate_grok_bin(value: str | None, *, from_client: bool = False) -> str:
    """Resolve/validate grok binary (R5).

    Client-supplied ``grok_bin`` is always rejected. Env/config values may be
    the bare name ``grok`` / ``grok.exe`` or an absolute path whose basename is
    ``grok`` / ``grok.exe`` (no arbitrary interpreters).
    """
    if from_client:
        raise GuardError(
            "GROK_BIN_CLIENT_FORBIDDEN",
            "grok_bin is not accepted from client arguments; set GROK_DELEGATE_BIN",
        )
    raw = (value or DEFAULT_GROK_BIN).strip()
    if not raw:
        raise GuardError("GROK_BIN_INVALID", "grok binary is empty")

    # Bare command name only.
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if raw in {DEFAULT_GROK_BIN, "grok.exe"} or base in {"grok", "grok.exe"}:
        if "/" not in raw.replace("\\", "/") and "\\" not in raw:
            # bare name
            if raw.lower() not in {"grok", "grok.exe"}:
                raise GuardError(
                    "GROK_BIN_INVALID",
                    f"grok binary name must be grok or grok.exe, got {raw!r}",
                )
            return raw
        # path form: basename must be grok/grok.exe; reject shells
        if base not in {"grok", "grok.exe"}:
            raise GuardError(
                "GROK_BIN_INVALID",
                f"grok binary path basename must be grok or grok.exe, got {base!r}",
            )
        # Reject obvious non-grok hijacks in path segments
        lowered = raw.lower().replace("\\", "/")
        for banned in ("cmd.exe", "powershell", "pwsh", "python", "bash", "sh.exe"):
            if banned in lowered and base not in {"grok", "grok.exe"}:
                raise GuardError("GROK_BIN_INVALID", "grok binary path looks unsafe")
        return raw

    raise GuardError(
        "GROK_BIN_INVALID",
        f"grok binary must be grok/grok.exe name or path, got {raw!r}",
    )


# The MCP host knows which directory the user actually opened; Claude Code
# exports it to the spawned server process. Other hosts may not set it, so it is
# a hint, never a requirement.
HOST_PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"
TRUST_HOST_ROOTS_ENV = "GROK_DELEGATE_TRUST_HOST_ROOTS"
_TRUE_FLAGS = frozenset({"1", "true", "yes", "on"})


def trust_host_roots_enabled(env: Mapping[str, str]) -> bool:
    """Whether host-reported directories may join the allowlist. Off by default.

    Opt-in on purpose. Granting a root because the host named it widens the
    boundary this module exists to hold — the operator's explicit list stops
    being the whole answer. That is a fair trade when the host is the operator's
    own editor, but it is their call to make, not ours to assume for them.
    """
    return str(env.get(TRUST_HOST_ROOTS_ENV, "")).strip().lower() in _TRUE_FLAGS


def host_provided_roots(env: Mapping[str, str]) -> list[str]:
    """Directories the host reports, empty unless the operator opted in.

    Returns raw strings; resolution and de-duplication belong to the caller that
    owns the allowlist, so this stays pure and testable.
    """
    if not trust_host_roots_enabled(env):
        return []
    raw = str(env.get(HOST_PROJECT_DIR_ENV, "") or "").strip().strip('"').strip("'")
    return [raw] if raw else []


MODEL_ENV = "GROK_DELEGATE_MODEL"
REASONING_EFFORT_ENV = "GROK_DELEGATE_REASONING_EFFORT"


def configured_model(env: Mapping[str, str]) -> str | None:
    """Operator's model choice, or None to let the CLI pick its own default.

    None is the honest answer to "which model", not a missing value. Naming a
    default here would pin the bridge to whatever was current when this line was
    written -- the same mistake as pinning ``agentVersion``, and it ages the same
    way: the CLI ships a better default and the bridge quietly holds callers on
    the old one.
    """
    return validate_model(str(env.get(MODEL_ENV, "") or "").strip() or None)


def configured_reasoning_effort(env: Mapping[str, str]) -> str | None:
    """Operator's reasoning-effort floor for bridge-chosen budgets, or None.

    Only consulted where the bridge would otherwise invent a number: economy
    defaults and navigator cards. An effort the caller passed explicitly always
    wins over this.
    """
    return validate_reasoning_effort(str(env.get(REASONING_EFFORT_ENV, "") or "").strip() or None)


MAX_TURNS_ENV = "GROK_DELEGATE_MAX_TURNS"


def configured_max_turns(env: Mapping[str, str]) -> int | None:
    """Operator's turn budget, or None when they expressed no preference.

    Out-of-range and non-numeric values read as "no preference" rather than as
    an error: a typo here would otherwise fail every job until someone noticed,
    and the caller already has a defensible default to fall back on.
    """
    raw = str(env.get(MAX_TURNS_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= HARD_CAP_MAX_TURNS else None


def parse_allowed_roots_env(raw: str | None) -> list[str]:
    """Split allowlist env string (``;`` or newline separated) into path strings."""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    # Support JSON array for operators who prefer it.
    if text.startswith("["):
        import json

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GuardError(
                "ALLOWED_ROOTS_INVALID",
                f"GROK_DELEGATE_ALLOWED_ROOTS JSON is invalid: {exc}",
            ) from exc
        if not isinstance(data, list):
            raise GuardError(
                "ALLOWED_ROOTS_INVALID",
                "GROK_DELEGATE_ALLOWED_ROOTS JSON must be an array of paths",
            )
        return [str(x).strip() for x in data if str(x).strip()]
    parts: list[str] = []
    for chunk in text.replace("\n", ";").split(";"):
        item = chunk.strip().strip('"').strip("'")
        if item:
            parts.append(item)
    return parts


def paths_equal(a: Path | str, b: Path | str) -> bool:
    """Case-aware path equality after resolve (Windows-safe)."""
    try:
        pa = Path(a).resolve()
        pb = Path(b).resolve()
    except OSError:
        return False
    # On Windows paths are case-insensitive.
    import os

    if os.name == "nt":
        return str(pa).lower() == str(pb).lower()
    return pa == pb


def path_in_allowlist(candidate: Path | str, allowlist: Sequence[Path | str]) -> bool:
    """True if candidate resolve() equals one allowlist entry."""
    for entry in allowlist:
        if paths_equal(candidate, entry):
            return True
    return False


def confine_path_to_root(
    path: str | Path,
    root: str | Path,
    *,
    field: str = "path",
) -> Path:
    """Resolve ``path`` and require it stays inside ``root`` (server-side).

    Rejects ``..`` escapes and symlink walks that leave root after resolve().
    """
    try:
        root_r = Path(root).resolve()
    except OSError as exc:
        raise GuardError("PATH_ROOT_INVALID", f"invalid root for {field}: {exc}") from exc

    p = Path(path)
    try:
        if not p.is_absolute():
            candidate = (root_r / p).resolve()
        else:
            candidate = p.resolve()
    except OSError as exc:
        raise GuardError("PATH_INVALID", f"invalid {field}: {exc}") from exc

    try:
        candidate.relative_to(root_r)
    except ValueError as exc:
        raise GuardError(
            "PATH_ESCAPE",
            f"{field} resolves outside worktree/root (escape rejected)",
        ) from exc
    return candidate
