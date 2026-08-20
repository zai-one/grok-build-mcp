"""Work the bridge refused that it should have accepted.

A gate that fails closed is right to be strict, but a gate that is strict for
the wrong reason costs the operator a whole delegation cycle and reads exactly
like a real refusal. These are the three shapes an audit found: a new directory,
a path spelled in a different case, and evidence that arrives empty.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from grok_delegate import agent_runtime
from grok_delegate.contracts import finalize_receipt, validate_task_packet
from grok_delegate.runner import collect_diff


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)


def _seed(root: Path) -> str:
    _git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (root / "test_acceptance.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.invalid", "commit", "-q", "-m", "seed")
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


# --- a file in a directory git has never seen ---------------------------------


def test_a_new_directory_is_reported_as_its_files(tmp_path) -> None:
    """`git status --porcelain` collapses an untracked tree to `?? src/`.

    The worker was asked for `src/app.py`, delivered exactly that, and the
    receipt reported a change to `src` -- a path nobody expected -- so the job
    was blocked for doing what it was told.
    """
    base = _seed(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")

    changed = collect_diff(tmp_path, base_ref=base)["changed_files"]
    assert "src/app.py" in changed
    assert "src/" not in changed and "src" not in changed


def test_the_collapsed_form_also_hid_a_second_file(tmp_path) -> None:
    """The dangerous half: one `?? src/` covered every file inside it."""
    base = _seed(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "src" / "sneaky.py").write_text("exfiltrate()\n", encoding="utf-8")

    changed = collect_diff(tmp_path, base_ref=base)["changed_files"]
    assert sorted(p for p in changed if p.startswith("src/")) == ["src/app.py", "src/sneaky.py"]


def test_a_nested_artifact_is_accepted_end_to_end(monkeypatch, tmp_path) -> None:
    class _Worker:
        name = "stdio"

        def run(self, _task, **kwargs):
            target = Path(kwargs["cwd"]) / "src"
            target.mkdir(exist_ok=True)
            (target / "app.py").write_text("print(1)\n", encoding="utf-8")
            return {
                "status": "completed",
                "session_id": "nested",
                "summary": "made src/app.py",
                "tests": [],
                "events": [],
                "worker_alive_after_shutdown": False,
            }

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(tmp_path / "lanes"))
    _seed(root)

    task = validate_task_packet(
        {
            "objective": "create src/app.py",
            "role": "execute",
            "project_root": str(root),
            "permission_profile": "workspace",
            "max_turns": 5,
            "timeout_seconds": 120,
            "inputs": [],
            "constraints": [],
            "acceptance_criteria": [],
            "expected_artifacts": ["src/app.py"],
            "correlation_id": "nested-artifact",
            "test_commands": [f"{sys.executable} -m pytest -q"],
        },
        allowed_roots=[root],
    )
    receipt = agent_runtime.run_task(
        task,
        transport="stdio",
        lane="nested-artifact",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": _Worker()}),
        cancel_event=threading.Event(),
    )
    assert receipt["status"] == "completed", receipt.get("blocked_reason")
    assert receipt["changed_files"] == ["src/app.py"]


# --- one file, two spellings ---------------------------------------------------


def _task(**overrides):
    value = {
        "objective": "make the change",
        "role": "execute",
        "expected_artifacts": ["EXPECTED.TXT"],
        "test_commands": ["python -m pytest -q"],
    }
    value.update(overrides)
    return value


def _receipt(**overrides):
    value = {
        "status": "completed",
        "changed_files": ["expected.txt"],
        "full_changed_files": ["expected.txt"],
        "artifacts": ["EXPECTED.TXT"],
        "tests": [
            {
                "command": "python -m pytest -q",
                "passed": True,
                "returncode": 0,
                "source": "bridge-verifier",
            }
        ],
    }
    value.update(overrides)
    return value


@pytest.mark.skipif(
    not (sys.platform == "win32" or sys.platform == "darwin"),
    reason="case folding only applies where the filesystem folds case",
)
def test_one_file_under_two_spellings_is_one_file() -> None:
    """git reports the index spelling; the task asked in another case.

    Compared as strings, the same file arrived as both a missing artifact and
    somebody else's change, and the job was blocked twice over for it.
    """
    out = finalize_receipt(_receipt(), _task())
    assert out["status"] == "completed", out.get("blocked_reason")


def test_a_genuinely_different_file_is_still_unexpected() -> None:
    out = finalize_receipt(_receipt(full_changed_files=["expected.txt", "other.txt"]), _task())
    assert out["status"] == "blocked"
    assert "other.txt" in out["blocked_reason"]


# --- evidence for a file git has not tracked yet -------------------------------


def test_a_created_file_arrives_with_its_contents(monkeypatch, tmp_path) -> None:
    """`git diff` cannot show an untracked file, and "create this file" is the
    most ordinary execute there is -- so the receipt for it carried an empty
    diff beside a non-empty changed_files."""

    class _Worker:
        name = "stdio"

        def run(self, _task, **kwargs):
            (Path(kwargs["cwd"]) / "expected.txt").write_text(
                "BRAND NEW CONTENT\n", encoding="utf-8"
            )
            return {
                "status": "completed",
                "session_id": "new-file",
                "summary": "created it",
                "tests": [],
                "events": [],
                "worker_alive_after_shutdown": False,
            }

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("GROK_DELEGATE_LANES_PARENT", str(tmp_path / "lanes"))
    _seed(root)

    task = validate_task_packet(
        {
            "objective": "create expected.txt",
            "role": "execute",
            "project_root": str(root),
            "permission_profile": "workspace",
            "max_turns": 5,
            "timeout_seconds": 120,
            "inputs": [],
            "constraints": [],
            "acceptance_criteria": [],
            "expected_artifacts": ["expected.txt"],
            "correlation_id": "new-file-diff",
            "test_commands": [f"{sys.executable} -m pytest -q"],
        },
        allowed_roots=[root],
    )
    receipt = agent_runtime.run_task(
        task,
        transport="stdio",
        lane="new-file-diff",
        router=agent_runtime.TransportRouter(grok_bin="grok", adapters={"stdio": _Worker()}),
        cancel_event=threading.Event(),
    )
    assert receipt["status"] == "completed", receipt.get("blocked_reason")
    assert receipt["lane_commit"]["committed"] is True
    assert "BRAND NEW CONTENT" in receipt["unified_diff"], "the host must be able to read the work"


# --- what the 2026-08-20 audit routine found in the command gate ----------------
#
# `py -3 scripts/routines.py --only audit.security` handed Grok the gate and
# asked it to find a spelling that gets past it, and a legitimate operation it
# refuses by mistake. Everything below is the second kind, reproduced before it
# was believed. A denied test command is not a harmless no: the denial can end
# the worker's turn, so the job pays for the whole cycle and returns nothing.


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    for name in ("test_app.py", "test_form.py", "test_transform.py", "model.py",
                 "test_ssh.py", "test_oauth.py", "test_curl.py", "test_credentials.py"):
        (tmp_path / "tests" / name).write_text("", encoding="utf-8")
    return tmp_path.resolve()


@pytest.mark.parametrize(
    "path",
    ["tests/test_form.py", "tests/test_transform.py", "tests/model.py",
     "tests/test_ssh.py", "tests/test_oauth.py", "tests/test_curl.py",
     "tests/test_credentials.py"],
)
def test_an_ordinary_filename_is_not_a_forbidden_command(path: str, worktree: Path) -> None:
    """`rm`, `del`, `ssh` and `oauth` were matched anywhere in the string.

    So `pytest tests/test_form.py` was refused for containing `rm`, and
    `tests/model.py` for containing `del`. Both are names any repository has.
    """
    from grok_delegate.acp import _command_allowed

    assert _command_allowed(f"py -3 -m pytest {path}", worktree) is True, path


@pytest.mark.parametrize(
    "command",
    ["rm -rf tests", "rmdir tests", "del tests", "ssh user@host", "curl http://x",
     "wget http://x", "git push origin main", "git reset --hard"],
)
def test_a_forbidden_command_is_still_forbidden(command: str, worktree: Path) -> None:
    from grok_delegate.acp import _command_allowed

    assert _command_allowed(command, worktree) is False, command


@pytest.mark.parametrize(
    "command",
    ["git show HEAD:.env", "Get-Content id_rsa", "Get-Content key.pem",
     "Get-Content auth.json", "Get-Content credentials.json", "Get-Content .npmrc"],
)
def test_a_secret_by_name_is_still_refused(command: str, worktree: Path) -> None:
    """Anchoring the command names must not loosen the file names beside them."""
    from grok_delegate.acp import _command_allowed

    assert _command_allowed(command, worktree) is False, command


def test_the_windows_launcher_may_name_a_minor_version(worktree: Path) -> None:
    """`py -3` passed and `py -3.12` did not, which is how an operator pins one."""
    from grok_delegate.acp import _command_allowed

    assert _command_allowed("py -3.12 -m pytest tests", worktree) is True


def test_a_directory_is_not_a_planted_interpreter(worktree: Path) -> None:
    """argv[0] is refused when the worker could have written that file.

    The check asked whether the name `exists`, and a *directory* named `go` --
    which no operating system will execute -- made `go test ./...` look like a
    worker handing the verifier its own binary.
    """
    from grok_delegate.acp import _command_allowed

    (worktree / "go").mkdir()
    assert _command_allowed("go test ./...", worktree) is True


def test_a_real_planted_interpreter_is_still_refused(worktree: Path) -> None:
    """The inverted rule that matters: argv[0] must stay OUT of the worktree."""
    from grok_delegate.acp import _command_allowed

    (worktree / "go").write_text("#!/bin/sh\n", encoding="utf-8")
    assert _command_allowed("go test ./...", worktree) is False


def test_a_path_hidden_one_equals_further_along_is_still_confined(worktree: Path) -> None:
    """`-o addopts=--rootdir=../outside` split once into another flag.

    The value still started with `-`, so it was skipped as "not a path" while the
    real path sat one `=` further along.
    """
    from grok_delegate.acp import _command_allowed

    assert _command_allowed(
        "py -3 -m pytest -o addopts=--rootdir=../outside", worktree
    ) is False


# --- the turn a refused write costs -----------------------------------------------


def test_a_read_only_worker_is_told_it_cannot_write() -> None:
    """The one failure no gate change can prevent, so the prompt has to.

    `evidence/live-acp/session-permission-cancel.jsonl`, captured on the
    operator's own machine: the worker asked to edit a file, the bridge answered
    `reject_once` -- the correct, non-fatal answer -- and the CLI sent
    `session/cancel` regardless. `stopReason` came back `cancelled`, the receipt
    said `ACP_STOP_cancelled`, and the job delivered one opening sentence while
    the CLI's own log recorded a successful inference.

    A read-only role can never legally write, so every write it attempts costs
    the whole turn. The only defence is that it never reaches for one.
    """
    from grok_delegate.contracts import build_prompt

    for role in ("consult", "skeptic"):
        prompt = build_prompt({
            "objective": "Review this.", "role": role, "permission_profile": "read-only",
        })
        assert "cannot create, modify" in prompt, role
        assert "ends the whole turn" in prompt, role
        assert "IS the deliverable" in prompt, role


def test_a_write_role_is_not_told_it_cannot_write() -> None:
    from grok_delegate.contracts import build_prompt

    prompt = build_prompt({
        "objective": "Add mul.", "role": "execute", "permission_profile": "workspace",
        "expected_artifacts": ["app.py"], "test_commands": ["py -3 -m pytest tests -q"],
    })
    assert "cannot create, modify" not in prompt
    assert "Make the requested file change now." in prompt
