"""Versioned task and receipt contracts for the MCP -> Grok transports.

The runtime intentionally validates without a third-party JSON Schema engine so
the local MCP server remains a stdlib-only install.  The equivalent published
schemas live under ``schemas/`` and contract tests keep the two surfaces aligned.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .guard import GuardError, path_in_allowlist
from .economy import apply_task_economy_defaults

TASK_SCHEMA_ID = "grok-task-packet.v1"
RECEIPT_SCHEMA_ID = "grok-work-receipt.v1"
EVENT_SCHEMA_ID = "grok-agent-event.v1"

ROLES = frozenset({"consult", "execute", "skeptic", "fix"})
TRANSPORTS = frozenset({"legacy", "stdio", "websocket", "auto"})
PERMISSION_PROFILES = frozenset({"read-only", "workspace"})
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

MAX_OBJECTIVE = 12_000
MAX_ITEM = 2_000
MAX_ITEMS = 64
MAX_CORRELATION = 128
MAX_PATH = 1_024
MAX_EVENTS = 64
MAX_EVENT_TEXT = 2_000

_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TASK_FIELDS = frozenset(
    {
        "schema_version",
        "objective",
        "role",
        "project_root",
        "base_ref",
        "model",
        "reasoning_effort",
        "permission_profile",
        "max_turns",
        "timeout_seconds",
        "inputs",
        "constraints",
        "acceptance_criteria",
        "expected_artifacts",
        "test_commands",
        "correlation_id",
    }
)


def validate_transport(value: Any) -> str:
    transport = str(value or "stdio").strip().lower()
    if transport not in TRANSPORTS:
        raise GuardError(
            "TRANSPORT_INVALID",
            f"transport must be one of {sorted(TRANSPORTS)}",
        )
    # Round 8 auto is intentionally not a cascading fallback.
    return "stdio" if transport == "auto" else transport


def validate_task_packet(
    value: Mapping[str, Any] | None,
    *,
    allowed_roots: Sequence[Path | str],
    forced_role: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GuardError("TASK_PACKET_INVALID", "task must be a JSON object")
    value = apply_task_economy_defaults(dict(value))
    unknown = sorted(set(value) - _TASK_FIELDS)
    if unknown:
        raise GuardError(
            "TASK_PACKET_UNKNOWN_FIELDS",
            f"unknown task fields: {', '.join(unknown)}",
        )
    supplied_schema = value.get("schema_version")
    if supplied_schema is not None and supplied_schema != TASK_SCHEMA_ID:
        raise GuardError("TASK_SCHEMA_VERSION_INVALID", f"schema_version must be {TASK_SCHEMA_ID}")

    objective = _bounded_string(value.get("objective"), "objective", MAX_OBJECTIVE)
    supplied_role = str(value.get("role") or "").strip().lower()
    if forced_role and supplied_role and supplied_role != forced_role:
        raise GuardError(
            "TASK_ROLE_MISMATCH",
            f"this tool requires role={forced_role}, got role={supplied_role}",
        )
    role = str(forced_role or supplied_role).strip().lower()
    if role not in ROLES:
        raise GuardError("TASK_ROLE_INVALID", f"role must be one of {sorted(ROLES)}")

    raw_root = _bounded_string(value.get("project_root"), "project_root", MAX_PATH)
    try:
        root = Path(raw_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GuardError("PROJECT_ROOT_INVALID", f"cannot resolve project_root: {exc}") from exc
    if not root.is_dir():
        raise GuardError("PROJECT_ROOT_INVALID", "project_root must be a directory")
    allow = [Path(p).expanduser().resolve() for p in allowed_roots]
    if not path_in_allowlist(root, allow):
        raise GuardError(
            "PROJECT_ROOT_UNTRUSTED",
            "project_root must resolve exactly to an allowlisted root",
        )

    expected_profile = "read-only" if role in {"consult", "skeptic"} else "workspace"
    profile = str(value.get("permission_profile") or expected_profile).strip().lower()
    if profile not in PERMISSION_PROFILES:
        raise GuardError(
            "PERMISSION_PROFILE_INVALID",
            f"permission_profile must be one of {sorted(PERMISSION_PROFILES)}",
        )
    if profile != expected_profile:
        raise GuardError(
            "PERMISSION_PROFILE_ROLE_MISMATCH",
            f"role {role} requires permission_profile={expected_profile}",
        )

    max_turns = _bounded_int(value.get("max_turns", 40), "max_turns", 1, 60)
    timeout = _bounded_int(value.get("timeout_seconds", 1800), "timeout_seconds", 1, 3600)
    model = _optional_string(value.get("model"), "model", 128) or "grok-4.5"
    effort = (_optional_string(value.get("reasoning_effort"), "reasoning_effort", 16) or "high").lower()
    if effort not in REASONING_EFFORTS:
        raise GuardError(
            "REASONING_EFFORT_INVALID",
            f"reasoning_effort must be one of {sorted(REASONING_EFFORTS)}",
        )
    base_ref = _optional_string(value.get("base_ref"), "base_ref", 256) or "master"
    correlation = _bounded_string(value.get("correlation_id"), "correlation_id", MAX_CORRELATION)
    if not _CORRELATION_RE.fullmatch(correlation):
        raise GuardError(
            "CORRELATION_ID_INVALID",
            "correlation_id must contain only letters, digits, dot, colon, underscore or dash",
        )

    artifacts = _bounded_string_list(value.get("expected_artifacts", []), "expected_artifacts")
    if role in {"execute", "fix"} and not artifacts:
        raise GuardError(
            "EXPECTED_ARTIFACTS_REQUIRED",
            "execute and fix tasks require at least one expected_artifacts entry",
        )
    normalized_artifacts: list[str] = []
    for item in artifacts:
        candidate = Path(item)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise GuardError(
                "EXPECTED_ARTIFACT_INVALID",
                "expected_artifacts entries must be relative and must not contain '..'",
            )
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise GuardError("EXPECTED_ARTIFACT_ESCAPE", "expected artifact escapes project_root") from exc
        normalized_artifacts.append(candidate.as_posix())

    test_commands = _bounded_string_list(value.get("test_commands", []), "test_commands")
    if role in {"execute", "fix"} and not test_commands:
        raise GuardError(
            "TEST_COMMANDS_REQUIRED",
            "execute and fix tasks require at least one bridge-verifiable test_commands entry",
        )

    return {
        "schema_version": TASK_SCHEMA_ID,
        "objective": objective,
        "role": role,
        "project_root": str(root),
        "base_ref": base_ref,
        "model": model,
        "reasoning_effort": effort,
        "permission_profile": profile,
        "max_turns": max_turns,
        "timeout_seconds": timeout,
        "inputs": _bounded_string_list(value.get("inputs", []), "inputs"),
        "constraints": _bounded_string_list(value.get("constraints", []), "constraints"),
        "acceptance_criteria": _bounded_string_list(
            value.get("acceptance_criteria", []), "acceptance_criteria"
        ),
        "expected_artifacts": normalized_artifacts,
        "test_commands": test_commands,
        "correlation_id": correlation,
    }


def objective_hash(objective: str) -> str:
    return hashlib.sha256(objective.encode("utf-8")).hexdigest()


def build_prompt(task: Mapping[str, Any]) -> str:
    lines = [
        str(task["objective"]),
        "",
        "Role: " + str(task["role"]),
        "Permission profile: " + str(task["permission_profile"]),
        "Work only inside the supplied cwd. Never push, merge, change credentials, or use --always-approve.",
    ]
    for label, key in (
        ("Inputs", "inputs"),
        ("Constraints", "constraints"),
        ("Acceptance criteria", "acceptance_criteria"),
        ("Expected artifacts", "expected_artifacts"),
        ("Test commands", "test_commands"),
    ):
        values = list(task.get(key) or [])
        if values:
            lines.append("")
            lines.append(label + ":")
            lines.extend(f"- {value}" for value in values)
    if task["role"] in {"execute", "fix"}:
        lines.extend(
            [
                "",
                "Make the requested file change now. Run the relevant bounded test before finishing.",
                "A summary without a real filesystem diff and acceptance evidence is not success.",
            ]
        )
    return "\n".join(lines)


def finalize_receipt(receipt: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(receipt)
    out["schema_version"] = RECEIPT_SCHEMA_ID
    out["objective_hash"] = objective_hash(str(task["objective"]))
    out.setdefault("changed_files", [])
    out.setdefault("full_changed_files", list(out["changed_files"]))
    out.setdefault("commits", [])
    out.setdefault("diffstat", "")
    out.setdefault("unified_diff", "")
    out.setdefault("tests", [])
    out.setdefault("artifacts", [])
    out.setdefault("findings", [])
    out.setdefault("summary", "")
    out.setdefault("blocked_reason", None)

    status = str(out.get("status") or "failed")
    if out.get("worker_alive_after_shutdown") is True:
        status = "failed"
        out["blocked_reason"] = "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"
        out["error_code"] = "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"
        out["error"] = "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"
    if task["role"] in {"execute", "fix"} and status == "completed":
        if not out["changed_files"]:
            status = "no_changes"
            out["blocked_reason"] = "EXECUTE_NO_CHANGES"
        else:
            missing = [p for p in task.get("expected_artifacts", []) if p not in out["artifacts"]]
            if missing:
                status = "blocked"
                out["blocked_reason"] = "EXPECTED_ARTIFACT_MISSING: " + ", ".join(missing)
            unchanged_artifacts = [
                p for p in task.get("expected_artifacts", [])
                if p not in {str(changed).replace("\\", "/").rstrip("/") for changed in out["changed_files"]}
            ]
            if unchanged_artifacts:
                status = "blocked"
                out["blocked_reason"] = "EXPECTED_ARTIFACT_NOT_CHANGED: " + ", ".join(unchanged_artifacts)
            expected_set = {
                str(path).replace("\\", "/").rstrip("/")
                for path in task.get("expected_artifacts", [])
            }
            unexpected_changes = [
                str(path).replace("\\", "/").rstrip("/")
                for path in out["full_changed_files"]
                if str(path).replace("\\", "/").rstrip("/") not in expected_set
            ]
            if unexpected_changes:
                status = "blocked"
                out["blocked_reason"] = "UNEXPECTED_CHANGED_FILES: " + ", ".join(unexpected_changes)
            expected_tests = list(task.get("test_commands", []))
            valid_tests = [
                test for test in out["tests"]
                if isinstance(test, Mapping)
                and str(test.get("command") or "") in expected_tests
                and test.get("passed") is True
                and test.get("returncode") == 0
                and test.get("source") == "bridge-verifier"
            ]
            if len({test["command"] for test in valid_tests}) != len(set(expected_tests)):
                status = "blocked"
                out["blocked_reason"] = "TEST_EVIDENCE_MISSING"
            if any(
                isinstance(test, Mapping)
                and str(test.get("command") or "") in expected_tests
                and not bool(test.get("passed"))
                for test in out["tests"]
            ):
                status = "failed"
                out["blocked_reason"] = "TEST_FAILED"
    out["status"] = status
    # jobs.start_job owns transport-independent lifecycle state and keys it from
    # this boolean.  A terminal receipt without it was incorrectly persisted as
    # state=error even when every evidence gate passed.
    out["ok"] = status == "completed"
    # The finalized mapping is also the live MCP response.  Sanitize the whole
    # object here (not only when it is later persisted) so paths, findings, and
    # mapping keys cannot leak credential-shaped material to the caller.
    return redact_value(out)


def bounded_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively bounded, JSON-safe event with secret-looking fields removed."""
    deny_keys = {
        "secret", "token", "authorization", "signature", "api_key", "apikey",
        "password", "passwd", "cookie", "setcookie", "accesstoken", "refreshtoken",
        "clientsecret", "privatekey", "credential", "credentials",
    }

    def clean(value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "<TRUNCATED_DEPTH>"
        if isinstance(value, Mapping):
            return {
                redact_text(str(k))[:MAX_EVENT_TEXT]: clean(v, depth + 1)
                for k, v in value.items()
                if re.sub(r"[^a-z0-9]", "", str(k).lower()) not in deny_keys
            }
        if isinstance(value, (list, tuple)):
            return [clean(v, depth + 1) for v in value[:64]]
        if isinstance(value, (set, frozenset)):
            cleaned = [clean(v, depth + 1) for v in list(value)[:64]]
            return sorted(cleaned, key=str)
        if isinstance(value, str):
            return redact_text(value)[:MAX_EVENT_TEXT]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return redact_text(str(value))[:MAX_EVENT_TEXT]

    return clean(dict(event))


_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)([^\s,}\]]+)"),
    re.compile(r"(?i)((?:api[_-]?key|server[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|cookie|client[_-]?secret)\s*[:=]\s*[\"']?)([^\s\"'&,}\]]+)"),
    re.compile(r"(?i)([\"'](?:password|passwd|accessToken|refreshToken|clientSecret|cookie)[\"']\s*:\s*[\"'])([^\"']+)"),
)

_BARE_CREDENTIAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:xai|sk)-[A-Za-z0-9._-]{12,}",
    re.IGNORECASE | re.ASCII,
)
_PEM_PRIVATE_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]{0,64}PRIVATE KEY-----[\s\S]*?"
    r"-----END [^-\r\n]{0,64}PRIVATE KEY-----",
    re.IGNORECASE,
)
_PEM_PRIVATE_MARKER_PATTERN = re.compile(
    r"-----(?:BEGIN|END) [^-\r\n]{0,64}(?:PRIVATE KEY|SECRET|ENCRYPTED)[^-\r\n]{0,64}-----",
    re.IGNORECASE,
)
_PEM_UNTERMINATED_PRIVATE_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]{0,64}PRIVATE KEY-----[\s\S]*\Z",
    re.IGNORECASE,
)


def redact_text(value: str) -> str:
    out = str(value)
    out = _PEM_PRIVATE_BLOCK_PATTERN.sub("<REDACTED_PEM_PRIVATE_KEY>", out)
    out = _PEM_UNTERMINATED_PRIVATE_BLOCK_PATTERN.sub("<REDACTED_PEM_PRIVATE_KEY>", out)
    out = _PEM_PRIVATE_MARKER_PATTERN.sub("<REDACTED_PEM_MARKER>", out)
    out = _BARE_CREDENTIAL_PATTERN.sub("<REDACTED>", out)
    for pattern in _SECRET_TEXT_PATTERNS:
        out = pattern.sub(r"\1<REDACTED>", out)
    return out


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "<TRUNCATED_DEPTH>"
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            raw_key = str(key)
            safe_key = redact_text(raw_key)[:MAX_EVENT_TEXT]
            normalized = re.sub(r"[^a-z0-9]", "", raw_key.lower())
            if normalized in {
                "secret", "token", "authorization", "signature", "apikey", "password",
                "passwd", "cookie", "setcookie", "accesstoken", "refreshtoken",
                "clientsecret", "privatekey", "credential", "credentials",
            }:
                out[safe_key] = "<REDACTED>"
            else:
                out[safe_key] = redact_value(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_value(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        cleaned = [redact_value(item, depth + 1) for item in value]
        return sorted(cleaned, key=str)
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def _bounded_string(value: Any, field: str, cap: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > cap or any(c in text for c in ("\x00",)):
        raise GuardError("TASK_PACKET_BOUNDS", f"{field} exceeds its bound or contains NUL")
    return text


def _optional_string(value: Any, field: str, cap: int) -> str | None:
    if value is None or value == "":
        return None
    return _bounded_string(value, field, cap)


def _bounded_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be an array")
    if len(value) > MAX_ITEMS:
        raise GuardError("TASK_PACKET_BOUNDS", f"{field} exceeds {MAX_ITEMS} entries")
    return [_bounded_string(item, f"{field}[]", MAX_ITEM) for item in value]


def _bounded_int(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be an integer") from exc
    if result < low or result > high:
        raise GuardError("TASK_PACKET_BOUNDS", f"{field} must be between {low} and {high}")
    return result


__all__ = [
    "EVENT_SCHEMA_ID",
    "MAX_EVENTS",
    "RECEIPT_SCHEMA_ID",
    "ROLES",
    "TASK_SCHEMA_ID",
    "TRANSPORTS",
    "bounded_event",
    "build_prompt",
    "finalize_receipt",
    "objective_hash",
    "redact_text",
    "redact_value",
    "validate_task_packet",
    "validate_transport",
]
