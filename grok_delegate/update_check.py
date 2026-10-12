"""Suggest-only update check against public GitHub releases of zai-one/grok-build-mcp.

One unauthenticated HTTPS GET with a 3 s deadline (DNS included), cached for 24
hours. Offline, rate-limited or malformed answers degrade to
``status="unavailable"`` and are not retried for 30 minutes; ``force`` is honoured
at most once a minute. Only a plain version tag is ever reported.
Nothing is ever downloaded or installed; the result only names the command the
operator may run. Set ``GROK_DELEGATE_DISABLE_UPDATE_CHECK=1`` to turn the check off.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .guard import SERVER_VERSION as __version__

REPO = "zai-one/grok-build-mcp"
DISTRIBUTION = "grok-delegate"
DISABLE_ENV = "GROK_DELEGATE_DISABLE_UPDATE_CHECK"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
CACHE_TTL_SECONDS = 24 * 60 * 60
TIMEOUT_SECONDS = 3.0
MAX_RESPONSE_BYTES = 1_048_576
#: A plain release tag and nothing else: ``v1``, ``1.2``, ``v1.2.3.4``. Anything
#: that fails this never reaches a version field, a command or a URL.
_VERSION = re.compile(r"v?(\d{1,6}(?:\.\d{1,6}){0,3})")
#: What may follow a trusted GitHub URL prefix: path characters only, so a value
#: read from the API or the cache cannot smuggle quotes, spaces or shell syntax.
_URL_TAIL = re.compile(r"[A-Za-z0-9._~/%+-]{1,512}")
#: After a failed fetch, wait this long before asking GitHub again.
FAILURE_BACKOFF_SECONDS = 30 * 60
#: ``force=true`` is honoured at most once per this many seconds.
FORCE_MIN_INTERVAL_SECONDS = 60

Fetcher = Callable[[str, float], bytes]

_lock = threading.Lock()


def disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def parse_version(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION.fullmatch(value)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _normalized(value: Any) -> str | None:
    version = parse_version(value)
    return ".".join(str(part) for part in version) if version is not None else None


def _is_newer(latest: str, current: str) -> bool | None:
    left, right = parse_version(latest), parse_version(current)
    if left is None or right is None:
        return None
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _github_url(value: Any, prefix: str) -> str | None:
    """``value`` when it is a plain URL under ``prefix``, otherwise None."""
    if isinstance(value, str) and value.startswith(prefix) and _URL_TAIL.fullmatch(value[len(prefix) :]):
        return value
    return None


def cache_path() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    elif os.environ.get("XDG_CACHE_HOME"):
        base = Path(os.environ["XDG_CACHE_HOME"])
    else:
        base = Path.home() / ".cache"
    return base / DISTRIBUTION / "update-check.json"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _read_cache() -> dict[str, Any] | None:
    """The cached state, validated field by field, or None when there is none.

    The cache is a file anyone with the user's rights can edit, so it is read
    like a network answer: a version that is not a plain tag or a URL outside
    this repository is dropped, never passed on.
    """
    try:
        path = cache_path()
        if path.stat().st_size > 65_536:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    checked_at = _number(data.get("checked_at"))
    latest = _normalized(data.get("latest_version"))
    release = None
    if checked_at is not None and latest is not None:
        release = {
            "checked_at": checked_at,
            "latest_version": latest,
            "release_url": _github_url(data.get("release_url"), f"{RELEASES_URL}/"),
            "wheel_url": _github_url(data.get("wheel_url"), f"{RELEASES_URL}/download/"),
        }
    attempted_at = _number(data.get("attempted_at"))
    if attempted_at is None and release is not None:
        attempted_at = release["checked_at"]  # written before attempts were recorded
    if attempted_at is None:
        return None
    return {"attempted_at": attempted_at, "failed": data.get("failed") is True, "release": release}


def _write_cache(state: dict[str, Any]) -> None:
    record = {"attempted_at": state["attempted_at"], "failed": state["failed"], **(state["release"] or {})}
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def _fresh(state: dict[str, Any] | None, moment: float) -> bool:
    release = state["release"] if state else None
    return release is not None and 0 <= moment - release["checked_at"] < CACHE_TTL_SECONDS


def _fetch_due(state: dict[str, Any] | None, moment: float, *, force: bool) -> bool:
    """Whether to ask GitHub now. Every attempt counts, successful or not."""
    if state is None:
        return True
    since = moment - state["attempted_at"]
    if force:
        return not 0 <= since < FORCE_MIN_INTERVAL_SECONDS
    if state["failed"] and 0 <= since < FAILURE_BACKOFF_SECONDS:
        return False
    return not _fresh(state, moment)


def _urlopen_read(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"{DISTRIBUTION}/{__version__} update-check",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- fixed https URL
        return response.read(MAX_RESPONSE_BYTES + 1)


def _default_fetch(url: str, timeout: float) -> bytes:
    """Fetch with ``timeout`` bounding the whole call, DNS lookup included.

    ``urlopen``'s timeout covers the socket, not ``getaddrinfo``, so a stalled
    resolver could hold the caller far past it. The request runs on a daemon
    thread and the caller stops waiting at the deadline.
    """
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["body"] = _urlopen_read(url, timeout)
        except BaseException as exc:  # noqa: BLE001 -- handed back to the caller below
            box["error"] = exc

    worker = threading.Thread(target=run, name="update-check-fetch", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no answer from GitHub within {timeout:g} s")
    if "error" in box:
        raise box["error"]
    return box["body"]


def _parse_release(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("release response too large")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("unexpected release response")
    latest = _normalized(data.get("tag_name"))
    if latest is None:
        raise ValueError("release tag is not a plain version")
    release_url = _github_url(data.get("html_url"), f"{RELEASES_URL}/") or f"{RELEASES_URL}/tag/v{latest}"
    wheel_url = None
    for asset in data.get("assets") or []:
        url = asset.get("browser_download_url") if isinstance(asset, dict) else None
        url = _github_url(url, f"{RELEASES_URL}/download/")
        if url is not None and url.endswith(".whl"):
            wheel_url = url
            break
    return {"latest_version": latest, "release_url": release_url, "wheel_url": wheel_url}


def _install_kind() -> str:
    """Return ``git`` for this package's own source checkout, otherwise ``package``.

    ``bridge_checkout_dir`` only answers for a checkout whose ``pyproject.toml``
    names this distribution and whose ``grok_delegate`` is the running package,
    so a pip install inside some other repository's venv reads as ``package``.
    """
    from .updater import bridge_checkout_dir

    return "git" if bridge_checkout_dir() is not None else "package"


def update_command(latest: str | None, wheel_url: str | None) -> str | None:
    if _install_kind() == "git":
        # The checkout path stays out of this string; grok_agent_update knows it.
        return (
            "grok_agent_update (preview), then grok_agent_update confirm=true, then restart the MCP host; "
            "manual: git pull --ff-only && python -m pip install -e ."
        )
    version = _normalized(latest)
    if version is None:
        return None
    return f'python -m pip install --upgrade "{DISTRIBUTION} @ git+https://github.com/{REPO}@v{version}"'


def check_for_update(
    *, force: bool = False, fetch: Fetcher | None = None, now: float | None = None
) -> dict[str, Any]:
    """Compare the installed version with the latest GitHub release. Never raises.

    Every attempt is recorded. A failed one is not retried for 30 minutes, and
    ``force`` is honoured at most once a minute, so a host calling in a loop or
    an offline machine costs at most one request per window.
    """
    current = __version__
    result: dict[str, Any] = {
        "current_version": current,
        "latest_version": None,
        "update_available": None,
        "status": "unavailable",
        "source": None,
        "checked_at": None,
        "release_notes_url": RELEASES_URL,
        "update_command": None,
        "auto_update": False,
        "disable_with": f"{DISABLE_ENV}=1",
    }
    if disabled():
        result["status"] = "disabled"
        return result
    moment = time.time() if now is None else now
    with _lock:
        state = _read_cache()
        if _fetch_due(state, moment, force=force):
            release = state["release"] if state else None
            try:
                fetched = _parse_release((fetch or _default_fetch)(LATEST_RELEASE_API, TIMEOUT_SECONDS))
            except Exception:  # noqa: BLE001 -- offline, HTTP error, rate limit, bad JSON: degrade quietly
                fetched = None
            if fetched is not None:
                release = {"checked_at": moment, **fetched}
            state = {"attempted_at": moment, "failed": fetched is None, "release": release}
            _write_cache(state)
            usable = fetched is not None
            result["source"] = "github" if usable else ("stale_cache" if release else None)
        else:
            usable = _fresh(state, moment)
            release = state["release"] if state else None
            result["source"] = "cache" if usable else ("stale_cache" if release else None)
    if release is None:
        return result
    newer = _is_newer(release["latest_version"], current)
    if not usable:
        status = "unavailable"
    elif newer is None:
        status = "unknown"  # the running version is not a plain version, so no comparison
    else:
        status = "update_available" if newer else "up_to_date"
    result.update(
        latest_version=release["latest_version"],
        update_available=newer,
        status=status,
        checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(release["checked_at"])),
        release_notes_url=release.get("release_url") or RELEASES_URL,
        update_command=update_command(release["latest_version"], release.get("wheel_url")) if newer else None,
    )
    return result


def cached_update_hint() -> str | None:
    """One-line startup hint from the cache only; never touches the network."""
    if disabled():
        return None
    state = _read_cache()
    release = state["release"] if state else None
    if release is None or not _is_newer(release["latest_version"], __version__):
        return None
    return (
        f"Update available: {DISTRIBUTION} {__version__} -> {release['latest_version']}. "
        f"Call grok_agent_check_update for release notes and the update command; "
        "nothing is installed automatically."
    )


def refresh_in_background() -> threading.Thread | None:
    """Refresh a stale cache on a daemon thread so startup never waits on the network.

    Obeys the same rules as an unforced check: nothing while the cache is fresh,
    and nothing for 30 minutes after a failed attempt. Returns the thread, if any.
    """
    if disabled() or not _fetch_due(_read_cache(), time.time(), force=False):
        return None
    worker = threading.Thread(target=check_for_update, name="update-check", daemon=True)
    worker.start()
    return worker
