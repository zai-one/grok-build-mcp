"""Fixed-command gate runner for grok_delegate (trusted Python only).

The model must never choose what runs. Commands are hardcoded constants here;
callers select only a named profile plus optional relative paths that are
confined to the worktree. This keeps interpreters out of the model's execute
allow list while still letting the driver verify a lane (R7-A).
"""

from __future__ import annotations

import inspect
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from .audit import _SECRET_PATTERNS
    from .guard import GuardError, confine_path_to_root, structured_error
except ImportError:  # flat import when package dir is on sys.path
    from audit import _SECRET_PATTERNS  # type: ignore
    from guard import GuardError, confine_path_to_root, structured_error  # type: ignore

# Named, hardcoded command tuples. Never built from caller text.
GATE_PROFILES: dict[str, tuple[tuple[str, ...], ...]] = {
    "python": (("py", "-3", "-m", "pytest", "tests", "-q"),),
    "node": (
        ("npx", "tsc", "--noEmit"),
        ("npx", "vitest", "run", "--reporter=basic"),
        ("npx", "eslint"),
    ),
}

# Parallel to GATE_PROFILES: True when that command index accepts appended paths.
# Declared here so path append is an explicit profile property, not a free-form
# argv rewrite from the caller.
_GATE_ACCEPTS_PATHS: dict[str, tuple[bool, ...]] = {
    "python": (True,),  # pytest accepts file/dir paths
    "node": (
        False,  # tsc --noEmit is project-mode; do not append free paths
        True,  # vitest run accepts path filters
        True,  # eslint accepts path args
    ),
}

# Wall-clock timeout per command (seconds).
DEFAULT_GATE_TIMEOUT_SECONDS = 300.0

# Bound on captured combined output (chars). Tail is kept; head is dropped.
DEFAULT_OUTPUT_CHAR_CAP = 8_000

# Bound on a single line after redaction (chars).
DEFAULT_LINE_CHAR_CAP = 500

# Marker inserted when output is truncated (tail retained).
_TRUNCATE_MARKER = "…(truncated)\n"

SubprocessRunner = Callable[[Sequence[str], Path | None, float], dict[str, Any]]

# Public parameter names for run_gates — used by tests to prove there is no
# command-injection surface on the API.
_RUN_GATES_PARAMS = frozenset(
    {"worktree", "profile", "paths", "timeout_seconds", "subprocess_runner"}
)


def run_gates(
    worktree: str | Path,
    profile: str,
    *,
    paths: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_GATE_TIMEOUT_SECONDS,
    subprocess_runner: SubprocessRunner | None = None,
) -> dict[str, Any]:
    """Run every command in a named gate profile against ``worktree``.

    Why: lanes cannot invoke interpreters themselves; this trusted helper runs
    fixed argv tuples only. There is deliberately **no** ``command`` parameter
    so caller text can never become a shell payload.

    Returns a per-command report plus aggregate ``ok`` and ``summary`` counts.
    Failures never raise for expected runtime conditions (missing binary,
    timeout, non-zero exit); they become structured ``ok: false`` results.
    """
    # API surface guard: keep the parameter set intentional and free of a
    # free-form command slot even if a future edit drifts the signature.
    _assert_run_gates_signature()

    if profile not in GATE_PROFILES:
        return structured_error(
            "GATE_PROFILE_UNKNOWN",
            f"unknown gate profile {profile!r}; known: {sorted(GATE_PROFILES)}",
            profile=profile,
            results=[],
            summary=_empty_summary(),
        )

    root = Path(worktree)
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        return structured_error(
            "GATE_WORKTREE_INVALID",
            f"worktree path is invalid: {exc}",
            profile=profile,
            results=[],
            summary=_empty_summary(),
        )

    # Empty list is equivalent to None: do not append path args.
    path_args = list(paths) if paths else []
    try:
        confined_paths = _confine_gate_paths(path_args, root_resolved)
    except GuardError as exc:
        # Map path confinement failures onto the gate-specific escape code so
        # drivers can branch on one stable token.
        code = "GATE_PATH_ESCAPE"
        if exc.code not in {"PATH_ESCAPE", "PATH_INVALID", "PATH_ROOT_INVALID"}:
            code = "GATE_PATH_ESCAPE"
        return structured_error(
            code,
            exc.message,
            profile=profile,
            results=[],
            summary=_empty_summary(),
        )

    # Node profile needs local deps; missing node_modules is an environment
    # problem, not a flaky stack trace the driver must parse.
    if profile == "node" and not (root_resolved / "node_modules").is_dir():
        return structured_error(
            "GATE_ENV_MISSING",
            "node_modules is missing under the worktree; install dependencies before node gates",
            profile=profile,
            worktree=str(root_resolved),
            results=[],
            summary=_empty_summary(),
        )

    commands = GATE_PROFILES[profile]
    accepts = _GATE_ACCEPTS_PATHS.get(profile) or tuple(False for _ in commands)
    runner = subprocess_runner or _default_subprocess_runner

    results: list[dict[str, Any]] = []
    for index, base_cmd in enumerate(commands):
        argv = list(base_cmd)
        if path_args and index < len(accepts) and accepts[index]:
            # Append the caller-relative tokens (not absolute resolved paths)
            # so tools see worktree-relative paths and spaces stay one argv cell.
            argv.extend(confined_paths)

        started = time.monotonic()
        try:
            proc = runner(argv, root_resolved, float(timeout_seconds))
        except FileNotFoundError as exc:
            duration = time.monotonic() - started
            results.append(
                _command_result(
                    command=argv,
                    returncode=127,
                    ok=False,
                    duration_s=duration,
                    output_tail=_redact_and_bound(
                        f"binary not found: {argv[0] if argv else '?'}: {exc}"
                    ),
                    timed_out=False,
                    reason="GATE_BINARY_MISSING",
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 — never crash the gate report
            duration = time.monotonic() - started
            results.append(
                _command_result(
                    command=argv,
                    returncode=1,
                    ok=False,
                    duration_s=duration,
                    output_tail=_redact_and_bound(f"gate runner error: {type(exc).__name__}: {exc}"),
                    timed_out=False,
                    reason="GATE_RUNNER_ERROR",
                )
            )
            continue

        duration = time.monotonic() - started
        timed_out = bool(proc.get("timedOut") or proc.get("timed_out"))
        missing = bool(proc.get("missing"))
        returncode = int(proc.get("returncode") if proc.get("returncode") is not None else 1)
        combined = _combine_output(proc)
        if missing:
            reason = "GATE_BINARY_MISSING"
            ok_cmd = False
            if not combined.strip():
                combined = f"binary not found: {argv[0] if argv else '?'}"
        elif timed_out:
            reason = "GATE_TIMEOUT"
            ok_cmd = False
        elif returncode != 0:
            reason = "GATE_COMMAND_FAILED"
            ok_cmd = False
        else:
            reason = None
            ok_cmd = True

        results.append(
            _command_result(
                command=argv,
                returncode=returncode,
                ok=ok_cmd,
                duration_s=duration,
                output_tail=_redact_and_bound(combined),
                timed_out=timed_out,
                reason=reason,
            )
        )

    passed = sum(1 for r in results if r.get("ok"))
    failed = len(results) - passed
    timed_out_n = sum(1 for r in results if r.get("timed_out"))
    aggregate_ok = failed == 0 and len(results) > 0
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "timed_out": timed_out_n,
    }
    out: dict[str, Any] = {
        "ok": aggregate_ok,
        "profile": profile,
        "worktree": str(root_resolved),
        "results": results,
        "summary": summary,
    }
    if not aggregate_ok:
        out["error"] = "GATE_FAILED"
        out["message"] = (
            f"gate profile {profile!r}: {passed}/{len(results)} passed, "
            f"{failed} failed, {timed_out_n} timed out"
        )
    return out


def _assert_run_gates_signature() -> None:
    """Fail closed if a free-form command parameter is ever added to run_gates."""
    params = set(inspect.signature(run_gates).parameters)
    if "command" in params or "commands" in params or "cmd" in params:
        raise RuntimeError("run_gates must not accept a free-form command parameter")
    # Soft check: unexpected params are not fatal at runtime but signal drift.
    _ = params - _RUN_GATES_PARAMS  # reserved for future strictness


def _empty_summary() -> dict[str, int]:
    return {"total": 0, "passed": 0, "failed": 0, "timed_out": 0}


def _confine_gate_paths(paths: Sequence[str], root: Path) -> list[str]:
    """Require every path to be relative and resolve inside ``root``.

    Returns the original relative tokens (normalized to forward-friendly form
    only for separators) so subprocess argv keeps spaces intact as one element.
    """
    confined: list[str] = []
    for raw in paths:
        token = str(raw)
        if not token or token.strip() == "":
            raise GuardError("PATH_INVALID", "gate path must be a non-empty relative path")
        # Absolute paths (POSIX, Windows drive, UNC) are always rejected even if
        # they happen to point inside the worktree — callers must stay relative.
        p = Path(token)
        if p.is_absolute() or _looks_absolute(token):
            raise GuardError(
                "PATH_ESCAPE",
                f"gate path must be relative to the worktree (absolute rejected): {token!r}",
            )
        # Resolve + relative_to via the shared helper; map any escape to GATE_PATH_ESCAPE.
        confine_path_to_root(token, root, field="paths")
        confined.append(token)
    return confined


def _looks_absolute(token: str) -> bool:
    """Extra absolute detection for Windows drive / UNC forms Path may miss."""
    s = token.replace("\\", "/")
    if s.startswith("/") or s.startswith("//"):
        return True
    if len(s) >= 3 and s[1] == ":" and s[0].isalpha() and s[2] == "/":
        return True
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        # Bare "C:foo" is still drive-qualified — reject.
        return True
    return False


def _combine_output(proc: Mapping[str, Any]) -> str:
    stdout = str(proc.get("stdout") or "")
    stderr = str(proc.get("stderr") or "")
    if stdout and stderr:
        return stdout + ("\n" if not stdout.endswith("\n") else "") + stderr
    return stdout or stderr


def _command_result(
    *,
    command: Sequence[str],
    returncode: int,
    ok: bool,
    duration_s: float,
    output_tail: str,
    timed_out: bool,
    reason: str | None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "command": list(command),
        "returncode": returncode,
        "ok": ok,
        "duration_s": round(float(duration_s), 4),
        "output_tail": output_tail,
        "timed_out": bool(timed_out),
    }
    if reason is not None:
        item["reason"] = reason
    return item


def _redact_and_bound(
    text: str,
    *,
    char_cap: int = DEFAULT_OUTPUT_CHAR_CAP,
    line_cap: int = DEFAULT_LINE_CHAR_CAP,
) -> str:
    """Redact secret-like material, bound long lines, keep a truncated tail."""
    redacted = _redact_secrets(text or "")
    lines = redacted.splitlines()
    bounded_lines: list[str] = []
    marker = _TRUNCATE_MARKER.strip()
    for line in lines:
        if len(line) > line_cap:
            # Keep the line's TAIL, same rule as the whole-output bound: a gate's
            # verdict sits at the end of the line (assertion text, error message),
            # while the head is usually a long path. Cutting the head off used to
            # discard exactly the part a reader needs, and it also dropped the
            # aggregate tail whenever the output was a single long line.
            bounded_lines.append(marker + line[-line_cap:])
        else:
            bounded_lines.append(line)
    joined = "\n".join(bounded_lines)
    if len(joined) <= char_cap:
        return joined
    # Keep the *tail* so failure summaries (usually last) survive the bound.
    return _TRUNCATE_MARKER + joined[-char_cap:]


def _redact_secrets(text: str) -> str:
    """Apply the same secret patterns as audit.py; replace matches, never raise."""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    # Hard substrings that must never leave the gate report.
    lowered = out
    for banned, replacement in (
        ("BEGIN PRIVATE KEY", "[REDACTED-KEY]"),
        ("BEGIN RSA PRIVATE KEY", "[REDACTED-KEY]"),
        (".grok/auth.json", "[REDACTED-AUTH-PATH]"),
        ("auth.json", "[REDACTED-AUTH-PATH]"),
    ):
        if banned.lower() in lowered.lower() or banned in out:
            # Case-sensitive replace for key headers; path-ish lower handled below.
            out = out.replace(banned, replacement)
    # Case-insensitive auth.json residual cleanup.
    out = re.sub(r"(?i)auth\.json", "[REDACTED-AUTH-PATH]", out)
    return out


def _default_subprocess_runner(
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Real subprocess for production gates. Tests inject a mock instead.

    Always decodes as UTF-8 (R7-F invariant) so non-ASCII tool output never
    crashes the reader on Windows cp1252 locales.
    """
    import subprocess

    cmd = [str(a) for a in args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "args": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "timedOut": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "args": cmd,
            "returncode": 124,
            "stdout": stdout or "",
            "stderr": stderr or f"timeout after {timeout}s",
            "timedOut": True,
        }
    except FileNotFoundError:
        return {
            "args": cmd,
            "returncode": 127,
            "stdout": "",
            "stderr": f"binary not found: {cmd[0] if cmd else '?'}",
            "timedOut": False,
            "missing": True,
        }
