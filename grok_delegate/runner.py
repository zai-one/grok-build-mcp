"""I/O wrapper for worktree prep, headless grok spawn, and diff collection.

Git and subprocess are injectable callables so unit tests never mutate the host
or spawn a real grok binary. Zero push/merge code paths (B4).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
    from .anchors import validate_goal_anchors  # type: ignore[no-redef]
    from .contracts import redact_text as _redact_text  # type: ignore[no-redef]
    from .economy import ECONOMY_MAX_UNIFIED_DIFF  # type: ignore[no-redef]
    from .verdict import (  # type: ignore[no-redef]
        default_lane_json_schema,
        parse_lane_verdict,
        reconcile_verdict,
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
    from anchors import validate_goal_anchors  # type: ignore
    from contracts import redact_text as _redact_text  # type: ignore
    from economy import ECONOMY_MAX_UNIFIED_DIFF  # type: ignore
    from verdict import (  # type: ignore
        default_lane_json_schema,
        parse_lane_verdict,
        reconcile_verdict,
    )

# Default wall-clock timeout for a single delegation (seconds).
DEFAULT_TIMEOUT_SECONDS = 900

# Per-git-call budget while preparing a lane. Sixty seconds is plenty for the
# command itself, but the background path can be starved by the host: measured
# 2026-07-26, a `git --version` inside the MCP server took as long as the
# client's poll interval, and `git worktree add` hit this ceiling three times
# over. Configurable so a slow host is a slow lane, not a failed one.
#
# Raising this ceiling is not the fix. Spawn, not the git child, is what the
# budget actually pays for inside a busy long-lived server. Measured on this
# host, ``subprocess.Popen(['git', '--version'])`` median of 8:
#   idle process ....................................    7.1 ms
#   16 threads running Python bytecode .............. 3258.7 ms
#   16 threads sleeping (same count, no GIL held) ...    6.5 ms
# Same machine, same binary, same thread count. GIL contention — not
# antivirus, sandboxing, or CPU load. A timed-out probe is retried once so a
# starved wrapper is a slow lane; a hung git fails twice and still fails.
# Checkout (``worktree add``) is not retried: it has its own budget and can
# legitimately run for minutes.
DEFAULT_GIT_TIMEOUT_SECONDS = 60.0

# Structured GIT_TIMEOUT fields for a probe that failed after that retry.
# The message text stays the "wrapper stopped waiting" wording; these name
# the measurement so the next person does not go looking at antivirus.
GIT_SPAWN_STARVATION_CAUSE = "starved_wrapper"
GIT_SPAWN_STARVATION_MEASUREMENT = (
    "Popen(['git','--version']) median of 8: "
    "idle 7.1ms; 16 GIL-holding threads 3258.7ms; 16 sleeping threads 6.5ms"
)

# One budget for every git call was wrong in both directions. A probe
# (`--version`, `rev-parse`, `status`) does milliseconds of work and only ever
# needs slack for a starved background thread; a checkout lays out the whole
# tree and legitimately runs for minutes on a large repo. Sharing the number
# meant the checkout ceiling was set by what a probe needs, and the reported
# failure was WORKTREE_CREATE_FAILED on a checkout that in fact succeeded
# (measured 2026-07-27: full 34-entry worktree on disk, lock released, wrapper
# already gone). Separate budgets, separate env knobs.
DEFAULT_GIT_CHECKOUT_TIMEOUT_SECONDS = 600.0

# Upper bound for either knob — a typo must not park a lane for a day.
_GIT_TIMEOUT_CAP_SECONDS = 3600.0


def _env_timeout(name: str, default: float) -> float:
    """Read a positive float from env *name*; fall back to *default* on junk."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, _GIT_TIMEOUT_CAP_SECONDS)


def git_timeout_seconds() -> float:
    """Per-git-probe timeout, overridable via GROK_DELEGATE_GIT_TIMEOUT_SECONDS."""
    return _env_timeout("GROK_DELEGATE_GIT_TIMEOUT_SECONDS", DEFAULT_GIT_TIMEOUT_SECONDS)


def git_checkout_timeout_seconds() -> float:
    """Budget for tree-laying git calls (``worktree add``).

    Overridable via GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS. Never lower than
    the probe budget: an operator who raised only the probe knob meant "this host
    is slow", and silently giving the checkout less than a probe would invert it.
    """
    value = _env_timeout(
        "GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS",
        DEFAULT_GIT_CHECKOUT_TIMEOUT_SECONDS,
    )
    return max(value, git_timeout_seconds())


# Process-lifetime cache of ``git --version``. Keyed on the resolved binary
# because the binary does not change while this process lives, and spawning it
# is the expensive part under GIL contention (idle 7.1ms vs 3258.7ms with 16
# bytecode threads). The lock is the whole thread-safety story;
# clear_git_version_cache exists so tests are not order-dependent.
_GIT_VERSION_LOCK = threading.Lock()
_GIT_VERSION_CACHE: dict[str, dict[str, Any]] = {}


def _git_version_cache_key(binary: str) -> str:
    located = str(binary or "git")
    if not os.path.dirname(located):
        found = shutil.which(located)
        if found:
            located = found
    try:
        located = str(Path(located).resolve())
    except OSError:
        located = os.path.abspath(located)
    return os.path.normcase(located)


def clear_git_version_cache() -> None:
    """Drop the process-lifetime ``git --version`` cache.

    Production never needs this: the binary does not change. Tests do, or a
    cached hit from an earlier case would leak into the next.
    """
    with _GIT_VERSION_LOCK:
        _GIT_VERSION_CACHE.clear()


def cached_git_version(
    git: GitRunner,
    cwd: Path | None,
    timeout: float,
    *,
    binary: str = "git",
) -> dict[str, Any]:
    """Return ``git --version``, spawning at most once per resolved binary.

    Timeouts are not cached: a GIL-starved spawn is transient, and the retry
    in ``_run_git_probe`` must be allowed to try again.
    """
    key = _git_version_cache_key(binary)
    with _GIT_VERSION_LOCK:
        hit = _GIT_VERSION_CACHE.get(key)
        if hit is not None:
            return dict(hit)
    result = dict(git(["--version"], cwd, timeout))
    if not result.get("timedOut"):
        with _GIT_VERSION_LOCK:
            existing = _GIT_VERSION_CACHE.get(key)
            if existing is not None:
                return dict(existing)
            _GIT_VERSION_CACHE[key] = dict(result)
    return result


# Cap on captured stdout/stderr size (chars) before truncation in result.
DEFAULT_OUTPUT_CHAR_CAP = 200_000

# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------
#
# A dispatch spends its first minutes with no observable trace: no grok process,
# no lane directory, no branch. ``poll`` reported only a pid and started_at, so
# that state was indistinguishable from a hung server, and the honest reading
# ("it is still preparing") was unavailable to the operator — the wrong reading
# ("the channel is dead") cost more than a day of debugging on this host.
#
# The sink is thread-local on purpose: jobs.py runs each delegation on its own
# daemon thread and installs a sink bound to that job's record, so concurrent
# lanes cannot report into each other. A missing sink is the normal case (direct
# calls, unit tests) and costs one getattr.

PHASE_PREFLIGHT = "preflight"
PHASE_WORKTREE = "worktree"
PHASE_RECOVER = "worktree_recover"
PHASE_ANCHORS = "anchors"
PHASE_EXECUTOR = "executor"
PHASE_COLLECT = "collect"

_PROGRESS = threading.local()


def set_progress_sink(sink: "Callable[[dict[str, Any]], None] | None") -> None:
    """Install (or clear) this thread's progress callback."""
    _PROGRESS.sink = sink


def _step_label(cmd: Sequence[str]) -> str:
    """Compact "what is it doing" label: the verb and its flags, not the paths.

    ``git -C <long absolute path> rev-parse --abbrev-ref HEAD`` reads as noise;
    ``git rev-parse --abbrev-ref`` answers the question a poller is asking.
    """
    tokens: list[str] = []
    skip_next = False
    for token in cmd:
        if skip_next:
            skip_next = False
            continue
        text = str(token)
        if text in {"-C", "-c"}:
            skip_next = True
            continue
        # Drop bare path operands; the record already carries worktree_path.
        if ("/" in text or "\\" in text) and not text.startswith("-"):
            continue
        tokens.append(text)
        if len(tokens) >= 4:
            break
    return " ".join(tokens)


def report_progress(**fields: Any) -> None:
    """Publish progress fields to this thread's sink, if one is installed.

    Never raises: observability that can break a lane is worse than none.
    """
    sink = getattr(_PROGRESS, "sink", None)
    if sink is None:
        return
    try:
        sink(dict(fields))
    except Exception:  # noqa: BLE001 — a broken sink must not fail the lane
        pass

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


def _spawn_git(
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """One git subprocess. Records how long ``Popen`` itself took.

    Under GIL contention that spawn is the 500x cost (idle 7.1ms vs 3258.7ms
    with 16 bytecode threads); the child still finishes in milliseconds.
    ``subprocess.run`` does not expose the split, so this is Popen then
    communicate, with the same UTF-8 decoding as before.
    """
    import subprocess

    cmd = ["git", *[str(a) for a in args]]
    report_progress(last_step=_step_label(cmd), last_step_at=time.time())
    spawn_started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv is filtered by the caller
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Decode as UTF-8, never the Windows locale codepage: git output can
            # carry non-cp1252 bytes (branch/file names), which would raise
            # UnicodeDecodeError in the reader thread and lose the result.
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {
            "args": cmd,
            "returncode": 127,
            "stdout": "",
            "stderr": "git not found",
            "timedOut": False,
            "missing": True,
            "spawn_seconds": time.monotonic() - spawn_started,
        }
    spawn_seconds = time.monotonic() - spawn_started
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            "args": cmd,
            "returncode": proc.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timedOut": False,
            "spawn_seconds": spawn_seconds,
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            stdout, stderr = proc.communicate()
        except Exception:  # noqa: BLE001 — never lose the timeout verdict itself
            stdout, stderr = "", ""
        return {
            "args": cmd,
            "returncode": 124,
            "stdout": stdout if isinstance(stdout, str) else "",
            "stderr": f"timeout after {timeout}s",
            "timedOut": True,
            "spawn_seconds": spawn_seconds,
        }


def default_git_runner(
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Real git subprocess (production path). Tests inject a mock instead."""
    _reject_forbidden_git_args(args)
    argv = [str(a) for a in args]
    if argv == ["--version"]:
        # The binary does not change while this process lives; skip a spawn
        # that is 500x slower when other threads hold the GIL.
        return cached_git_version(
            _spawn_git, cwd, timeout, binary=shutil.which("git") or "git"
        )
    return _spawn_git(args, cwd, timeout)


def _is_worktree_add(args: Sequence[str]) -> bool:
    tokens = [str(a) for a in args]
    return "worktree" in tokens and "add" in tokens


def _call_git(
    git: GitRunner,
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    result = git(args, cwd, timeout)
    if not isinstance(result, dict):
        result = {
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "timedOut": False,
        }
    else:
        result = dict(result)
    if "spawn_seconds" not in result:
        result["spawn_seconds"] = time.monotonic() - started
    return result


def _run_git_probe(
    git: GitRunner,
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Run a git probe; retry once on timeout.

    Process spawn, not the child, is what blows the budget inside a busy
    server: measured on this host, ``Popen(['git', '--version'])`` median of 8
    is 7.1 ms idle, 3258.7 ms with 16 threads holding the GIL, and 6.5 ms with
    16 sleeping threads. One retry turns a starved probe into a slow lane; a
    genuinely hung git fails twice and still fails the dispatch. Checkout
    (``worktree add``) is not a probe and is not retried here.
    """
    first = _call_git(git, args, cwd, timeout)
    if not first.get("timedOut"):
        return first
    second = _call_git(git, args, cwd, timeout)
    second["retried_after_timeout"] = True
    return second


def _with_probe_retry(git: GitRunner) -> GitRunner:
    """Retry timed-out probes once; never retry ``git worktree add``."""

    def wrapped(
        args: Sequence[str],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        if _is_worktree_add(args):
            return git(args, cwd, timeout)
        return _run_git_probe(git, args, cwd, timeout)

    return wrapped


def default_subprocess_runner(
    args: Sequence[str],
    cwd: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Real subprocess for grok spawn (production path).

    Popen rather than ``subprocess.run`` for one reason: the executor's real pid
    has to be published while it is still running. ``run`` never exposes it, so
    the job record carried the MCP server's own pid instead — which made the
    stale-record guard a no-op (the server is always alive) and turned the
    operator instruction "kill the hung job's pid" into "kill the server and
    every other lane with it".
    """
    import subprocess

    _reject_always_approve(args)
    cmd = [str(a) for a in args]
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv is guard-validated above
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Grok emits UTF-8 (goal text, summaries, box drawing). Decoding with
            # the Windows locale codepage crashes the reader thread on the first
            # non-cp1252 byte and drops the whole delegation result.
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {
            "args": cmd,
            "returncode": 127,
            "stdout": "",
            "stderr": f"binary not found: {cmd[0] if cmd else '?'}",
            "timedOut": False,
            "missing": True,
        }

    report_progress(worker_pid=proc.pid, phase=PHASE_EXECUTOR, phase_at=time.time())
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            "args": cmd,
            "pid": proc.pid,
            "returncode": proc.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timedOut": False,
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        # Drain after the kill so the partial transcript is not lost — on a
        # timeout that tail is usually the only evidence of what the run did.
        try:
            stdout, stderr = proc.communicate()
        except Exception:  # noqa: BLE001 — never lose the timeout verdict itself
            stdout, stderr = "", ""
        marker = f"timeout after {timeout}s"
        return {
            "args": cmd,
            "pid": proc.pid,
            "returncode": 124,
            "stdout": stdout or "",
            "stderr": f"{marker}\n{stderr}" if stderr else marker,
            "timedOut": True,
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
    """Where this repo's lanes live: explicit, else `<project>/.grok/lanes`.

    The old default put them in a sibling `pcp-lanes` -- a name inherited from
    another project, in a directory the operator never asked to have filled.
    Three call sites computed three different defaults, so `grok_agent_status`
    reported a path `execute` would never use.
    """
    if lanes_parent is not None:
        return Path(lanes_parent).resolve()
    return in_project_lanes_parent(repo_root)


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


#: Where a lane may live inside the project it belongs to. The leading dot is
#: the whole reason this is safe rather than a mess: pytest skips `.*` by
#: default, ripgrep and most indexers skip hidden directories, and one
#: `.gitignore` line hides it from git. A worktree dropped into the source tree
#: proper would be walked by all three, so that stays refused.
LANE_HOME_DIRNAME = ".grok"
LANE_SUBDIRNAME = "lanes"


def in_project_lanes_parent(repo_root: Path) -> Path:
    """`<project>/.grok/lanes` -- lanes live with the work they belong to."""
    return Path(repo_root).resolve() / LANE_HOME_DIRNAME / LANE_SUBDIRNAME


def is_hidden_inside(child: Path, parent: Path) -> bool:
    """Inside *parent*, and reached only through dot-directories.

    This is the exception the inside-repo guard allows. `<repo>/.grok/lanes/x`
    passes; `<repo>/lanes/x` and `<repo>/src/.grok/x` do not -- the second
    because its first segment is an ordinary directory that tools do walk.
    """
    try:
        relative = Path(child).resolve().relative_to(Path(parent).resolve())
    except (ValueError, OSError):
        return False
    parts = relative.parts
    return bool(parts) and parts[0].startswith(".")


def _git_timeout_error(
    step: str,
    result: dict[str, Any],
    timeout: float,
    **extra: Any,
) -> dict[str, Any]:
    """Structured GIT_TIMEOUT naming the step that ran out of budget.

    Distinct from the capability errors (GIT_MISSING, BASE_UNREACHABLE,
    WORKTREE_CREATE_FAILED) on purpose. Those say "this host cannot do it" and
    are terminal; a timeout says "I stopped waiting" and is retryable. Collapsing
    the two is what made a working channel read as a broken environment for
    over a day: `git --version` returning in 0.13s from a shell was reported as
    GIT_MISSING because the same call was starved to 60s inside the server.
    """
    extra.setdefault("spawn_seconds", result.get("spawn_seconds"))
    if result.get("retried_after_timeout"):
        # A probe already got its one retry. Name the measurement: spawn is
        # what timed out (idle 7.1ms vs 3258.7ms with 16 GIL-holding threads),
        # not a broken git binary and not antivirus.
        extra.setdefault("retried", True)
        extra.setdefault("known_cause", GIT_SPAWN_STARVATION_CAUSE)
        extra.setdefault(
            "spawn_measurement", GIT_SPAWN_STARVATION_MEASUREMENT
        )
    return structured_error(
        "GIT_TIMEOUT",
        f"git {step} exceeded its {timeout:g}s budget (the command was not "
        f"observed to fail — the wrapper stopped waiting)",
        detail=(result.get("stderr") or "")[:500],
        step=step,
        timeout_seconds=timeout,
        **extra,
    )


def _worktree_lock_state(
    git: GitRunner,
    root: Path,
    target: Path,
    timeout: float,
) -> str:
    """``locked`` / ``unlocked`` / ``absent`` / ``unknown`` for *target*.

    ``git worktree add`` registers the worktree and locks it with reason
    ``initializing`` before laying out the tree, releasing the lock when the
    checkout completes. That lock is the only reliable "the tree is still being
    written" signal available from outside, and reusing a still-initializing
    worktree would hand the executor a half-checked-out repo.
    """
    listing = git(["worktree", "list", "--porcelain"], root, timeout)
    if listing.get("returncode", 1) != 0:
        return "unknown"
    try:
        target_r = Path(target).resolve()
    except OSError:
        return "unknown"

    in_block = False
    for raw in (listing.get("stdout") or "").splitlines():
        line = raw.strip()
        if line.startswith("worktree "):
            path = line[len("worktree ") :].strip()
            try:
                in_block = Path(path).resolve() == target_r
            except OSError:
                in_block = False
            continue
        if not line:
            # Blank line ends a record; a lock would have appeared inside it.
            if in_block:
                return "unlocked"
            continue
        if in_block and (line == "locked" or line.startswith("locked ")):
            return "locked"
    return "unlocked" if in_block else "absent"


def _settled_worktree(
    git: GitRunner,
    root: Path,
    target: Path,
    branch: str,
    timeout: float,
) -> bool:
    """True when *target* is a finished checkout of *branch* (not initializing)."""
    if not Path(target).exists():
        return False
    if _worktree_lock_state(git, root, target, timeout) != "unlocked":
        return False
    head = git(["-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"], root, timeout)
    if head.get("returncode", 1) != 0:
        return False
    return (head.get("stdout") or "").strip() == branch


# How long to keep watching a timed-out checkout before giving up on it, and how
# often to look. Short on purpose: GIT_TIMEOUT is retryable, so an unsettled tree
# is picked up by the next attempt's reuse path rather than waited out here.
CHECKOUT_SETTLE_GRACE_SECONDS = 30.0
CHECKOUT_SETTLE_POLL_SECONDS = 2.0

# Indirection so tests can drive the settle loop without real sleeping.
_settle_sleep: "Callable[[float], None]" = time.sleep


def _after_checkout_timeout(
    *,
    git: GitRunner,
    root: Path,
    target: Path,
    branch: str,
    base_ref: str,
    probe_timeout: float,
    budget: float,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Decide what a timed-out ``worktree add`` actually left behind.

    Killing the wrapper's subprocess does not cancel the work. ``git worktree
    add`` runs the checkout in a spawned child, and on Windows terminating the
    parent leaves that child to finish: measured 2026-07-27, the wrapper reported
    WORKTREE_CREATE_FAILED while a complete 34-entry worktree sat on disk, clean,
    on the right branch, lock released. Reporting failure there is not just a bad
    label — it strands a valid lane behind WORKTREE_EXISTS_CONFLICT on the next
    dispatch, taking the lane name out of service until someone cleans up by hand.

    So: look before concluding. A settled tree on the expected branch is a
    success. Anything else is a retryable GIT_TIMEOUT, and the worktree is left
    in place for the next attempt to adopt.
    """
    report_progress(phase=PHASE_RECOVER, phase_at=time.time())
    waited = 0.0
    while True:
        if _settled_worktree(git, root, target, branch, probe_timeout):
            return {
                "ok": True,
                "lane": branch,
                "branch": branch,
                "worktree_path": str(target),
                "base_ref": base_ref,
                "reused": False,
                "recovered_after_timeout": True,
                "timeout_seconds": budget,
            }
        if waited >= CHECKOUT_SETTLE_GRACE_SECONDS:
            break
        _settle_sleep(CHECKOUT_SETTLE_POLL_SECONDS)
        waited += CHECKOUT_SETTLE_POLL_SECONDS

    return _git_timeout_error(
        "worktree add",
        result,
        budget,
        worktree_path=str(target),
        lane=branch,
        branch=branch,
        settle_grace_seconds=CHECKOUT_SETTLE_GRACE_SECONDS,
    )


def ensure_lane_dir_ignored(
    repo_root: Path,
    *,
    git_runner: GitRunner | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Make sure the lane directory is ignored by the project it lives in.

    A lane inside the project is only tidy while git cannot see it. Without this
    the operator's own `git status` fills with a checkout they did not make.

    Asks git rather than parsing the file: the rule may already be in
    `.gitignore`, in `.git/info/exclude`, or in a parent's file. One line is
    appended only when git says the path is genuinely not ignored, so calling it
    on every run is safe.
    """
    git = git_runner or default_git_runner
    root = Path(repo_root).resolve()
    probe = LANE_HOME_DIRNAME + "/"
    try:
        checked = git(["-C", str(root), "check-ignore", "-q", probe], None, timeout)
    except Exception:  # noqa: BLE001 - never fail a job over housekeeping
        return {"ok": False, "ignored": False, "written": False, "reason": "CHECK_FAILED"}
    if checked.get("returncode") == 0:
        return {"ok": True, "ignored": True, "written": False, "reason": "ALREADY_IGNORED"}

    target = root / ".gitignore"
    block = "\n# grok-mcp worktrees for delegated jobs\n" + probe + "\n"
    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if any(line.strip() == probe for line in existing.splitlines()):
            return {"ok": True, "ignored": True, "written": False, "reason": "ALREADY_LISTED"}
        separator = "" if (not existing or existing.endswith("\n")) else "\n"
        target.write_text(existing + separator + block, encoding="utf-8")
    except OSError:
        return {"ok": False, "ignored": False, "written": False, "reason": "WRITE_FAILED"}
    return {"ok": True, "ignored": True, "written": True, "reason": "APPENDED"}


def prepare_worktree(
    *,
    repo_root: Path,
    lane: str,
    base_ref: str = "origin/dev",
    lanes_parent: Path | None = None,
    git_runner: GitRunner | None = None,
    timeout: float = 60.0,
    checkout_timeout: float | None = None,
    require_clean_base: bool = True,
) -> dict[str, Any]:
    """Create isolated worktree on grok/* branch off base_ref (fail-closed).

    Rejects: missing git, dirty main tree (when require_clean_base), target path
    inside the main repo working tree, reserved lanes (via normalize_lane).
    Does not spawn grok. Does not push or merge.

    ``timeout`` budgets the probes; ``checkout_timeout`` (default: the same
    value, so direct callers keep the old behaviour) budgets ``worktree add``.
    """
    git = _with_probe_retry(git_runner or default_git_runner)
    root = Path(repo_root).resolve()

    try:
        normalized = normalize_lane(lane)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    parent = resolve_lanes_parent(root, lanes_parent)
    target = worktree_path_for_lane(parent, normalized)

    if is_path_inside(target, root) and not is_hidden_inside(target, root):
        return structured_error(
            "WORKTREE_INSIDE_REPO",
            "target worktree path resolves into the visible source tree; lanes may "
            f"live inside the project only under a dot-directory such as "
            f"{LANE_HOME_DIRNAME}/{LANE_SUBDIRNAME}",
            target=str(target),
            repo_root=str(root),
        )

    checkout_budget = timeout if checkout_timeout is None else float(checkout_timeout)

    report_progress(phase=PHASE_PREFLIGHT, phase_at=time.time())

    # Probe git availability.
    version = git(["--version"], root, timeout)
    if version.get("timedOut"):
        return _git_timeout_error("--version", version, timeout)
    if version.get("missing") or version.get("returncode", 1) != 0:
        return structured_error(
            "GIT_MISSING",
            "git is not available or failed --version",
            detail=(version.get("stderr") or "")[:500],
        )

    # Base reachability.
    rev = git(["rev-parse", "--verify", base_ref], root, timeout)
    if rev.get("timedOut"):
        return _git_timeout_error("rev-parse", rev, timeout, base_ref=base_ref)
    if rev.get("returncode", 1) != 0:
        return structured_error(
            "BASE_UNREACHABLE",
            f"base_ref {base_ref!r} is not reachable",
            detail=(rev.get("stderr") or "")[:500],
        )

    if require_clean_base:
        dirty = git(["status", "--porcelain"], root, timeout)
        if dirty.get("timedOut"):
            return _git_timeout_error("status", dirty, timeout)
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
        report_progress(phase=PHASE_WORKTREE, phase_at=time.time(), reusing=True)
        existing_branch = git(
            ["-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
            root,
            timeout,
        )
        if existing_branch.get("timedOut"):
            return _git_timeout_error(
                "rev-parse HEAD (existing worktree)",
                existing_branch,
                timeout,
                worktree_path=str(target),
            )
        head = (existing_branch.get("stdout") or "").strip()
        if existing_branch.get("returncode") == 0 and head == normalized:
            # A worktree from a previous attempt may still be laying out its
            # tree (git holds an ``initializing`` lock until the checkout ends).
            # Reusing it here would drop the executor into a half-written repo.
            lock_state = _worktree_lock_state(git, root, target, timeout)
            if lock_state == "locked":
                return structured_error(
                    "WORKTREE_INITIALIZING",
                    f"worktree {target} is still being checked out by git; "
                    "retry once the lock clears",
                    worktree_path=str(target),
                    lane=normalized,
                    branch=normalized,
                )
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
    report_progress(phase=PHASE_WORKTREE, phase_at=time.time(), reusing=False)
    add = git(
        ["worktree", "add", "-b", normalized, str(target), base_ref],
        root,
        checkout_budget,
    )
    if add.get("timedOut"):
        return _after_checkout_timeout(
            git=git,
            root=root,
            target=target,
            branch=normalized,
            base_ref=base_ref,
            probe_timeout=timeout,
            budget=checkout_budget,
            result=add,
        )
    if add.get("returncode", 1) != 0:
        stderr = (add.get("stderr") or "") + (add.get("stdout") or "")
        if "already exists" in stderr.lower() or "already checked out" in stderr.lower():
            add2 = git(
                ["worktree", "add", str(target), normalized],
                root,
                checkout_budget,
            )
            if add2.get("timedOut"):
                return _after_checkout_timeout(
                    git=git,
                    root=root,
                    target=target,
                    branch=normalized,
                    base_ref=base_ref,
                    probe_timeout=timeout,
                    budget=checkout_budget,
                    result=add2,
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


def _bounded_unified_diff(text: str, *, cap: int = ECONOMY_MAX_UNIFIED_DIFF) -> str:
    raw = _redact_text(text or "")
    encoded = raw.encode("utf-8", errors="replace")
    if len(encoded) <= cap:
        return raw
    clipped = encoded[:cap].decode("utf-8", errors="replace")
    return clipped + "\n…(truncated)"


#: Identity for a commit the bridge makes on the worker's behalf. Passed with
#: ``-c`` per invocation, never written to config, and deliberately not the
#: operator: the log should say which of the two actually made the commit.
_BRIDGE_COMMIT_IDENTITY = (
    "-c", "user.name=grok-delegate",
    "-c", "user.email=grok-delegate@localhost",
)


def commit_lane_work(
    worktree_path: Path | str,
    *,
    branch: str,
    correlation_id: str,
    paths: Sequence[str] | None = None,
    git_runner: GitRunner | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Commit whatever the worker left uncommitted, on its own lane branch.

    Asking the worker to commit was already the rule, and the rule was observed
    to lose: a job that exhausts ``max_turns`` mid-review stops wherever it
    stands, leaving real edits in the worktree and nothing in ``git log``. An
    instruction that only holds while the worker has turns left is not a
    mechanism. This runs after the adapter returns, so it holds whatever the
    reason for stopping was.

    Refuses on anything that is not a ``grok/*`` lane. The bridge committing to
    a branch a human might merge is the one outcome worse than losing the work,
    and pushing remains impossible here -- ``_reject_forbidden_git_args`` has no
    exception for this path.
    """
    git = git_runner or default_git_runner
    wt = Path(worktree_path)
    lane = str(branch or "")
    if not lane.startswith("grok/"):
        return {"ok": True, "committed": False, "reason": "NOT_A_LANE_BRANCH", "sha": None}

    status = git(["-C", str(wt), "status", "--porcelain"], None, timeout)
    if status.get("returncode") != 0 or status.get("timedOut"):
        return {"ok": False, "committed": False, "reason": "STATUS_FAILED", "sha": None}
    if not str(status.get("stdout") or "").strip():
        return {"ok": True, "committed": False, "reason": "NOTHING_TO_COMMIT", "sha": None}

    # Confirm from git, not from the caller, that this worktree really is on the
    # lane: a stale `branch` argument must not be enough to commit anywhere.
    head = git(["-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"], None, timeout)
    if head.get("returncode") != 0 or str(head.get("stdout") or "").strip() != lane:
        return {"ok": False, "committed": False, "reason": "BRANCH_MISMATCH", "sha": None}

    # Stage what the worker was approved to write, not everything lying around.
    # `add -A` put a foreign MCP server's log and two `__pycache__` blobs into a
    # branch a human is meant to review -- files the acceptance gate had already
    # named as somebody else's. Judging them foreign and committing them anyway
    # was the bridge disagreeing with itself. With no attribution at all the old
    # sweep stands, because then the gate judges every path and would block.
    argv = ["-C", str(wt), "add", "-A"]
    if paths:
        argv += ["--", *[str(p) for p in paths]]
    staged = git(argv, None, timeout)
    if staged.get("returncode") != 0 or staged.get("timedOut"):
        return {"ok": False, "committed": False, "reason": "ADD_FAILED", "sha": None}
    if paths:
        # Nothing of the worker's survived the filter: saying "committed" here
        # would be the same lie in a smaller font.
        pending = git(["-C", str(wt), "diff", "--cached", "--name-only"], None, timeout)
        if not str(pending.get("stdout") or "").strip():
            return {"ok": True, "committed": False, "reason": "NOTHING_TO_COMMIT", "sha": None}

    message = f"grok(lane): worker output for {str(correlation_id)[:120]}"
    done = git(
        ["-C", str(wt), *_BRIDGE_COMMIT_IDENTITY, "commit", "-m", message],
        None,
        timeout,
    )
    if done.get("returncode") != 0 or done.get("timedOut"):
        # A hook may legitimately reject this. Say so; never retry with
        # --no-verify, which would make the bridge the thing that bypasses it.
        return {"ok": False, "committed": False, "reason": "COMMIT_FAILED", "sha": None}

    sha = git(["-C", str(wt), "rev-parse", "HEAD"], None, timeout)
    return {
        "ok": True,
        "committed": True,
        "reason": None,
        "sha": str(sha.get("stdout") or "").strip() or None,
    }


def collect_diff(
    worktree_path: Path | str,
    *,
    git_runner: GitRunner | None = None,
    timeout: float = 60.0,
    base_ref: str | None = None,
) -> dict[str, Any]:
    """Collect changed files, diffstat, commits, and a bounded unified diff.

    R6: when ``base_ref`` is given, work the executor already COMMITTED is included
    (diff/log against the base). Diffing HEAD alone reported ``changed_files: []``
    for a lane that did exactly what its rules asked — commit its work — which is
    indistinguishable from "the executor did nothing".
    Unified diffs are optional evidence with a hard byte cap; they never fail
    the snapshot and are never returned unbounded.
    """
    git = git_runner or default_git_runner
    wt = Path(worktree_path)
    commits: list[str] = []

    def snapshot_failure(probe: str, result: Mapping[str, Any]) -> dict[str, Any]:
        """Return a bounded failure without copying possibly-secret git output."""
        return {
            "ok": False,
            "error": "DIFF_SNAPSHOT_FAILED",
            "error_code": "DIFF_SNAPSHOT_FAILED",
            "failed_probe": probe,
            "probe_returncode": result.get("returncode"),
            "probe_timed_out": bool(result.get("timedOut")),
            "probe_missing": bool(result.get("missing")),
            "changed_files": [],
            "diffstat": "",
            "unified_diff": "",
            "commits": [],
        }

    def probe_failed(result: Mapping[str, Any]) -> bool:
        return (
            result.get("returncode") != 0
            or bool(result.get("timedOut"))
            or bool(result.get("missing"))
        )

    name_status = git(
        ["-C", str(wt), "diff", "--name-only", "HEAD"],
        None,
        timeout,
    )
    # `-uall`, not the default. Plain porcelain collapses a new directory to a
    # single `?? src/` entry, so a worker asked for `src/app.py` delivered it and
    # the receipt reported `src` -- a path nobody expected -- and blocked the job.
    # The collapsed form also hides a second, unexpected file inside the same new
    # directory, which is the more dangerous half of the same shortcut.
    porcelain = git(
        ["-C", str(wt), "status", "--porcelain", "-uall"],
        None,
        timeout,
    )
    stat = git(
        ["-C", str(wt), "diff", "--stat", "HEAD"],
        None,
        timeout,
    )

    for probe, result in (
        ("diff_name_only_head", name_status),
        ("status_porcelain", porcelain),
        ("diff_stat_head", stat),
    ):
        if probe_failed(result):
            return snapshot_failure(probe, result)

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
        if probe_failed(committed):
            return snapshot_failure("diff_name_only_base", committed)
        for line in (committed.get("stdout") or "").splitlines():
            p = line.strip()
            if p and p not in changed:
                changed.append(p)

        log = git(
            ["-C", str(wt), "log", "--oneline", f"{base}..HEAD"],
            None,
            timeout,
        )
        if probe_failed(log):
            return snapshot_failure("log_base_head", log)
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
            if probe_failed(committed_stat):
                return snapshot_failure("diff_stat_base", committed_stat)
            diffstat = (committed_stat.get("stdout") or "").strip()
            if len(diffstat) > 8000:
                diffstat = diffstat[:8000] + "\n…(truncated)"

    unified_chunks: list[str] = []
    head_unified = git(
        ["-C", str(wt), "diff", "--unified=3", "HEAD"],
        None,
        timeout,
    )
    if not probe_failed(head_unified):
        unified_chunks.append(str(head_unified.get("stdout") or ""))
    if base_ref:
        base_unified = git(
            ["-C", str(wt), "diff", "--unified=3", f"{base_ref}...HEAD"],
            None,
            timeout,
        )
        if not probe_failed(base_unified):
            unified_chunks.append(str(base_unified.get("stdout") or ""))
    unified_diff = _bounded_unified_diff("\n".join(chunk for chunk in unified_chunks if chunk.strip()))

    return {
        "ok": True,
        "changed_files": changed,
        "diffstat": diffstat,
        "unified_diff": unified_diff,
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
        # R7-C: the parsed headless JSON, so delegate() can extract a lane verdict
        # without re-parsing stdout. Bounded upstream by output_char_cap.
        "parsed_payload": parsed,
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
    # R6: default flipped to False. A lane worktree is created from a COMMITTED
    # base_ref, so uncommitted work in the main tree cannot affect it — yet the old
    # default rejected every dispatch with BASE_DIRTY until the caller stashed
    # unrelated changes. Callers that want the stricter behavior still pass True.
    require_clean_base: bool = False,
    # R7-B: opt-in hard failure when every path the goal cites is missing. Off by
    # default because creation goals legitimately cite files that do not exist yet;
    # reference-only goals (fix/refactor an existing file) can switch it on.
    fail_on_missing_anchors: bool = False,
    # R7-C: request a machine-readable lane verdict by default; opt out for
    # callers that supply their own json_schema or want raw prose only.
    lane_verdict: bool = True,
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
        timeout=git_timeout_seconds(),
        checkout_timeout=git_checkout_timeout_seconds(),
        require_clean_base=require_clean_base,
    )
    if not prep.get("ok"):
        return prep

    wt = prep["worktree_path"]
    branch = prep["branch"]
    normalized = prep["lane"]

    # R7-B: pre-validate the paths the goal cites, and REPORT them by default.
    #
    # A fabricated citation does kill sessions (measured: one fake anchor burned
    # three dispatches). But blocking on "all anchors missing" is wrong as a
    # default, because a creation goal legitimately cites files that do not exist
    # yet — "deliver grok_delegate/verdict.py plus tests/test_verdict.py" has zero
    # existing anchors. That default blocked exactly such a dispatch the first time
    # it ran, so the hard failure is now opt-in via fail_on_missing_anchors and the
    # normal path just surfaces missing_anchors for the driver and the integrator.
    report_progress(phase=PHASE_ANCHORS, phase_at=time.time(), worktree_path=wt)
    anchor_check = validate_goal_anchors(goal, wt)
    missing_anchors = list(anchor_check.get("missing") or ())
    checked_anchors = list(anchor_check.get("checked") or ())
    if (
        fail_on_missing_anchors
        and checked_anchors
        and len(missing_anchors) == len(checked_anchors)
    ):
        return structured_error(
            "ANCHOR_MISSING",
            "every path cited by the goal is missing from the lane worktree",
            lane=normalized,
            branch=branch,
            worktree_path=wt,
            missing_anchors=missing_anchors[:32],
        )

    effective_schema = json_schema
    if effective_schema is None and lane_verdict:
        effective_schema = default_lane_json_schema()

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
        json_schema=effective_schema,
        no_subagents=no_subagents,
        disable_web_search=disable_web_search,
        resume=resume,
        continue_session=continue_session,
        fork_session=fork_session,
        session_id=session_id,
    )

    # Always collect diffstat when worktree exists (even on executor error).
    # R6: base_ref-aware so committed lane work is reported, not just dirty files.
    report_progress(phase=PHASE_COLLECT, phase_at=time.time())
    diff = collect_diff(wt, git_runner=git_runner, base_ref=base_ref)

    if not diff.get("ok"):
        return {
            "ok": False,
            "lane": normalized,
            "branch": branch,
            "worktree_path": wt,
            "turns_used": run_result.get("turns_used"),
            "status": "failed",
            "summary": "",
            "changed_files": [],
            "diffstat": "",
            "unified_diff": "",
            "commits": [],
            "missing_anchors": missing_anchors,
            "verdict": None,
            "verdict_status": "unavailable",
            "error": "DIFF_SNAPSHOT_FAILED",
            "error_code": "DIFF_SNAPSHOT_FAILED",
            "failed_probe": diff.get("failed_probe"),
            "message": "mandatory git diff snapshot probe failed",
        }

    # R7-C: trust git over prose. A verdict claiming files or a commit that the
    # diff cannot confirm is VERDICT_UNSUPPORTED, so a lane cannot self-certify.
    verdict_input = run_result.get("parsed_payload")
    # _parse_grok_json falls back to {"raw_preview": ...} / {"value": ...} when the
    # executor emitted no JSON at all. Passing that through would report a malformed
    # verdict (VERDICT_INVALID) for a lane that simply never produced one; absence is
    # VERDICT_MISSING, and the distinction is what the driver acts on.
    if isinstance(verdict_input, dict) and set(verdict_input) <= {"raw_preview", "value"}:
        verdict_input = None
    parsed_verdict = parse_lane_verdict(verdict_input)
    reconciled = reconcile_verdict(parsed_verdict, diff)
    verdict_payload = reconciled.get("verdict")
    verdict_status = reconciled.get("status")

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
            "unified_diff": diff.get("unified_diff") or "",
            "commits": diff.get("commits") or [],
            "missing_anchors": missing_anchors,
            "verdict": verdict_payload,
            "verdict_status": verdict_status,
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
        "unified_diff": diff.get("unified_diff") or "",
        "commits": diff.get("commits") or [],
        "missing_anchors": missing_anchors,
        "verdict": verdict_payload,
        "verdict_status": verdict_status,
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
    "CHECKOUT_SETTLE_GRACE_SECONDS",
    "CHECKOUT_SETTLE_POLL_SECONDS",
    "DEFAULT_GIT_CHECKOUT_TIMEOUT_SECONDS",
    "DEFAULT_GIT_TIMEOUT_SECONDS",
    "GIT_SPAWN_STARVATION_CAUSE",
    "GIT_SPAWN_STARVATION_MEASUREMENT",
    "PHASE_ANCHORS",
    "PHASE_COLLECT",
    "PHASE_EXECUTOR",
    "PHASE_PREFLIGHT",
    "PHASE_RECOVER",
    "PHASE_WORKTREE",
    "DelegationResult",
    "RunnerConfig",
    "cached_git_version",
    "clear_git_version_cache",
    "collect_diff",
    "default_git_runner",
    "default_subprocess_runner",
    "delegate",
    "git_checkout_timeout_seconds",
    "git_timeout_seconds",
    "is_path_inside",
    "prepare_worktree",
    "report_progress",
    "resolve_lanes_parent",
    "run_delegation",
    "run_readonly_cli",
    "set_progress_sink",
    "worktree_path_for_lane",
]
