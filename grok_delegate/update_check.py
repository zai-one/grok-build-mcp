"""Suggest-only update check against public GitHub releases of zai-one/grok-build-mcp.

One unauthenticated HTTPS GET with a short timeout, cached for 24 hours.
Offline, rate-limited or malformed answers degrade to ``status="unavailable"``.
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
_VERSION = re.compile(r"v?(\d{1,6}(?:\.\d{1,6}){0,3})")
_CACHE_KEYS = ("checked_at", "latest_version", "release_url", "wheel_url")

Fetcher = Callable[[str, float], bytes]


def disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def parse_version(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION.fullmatch(value.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _is_newer(latest: str, current: str) -> bool | None:
    left, right = parse_version(latest), parse_version(current)
    if left is None or right is None:
        return None
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def cache_path() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    elif os.environ.get("XDG_CACHE_HOME"):
        base = Path(os.environ["XDG_CACHE_HOME"])
    else:
        base = Path.home() / ".cache"
    return base / DISTRIBUTION / "update-check.json"


def _read_cache() -> dict[str, Any] | None:
    try:
        path = cache_path()
        if path.stat().st_size > 65_536:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("checked_at"), int | float)
        or parse_version(data.get("latest_version")) is None
    ):
        return None
    return {key: data.get(key) for key in _CACHE_KEYS}


def _write_cache(record: dict[str, Any]) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def _default_fetch(url: str, timeout: float) -> bytes:
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


def _parse_release(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("release response too large")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("unexpected release response")
    tag = data.get("tag_name")
    version = parse_version(tag)
    if version is None:
        raise ValueError("release tag is not a version")
    latest = ".".join(str(part) for part in version)
    release_url = data.get("html_url")
    if not isinstance(release_url, str) or not release_url.startswith(f"https://github.com/{REPO}/"):
        release_url = f"{RELEASES_URL}/tag/{tag}"
    wheel_url = None
    for asset in data.get("assets") or []:
        url = asset.get("browser_download_url") if isinstance(asset, dict) else None
        if (
            isinstance(url, str)
            and url.startswith(f"https://github.com/{REPO}/releases/download/")
            and url.endswith(".whl")
        ):
            wheel_url = url
            break
    return {"latest_version": latest, "release_url": release_url, "wheel_url": wheel_url}


def _install_kind() -> str:
    """Return ``git`` for a source checkout, otherwise ``package``."""
    from .updater import bridge_checkout_dir

    root = bridge_checkout_dir()
    return "git" if root is not None and (root / "pyproject.toml").is_file() else "package"


def update_command(latest: str | None, wheel_url: str | None) -> str:
    if _install_kind() == "git":
        # The checkout path stays out of this string; grok_agent_update knows it.
        return (
            "grok_agent_update (preview), then grok_agent_update confirm=true, then restart the MCP host; "
            "manual: git pull --ff-only && python -m pip install -e ."
        )
    return f'python -m pip install --upgrade "{DISTRIBUTION} @ git+https://github.com/{REPO}@v{latest}"'


def check_for_update(
    *, force: bool = False, fetch: Fetcher | None = None, now: float | None = None
) -> dict[str, Any]:
    """Compare the installed version with the latest GitHub release. Never raises."""
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
    record = _read_cache()
    fresh = record is not None and 0 <= moment - record["checked_at"] < CACHE_TTL_SECONDS
    if force or not fresh:
        try:
            fetched = _parse_release((fetch or _default_fetch)(LATEST_RELEASE_API, TIMEOUT_SECONDS))
        except Exception:  # noqa: BLE001 -- offline, HTTP error, rate limit, bad JSON: degrade quietly
            fetched = None
        if fetched is not None:
            record = {"checked_at": moment, **fetched}
            _write_cache(record)
            result["source"] = "github"
        elif record is not None:
            result["source"] = "stale_cache"
    else:
        result["source"] = "cache"
    if record is None:
        return result
    newer = _is_newer(record["latest_version"], current)
    result.update(
        latest_version=record["latest_version"],
        update_available=newer,
        status="update_available" if newer else "up_to_date",
        checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record["checked_at"])),
        release_notes_url=record.get("release_url") or RELEASES_URL,
        update_command=update_command(record["latest_version"], record.get("wheel_url")) if newer else None,
    )
    return result


def cached_update_hint() -> str | None:
    """One-line startup hint from the cache only; never touches the network."""
    if disabled():
        return None
    record = _read_cache()
    if record is None or not _is_newer(record["latest_version"], __version__):
        return None
    return (
        f"Update available: {DISTRIBUTION} {__version__} -> {record['latest_version']}. "
        f"Call grok_agent_check_update for release notes and the update command; "
        "nothing is installed automatically."
    )


def refresh_in_background() -> None:
    """Refresh a stale cache on a daemon thread so startup never waits on the network."""
    if disabled():
        return
    record = _read_cache()
    if record is not None and 0 <= time.time() - record["checked_at"] < CACHE_TTL_SECONDS:
        return
    threading.Thread(target=check_for_update, name="update-check", daemon=True).start()
