"""Read-only status probes for grok_delegate MCP tools (no mutations).

Never reads auth.json. Never runs doctor fix / logout / update / plugin / mcp
config management. All probes use bounded timeouts and injectable runners.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from .guard import (
        DEFAULT_EXECUTE_SANDBOX,
        DEFAULT_GROK_BIN,
        DEFAULT_PLAN_SANDBOX,
        HARD_CAP_MAX_TURNS,
        KNOWN_SANDBOX_PROFILES,
        SERVER_VERSION,
        GuardError,
        structured_error,
        validate_grok_bin,
    )
    from .runner import SubprocessRunner, WhichFn, run_readonly_cli
except ImportError:  # flat import
    from guard import (  # type: ignore
        DEFAULT_EXECUTE_SANDBOX,
        DEFAULT_GROK_BIN,
        DEFAULT_PLAN_SANDBOX,
        HARD_CAP_MAX_TURNS,
        KNOWN_SANDBOX_PROFILES,
        SERVER_VERSION,
        GuardError,
        structured_error,
        validate_grok_bin,
    )
    from runner import SubprocessRunner, WhichFn, run_readonly_cli  # type: ignore

# Bounded timeouts for status CLI probes (seconds).
STATUS_TIMEOUT_SECONDS = 20.0
DOCTOR_TIMEOUT_SECONDS = 30.0
MODELS_TIMEOUT_SECONDS = 30.0
INSPECT_TIMEOUT_SECONDS = 30.0

DEFAULT_STATUS_TIMEOUT = STATUS_TIMEOUT_SECONDS


def _parse_json_stdout(stdout: str) -> Any:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try first JSON object/array in stream.
        for start in ("{", "["):
            i = text.find(start)
            if i < 0:
                continue
            try:
                return json.loads(text[i:])
            except json.JSONDecodeError:
                continue
    return None


def probe_grok_version(
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    timeout_seconds: float = STATUS_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run ``grok version --json`` (read-only)."""
    try:
        out = run_readonly_cli(
            ["version", "--json"],
            grok_bin=grok_bin,
            timeout_seconds=timeout_seconds,
            subprocess_runner=subprocess_runner,
            which=which,
        )
    except GuardError as exc:
        return structured_error(exc.code, exc.message)
    if not out.get("ok") and out.get("error"):
        return out
    parsed = _parse_json_stdout(str(out.get("stdout") or ""))
    version = None
    channel = None
    if isinstance(parsed, dict):
        version = parsed.get("currentVersion") or parsed.get("version")
        channel = parsed.get("channel")
    return {
        "ok": bool(out.get("ok")),
        "version": version,
        "channel": channel,
        "raw": parsed if isinstance(parsed, dict) else None,
        "returncode": out.get("returncode"),
        "stderr_preview": (str(out.get("stderr") or ""))[:300] if not out.get("ok") else "",
    }


def probe_auth_presence(
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    timeout_seconds: float = MODELS_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Detect session without reading auth.json.

    Uses success of read-only ``grok models`` plus login phrase heuristics.
    Never opens credential files or returns tokens.
    """
    try:
        out = run_readonly_cli(
            ["models"],
            grok_bin=grok_bin,
            timeout_seconds=timeout_seconds,
            subprocess_runner=subprocess_runner,
            which=which,
        )
    except GuardError as exc:
        return structured_error(exc.code, exc.message)
    if out.get("error"):
        return {
            "ok": False,
            "auth_present": False,
            "error": out.get("error"),
            "message": out.get("message"),
        }
    stdout = str(out.get("stdout") or "")
    stderr = str(out.get("stderr") or "")
    blob = (stdout + "\n" + stderr).lower()
    # Positive signals (no secrets).
    logged_in = (
        "logged in" in blob
        or "available models" in blob
        or "default model" in blob
        or (out.get("ok") and "model" in blob)
    )
    # Negative signals.
    if any(
        x in blob
        for x in (
            "not logged in",
            "please login",
            "please log in",
            "authentication required",
            "unauthorized",
        )
    ):
        logged_in = False
    return {
        "ok": True,
        "auth_present": bool(logged_in and out.get("returncode") == 0),
        "probe": "models",
        "returncode": out.get("returncode"),
    }


def probe_git_available(
    *,
    git_runner: Callable[[Sequence[str], Path | None, float], dict[str, Any]] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Check git --version without mutating anything."""
    runner = git_runner
    if runner is None:
        try:
            from .runner import default_git_runner as gr
        except ImportError:
            from runner import default_git_runner as gr  # type: ignore

        runner = gr
    try:
        result = runner(["--version"], None, timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "available": False, "detail": f"{type(exc).__name__}: {exc}"}
    available = not result.get("missing") and result.get("returncode") == 0
    version = (result.get("stdout") or "").strip().splitlines()[:1]
    return {
        "ok": True,
        "available": bool(available),
        "version": version[0] if version else None,
    }


def run_doctor_json(
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    timeout_seconds: float = DOCTOR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Wrap ``grok doctor --json`` only — never ``doctor fix``."""
    try:
        out = run_readonly_cli(
            ["doctor", "--json"],
            grok_bin=grok_bin,
            timeout_seconds=timeout_seconds,
            subprocess_runner=subprocess_runner,
            which=which,
        )
    except GuardError as exc:
        return structured_error(exc.code, exc.message)
    if out.get("error"):
        return out
    parsed = _parse_json_stdout(str(out.get("stdout") or ""))
    return {
        "ok": bool(out.get("ok")),
        "doctor": parsed if parsed is not None else {"text_preview": (out.get("stdout") or "")[:2000]},
        "returncode": out.get("returncode"),
        "stderr_preview": (str(out.get("stderr") or ""))[:500] if not out.get("ok") else "",
    }


def run_models(
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    timeout_seconds: float = MODELS_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Wrap ``grok models`` (text or structured preview; no secrets)."""
    try:
        out = run_readonly_cli(
            ["models"],
            grok_bin=grok_bin,
            timeout_seconds=timeout_seconds,
            subprocess_runner=subprocess_runner,
            which=which,
        )
    except GuardError as exc:
        return structured_error(exc.code, exc.message)
    if out.get("error"):
        return out
    stdout = str(out.get("stdout") or "")
    # Strip any accidental key-looking lines (defense).
    safe_lines = []
    for line in stdout.splitlines():
        low = line.lower()
        if "api_key" in low or "authorization" in low or "bearer " in low:
            continue
        safe_lines.append(line)
    text = "\n".join(safe_lines)[:4000]
    return {
        "ok": bool(out.get("ok")),
        "models_text": text,
        "returncode": out.get("returncode"),
        "stderr_preview": (str(out.get("stderr") or ""))[:300] if not out.get("ok") else "",
    }


def run_inspect_json(
    project_root: Path | str,
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    timeout_seconds: float = INSPECT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Wrap ``grok inspect --json`` for an already-trusted project path."""
    root = Path(project_root).resolve()
    try:
        out = run_readonly_cli(
            ["inspect", "--json"],
            grok_bin=grok_bin,
            cwd=root,
            timeout_seconds=timeout_seconds,
            subprocess_runner=subprocess_runner,
            which=which,
        )
    except GuardError as exc:
        return structured_error(exc.code, exc.message)
    if out.get("error"):
        return out
    parsed = _parse_json_stdout(str(out.get("stdout") or ""))
    # Redact any path that looks like auth.
    if isinstance(parsed, dict):
        parsed = _redact_inspect_blob(parsed)
    return {
        "ok": bool(out.get("ok")),
        "project_root": str(root),
        "inspect": parsed if parsed is not None else {"text_preview": (out.get("stdout") or "")[:2000]},
        "returncode": out.get("returncode"),
        "stderr_preview": (str(out.get("stderr") or ""))[:500] if not out.get("ok") else "",
    }


def _redact_inspect_blob(data: Mapping[str, Any]) -> dict[str, Any]:
    """Drop secret-looking keys from inspect JSON (never surface tokens)."""
    banned = {"token", "apiKey", "api_key", "authorization", "secret", "password", "auth"}
    out: dict[str, Any] = {}
    for k, v in data.items():
        if str(k).lower() in banned or "auth.json" in str(k).lower():
            continue
        if isinstance(v, dict):
            out[str(k)] = _redact_inspect_blob(v)
        elif isinstance(v, list):
            out[str(k)] = [
                _redact_inspect_blob(x) if isinstance(x, dict) else x for x in v[:200]
            ]
        elif isinstance(v, str) and ("auth.json" in v.lower() or "/.grok/auth" in v.replace("\\", "/").lower()):
            out[str(k)] = "[redacted-path]"
        else:
            out[str(k)] = v
    return out


def build_status_report(
    *,
    allowed_roots: Sequence[Path | str],
    lanes_parent_map: Mapping[str, str] | None = None,
    grok_bin: str = DEFAULT_GROK_BIN,
    sandbox_enabled: bool = True,
    default_execute_sandbox: str = DEFAULT_EXECUTE_SANDBOX,
    default_plan_sandbox: str = DEFAULT_PLAN_SANDBOX,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    git_runner: Callable[[Sequence[str], Path | None, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate health JSON for ``grok_delegate_status`` (secret-free)."""
    which_fn = which or shutil.which
    bin_name: str
    try:
        bin_name = validate_grok_bin(grok_bin, from_client=False)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    resolved_bin = which_fn(bin_name) if callable(which_fn) else None
    bin_found = bool(resolved_bin) or Path(bin_name).is_file()

    version_info: dict[str, Any]
    auth_info: dict[str, Any]
    if bin_found:
        version_info = probe_grok_version(
            grok_bin=bin_name,
            subprocess_runner=subprocess_runner,
            which=which,
        )
        auth_info = probe_auth_presence(
            grok_bin=bin_name,
            subprocess_runner=subprocess_runner,
            which=which,
        )
    else:
        version_info = {"ok": False, "version": None, "error": "GROK_MISSING"}
        auth_info = {"ok": False, "auth_present": False, "error": "GROK_MISSING"}

    git_info = probe_git_available(git_runner=git_runner)

    roots = [str(Path(p).resolve()) for p in allowed_roots]
    lanes_map: dict[str, str] = {}
    if lanes_parent_map:
        for k, v in lanes_parent_map.items():
            lanes_map[str(k)] = str(v)
    else:
        for r in roots:
            lanes_map[r] = str(Path(r).resolve().parent / "pcp-lanes")

    env_sandbox = os.environ.get("GROK_SANDBOX") or os.environ.get("GROK_DELEGATE_SANDBOX")

    return {
        "ok": True,
        "server": {
            "name": "grok-delegate",
            "version": SERVER_VERSION,
            "hard_cap_max_turns": HARD_CAP_MAX_TURNS,
        },
        "grok": {
            "binary": bin_name,
            "binary_found": bin_found,
            "resolved_path_present": bool(resolved_bin),
            # Never return full home-relative auth paths.
            "version": version_info.get("version"),
            "channel": version_info.get("channel"),
            "version_probe_ok": bool(version_info.get("ok")),
        },
        "auth": {
            "present": bool(auth_info.get("auth_present")),
            "probe": auth_info.get("probe") or "models",
            # Explicit: we do not read auth.json
            "auth_json_read": False,
        },
        "git": git_info,
        "roots": {
            "allowed": roots,
            "count": len(roots),
            "lanes_parent_by_root": lanes_map,
            "configured": len(roots) > 0,
        },
        "permissions": {
            "execute_mode": "dontAsk",
            "plan_mode": "plan",
            "bypassPermissions": False,
            "always_approve": False,
        },
        "sandbox": {
            "enabled": bool(sandbox_enabled),
            "known_profiles": sorted(KNOWN_SANDBOX_PROFILES),
            "default_execute": default_execute_sandbox if sandbox_enabled else "off",
            "default_plan": default_plan_sandbox if sandbox_enabled else "off",
            "env_GROK_SANDBOX": env_sandbox if env_sandbox else None,
            # Docs: OS primitives are Landlock/Seatbelt — Windows may not enforce.
            "os_enforcement_note": (
                "CLI accepts --sandbox profiles; kernel enforcement documented for "
                "Linux (Landlock) and macOS (Seatbelt). On Windows, presence of the "
                "flag is not a claim of OS-level confinement."
            ),
        },
    }
