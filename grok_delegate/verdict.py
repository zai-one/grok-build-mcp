"""Structured lane verdict parse + git reconciliation (R7-C).

Why: free-form executor prose cannot tell a driver whether a lane finished,
gave up, or lied about commits. A fixed JSON shape plus a git cross-check
makes "done vs empty vs blocked" machine-readable. Trust git over prose —
a claim of files_written/committed that collect_diff cannot confirm is
VERDICT_UNSUPPORTED. Pure stdlib; Claude wires this into runner later.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, MutableMapping, Sequence

# Bound raw input before json.loads so a multi-megabyte stdout cannot blow
# memory. Truncation may yield VERDICT_MISSING, which is the safe outcome.
MAX_VERDICT_INPUT_CHARS = 64_000

# Bound list fields after a successful parse (paths / findings).
MAX_VERDICT_LIST_ITEMS = 256

# Bound individual string fields after parse (summary, reasons, paths).
MAX_VERDICT_STRING_CHARS = 4_000

# Required keys of the lane-verdict payload (must all be present).
REQUIRED_VERDICT_FIELDS: tuple[str, ...] = (
    "files_written",
    "committed",
    "tests_added",
    "gates_run",
    "self_skeptic_findings",
    "blocked_reason",
    "summary",
)

# JSON-schema shaped object passed to the CLI ``--json-schema`` flag (Claude
# wires the default into delegate; callers may opt out with None).
LANE_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": list(REQUIRED_VERDICT_FIELDS),
    "properties": {
        "files_written": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Relative paths the lane claims to have written.",
        },
        "committed": {
            "type": "boolean",
            "description": "Whether the lane claims to have created a git commit.",
        },
        "tests_added": {
            "type": "integer",
            "minimum": 0,
            "description": "Number of new tests the lane claims to have added.",
        },
        "gates_run": {
            "type": "boolean",
            "description": "Whether the lane claims local gates were executed.",
        },
        "self_skeptic_findings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Adversarial self-review notes from the lane.",
        },
        "blocked_reason": {
            "type": ["string", "null"],
            "description": "Stable block token when the lane could not finish; null otherwise.",
        },
        "summary": {
            "type": "string",
            "description": "Short human-readable outcome summary.",
        },
    },
}


def default_lane_json_schema(*, opt_out: bool = False) -> dict[str, Any] | None:
    """Return the default schema for delegate, or None when the caller opts out.

    Why: runner wiring (Claude) needs a single place to decide "schema on by
    default, allow opt-out" without inventing a second schema copy.
    """
    if opt_out:
        return None
    return LANE_VERDICT_SCHEMA


def parse_lane_verdict(stdout_or_json: Any) -> dict[str, Any]:
    """Parse a lane verdict from stdout text or a JSON value. Never raises.

    Why: the executor may emit free-form text, truncated JSON, or nothing.
    Drivers need a stable ``ok``/``reason`` shape rather than exception handling.

    Outcomes:
    - valid object with all required fields → ``ok: true`` + normalized ``verdict``
    - invalid / absent / non-object JSON → ``ok: false``, ``reason: VERDICT_MISSING``
    - object missing a required field (or wrong type) → ``ok: false``,
      ``reason: VERDICT_INVALID`` naming the field
    - extra fields are tolerated and preserved (bounded)
    - non-ASCII is preserved (UTF-8 decode with replacement for bytes)
    """
    try:
        return _parse_lane_verdict_inner(stdout_or_json)
    except Exception as exc:  # pragma: no cover - belt-and-braces; never raise
        return _missing(f"verdict parse failed unexpectedly: {type(exc).__name__}")


def reconcile_verdict(
    verdict: Mapping[str, Any] | None,
    diff: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Cross-check a verdict claim against ``collect_diff`` git reality.

    Why: a lane can claim success it did not achieve. The driver must trust
    git (changed_files / commits), not prose. Claims that git cannot confirm
    become ``VERDICT_UNSUPPORTED``. A set ``blocked_reason`` surfaces as
    ``blocked`` even when files did change.
    """
    try:
        return _reconcile_verdict_inner(verdict, diff)
    except Exception as exc:  # pragma: no cover
        return {
            "ok": False,
            "status": "VERDICT_MISSING",
            "reason": "VERDICT_MISSING",
            "message": f"reconcile failed unexpectedly: {type(exc).__name__}",
            "verdict": None,
            "changed_files": [],
            "commits": [],
        }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_lane_verdict_inner(stdout_or_json: Any) -> dict[str, Any]:
    if stdout_or_json is None:
        return _missing("verdict is absent")

    # Already a mapping (e.g. pre-parsed CLI JSON) — validate, do not re-dump.
    if isinstance(stdout_or_json, Mapping):
        return _validate_verdict_object(dict(stdout_or_json))

    # Bytes → UTF-8 with replacement so a cp1252 path never crashes.
    if isinstance(stdout_or_json, (bytes, bytearray)):
        text = bytes(stdout_or_json).decode("utf-8", errors="replace")
    elif isinstance(stdout_or_json, str):
        text = stdout_or_json
    else:
        # Non-string scalars (int/list/bool) are not a verdict object.
        # Lists/scalars that *are* JSON values get handled below via dumps path.
        try:
            text = json.dumps(stdout_or_json, ensure_ascii=False)
        except (TypeError, ValueError):
            return _missing("verdict input is not JSON-serializable")

    if not text or not str(text).strip():
        return _missing("verdict is empty")

    text = str(text)
    if len(text) > MAX_VERDICT_INPUT_CHARS:
        # Bound first; truncated JSON usually fails closed as VERDICT_MISSING.
        text = text[:MAX_VERDICT_INPUT_CHARS]

    payload = _loads_json_object(text)
    if payload is None:
        return _missing("verdict JSON missing or not an object")

    return _validate_verdict_object(payload)


def _loads_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extract a JSON object from pure JSON or mixed stdout."""
    stripped = text.strip()
    if not stripped:
        return None

    # Pure JSON first.
    try:
        parsed: Any = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None

    if parsed is None:
        # Mixed stdout: take the first balanced {...} span.
        extracted = _extract_first_json_object(stripped)
        if extracted is None:
            return None
        try:
            parsed = json.loads(extracted)
        except json.JSONDecodeError:
            return None

    if not isinstance(parsed, dict):
        # Arrays, strings, numbers, null → not a verdict object.
        return None
    return parsed


def _extract_first_json_object(text: str) -> str | None:
    """Return the first top-level ``{...}`` span, respecting JSON strings."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def _validate_verdict_object(raw: Mapping[str, Any]) -> dict[str, Any]:
    for field in REQUIRED_VERDICT_FIELDS:
        if field not in raw:
            return _invalid(field, f"missing required field {field!r}")

    # Type checks — name the field on mismatch so drivers can diagnose.
    files_written = raw.get("files_written")
    if not _is_str_list(files_written):
        return _invalid("files_written", "files_written must be a list of strings")

    committed = raw.get("committed")
    if not isinstance(committed, bool):
        return _invalid("committed", "committed must be a boolean")

    tests_added = raw.get("tests_added")
    # bool is a subclass of int — reject True/False as a test count.
    if isinstance(tests_added, bool) or not isinstance(tests_added, int):
        return _invalid("tests_added", "tests_added must be an integer")
    if tests_added < 0:
        return _invalid("tests_added", "tests_added must be >= 0")

    gates_run = raw.get("gates_run")
    if not isinstance(gates_run, bool):
        return _invalid("gates_run", "gates_run must be a boolean")

    findings = raw.get("self_skeptic_findings")
    if not _is_str_list(findings):
        return _invalid(
            "self_skeptic_findings",
            "self_skeptic_findings must be a list of strings",
        )

    blocked_reason = raw.get("blocked_reason")
    if blocked_reason is not None and not isinstance(blocked_reason, str):
        return _invalid("blocked_reason", "blocked_reason must be a string or null")

    summary = raw.get("summary")
    if not isinstance(summary, str):
        return _invalid("summary", "summary must be a string")

    # Normalize + bound; preserve extra keys (also bounded if strings/lists).
    verdict: dict[str, Any] = {
        "files_written": _bound_str_list(files_written),
        "committed": bool(committed),
        "tests_added": int(tests_added),
        "gates_run": bool(gates_run),
        "self_skeptic_findings": _bound_str_list(findings),
        "blocked_reason": (
            None
            if blocked_reason is None
            else _bound_str(blocked_reason)
        ),
        "summary": _bound_str(summary),
    }
    for key, value in raw.items():
        if key in verdict:
            continue
        verdict[key] = _bound_extra(value)

    return {
        "ok": True,
        "reason": None,
        "message": None,
        "field": None,
        "verdict": verdict,
    }


def _reconcile_verdict_inner(
    verdict: Mapping[str, Any] | None,
    diff: Mapping[str, Any] | None,
) -> dict[str, Any]:
    # Accept either the inner payload or a parse_lane_verdict result.
    payload, parse_error = _unwrap_verdict(verdict)
    changed_files, commits = _diff_lists(diff)

    base: dict[str, Any] = {
        "verdict": payload,
        "changed_files": list(changed_files),
        "commits": list(commits),
    }

    if parse_error is not None:
        return {
            "ok": False,
            "status": parse_error["reason"],
            "reason": parse_error["reason"],
            "message": parse_error.get("message"),
            "field": parse_error.get("field"),
            **base,
        }

    assert payload is not None  # for type checkers

    blocked = payload.get("blocked_reason")
    if isinstance(blocked, str) and blocked.strip():
        # Blocked wins even when git shows real work — operator must see it.
        return {
            "ok": False,
            "status": "blocked",
            "reason": blocked.strip(),
            "message": f"lane reported blocked_reason={blocked.strip()!r}",
            "field": None,
            **base,
        }

    claimed_files = [
        p for p in (payload.get("files_written") or []) if isinstance(p, str) and p.strip()
    ]
    claimed_committed = bool(payload.get("committed"))

    git_empty = (not changed_files) and (not commits)

    # committed:true with zero commits is unsupported even if working tree dirty.
    if claimed_committed and not commits:
        return {
            "ok": False,
            "status": "VERDICT_UNSUPPORTED",
            "reason": "VERDICT_UNSUPPORTED",
            "message": "verdict claims committed=true but git reports zero commits",
            "field": "committed",
            **base,
        }

    # files_written non-empty while git has neither changes nor commits.
    if claimed_files and git_empty:
        return {
            "ok": False,
            "status": "VERDICT_UNSUPPORTED",
            "reason": "VERDICT_UNSUPPORTED",
            "message": (
                "verdict claims files_written but collect_diff reports "
                "empty changed_files and empty commits"
            ),
            "field": "files_written",
            **base,
        }

    return {
        "ok": True,
        "status": "ok",
        "reason": None,
        "message": None,
        "field": None,
        **base,
    }


def _unwrap_verdict(
    verdict: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (payload, error). error is a small dict with reason/message/field."""
    if verdict is None:
        return None, {
            "reason": "VERDICT_MISSING",
            "message": "verdict is absent",
            "field": None,
        }

    if not isinstance(verdict, Mapping):
        return None, {
            "reason": "VERDICT_MISSING",
            "message": "verdict is not an object",
            "field": None,
        }

    # parse_lane_verdict result shape.
    if "verdict" in verdict and ("ok" in verdict or "reason" in verdict):
        if not verdict.get("ok"):
            reason = str(verdict.get("reason") or "VERDICT_MISSING")
            return (
                dict(verdict["verdict"])
                if isinstance(verdict.get("verdict"), Mapping)
                else None
            ), {
                "reason": reason,
                "message": verdict.get("message"),
                "field": verdict.get("field"),
            }
        inner = verdict.get("verdict")
        if not isinstance(inner, Mapping):
            return None, {
                "reason": "VERDICT_MISSING",
                "message": "parsed verdict payload is missing",
                "field": None,
            }
        # Re-validate in case a caller forged ok:true.
        checked = _validate_verdict_object(dict(inner))
        if not checked.get("ok"):
            return None, {
                "reason": checked.get("reason") or "VERDICT_INVALID",
                "message": checked.get("message"),
                "field": checked.get("field"),
            }
        return dict(checked["verdict"]), None

    # Raw payload — validate required fields.
    checked = _validate_verdict_object(dict(verdict))
    if not checked.get("ok"):
        return None, {
            "reason": checked.get("reason") or "VERDICT_INVALID",
            "message": checked.get("message"),
            "field": checked.get("field"),
        }
    return dict(checked["verdict"]), None


def _diff_lists(diff: Mapping[str, Any] | None) -> tuple[list[str], list[str]]:
    if not isinstance(diff, Mapping):
        return [], []
    changed_raw = diff.get("changed_files") or []
    commits_raw = diff.get("commits") or []
    changed = [str(x) for x in changed_raw if x is not None and str(x).strip()]
    commits = [str(x) for x in commits_raw if x is not None and str(x).strip()]
    return changed, commits


def _is_str_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(item, str) for item in value)


def _bound_str(value: str) -> str:
    if len(value) <= MAX_VERDICT_STRING_CHARS:
        return value
    return value[:MAX_VERDICT_STRING_CHARS] + "…(truncated)"


def _bound_str_list(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    for item in list(values)[:MAX_VERDICT_LIST_ITEMS]:
        out.append(_bound_str(item))
    return out


def _bound_extra(value: Any) -> Any:
    if isinstance(value, str):
        return _bound_str(value)
    if isinstance(value, list):
        trimmed = list(value)[:MAX_VERDICT_LIST_ITEMS]
        return [_bound_extra(v) for v in trimmed]
    if isinstance(value, Mapping):
        # Shallow bound only — verdict extras should stay small.
        out: MutableMapping[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= MAX_VERDICT_LIST_ITEMS:
                break
            out[str(k)[:MAX_VERDICT_STRING_CHARS]] = _bound_extra(v)
        return dict(out)
    return value


def _missing(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "VERDICT_MISSING",
        "message": message,
        "field": None,
        "verdict": None,
    }


def _invalid(field: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "VERDICT_INVALID",
        "message": message,
        "field": field,
        "verdict": None,
    }
