"""The bridge notices when it lags its remote, and never guesses that it does not.

Three copies of the code exist at once -- GitHub, the checkout, the loaded
process -- and nothing reconciled them, so a landed fix could look unfixed
because it never reached the process. Checking is cheap; applying is confirmed.
"""

from __future__ import annotations

from pathlib import Path

from grok_delegate.server import handle_tool_call, list_tools
from grok_delegate.updater import (
    behind_count,
    bridge_checkout_dir,
    checkout_is_dirty,
    local_head,
    plan_update,
    remote_head,
    update_status,
)

LOCAL = "a" * 40
REMOTE = "b" * 40


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(mapping, *, raises=None):
    """A fake git keyed on the git verb, so no test needs git or a network."""

    def run(argv, **_kwargs):
        if raises is not None:
            raise raises
        for verb, result in mapping.items():
            if verb in argv:
                return result
        return _Result(returncode=1, stderr="unexpected argv")

    return run


# --- finding the checkout -----------------------------------------------------


def test_checkout_is_found_from_a_git_directory(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    assert bridge_checkout_dir(nested) == tmp_path


def test_checkout_is_found_when_git_is_a_file(tmp_path) -> None:
    """A worktree carries .git as a file; treating it as absent would be wrong."""
    (tmp_path / ".git").write_text("gitdir: ../real/.git", encoding="utf-8")
    assert bridge_checkout_dir(tmp_path) == tmp_path


def test_no_checkout_is_a_legitimate_answer(tmp_path) -> None:
    assert bridge_checkout_dir(tmp_path / "nowhere-at-all") is None


# --- reading the two sides ----------------------------------------------------


def test_local_head_reads_the_sha(tmp_path) -> None:
    runner = _runner({"rev-parse": _Result(stdout=LOCAL + "\n")})
    assert local_head(tmp_path, git_runner=runner) == LOCAL


def test_local_head_is_unknown_when_git_fails(tmp_path) -> None:
    runner = _runner({"rev-parse": _Result(returncode=128, stderr="not a repository")})
    assert local_head(tmp_path, git_runner=runner) is None


def test_remote_head_parses_a_real_ls_remote_line(tmp_path) -> None:
    runner = _runner({"ls-remote": _Result(stdout=f"{REMOTE}\trefs/heads/main\n")})
    assert remote_head(tmp_path, git_runner=runner) == REMOTE


def test_remote_head_is_unknown_when_the_remote_is_unreachable(tmp_path) -> None:
    runner = _runner({"ls-remote": _Result(returncode=128, stderr="could not read")})
    assert remote_head(tmp_path, git_runner=runner) is None


def test_checking_never_fetches(tmp_path) -> None:
    """Checking for an update must not mutate the operator's checkout."""
    seen: list[list[str]] = []

    def run(argv, **_kwargs):
        seen.append(list(argv))
        return _Result(stdout=f"{REMOTE}\trefs/heads/main\n")

    remote_head(tmp_path, git_runner=run)
    assert all("fetch" not in argv for argv in seen)
    assert all("pull" not in argv for argv in seen)


# --- the verdict --------------------------------------------------------------


def test_update_is_available_when_the_two_sha_differ(tmp_path) -> None:
    runner = _runner(
        {
            "rev-parse": _Result(stdout=LOCAL + "\n"),
            "ls-remote": _Result(stdout=f"{REMOTE}\trefs/heads/main\n"),
            "rev-list": _Result(stdout="3\n"),
        }
    )
    status = update_status(git_runner=runner, checkout=tmp_path)
    assert status["available"] is True
    assert status["behind"] == 3
    assert status["reason"] is None


def test_no_update_when_the_sha_match(tmp_path) -> None:
    runner = _runner(
        {
            "rev-parse": _Result(stdout=LOCAL + "\n"),
            "ls-remote": _Result(stdout=f"{LOCAL}\trefs/heads/main\n"),
        }
    )
    status = update_status(git_runner=runner, checkout=tmp_path)
    assert status["available"] is False
    assert status["reason"] is None


def test_an_unreachable_remote_never_reads_as_up_to_date(tmp_path) -> None:
    """"Cannot tell" and "up to date" must not be the same answer."""
    runner = _runner(
        {
            "rev-parse": _Result(stdout=LOCAL + "\n"),
            "ls-remote": _Result(returncode=128, stderr="offline"),
        }
    )
    status = update_status(git_runner=runner, checkout=tmp_path)
    assert status["available"] is False
    assert status["reason"] == "REMOTE_UNREACHABLE"


def test_a_raising_git_does_not_escape(tmp_path) -> None:
    status = update_status(git_runner=_runner({}, raises=OSError("git missing")), checkout=tmp_path)
    assert status["available"] is False
    assert status["reason"] == "LOCAL_HEAD_UNKNOWN"


def test_no_checkout_is_reported_not_raised(monkeypatch) -> None:
    """A wheel install has no checkout to compare; that is an answer, not a crash."""
    import grok_delegate.updater as updater

    monkeypatch.setattr(updater, "bridge_checkout_dir", lambda *a, **k: None)
    status = updater.update_status(git_runner=_runner({}), checkout=None)
    assert status["reason"] == "NO_CHECKOUT"
    assert status["available"] is False
    assert status["checkout"] is None


def test_behind_count_is_optional(tmp_path) -> None:
    """An unfetched remote commit is normal, not a failure."""
    runner = _runner({"rev-list": _Result(returncode=128, stderr="unknown revision")})
    assert behind_count(tmp_path, REMOTE, git_runner=runner) is None


# --- guarding the apply step --------------------------------------------------


def test_a_dirty_checkout_is_detected(tmp_path) -> None:
    runner = _runner({"status": _Result(stdout=" M grok_delegate/server.py\n")})
    assert checkout_is_dirty(tmp_path, git_runner=runner) is True


def test_a_clean_checkout_is_detected(tmp_path) -> None:
    assert checkout_is_dirty(tmp_path, git_runner=_runner({"status": _Result(stdout="")})) is False


def test_the_plan_names_both_steps_and_the_restart(tmp_path) -> None:
    plan = plan_update({"checkout": str(tmp_path)}, python_executable="python")
    whats = " ".join(step["what"] for step in plan["steps"])
    assert "pull" in whats and "reinstall" in whats
    assert "restart" in plan["then"]


# --- the tool -----------------------------------------------------------------


def test_update_tool_is_listed() -> None:
    assert "grok_agent_update" in {tool["name"] for tool in list_tools()}


def test_update_tool_previews_without_confirm() -> None:
    """Preview must never mutate anything, whatever the answer turns out to be."""
    result = handle_tool_call("grok_agent_update", {})
    assert result["applied"] is False
    assert "plan" in result
    assert result["plan"]["steps"]


def test_update_tool_rejects_unknown_arguments() -> None:
    result = handle_tool_call("grok_agent_update", {"force": True})
    assert result["ok"] is False
    assert result["error"] == "ARGUMENTS_UNKNOWN"


def test_status_carries_the_update_block() -> None:
    from grok_delegate.status import update_report

    report = update_report()
    assert "available" in report
    assert isinstance(report["available"], bool)


# --- the output ceiling must follow the turn budget ---------------------------


def test_output_cap_scales_with_the_turn_budget() -> None:
    """A fixed ceiling turned a long, healthy job into ACP_OUTPUT_LIMIT.

    Found by running a real `max` preset job: 40 turns of xhigh reasoning passed
    1MB of legitimate output and the bridge threw away every edit it had made.
    """
    from grok_delegate.acp import DEFAULT_OUTPUT_BYTES, output_cap_for

    assert output_cap_for({"max_turns": 5}) == DEFAULT_OUTPUT_BYTES
    assert output_cap_for({"max_turns": 40}) > output_cap_for({"max_turns": 12})
    assert output_cap_for({"max_turns": 40}) >= 8_000_000


def test_an_explicit_cap_is_never_widened() -> None:
    """A caller that named a cap meant it; scaling past it would break the guard."""
    from grok_delegate.acp import output_cap_for

    assert output_cap_for({"max_turns": 40}, configured=16_384) == 16_384


def test_a_missing_turn_budget_falls_back_to_the_default() -> None:
    from grok_delegate.acp import DEFAULT_OUTPUT_BYTES, output_cap_for

    assert output_cap_for({}) == DEFAULT_OUTPUT_BYTES
    assert output_cap_for({"max_turns": "nonsense"}) == DEFAULT_OUTPUT_BYTES
