"""Does the running bridge lag the repository it was installed from?

The server runs from an editable install of a git checkout, so three copies of
the code exist at once: GitHub, that checkout, and the process already loaded in
memory. Nothing reconciles them, and the failure is silent -- a fixed bug appears
unfixed because the fix never reached the process. This module answers the
question; applying the answer is a separate, confirmed step.

Everything here degrades to "unknown" instead of raising. Being unable to check
for an update is not a problem the caller should have to catch, and it must never
be the reason a job fails.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

#: Seconds any single git call may take. A slow or hanging network must not
#: become a slow status call.
GIT_TIMEOUT_SECONDS = 8.0

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

GitRunner = Callable[[list[str]], Any]


def default_git_runner(argv: list[str], *, timeout: float = GIT_TIMEOUT_SECONDS) -> Any:
    """Run git, capturing output. Injectable so unit tests never touch git."""
    return subprocess.run(  # noqa: S603 - argv is built here, never from a caller
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def bridge_checkout_dir(start: Path | str | None = None) -> Path | None:
    """The git checkout this package was imported from, or None.

    None is a legitimate answer: a wheel install has no checkout to compare, and
    reporting that honestly beats guessing at a directory.
    """
    if start is None:
        start = Path(__file__).resolve().parent
    here = Path(start).resolve()
    for candidate in (here, *here.parents):
        marker = candidate / ".git"
        # A worktree carries `.git` as a file, not a directory.
        if marker.is_dir() or marker.is_file():
            return candidate
    return None


def _stdout(result: Any) -> str:
    if result is None:
        return ""
    if getattr(result, "returncode", 1) != 0:
        return ""
    return str(getattr(result, "stdout", "") or "")


def local_head(checkout: Path | str, *, git_runner: GitRunner) -> str | None:
    """SHA the checkout is on, or None when git cannot say."""
    try:
        result = git_runner(["git", "-C", str(checkout), "rev-parse", "HEAD"])
    except Exception:
        return None
    text = _stdout(result).strip()
    return text if _SHA_RE.match(text) else None


def remote_head(
    checkout: Path | str,
    *,
    git_runner: GitRunner,
    remote: str = "origin",
    branch: str = "main",
) -> str | None:
    """SHA the remote branch is on, or None when the remote cannot be reached.

    Uses ``ls-remote`` rather than ``fetch`` on purpose: checking for an update
    must not mutate the operator's checkout.
    """
    try:
        result = git_runner(["git", "-C", str(checkout), "ls-remote", "--heads", remote, branch])
    except Exception:
        return None
    for line in _stdout(result).splitlines():
        head = line.split("\t")[0].strip() if "\t" in line else line.split()[0:1]
        candidate = head if isinstance(head, str) else (head[0] if head else "")
        if _SHA_RE.match(candidate):
            return candidate
    return None


def behind_count(
    checkout: Path | str, remote_sha: str, *, git_runner: GitRunner
) -> int | None:
    """How many commits the checkout is behind, when the object is known locally.

    None means "cannot tell" -- typically the remote commit has not been fetched,
    which is normal and not worth a failure.
    """
    try:
        result = git_runner(
            ["git", "-C", str(checkout), "rev-list", "--count", f"HEAD..{remote_sha}"]
        )
    except Exception:
        return None
    text = _stdout(result).strip()
    try:
        return int(text)
    except ValueError:
        return None


def update_status(
    *,
    git_runner: GitRunner | None = None,
    checkout: Path | str | None = None,
    remote: str = "origin",
    branch: str = "main",
) -> dict[str, Any]:
    """Whether the running bridge is behind its remote.

    ``available`` is True only when both SHAs are known and differ, so an
    unreachable network reads as "cannot tell", never as "up to date".
    """
    runner = git_runner or default_git_runner
    root = checkout if checkout is not None else bridge_checkout_dir()
    out: dict[str, Any] = {
        "checkout": str(root) if root else None,
        "local_sha": None,
        "remote_sha": None,
        "behind": None,
        "available": False,
        "reason": None,
    }
    if root is None:
        out["reason"] = "NO_CHECKOUT"
        return out

    out["local_sha"] = local_head(root, git_runner=runner)
    if out["local_sha"] is None:
        out["reason"] = "LOCAL_HEAD_UNKNOWN"
        return out

    out["remote_sha"] = remote_head(root, git_runner=runner, remote=remote, branch=branch)
    if out["remote_sha"] is None:
        out["reason"] = "REMOTE_UNREACHABLE"
        return out

    if out["local_sha"] != out["remote_sha"]:
        out["available"] = True
        out["behind"] = behind_count(root, out["remote_sha"], git_runner=runner)
    return out


def checkout_is_dirty(checkout: Path | str, *, git_runner: GitRunner) -> bool | None:
    """True when the checkout has local modifications, None when git cannot say.

    Consulted before pulling: overwriting someone's uncommitted work to install
    an update would be a far worse outcome than staying a version behind.
    """
    try:
        result = git_runner(["git", "-C", str(checkout), "status", "--porcelain"])
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    return bool(str(getattr(result, "stdout", "") or "").strip())


def plan_update(status: Mapping[str, Any], *, python_executable: str) -> dict[str, Any]:
    """The exact steps an update would run, for the operator to approve."""
    checkout = status.get("checkout")
    return {
        "steps": [
            {"what": "pull the checkout", "argv": ["git", "-C", str(checkout), "pull", "--ff-only"]},
            {
                "what": "reinstall the package",
                "argv": [python_executable, "-m", "pip", "install", "-e", str(checkout)],
            },
        ],
        # The server cannot restart itself; the host owns the process.
        "then": "restart the MCP host so the running process picks up the new code",
    }
