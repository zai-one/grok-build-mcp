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


def _checkout(root: Path, *, name: str = "grok-delegate", git_file: bool = False) -> Path:
    """A minimal source checkout of this package under ``root``; returns its __init__.py."""
    root.mkdir(parents=True, exist_ok=True)
    if git_file:
        (root / ".git").write_text("gitdir: ../real/.git", encoding="utf-8")
    else:
        (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(
        f'[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "{name}"\nversion = "1.0"\n',
        encoding="utf-8",
    )
    package = root / "grok_delegate"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    return package / "__init__.py"


def test_the_real_checkout_is_found(tmp_path) -> None:
    init = _checkout(tmp_path / "grok-build-mcp")
    assert bridge_checkout_dir(package_init=init) == tmp_path / "grok-build-mcp"
    nested = init.parent / "sub"
    nested.mkdir()
    assert bridge_checkout_dir(nested, package_init=init) == tmp_path / "grok-build-mcp"


def test_checkout_is_found_when_git_is_a_file(tmp_path) -> None:
    """A worktree carries .git as a file; treating it as absent would be wrong."""
    init = _checkout(tmp_path / "lane", git_file=True)
    assert bridge_checkout_dir(package_init=init) == tmp_path / "lane"


def test_a_venv_install_inside_a_foreign_repo_is_not_a_checkout(tmp_path) -> None:
    """pip-installed into another project's venv: the first .git up is not ours."""
    other = tmp_path / "other-project"
    (other / ".git").mkdir(parents=True)
    (other / "pyproject.toml").write_text('[project]\nname = "other-project"\n', encoding="utf-8")
    site = other / ".venv" / "lib" / "python3.13" / "site-packages" / "grok_delegate"
    site.mkdir(parents=True)
    (site / "__init__.py").write_text("", encoding="utf-8")
    assert bridge_checkout_dir(package_init=site / "__init__.py") is None


def test_a_bridge_clone_whose_venv_holds_a_pip_copy_is_not_this_checkout(tmp_path) -> None:
    """Right name, wrong files: the running package is the venv copy, not the clone's."""
    root = tmp_path / "grok-build-mcp"
    _checkout(root)
    site = root / ".venv" / "lib" / "site-packages" / "grok_delegate"
    site.mkdir(parents=True)
    (site / "__init__.py").write_text("", encoding="utf-8")
    assert bridge_checkout_dir(package_init=site / "__init__.py") is None


def test_a_checkout_of_another_distribution_is_rejected(tmp_path) -> None:
    init = _checkout(tmp_path / "fork", name="something-else")
    assert bridge_checkout_dir(package_init=init) is None


def test_no_checkout_is_a_legitimate_answer(tmp_path) -> None:
    assert bridge_checkout_dir(tmp_path / "nowhere-at-all") is None
    loose = tmp_path / "loose" / "grok_delegate"
    loose.mkdir(parents=True)
    (loose / "__init__.py").write_text("", encoding="utf-8")
    assert bridge_checkout_dir(package_init=loose / "__init__.py") is None


def test_the_running_package_is_judged_by_its_own_files() -> None:
    """Whatever the install, the answer is either None or a root holding this very package."""
    import grok_delegate

    root = bridge_checkout_dir()
    if root is not None:
        assert (root / "grok_delegate" / "__init__.py").resolve() == Path(grok_delegate.__file__).resolve()


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


def test_update_tool_previews_without_confirm(monkeypatch) -> None:
    """Preview must never mutate anything, whatever the answer turns out to be."""
    import grok_delegate.updater as updater

    # A fake git: the real one would ls-remote GitHub from a test.
    monkeypatch.setattr(
        updater,
        "default_git_runner",
        _runner({"rev-parse": _Result(stdout=LOCAL + "\n"), "ls-remote": _Result(stdout=f"{REMOTE}\trefs/heads/main\n")}),
    )
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


def test_the_opt_out_keeps_status_off_the_network(monkeypatch) -> None:
    """GROK_DELEGATE_DISABLE_UPDATE_CHECK also stops the status call's ls-remote."""
    import grok_delegate.updater as updater
    from grok_delegate.status import update_report

    seen: list[list[str]] = []

    def run(argv, **_kwargs):
        seen.append(list(argv))
        return _Result(stdout=LOCAL + "\n")

    monkeypatch.setenv("GROK_DELEGATE_DISABLE_UPDATE_CHECK", "1")
    monkeypatch.setattr(updater, "default_git_runner", run)
    monkeypatch.setattr(updater, "bridge_checkout_dir", lambda *a, **k: Path("checkout"))
    report = update_report()
    assert report["available"] is False and report["reason"] == "REMOTE_CHECK_DISABLED"
    assert all("ls-remote" not in argv for argv in seen)


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


def test_the_project_name_is_read_without_tomllib(tmp_path, monkeypatch) -> None:
    """Python 3.10 has no tomllib; the [project] name must still be found, and only there."""
    import sys

    from grok_delegate.updater import _declared_name

    monkeypatch.setitem(sys.modules, "tomllib", None)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.other]\nname = "decoy"\n\n[project]\nversion = "1"\nname = "grok-delegate"\n\n[tool.x]\nname = "y"\n',
        encoding="utf-8",
    )
    assert _declared_name(pyproject) == "grok-delegate"
    pyproject.write_text('[tool.other]\nname = "grok-delegate"\n', encoding="utf-8")
    assert _declared_name(pyproject) is None
