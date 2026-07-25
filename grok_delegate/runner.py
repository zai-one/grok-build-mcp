"""I/O wrapper for worktree prep, headless grok spawn, and diff collection.

Git and subprocess are injectable callables so unit tests never mutate the host
or spawn a real grok binary. Zero push/merge code paths (B4).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from .guard import (
        ALWAYS_APPROVE_FLAG,
        DEFAULT_GROK_BIN,
        HARD_CAP_MAX_TURNS,
        GuardError,
        assert_argv_safe,
        build_grok_argv,
        build_permission_profile,
        confine_path_to_root,
        enforce_bounds,
        normalize_lane,
        structured_error,
        validate_grok_bin,
    )
except ImportError:  # flat import when package dir is on sys.path
    from guard import (  # type: ignore
        ALWAYS_APPROVE_FLAG,
        DEFAULT_GROK_BIN,
        HARD_CAP_MAX_TURNS,
        GuardError,
        assert_argv_safe,
        build_grok_argv,
        build_permission_profile,
        confine_path_to_root,
        enforce_bounds,
        normalize_lane,
        structured_error,
        validate_grok_bin,
    )

# Default wall-clock timeout for a single delegation (seconds).
DEFAULT_TIMEOUT_SECONDS = 900

# Cap on captured stdout/stderr size (chars) before truncation in result.
DEFAULT_OUTPUT_CHAR_CAP = 200_000

# Forbidden git subcommands — never assembled by this module.
_FORBIDDEN_GIT_VERBS = frozenset(
    {
        "push",
        "merge",
        "pull",
        "rebase",
        "cherry-pick",
        "reset",
        "clean",
    }
)

GitRunner = Callable[[Sequence[str], Path | None, float], dict[str, Any]]
SubprocessRunner = Callable[[Sequence[str], Path | None, float], dict[str, Any]]
WhichFn = Callable[[str], str | None]


@dataclass
class RunnerConfig:
    """Runtime knobs for delegation (all host-local, no secrets)."""

    repo_root: Path
    lanes_parent: Path | None = None
    grok_bin: str = DEFAULT_GROK_BIN
    base_ref: str = "origin/dev"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    hard_cap_turns: int = HARD_CAP_MAX_TURNS
    output_char_cap: int = DEFAULT_OUTPUT_CHAR_CAP
    principal: str = "local-dev"


@dataclass
class DelegationResult:
    ok: bool
    lane: str = ""
    branch: str = ""
    worktree_path: str = ""
    turns_used: int | None = None
    status: str = ""
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    diffstat: str = ""
    error: str | None = None
    message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "lane": self.lane,
            "branch": self.branch,
            "worktree_path": self.worktree_path,
            "turns_used": self.turns_used,
            "status": self.status,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "diffstat": self.diffstat,
        }
        if self.error is not None:
            d["error"] = self.error
        if self.message is not None:
            d["message"] = self.message
        if self.extra:
            d["extra"] = dict(self.extra)
        return d


def default_git_runner(
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Real git subprocess (production path). Tests inject a mock instead."""
    import subprocess

    _reject_forbidden_git_args(args)
    cmd = ["git", *[str(a) for a in args]]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            # Decode as UTF-8, never the Windows locale codepage: git output can
            # carry non-cp1252 bytes (branch/file names), which would raise
            # UnicodeDecodeError in the reader thread and lose the result.
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
        return {
            "args": cmd,
            "returncode": 124,
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": f"timeout after {timeout}s",
            "timedOut": True,
        }
    except FileNotFoundError:
        return {
            "args": cmd,
            "returncode": 127,
            "stdout": "",
            "stderr": "git not found",
            "timedOut": False,
            "missing": True,
        }


def default_subprocess_runner(
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Real subprocess for grok spawn (production path)."""
    import subprocess

    _reject_always_approve(args)
    cmd = [str(a) for a in args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            # Grok emits UTF-8 (goal text, summaries, box drawing). Decoding with
            # the Windows locale codepage crashes the reader thread on the first
            # non-cp1252 byte and drops the whole delegation result.
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
        return {
            "args": cmd,
            "returncode": 124,
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": f"timeout after {timeout}s",
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


def _reject_forbidden_git_args(args: Sequence[str]) -> None:
    """Fail closed if caller tries to assemble push/merge via this module."""
    tokens = [str(a).lower() for a in args]
    # Skip global git options before the verb.
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in {"-c", "-C"} and i + 1 < len(tokens):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        verb = t
        if verb in _FORBIDDEN_GIT_VERBS:
            raise GuardError(
                "GIT_VERB_FORBIDDEN",
                f"git {verb} is forbidden in grok-delegate runner",
            )
        break


def _reject_always_approve(args: Sequence[str]) -> None:
    for a in args:
        s = str(a)
        if s == ALWAYS_APPROVE_FLAG or s.startswith(f"{ALWAYS_APPROVE_FLAG}="):
            raise GuardError(
                "ALWAYS_APPROVE_FORBIDDEN",
                "subprocess argv must never include --always-approve",
            )


def resolve_lanes_parent(repo_root: Path, lanes_parent: Path | None = None) -> Path:
    """Default: sibling ``pcp-lanes`` directory next to the main repo."""
    if lanes_parent is not None:
        return Path(lanes_parent).resolve()
    return (Path(repo_root).resolve().parent / "pcp-lanes")


def worktree_path_for_lane(lanes_parent: Path, lane: str) -> Path:
    """Map ``grok/<slug>`` → ``<lanes_parent>/<slug>``."""
    slug = lane.split("/", 1)[-1]
    return (Path(lanes_parent) / slug).resolve()


def is_path_inside(child: Path, parent: Path) -> bool:
    """True if child resolves inside parent (or is parent)."""
    try:
        child_r = Path(child).resolve()
        parent_r = Path(parent).resolve()
        child_r.relative_to(parent_r)
        return True
    except (ValueError, OSError):
        return False


def prepare_worktree(
    *,
    repo_root: Path,
    lane: str,
    base_ref: str = "origin/dev",
    lanes_parent: Path | None = None,
    git_runner: GitRunner | None = None,
    timeout: float = 60.0,
    require_clean_base: bool = True,
) -> dict[str, Any]:
    """Create isolated worktree on grok/* branch off base_ref (fail-closed).

    Rejects: missing git, dirty main tree (when require_clean_base), target path
    inside the main repo working tree, reserved lanes (via normalize_lane).
    Does not spawn grok. Does not push or merge.
    """
    git = git_runner or default_git_runner
    root = Path(repo_root).resolve()

    try:
        normalized = normalize_lane(lane)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    parent = resolve_lanes_parent(root, lanes_parent)
    target = worktree_path_for_lane(parent, normalized)

    if is_path_inside(target, root):
        return structured_error(
            "WORKTREE_INSIDE_REPO",
            "target worktree path resolves inside the main repo working tree; "
            "use an external pcp-lanes path",
            target=str(target),
            repo_root=str(root),
        )

    # Probe git availability.
    version = git(["--version"], root, timeout)
    if version.get("missing") or version.get("returncode", 1) != 0:
        return structured_error(
            "GIT_MISSING",
            "git is not available or failed --version",
            detail=(version.get("stderr") or "")[:500],
        )

    # Base reachability.
    rev = git(["rev-parse", "--verify", base_ref], root, timeout)
    if rev.get("returncode", 1) != 0:
        return structured_error(
            "BASE_UNREACHABLE",
            f"base_ref {base_ref!r} is not reachable",
            detail=(rev.get("stderr") or "")[:500],
        )

    if require_clean_base:
        dirty = git(["status", "--porcelain"], root, timeout)
        if dirty.get("returncode", 1) != 0:
            return structured_error(
                "BASE_STATUS_FAILED",
                "could not read git status on base repo",
                detail=(dirty.get("stderr") or "")[:500],
            )
        if (dirty.get("stdout") or "").strip():
            return structured_error(
                "BASE_DIRTY",
                "main repo working tree is dirty; clean or stash before preparing a lane",
            )

    # If worktree already exists at target, reuse only when on expected branch.
    if target.exists():
        existing_branch = git(
            ["-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
            root,
            timeout,
        )
        head = (existing_branch.get("stdout") or "").strip()
        if existing_branch.get("returncode") == 0 and head == normalized:
            return {
                "ok": True,
                "lane": normalized,
                "branch": normalized,
                "worktree_path": str(target),
                "base_ref": base_ref,
                "reused": True,
            }
        return structured_error(
            "WORKTREE_EXISTS_CONFLICT",
            f"path {target} exists but is not on branch {normalized}",
            head=head or None,
        )

    parent.mkdir(parents=True, exist_ok=True)

    # Prefer: worktree add -b <branch> <path> <base>
    # If branch already exists, try worktree add <path> <branch>.
    add = git(
        ["worktree", "add", "-b", normalized, str(target), base_ref],
        root,
        timeout,
    )
    if add.get("returncode", 1) != 0:
        stderr = (add.get("stderr") or "") + (add.get("stdout") or "")
        if "already exists" in stderr.lower() or "already checked out" in stderr.lower():
            add2 = git(
                ["worktree", "add", str(target), normalized],
                root,
                timeout,
            )
            if add2.get("returncode", 1) != 0:
                return structured_error(
                    "WORKTREE_CREATE_FAILED",
                    "git worktree add failed",
                    detail=(add2.get("stderr") or add.get("stderr") or "")[:800],
                )
        else:
            return structured_error(
                "WORKTREE_CREATE_FAILED",
                "git worktree add failed",
                detail=(add.get("stderr") or "")[:800],
            )

    if not target.exists():
        return structured_error(
            "WORKTREE_MISSING_AFTER_ADD",
            "worktree path missing after git worktree add",
            target=str(target),
        )

    return {
        "ok": True,
        "lane": normalized,
        "branch": normalized,
        "worktree_path": str(target),
        "base_ref": base_ref,
        "reused": False,
    }


def collect_diff(
    worktree_path: Path | str,
    *,
    git_runner: GitRunner | None = None,
    timeout: float = 60.0,
    base_ref: str | None = None,
) -> dict[str, Any]:
    """Collect changed files + diffstat + lane commits. No full patch payload.

    R6: when ``base_ref`` is given, work the executor already COMMITTED is included
    (diff/log against the base). Diffing HEAD alone reported ``changed_files: []``
    for a lane that did exactly what its rules asked — commit its work — which is
    indistinguishable from "the executor did nothing".
    """
    git = git_runner or default_git_runner
    wt = Path(worktree_path)
    commits: list[str] = []
    name_status = git(
        ["-C", str(wt), "diff", "--name-only", "HEAD"],
        None,
        timeout,
    )
    # Also include untracked? For delegation, staged+unstaged vs HEAD is enough;
    # include working tree changes with status porcelain for untracked names.
    porcelain = git(
        ["-C", str(wt), "status", "--porcelain"],
        None,
        timeout,
    )
    stat = git(
        ["-C", str(wt), "diff", "--stat", "HEAD"],
        None,
        timeout,
    )

    changed: list[str] = []
    if name_status.get("returncode") == 0:
        for line in (name_status.get("stdout") or "").splitlines():
            p = line.strip()
            if p and p not in changed:
                changed.append(p)

    if porcelain.get("returncode") == 0:
        for line in (porcelain.get("stdout") or "").splitlines():
            # "?? path" or " M path" / "M  path"
            raw = line[3:] if len(line) > 3 else line
            raw = raw.strip().strip('"')
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[-1]
            if raw and raw not in changed:
                changed.append(raw)

    diffstat = ""
    if stat.get("returncode") == 0:
        # Truncate long stats; never include full unified diff.
        diffstat = (stat.get("stdout") or "").strip()
        if len(diffstat) > 8000:
            diffstat = diffstat[:8000] + "\n…(truncated)"

    # R6: fold in work the executor already committed on the lane branch.
    if base_ref:
        base = str(base_ref)
        committed = git(
            ["-C", str(wt), "diff", "--name-only", f"{base}...HEAD"],
            None,
            timeout,
        )
        if committed.get("returncode") == 0:
            for line in (committed.get("stdout") or "").splitlines():
                p = line.strip()
                if p and p not in changed:
                    changed.append(p)

        log = git(
            ["-C", str(wt), "log", "--oneline", f"{base}..HEAD"],
            None,
            timeout,
        )
        if log.get("returncode") == 0:
            for line in (log.get("stdout") or "").splitlines():
                entry = line.strip()
                if entry:
                    commits.append(entry)

        if not diffstat:
            committed_stat = git(
                ["-C", str(wt), "diff", "--stat", f"{base}...HEAD"],
                None,
                timeout,
            )
            if committed_stat.get("returncode") == 0:
                diffstat = (committed_stat.get("stdout") or "").strip()
                if len(diffstat) > 8000:
                    diffstat = diffstat[:8000] + "\n…(truncated)"

    return {
        "ok": True,
        "changed_files": changed,
        "diffstat": diffstat,
        "commits": commits,
    }


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + "\n…(truncated)"


def _parse_grok_json(stdout: str) -> dict[str, Any]:
    """Best-effort parse of headless JSON output; never raises."""
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {"value": data}
    except json.JSONDecodeError:
        # Try last JSON object in stream.
        matches = list(re.finditer(r"\{[\s\S]*\}", text))
        for m in reversed(matches):
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    return {"raw_preview": text[:500]}


def run_delegation(
    *,
    goal: str,
    worktree_path: Path | str,
    max_turns: int | None = None,
    model: str | None = None,
    plan_only: bool = False,
    grok_bin: str = DEFAULT_GROK_BIN,
    hard_cap: int = HARD_CAP_MAX_TURNS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    output_char_cap: int = DEFAULT_OUTPUT_CHAR_CAP,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    sandbox: str | None = None,
    sandbox_enabled: bool = True,
    reasoning_effort: str | None = None,
    rules: str | None = None,
    json_schema: str | dict[str, Any] | None = None,
    no_subagents: bool = False,
    disable_web_search: bool = False,
    resume: str | bool | None = None,
    continue_session: bool = False,
    fork_session: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Spawn headless grok against worktree cwd with guarded argv.

    Fail-closed if binary missing, argv unsafe, path escapes worktree, or
    timeout. Does not push/merge. Does not use CLI ``--worktree`` (own prep).
    """
    run = subprocess_runner or default_subprocess_runner
    which_fn = which or _default_which

    try:
        # Server-side path normalization: resolve and require path is itself
        # a concrete directory path we will pin as cwd (no .. smuggling).
        wt_path = Path(worktree_path).resolve()
        # Confine the resolved path to its own parent tree? The worktree is the
        # root of confinement for child paths; the cwd itself must be absolute
        # and must not contain a trailing ".." segment that escapes after resolve
        # (resolve already collapses). Re-validate by confining relative to parent.
        wt = str(wt_path)
        # Validate bin even when caller passes through (R5 defense in depth).
        grok_bin = validate_grok_bin(grok_bin, from_client=False)
        turns = enforce_bounds(max_turns, hard_cap=hard_cap)
        profile = build_permission_profile(plan_only=plan_only)
        argv = build_grok_argv(
            goal,
            wt,
            profile,
            turns,
            model=model,
            plan_only=plan_only,
            grok_bin=grok_bin,
            hard_cap=hard_cap,
            sandbox=sandbox,
            sandbox_enabled=sandbox_enabled,
            reasoning_effort=reasoning_effort,
            rules=rules,
            json_schema=json_schema,
            no_subagents=no_subagents,
            disable_web_search=disable_web_search,
            resume=resume,
            continue_session=continue_session,
            fork_session=fork_session,
            session_id=session_id,
        )
        assert_argv_safe(argv)
        # Ensure --cwd value still resolves inside the intended worktree root.
        cwd_idx = argv.index("--cwd")
        confined = confine_path_to_root(argv[cwd_idx + 1], wt_path, field="cwd")
        argv[cwd_idx + 1] = str(confined)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    resolved = which_fn(grok_bin)
    if resolved is None and not Path(grok_bin).is_file():
        return structured_error(
            "GROK_MISSING",
            f"grok binary not found: {grok_bin}",
        )
    if resolved:
        argv = [resolved, *argv[1:]]

    started = time.monotonic()
    result = run(argv, Path(wt), timeout_seconds)
    elapsed = time.monotonic() - started

    if result.get("missing"):
        return structured_error(
            "GROK_MISSING",
            f"grok binary not found: {grok_bin}",
        )

    stdout = _truncate(str(result.get("stdout") or ""), output_char_cap)
    stderr = _truncate(str(result.get("stderr") or ""), min(output_char_cap, 20_000))
    parsed = _parse_grok_json(stdout)

    turns_used = parsed.get("turns") or parsed.get("turns_used") or parsed.get("num_turns")
    if turns_used is not None:
        try:
            turns_used = int(turns_used)
        except (TypeError, ValueError):
            turns_used = None

    summary = ""
    for key in ("summary", "result", "message", "text"):
        val = parsed.get(key)
        if isinstance(val, str) and val.strip():
            summary = val.strip()[:2000]
            break
    if not summary and stdout:
        summary = stdout.strip()[:500]

    status = "ok" if result.get("returncode") == 0 and not result.get("timedOut") else "error"
    if result.get("timedOut"):
        status = "timeout"

    return {
        "ok": status == "ok",
        "status": status,
        "turns_used": turns_used if turns_used is not None else turns,
        "summary": summary,
        "returncode": result.get("returncode"),
        "elapsed_seconds": round(elapsed, 3),
        "stdout_truncated": stdout[:2000] if status != "ok" else "",
        "stderr_truncated": stderr[:1000] if status != "ok" else "",
        "plan_only": plan_only,
        "cwd": wt,
        # Never echo full argv goal content in audit path; keep length only here.
        "goal_chars": len(goal or ""),
    }


def _default_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def delegate(
    *,
    goal: str,
    lane: str,
    repo_root: Path | str,
    base_ref: str = "origin/dev",
    max_turns: int | None = None,
    model: str | None = None,
    plan_only: bool = False,
    lanes_parent: Path | str | None = None,
    grok_bin: str = DEFAULT_GROK_BIN,
    hard_cap: int = HARD_CAP_MAX_TURNS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    require_clean_base: bool = True,
    git_runner: GitRunner | None = None,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
    sandbox: str | None = None,
    sandbox_enabled: bool = True,
    reasoning_effort: str | None = None,
    rules: str | None = None,
    json_schema: str | dict[str, Any] | None = None,
    no_subagents: bool = False,
    disable_web_search: bool = False,
    resume: str | bool | None = None,
    continue_session: bool = False,
    fork_session: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Full path: prepare worktree → run headless → collect diffstat.

    No auto-merge, no push. Own prepare_worktree (not CLI ``-w/--worktree``).
    Returns structured result for MCP layer.
    """
    root = Path(repo_root).resolve()
    parent = Path(lanes_parent).resolve() if lanes_parent else None

    prep = prepare_worktree(
        repo_root=root,
        lane=lane,
        base_ref=base_ref,
        lanes_parent=parent,
        git_runner=git_runner,
        require_clean_base=require_clean_base,
    )
    if not prep.get("ok"):
        return prep

    wt = prep["worktree_path"]
    branch = prep["branch"]
    normalized = prep["lane"]

    run_result = run_delegation(
        goal=goal,
        worktree_path=wt,
        max_turns=max_turns,
        model=model,
        plan_only=plan_only,
        grok_bin=grok_bin,
        hard_cap=hard_cap,
        timeout_seconds=timeout_seconds,
        subprocess_runner=subprocess_runner,
        which=which,
        sandbox=sandbox,
        sandbox_enabled=sandbox_enabled,
        reasoning_effort=reasoning_effort,
        rules=rules,
        json_schema=json_schema,
        no_subagents=no_subagents,
        disable_web_search=disable_web_search,
        resume=resume,
        continue_session=continue_session,
        fork_session=fork_session,
        session_id=session_id,
    )

    # Always collect diffstat when worktree exists (even on executor error).
    # R6: base_ref-aware so committed lane work is reported, not just dirty files.
    diff = collect_diff(wt, git_runner=git_runner, base_ref=base_ref)

    if not run_result.get("ok"):
        out = {
            "ok": False,
            "lane": normalized,
            "branch": branch,
            "worktree_path": wt,
            "turns_used": run_result.get("turns_used"),
            "status": run_result.get("status") or "error",
            "summary": run_result.get("summary") or run_result.get("message") or "",
            "changed_files": diff.get("changed_files") or [],
            "diffstat": diff.get("diffstat") or "",
            "commits": diff.get("commits") or [],
            "error": run_result.get("error") or "DELEGATION_FAILED",
            "message": run_result.get("message") or "headless executor failed",
        }
        return out

    return {
        "ok": True,
        "lane": normalized,
        "branch": branch,
        "worktree_path": wt,
        "turns_used": run_result.get("turns_used"),
        "status": run_result.get("status") or "ok",
        "summary": run_result.get("summary") or "",
        "changed_files": diff.get("changed_files") or [],
        "diffstat": diff.get("diffstat") or "",
        "commits": diff.get("commits") or [],
    }


def run_readonly_cli(
    args: Sequence[str],
    *,
    grok_bin: str = DEFAULT_GROK_BIN,
    cwd: Path | str | None = None,
    timeout_seconds: float = 30.0,
    subprocess_runner: SubprocessRunner | None = None,
    which: WhichFn | None = None,
) -> dict[str, Any]:
    """Run a bounded read-only grok subcommand (version/doctor/models/inspect).

    Never passes mutating subcommands. Caller must supply safe args only.
    """
    run = subprocess_runner or default_subprocess_runner
    which_fn = which or _default_which
    try:
        bin_name = validate_grok_bin(grok_bin, from_client=False)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    _assert_readonly_cli_args(args)

    resolved = which_fn(bin_name)
    if resolved is None and not Path(bin_name).is_file():
        return structured_error("GROK_MISSING", f"grok binary not found: {bin_name}")
    exe = resolved or bin_name
    argv = [exe, *[str(a) for a in args]]
    _reject_always_approve(argv)

    result = run(argv, Path(cwd).resolve() if cwd else None, timeout_seconds)
    if result.get("missing"):
        return structured_error("GROK_MISSING", f"grok binary not found: {bin_name}")
    return {
        "ok": result.get("returncode") == 0 and not result.get("timedOut"),
        "returncode": result.get("returncode"),
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or "",
        "timedOut": bool(result.get("timedOut")),
        "argv0": exe,
    }


_READONLY_CLI_VERBS = frozenset(
    {
        "version",
        "v",
        "doctor",
        "models",
        "inspect",
        "help",
        "--version",
        "-v",
        "--help",
        "-h",
    }
)

_FORBIDDEN_CLI_VERBS = frozenset(
    {
        "login",
        "logout",
        "update",
        "setup",
        "plugin",
        "mcp",
        "sessions",
        "worktree",
        "memory",
        "leader",
        "wrap",
        "export",
        "trace",
        "agent",
        "dashboard",
        "completions",
    }
)


def _assert_readonly_cli_args(args: Sequence[str]) -> None:
    """Fail closed if a mutating / config-management CLI verb is requested."""
    tokens = [str(a) for a in args]
    if not tokens:
        raise GuardError("CLI_ARGS_EMPTY", "read-only CLI args are empty")
    # First non-flag token is the subcommand (or a global flag-only probe).
    verb = None
    for t in tokens:
        if t.startswith("-"):
            # Allow global flags like --json only after a verb; bare -p is not status.
            if t in {"--version", "-v", "--help", "-h"}:
                verb = t
                break
            continue
        verb = t.lower()
        break
    if verb is None:
        raise GuardError("CLI_VERB_MISSING", "read-only CLI requires a subcommand")
    if verb in _FORBIDDEN_CLI_VERBS or verb.startswith("plugin") or verb.startswith("mcp"):
        raise GuardError(
            "CLI_VERB_FORBIDDEN",
            f"CLI verb {verb!r} is not allowed on the read-only status path",
        )
    if verb not in _READONLY_CLI_VERBS:
        raise GuardError(
            "CLI_VERB_FORBIDDEN",
            f"CLI verb {verb!r} is not on the read-only allowlist",
        )
    # Extra hard reject for doctor fix / sessions delete smuggled as extra tokens.
    lowered = [t.lower() for t in tokens]
    for banned in ("fix", "delete", "install", "uninstall", "add", "remove", "login", "logout"):
        if banned in lowered:
            raise GuardError(
                "CLI_VERB_FORBIDDEN",
                f"mutating CLI token {banned!r} is not allowed on the read-only path",
            )


# Explicit export list documents that push/merge helpers do not exist.
__all__ = [
    "DelegationResult",
    "RunnerConfig",
    "collect_diff",
    "default_git_runner",
    "default_subprocess_runner",
    "delegate",
    "is_path_inside",
    "prepare_worktree",
    "resolve_lanes_parent",
    "run_delegation",
    "run_readonly_cli",
    "worktree_path_for_lane",
]
