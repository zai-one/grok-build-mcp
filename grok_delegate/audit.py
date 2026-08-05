"""Redaction-safe audit emission for grok_delegate (stderr JSON lines).

Mirrors tools/mcp fail-closed audit philosophy: principal/tool/lane/base_ref/
cwd/turns/outcome only. No key material, no ~/.grok contents, no full goal
text, no full diff payload (B6).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, TextIO

# Allowlisted top-level keys for emit().
ALLOWED_FIELDS = frozenset(
    {
        "principal",
        "tool",
        "lane",
        "base_ref",
        "cwd",
        "turns_used",
        "turns_requested",
        "outcome",
        "status",
        "error",
        "plan_only",
        "goal_chars",
        "goal_sha256_8",
        "changed_file_count",
        "elapsed_seconds",
        "ts",
        "branch",
        "worktree_path",
        "transport",
        "correlation_id",
    }
)

# Patterns that must never appear in serialized audit JSON.
_SECRET_PATTERNS = (
    re.compile(r"(?i)xai[_-]?api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
    re.compile(r"(?i)password\s*[:=]\s*\S+"),
    re.compile(r"(?i)secret\s*[:=]\s*\S+"),
)

# Paths / blobs that must not be logged.
_FORBIDDEN_SUBSTRINGS = (
    ".grok/auth.json",
    "~/.grok/",
    "auth.json",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
)


class AuditError(Exception):
    """Raised when an audit event would leak forbidden material."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def goal_fingerprint(goal: str | None) -> dict[str, Any]:
    """Length + short hash only — never the raw goal text."""
    text = goal or ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return {"goal_chars": len(text), "goal_sha256_8": digest}


def sanitize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Project to allowlisted fields and strip forbidden content.

    Fail-closed: if any *raw* field value embeds ``~/.grok``, ``auth.json`` path
    material, or secret-like patterns, raise ``AuditError`` rather than logging it.
    """
    # Pre-check raw values (before redaction) so leaks cannot be silently emitted.
    for key, value in event.items():
        if key not in ALLOWED_FIELDS:
            continue
        if isinstance(value, str):
            _assert_value_safe(value, field=key)

    out: MutableMapping[str, Any] = {}
    for key, value in event.items():
        if key not in ALLOWED_FIELDS:
            continue
        # Drop oversized string fields (e.g. accidental summary dumps).
        if isinstance(value, str) and len(value) > 2000:
            value = value[:2000] + "…(truncated)"
        if key in {"cwd", "worktree_path", "base_ref", "lane", "branch", "principal", "tool", "transport", "correlation_id"}:
            if isinstance(value, str):
                value = _redact_pathish(value)
        out[key] = value

    # Never allow raw goal or diff keys even if someone smuggles them.
    for banned in ("goal", "prompt", "diff", "patch", "stdout", "stderr", "argv", "auth", "token", "key"):
        out.pop(banned, None)

    blob = json.dumps(out, ensure_ascii=False, sort_keys=True)
    for pat in _SECRET_PATTERNS:
        if pat.search(blob):
            raise AuditError("audit event contains secret-like material")

    if "ts" not in out:
        out["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dict(out)


def _assert_value_safe(value: str, *, field: str) -> None:
    lowered = value.lower().replace("\\", "/")
    if "/.grok/" in lowered or lowered.endswith("/.grok") or "~/.grok" in lowered:
        raise AuditError(f"audit field {field!r} must not reference ~/.grok paths")
    if "auth.json" in lowered:
        raise AuditError(f"audit field {field!r} must not include auth.json path")
    if "begin private key" in lowered or "begin rsa private key" in lowered:
        raise AuditError(f"audit field {field!r} must not include private key material")
    for pat in _SECRET_PATTERNS:
        if pat.search(value):
            raise AuditError(f"audit field {field!r} contains secret-like material")


def _redact_pathish(value: str) -> str:
    """Collapse home .grok segments if somehow present."""
    cleaned = value.replace("\\", "/")
    if "/.grok/" in cleaned or cleaned.endswith("/.grok"):
        return "[redacted-grok-path]"
    if "auth.json" in cleaned.lower():
        return "[redacted-auth-path]"
    return value


def emit(
    event: Mapping[str, Any],
    *,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    """Write one redacted JSON audit line to stderr (or given stream)."""
    safe = sanitize_event(event)
    target = stream if stream is not None else sys.stderr
    line = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    target.write(line + "\n")
    target.flush()
    return safe


def build_delegation_audit(
    *,
    principal: str,
    tool: str,
    lane: str | None,
    base_ref: str | None,
    cwd: str | None,
    turns_used: int | None,
    outcome: str,
    goal: str | None = None,
    plan_only: bool = False,
    error: str | None = None,
    changed_file_count: int | None = None,
    elapsed_seconds: float | None = None,
    branch: str | None = None,
    worktree_path: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Compose a standard delegation audit event (pre-sanitize)."""
    event: dict[str, Any] = {
        "principal": principal,
        "tool": tool,
        "lane": lane or "",
        "base_ref": base_ref or "",
        "cwd": cwd or "",
        "turns_used": turns_used,
        "outcome": outcome,
        "plan_only": bool(plan_only),
    }
    if goal is not None:
        event.update(goal_fingerprint(goal))
    if error:
        event["error"] = error
    if changed_file_count is not None:
        event["changed_file_count"] = changed_file_count
    if elapsed_seconds is not None:
        event["elapsed_seconds"] = elapsed_seconds
    if branch:
        event["branch"] = branch
    if worktree_path:
        event["worktree_path"] = worktree_path
    if status:
        event["status"] = status
    return event
