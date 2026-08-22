"""Read-only status probes for grok_delegate MCP tools (no mutations).

Never reads auth.json. Never runs doctor fix / logout / update / plugin / mcp
config management. All probes use bounded timeouts and injectable runners.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
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
        host_provided_roots,
        structured_error,
        trust_host_roots_enabled,
        validate_grok_bin,
    )
    from .host_roots import host_roots, mcp_roots_enabled
    from .runner import (
        SubprocessRunner,
        WhichFn,
        in_project_lanes_parent,
        list_lanes,
        run_readonly_cli,
    )
except ImportError:  # flat import
    from guard import (  # type: ignore
        DEFAULT_EXECUTE_SANDBOX,
        DEFAULT_GROK_BIN,
        DEFAULT_PLAN_SANDBOX,
        HARD_CAP_MAX_TURNS,
        KNOWN_SANDBOX_PROFILES,
        SERVER_VERSION,
        GuardError,
        host_provided_roots,
        structured_error,
        trust_host_roots_enabled,
        validate_grok_bin,
    )
    from host_roots import host_roots, mcp_roots_enabled  # type: ignore
    from runner import (  # type: ignore
        SubprocessRunner,
        WhichFn,
        in_project_lanes_parent,
        list_lanes,
        run_readonly_cli,
    )

# Bounded timeouts for status CLI probes (seconds).
STATUS_TIMEOUT_SECONDS = 20.0
DOCTOR_TIMEOUT_SECONDS = 30.0
MODELS_TIMEOUT_SECONDS = 30.0
INSPECT_TIMEOUT_SECONDS = 30.0

DEFAULT_STATUS_TIMEOUT = STATUS_TIMEOUT_SECONDS

UPDATE_HINT = (
    "If bridge_version is older than the checkout, git pull, "
    "reinstall editable (`pip install -e .`), and restart the MCP host."
)


def compatibility_report(*, detected_cli_version: Any = None) -> dict[str, Any]:
    """Unpin-by-default compatibility block for status/doctor receipts."""
    from .acp import expected_agent_version

    expected = expected_agent_version()
    detected = None if detected_cli_version in (None, "") else str(detected_cli_version)
    mismatch = bool(expected and detected and expected != detected)
    warning = None
    if mismatch:
        warning = (
            f"CLI {detected} differs from GROK_DELEGATE_EXPECTED_AGENT_VERSION="
            f"{expected}; typed path is not blocked (pin is opt-in, warn-only)."
        )
    return {
        "bridge_version": SERVER_VERSION,
        "grok_delegate_version": SERVER_VERSION,
        "detected_cli_version": detected,
        "protocol": "acp/v1",
        "expected_agent_version": expected or "any",
        "pin_enabled": expected is not None,
        "mismatch": mismatch,
        "mismatch_blocks_typed_path": False,
        "skill_protocol": "session/v1.2",
        "update_hint": UPDATE_HINT,
        "warning": warning,
    }


def update_report() -> dict[str, Any]:
    """Whether the running bridge lags its remote, for the status receipt.

    Reported rather than acted on: noticing is cheap and belongs in the status
    every host already calls, while pulling and restarting is the operator's call
    and lives behind grok_agent_update.
    """
    try:
        from .updater import update_status

        report = update_status()
    except Exception:
        # Never let an update check be the reason status fails.
        return {"available": False, "reason": "CHECK_FAILED"}
    if report.get("available"):
        report["hint"] = "call grok_agent_update with confirm=true, then restart the MCP host"
    return report


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


#: How long a *present* session stays a fact without asking the CLI again.
AUTH_CACHE_TTL_SECONDS = 600.0

_AUTH_LOCK = threading.Lock()
#: binary -> (expires_at, result)
_AUTH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
#: binary -> event set when the probe running right now has stored its answer
_AUTH_INFLIGHT: dict[str, threading.Event] = {}


def clear_auth_probe_cache() -> None:
    """Forget the cached session probe.

    Production rarely needs this -- a login lasts longer than a server. Tests do,
    or one case's injected runner answers for the next.
    """
    with _AUTH_LOCK:
        _AUTH_CACHE.clear()
        for event in _AUTH_INFLIGHT.values():
            event.set()
        _AUTH_INFLIGHT.clear()


def cached_auth_presence(
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    ttl: float = AUTH_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    """``probe_auth_presence`` without paying for it every single call.

    The probe is ``grok models``, and on this machine that costs 12.7 s of
    network round trip -- measured three times, 12.72/12.76/12.80 -- which was
    the whole of ``grok_agent_status`` and the whole of ``session_begin``. The
    answer it buys changes about as often as the operator logs in.

    Only a *present* session is cached. Absence is the state the operator is
    about to fix with ``grok login``, and a cached "no" would keep saying no for
    ten minutes after the login succeeded. Concurrent callers share one probe
    rather than starting a second: two tools asking at once cost 12.7 s, not
    25.4 s.
    """
    if subprocess_runner is not None:
        # A caller carrying its own runner is asking about *that* runner, not
        # about the machine. Caching those answers under the binary name would
        # let one caller's stub speak for everybody else's probe.
        out = dict(probe_auth_presence(grok_bin=grok_bin, subprocess_runner=subprocess_runner, which=which))
        out["cached"] = False
        return out

    key = str(grok_bin or DEFAULT_GROK_BIN)
    while True:
        with _AUTH_LOCK:
            hit = _AUTH_CACHE.get(key)
            if hit is not None and hit[0] > time.monotonic():
                out = dict(hit[1])
                out["cached"] = True
                return out
            waiting = _AUTH_INFLIGHT.get(key)
            if waiting is None:
                mine = threading.Event()
                _AUTH_INFLIGHT[key] = mine
                break
        # Someone else is already asking; their answer is ours too.
        waiting.wait(MODELS_TIMEOUT_SECONDS + 5.0)
        with _AUTH_LOCK:
            hit = _AUTH_CACHE.get(key)
        if hit is not None and hit[0] > time.monotonic():
            out = dict(hit[1])
            out["cached"] = True
            return out
        # The other probe failed or was cleared: fall through and ask ourselves,
        # unless it is still registered, in which case try again.
        with _AUTH_LOCK:
            if _AUTH_INFLIGHT.get(key) is waiting:
                _AUTH_INFLIGHT.pop(key, None)

    started = time.perf_counter()
    try:
        result = dict(
            probe_auth_presence(
                grok_bin=key,
                subprocess_runner=subprocess_runner,
                which=which,
            )
        )
        result["cached"] = False
        result["probe_seconds"] = round(time.perf_counter() - started, 3)
        if result.get("auth_present") and ttl > 0:
            with _AUTH_LOCK:
                _AUTH_CACHE[key] = (time.monotonic() + ttl, dict(result))
        return result
    finally:
        # Released here and not after the store, so a probe that raises frees
        # its waiters instead of parking them for the full timeout.
        with _AUTH_LOCK:
            event = _AUTH_INFLIGHT.pop(key, None)
        if event is not None:
            event.set()


def prime_auth_probe_async(
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    which: WhichFn | None = None,
) -> threading.Thread | None:
    """Start paying for the session probe before anyone waits on it.

    A host opens the bridge and then thinks, or reads a file, or waits for the
    operator to type. That idle time is free, and 12.7 s of it is exactly what
    the first ``grok_agent_status`` used to charge. Returns the thread so a test
    can join it; None when the binary is not there to ask.
    """
    which_fn = which or shutil.which
    try:
        if not (which_fn(grok_bin) or Path(grok_bin).is_file()):
            return None
    except Exception:
        return None

    def run() -> None:
        try:
            cached_auth_presence(grok_bin=grok_bin, which=which_fn)
        except Exception:
            pass

    thread = threading.Thread(target=run, name="grok-auth-prewarm", daemon=True)
    thread.start()
    return thread


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
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate health JSON for ``grok_delegate_status`` (secret-free)."""
    env_source = env if env is not None else os.environ
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
        auth_info = cached_auth_presence(
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
            # The same resolver execute uses. Computing a second answer here is
            # how status came to report a directory no job would ever write to.
            lanes_map[r] = str(in_project_lanes_parent(Path(r)))

    lanes_live: list[dict[str, Any]] = []
    for root_path in roots:
        try:
            lanes_live.extend(
                list_lanes(Path(root_path), lanes_parent=lanes_map.get(root_path))
            )
        except Exception:
            continue
    # A repository that collected lanes for a month must not turn a status call
    # into a page of paths. The count still tells the truth about how many.
    lanes_total = len(lanes_live)
    lanes_live = lanes_live[:32]

    env_sandbox = os.environ.get("GROK_SANDBOX") or os.environ.get("GROK_DELEGATE_SANDBOX")
    compatibility = compatibility_report(detected_cli_version=version_info.get("version"))

    return {
        "ok": True,
        "server": {
            "name": "grok-delegate",
            "version": SERVER_VERSION,
            "hard_cap_max_turns": HARD_CAP_MAX_TURNS,
        },
        "compatibility": compatibility,
        "update": update_report(),
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
            # `grok models` is a network round trip. Saying whether this answer
            # came from it or from the cache is the difference between a status
            # call that looks mysteriously slow and one that explains itself.
            "cached": bool(auth_info.get("cached")),
            "probe_seconds": auth_info.get("probe_seconds"),
        },
        "git": git_info,
        "roots": {
            "allowed": roots,
            "count": len(roots),
            "lanes_parent_by_root": lanes_map,
            "configured": len(roots) > 0,
            # Without this an operator sees a root they never configured and has
            # no way to tell where it came from.
            #
            # `host_root_trusted` reads like "this root can be trusted" and is
            # nothing of the sort: it is the GROK_DELEGATE_TRUST_HOST_ROOTS flag,
            # which is off by default and governs an env-provided path only. The
            # channel that actually grants directories here is MCP `roots/list`,
            # on by default, and it was not represented in this block at all --
            # so the two fields together said "no host root" while the host had
            # declared one and the bridge had accepted it.
            "host_root_trusted": trust_host_roots_enabled(env_source),
            "host_root": (host_provided_roots(env_source) or [None])[0],
            "mcp_roots_enabled": mcp_roots_enabled(env_source),
            "mcp_roots": [str(path) for path in host_roots()[:8]],
        },
        # Unmerged work is invisible until somebody goes looking for it, and
        # a lane is exactly that: a branch a human still has to judge.
        "lanes": lanes_live,
        "lanes_total": lanes_total,
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
