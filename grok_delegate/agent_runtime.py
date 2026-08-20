"""Transport router and typed background-job orchestration for Round 8."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import jobs
from .acp import (
    StdioACPTransport,
    TransportAdapter,
    WebSocketACPTransport,
    _WindowsKillJob,
    _kill_process_tree,
    validated_test_argv,
)
from .contracts import build_prompt, finalize_receipt, redact_text, validate_task_packet, validate_transport
from .guard import GuardError, normalize_lane, structured_error, validate_grok_bin
from .runner import (
    GitRunner,
    collect_diff,
    commit_lane_work,
    delegate,
    ensure_lane_dir_ignored,
    in_project_lanes_parent,
    is_hidden_inside,
    is_path_inside,
    prepare_worktree,
)

_CONCURRENCY = max(1, min(int(os.environ.get("GROK_DELEGATE_CONCURRENCY", "1") or "1"), 2))
_MAX_QUEUED = max(1, min(int(os.environ.get("GROK_DELEGATE_MAX_QUEUED", "8") or "8"), 32))
_EXECUTOR = ThreadPoolExecutor(max_workers=_CONCURRENCY, thread_name_prefix="grok-agent")
_ADMISSION = threading.BoundedSemaphore(_CONCURRENCY + _MAX_QUEUED)
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_FUTURES: dict[str, Future[Any]] = {}
_JOB_META: dict[str, tuple[dict[str, Any], str]] = {}
_LOCK = threading.Lock()
_START_LOCK = threading.Lock()


class TransportRouter:
    """Explicit transport selection. ``auto`` is only an alias for stdio."""

    def __init__(
        self,
        *,
        grok_bin: str = "grok",
        adapters: Mapping[str, TransportAdapter] | None = None,
    ) -> None:
        self.grok_bin = validate_grok_bin(grok_bin, from_client=False)
        self.adapters: dict[str, TransportAdapter] = {
            "stdio": StdioACPTransport(grok_bin=self.grok_bin),
            "websocket": WebSocketACPTransport(grok_bin=self.grok_bin),
        }
        if adapters:
            self.adapters.update(adapters)

    def adapter(self, transport: str) -> TransportAdapter:
        chosen = validate_transport(transport)
        adapter = self.adapters.get(chosen)
        if adapter is None:
            raise GuardError(
                "TRANSPORT_UNAVAILABLE",
                f"transport {chosen!r} is not configured; no fallback was attempted",
            )
        return adapter


def start_agent_job(
    task_value: Mapping[str, Any],
    *,
    transport: str,
    allowed_roots: Sequence[Path | str],
    forced_role: str | None = None,
    lane: str | None = None,
    grok_bin: str = "grok",
    adapters: Mapping[str, TransportAdapter] | None = None,
) -> dict[str, Any]:
    try:
        task = validate_task_packet(task_value, allowed_roots=allowed_roots, forced_role=forced_role)
        chosen = validate_transport(transport)
        lane_name = _lane_name(lane, task)
    except GuardError as exc:
        return structured_error(exc.code, exc.message)

    jid = _job_id(task, chosen, lane_name)
    router = TransportRouter(grok_bin=grok_bin, adapters=adapters)
    cancel_event = threading.Event()

    def event_sink(event: dict[str, Any]) -> None:
        current = jobs.get_job(jid) or {}
        events = list(current.get("events") or [])
        events.append(event)
        fields: dict[str, Any] = {"events": events[-64:]}
        if event.get("kind") == "session_created":
            fields["session_id"] = (event.get("payload") or {}).get("sessionId")
        jobs.update_job(jid, fields)

    def work() -> dict[str, Any]:
        try:
            jobs.update_job(
                jid,
                {
                    "transport": chosen,
                    "correlation_id": task["correlation_id"],
                    "phase": "preflight",
                },
            )
            return run_task(
                task,
                transport=chosen,
                lane=lane_name,
                router=router,
                cancel_event=cancel_event,
                event_sink=event_sink,
            )
        finally:
            with _LOCK:
                _CANCEL_EVENTS.pop(jid, None)
                _FUTURES.pop(jid, None)
                _JOB_META.pop(jid, None)
            _ADMISSION.release()

    with _START_LOCK:
        existing = jobs.get_job(jid)
        # `unknown` is what a record rehydrated from a dead incarnation reads as:
        # the thread that owned it is gone and nothing will ever finish it. It is
        # a tombstone, not work in progress, and treating it as a replay meant
        # the same packet could not be retried until it happened to be evicted.
        if existing is not None and str(existing.get("state") or "") == "unknown":
            jobs.forget_job(jid)
            existing = None
        if existing is not None:
            return {
                "ok": True,
                "job_id": jid,
                "state": existing.get("state"),
                "transport": chosen,
                "idempotent_replay": True,
                "poll_with": "grok_agent_poll",
            }
        busy = jobs.lane_is_busy(lane_name)
        if busy is not None:
            return structured_error("LANE_BUSY", f"lane {lane_name} already has a running job")
        if not _ADMISSION.acquire(blocking=False):
            return structured_error(
                "QUEUE_FULL",
                f"agent queue is full (concurrency={_CONCURRENCY}, max_queued={_MAX_QUEUED})",
            )
        with _LOCK:
            _CANCEL_EVENTS[jid] = cancel_event
            _JOB_META[jid] = (dict(task), chosen)
        try:
            def submit_registered(callback: Callable[[], None]) -> None:
                registered = threading.Event()

                def gated_callback() -> None:
                    registered.wait()
                    callback()

                future = _EXECUTOR.submit(gated_callback)
                with _LOCK:
                    _FUTURES[jid] = future
                registered.set()

            record = jobs.start_job(
                work,
                lane=lane_name,
                tool=f"grok_agent_{task['role']}",
                job_id=jid,
                thread_starter=submit_registered,
            )
        except Exception:
            with _LOCK:
                _CANCEL_EVENTS.pop(jid, None)
                _FUTURES.pop(jid, None)
                _JOB_META.pop(jid, None)
            _ADMISSION.release()
            raise
    jobs.update_job(
        jid,
        {
            "transport": chosen,
            "correlation_id": task["correlation_id"],
        },
    )
    return {
        "ok": True,
        "job_id": jid,
        "state": record.get("state"),
        "transport": chosen,
        "lane": lane_name,
        "idempotent_replay": False,
        "poll_with": "grok_agent_poll",
    }


def cancel_agent_job(job_id: str) -> dict[str, Any]:
    record = jobs.get_job(job_id)
    if record is None:
        return structured_error("JOB_UNKNOWN", f"unknown job_id: {job_id}")
    if record.get("state") != jobs.STATE_RUNNING:
        return {
            "ok": True,
            "job_id": job_id,
            "state": record.get("state"),
            "cancel_requested": False,
            "already_terminal": True,
        }
    with _LOCK:
        event = _CANCEL_EVENTS.get(job_id)
        future = _FUTURES.get(job_id)
        meta = _JOB_META.get(job_id)
    if event is None:
        return structured_error(
            "JOB_NOT_OWNED",
            "running job belongs to another server incarnation and cannot be cancelled here",
        )
    event.set()
    if future is not None and future.cancel():
        task, transport = meta if meta is not None else ({}, str(record.get("transport") or "stdio"))
        started = _utc_now()
        receipt = _base_receipt(
            job_id, transport, started,
            status="cancelled", blocked_reason="CANCELLED_WHILE_QUEUED",
        )
        if task:
            receipt = finalize_receipt(receipt, task)
        jobs.cancel_queued_job(job_id, receipt)
        with _LOCK:
            _CANCEL_EVENTS.pop(job_id, None)
            _FUTURES.pop(job_id, None)
            _JOB_META.pop(job_id, None)
        _ADMISSION.release()
        return {
            "ok": True,
            "job_id": job_id,
            "state": jobs.STATE_DONE,
            "cancel_requested": True,
            "cancelled_while_queued": True,
        }
    jobs.update_job(job_id, {"cancel_requested": True})
    return {
        "ok": True,
        "job_id": job_id,
        "state": "running",
        "cancel_requested": True,
    }


def run_task(
    task: Mapping[str, Any],
    *,
    transport: str,
    lane: str,
    router: TransportRouter,
    cancel_event: threading.Event,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    jid = _job_id(task, transport, lane)
    started = _utc_now()
    root = Path(str(task["project_root"])).resolve(strict=True)
    role = str(task["role"])
    write_role = role in {"execute", "fix"}
    git_runner = None
    before_states: dict[str, tuple[Any, ...]] = {}
    before_commits: set[str] = set()
    branch: str | None = None
    base_ref = str(task["base_ref"])
    cwd = root

    def diff_snapshot_failure(
        snapshot: Mapping[str, Any],
        executor_result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = _base_receipt(
            jid,
            transport,
            started,
            status="failed",
            blocked_reason="DIFF_SNAPSHOT_FAILED",
        )
        receipt.update(
            {
                "branch": branch,
                "worktree_path": str(cwd),
                "full_changed_files": [],
                "error": "DIFF_SNAPSHOT_FAILED",
                "error_code": "DIFF_SNAPSHOT_FAILED",
                "failed_probe": snapshot.get("failed_probe"),
            }
        )
        if executor_result is not None:
            receipt.update(
                {
                    "session_id": executor_result.get("session_id"),
                    "summary": redact_text(str(executor_result.get("summary") or "")),
                    "worker_pid": executor_result.get("worker_pid"),
                    "agent_pid": executor_result.get("agent_pid"),
                    "worker_alive_after_shutdown": executor_result.get(
                        "worker_alive_after_shutdown"
                    ),
                }
            )
        return finalize_receipt(receipt, task)

    if cancel_event.is_set():
        return finalize_receipt(
            _base_receipt(jid, transport, started, status="cancelled", blocked_reason="CANCELLED_BEFORE_START"),
            task,
        )

    if write_role:
        git_exe = shutil.which("git") or "git"

        def git_runner(args: Sequence[str], process_cwd: Path | str | None, timeout: float) -> dict[str, Any]:
            return _run_owned_process(
                [git_exe, *[str(value) for value in args]],
                Path(process_cwd or root),
                float(timeout),
                cancel_event,
            )

        # The lane lives under the project's dot-directory, so git has to be
        # told to ignore it before the checkout appears -- otherwise the
        # operator's own status fills with a worktree they did not make.
        ensure_lane_dir_ignored(root, git_runner=git_runner, timeout=30.0)
        prep = prepare_worktree(
            repo_root=root,
            lane=lane,
            base_ref=str(task["base_ref"]),
            lanes_parent=_default_lanes_parent(root),
            git_runner=git_runner,
            timeout=min(float(task["timeout_seconds"]), 60.0),
            checkout_timeout=min(float(task["timeout_seconds"]), 600.0),
            require_clean_base=False,
        )
        if cancel_event.is_set():
            return finalize_receipt(
                _base_receipt(jid, transport, started, status="cancelled", blocked_reason="CANCELLED_DURING_PREFLIGHT"),
                task,
            )
        if not prep.get("ok"):
            return finalize_receipt(
                _base_receipt(
                    jid,
                    transport,
                    started,
                    status="blocked",
                    blocked_reason=str(prep.get("error") or "WORKTREE_PREP_FAILED"),
                ),
                task,
            )
        cwd = Path(str(prep["worktree_path"])).resolve()
        branch = str(prep["branch"])
        # Pin the base to a commit now. `base_ref` may be a moving name -- HEAD
        # is the default -- and every later diff is taken against it. Once
        # anything commits on the lane, "HEAD" means the new commit and the
        # diff collapses to empty, reporting a finished job as no_changes.
        #
        # Resolved in the main repository, not in the worktree, which is where
        # prepare_worktree already resolved it to create the branch. In a reused
        # lane the worktree's HEAD is the previous job's commit, so resolving
        # here made the base move forward with the lane and hid everything an
        # earlier job had left on the branch -- including files a receipt had
        # already refused.
        base_ref = _resolve_base_sha(root, str(task["base_ref"]), git_runner, timeout=30.0)
        before = collect_diff(cwd, base_ref=base_ref, git_runner=git_runner)
        if not before.get("ok"):
            return diff_snapshot_failure(before)
        before_states = {
            path: _path_state(cwd, path) for path in before.get("changed_files") or []
        }
        before_commits = {str(value) for value in before.get("commits") or []}
        if cancel_event.is_set():
            return finalize_receipt(
                _base_receipt(jid, transport, started, status="cancelled", blocked_reason="CANCELLED_DURING_PREFLIGHT"),
                task,
            )

    if transport == "legacy":
        if write_role:
            result = delegate(
                goal=build_prompt(task),
                lane=lane,
                repo_root=root,
                base_ref=base_ref,
                max_turns=int(task["max_turns"]),
                model=str(task["model"]),
                reasoning_effort=str(task["reasoning_effort"]),
                lanes_parent=_default_lanes_parent(root),
                grok_bin=router.grok_bin,
                timeout_seconds=float(task["timeout_seconds"]),
                lane_verdict=False,
                no_subagents=True,
                disable_web_search=True,
                git_runner=git_runner,
                subprocess_runner=lambda argv, process_cwd, timeout: _run_owned_process(
                    argv, Path(process_cwd or root), float(timeout), cancel_event
                ),
            )
            post = collect_diff(cwd, base_ref=base_ref, git_runner=git_runner)
            if not post.get("ok"):
                return diff_snapshot_failure(post, result)
            changed_before_tests = _changes_since(cwd, post.get("changed_files") or [], before_states)
            verified_tests, tests_skipped = _verify_or_explain(
                cwd,
                task,
                cancel_event,
                write_role=write_role,
                changed=changed_before_tests,
            )
            # Verifier commands are code too: re-read git only after they have
            # finished so a passing test cannot revert the artifact, introduce
            # an unexpected file, or otherwise leave a stale receipt.
            post = collect_diff(cwd, base_ref=base_ref, git_runner=git_runner)
            if not post.get("ok"):
                return diff_snapshot_failure(post, result)
            full_changed_files = [
                str(path).replace("\\", "/") for path in post.get("changed_files") or []
            ]
            changed = _changes_since(cwd, post.get("changed_files") or [], before_states)
            commits = [
                str(value) for value in post.get("commits") or []
                if str(value) not in before_commits
            ]
            receipt = _base_receipt(
                jid,
                transport,
                started,
                status=(
                    "cancelled" if cancel_event.is_set()
                    else ("completed" if result.get("ok") else str(result.get("status") or "failed"))
                ),
                blocked_reason=(
                    "CANCELLED" if cancel_event.is_set()
                    else (redact_text(str(result.get("error") or "")) or None)
                ),
            )
            receipt.update(
                {
                    "branch": result.get("branch"),
                    "worktree_path": str(cwd),
                    "changed_files": changed,
                    "full_changed_files": full_changed_files,
                    "commits": commits,
                    "diffstat": _fallback_diffstat(changed),
                    "unified_diff": "",
                    "summary": redact_text(str(result.get("summary") or "")),
                    "tests": verified_tests,
                    "tests_skipped_reason": tests_skipped,
                    "artifacts": _present_artifacts(cwd, task),
                }
            )
            return finalize_receipt(receipt, task)
        return finalize_receipt(_run_legacy_readonly(task, jid, router.grok_bin, cancel_event), task)

    jobs.update_job(jid, {"phase": "executor", "worktree_path": str(cwd)})
    adapter = router.adapter(transport)
    result = adapter.run(
        task,
        cwd=cwd,
        cancel_event=cancel_event,
        event_sink=event_sink,
    )
    jobs.update_job(
        jid,
        {
            "session_id": result.get("session_id"),
            "agent_pid": result.get("agent_pid"),
            "phase": "collect",
        },
    )
    diff = collect_diff(cwd, base_ref=base_ref, git_runner=git_runner) if write_role else {
        "changed_files": [], "commits": [], "diffstat": "", "unified_diff": ""
    }
    if write_role:
        if not diff.get("ok"):
            return diff_snapshot_failure(diff, result)
        diff["full_changed_files"] = [
            str(path).replace("\\", "/") for path in diff.get("changed_files") or []
        ]
        diff["changed_files"] = _changes_since(
            cwd, diff.get("changed_files") or [], before_states
        )
        diff["commits"] = [
            str(value) for value in diff.get("commits") or []
            if str(value) not in before_commits
        ]
        diff["diffstat"] = _fallback_diffstat(diff["changed_files"])
    # What the tree looked like when the worker stopped and before any test ran.
    # Acceptance is still judged after the verifier -- a test that reverts the
    # artifact must not pass -- but the two snapshots together also say which
    # side wrote each file, which one snapshot cannot.
    pre_test_states = _states_of(
        cwd,
        list(diff.get("full_changed_files") or []) + list(task.get("expected_artifacts") or []),
    )
    verified_tests, tests_skipped = _verify_or_explain(
        cwd,
        task,
        cancel_event,
        write_role=write_role,
        changed=diff.get("changed_files") or [],
    )
    if write_role:
        # Acceptance is based on the filesystem after the independent verifier,
        # never on the state that existed before tests ran.
        diff = collect_diff(cwd, base_ref=base_ref, git_runner=git_runner)
        if not diff.get("ok"):
            return diff_snapshot_failure(diff, result)
        diff["full_changed_files"] = [
            str(path).replace("\\", "/") for path in diff.get("changed_files") or []
        ]
        diff["changed_files"] = _changes_since(
            cwd, diff.get("changed_files") or [], before_states
        )
        diff["commits"] = [
            str(value) for value in diff.get("commits") or []
            if str(value) not in before_commits
        ]
        diff["diffstat"] = _fallback_diffstat(diff["changed_files"])
    verifier_touched = _changes_since(
        cwd,
        list(diff.get("full_changed_files") or []) + list(task.get("expected_artifacts") or []),
        pre_test_states,
    ) if write_role else []
    # Last, deliberately. Acceptance is read from the filesystem the verifier
    # left behind, so committing earlier would let a test that reverts the
    # artifact still look like delivered work. Committing after keeps that
    # check honest and still leaves the lane with the work in `git log`.
    lane_commit = (
        commit_lane_work(
            cwd,
            branch=str(branch or ""),
            correlation_id=str(task.get("correlation_id") or jid),
            # What the gate calls the worker's is what the lane gets. Anything
            # else in the tree belongs to somebody else, and the branch a human
            # reviews should not carry it.
            paths=sorted(
                {str(p) for p in (result.get("worker_written_files") or [])}
                | {str(p) for p in (task.get("expected_artifacts") or [])}
            ),
            git_runner=git_runner,
            timeout=min(float(task["timeout_seconds"]), 60.0),
        )
        if write_role and not cancel_event.is_set()
        else {"ok": True, "committed": False, "reason": "NOT_A_WRITE_ROLE", "sha": None}
    )
    commits = list(diff.get("commits") or [])
    if lane_commit.get("sha"):
        commits.append(str(lane_commit["sha"]))
        # `git diff` cannot show an untracked file, so the evidence for the most
        # ordinary execute there is -- "create this file" -- was an empty diff
        # next to a non-empty changed_files. Once the lane commit exists the same
        # content is reachable, so the rendering is refreshed from it. Acceptance
        # is NOT re-read here: it stays on the snapshot the verifier left, or a
        # test that reverted the artifact would be certified by the commit.
        after_commit = collect_diff(cwd, base_ref=base_ref, git_runner=git_runner)
        if after_commit.get("ok"):
            diff["unified_diff"] = after_commit.get("unified_diff") or diff.get("unified_diff") or ""
            diff["diffstat"] = after_commit.get("diffstat") or diff.get("diffstat") or ""
    receipt = _base_receipt(
        jid,
        transport,
        started,
        status=str(result.get("status") or "failed"),
        blocked_reason=result.get("blocked_reason"),
    )
    receipt.update(
        {
            "session_id": result.get("session_id"),
            "branch": branch,
            "worktree_path": str(cwd),
            "changed_files": diff.get("changed_files") or [],
            "full_changed_files": diff.get("full_changed_files") or [],
            "commits": commits,
            "diffstat": diff.get("diffstat") or _fallback_diffstat(diff.get("changed_files") or []),
            "unified_diff": diff.get("unified_diff") or "",
            "tests": verified_tests,
            "tests_skipped_reason": tests_skipped,
            "verifier_touched_files": verifier_touched,
            # Paths the permission gate actually let the worker write. Anything
            # else that moved in the tree belongs to somebody else -- another
            # MCP server the CLI has configured, a test run's byproducts -- and
            # the acceptance gate has no business blaming the worker for it.
            "worker_written_files": result.get("worker_written_files") or [],
            "lane_commit": lane_commit,
            "artifacts": _present_artifacts(cwd, task),
            "summary": redact_text(str(result.get("summary") or "")),
            "events": result.get("events") or [],
            "stop_reason": result.get("stop_reason"),
            "agent_version": result.get("agent_version"),
            "server_pid": os.getpid(),
            "worker_pid": result.get("worker_pid"),
            "agent_pid": result.get("agent_pid"),
            "worker_alive_after_shutdown": result.get("worker_alive_after_shutdown"),
            "error": redact_text(str(result.get("error") or "")) or None,
        }
    )
    return finalize_receipt(receipt, task)


def _run_legacy_readonly(
    task: Mapping[str, Any],
    job_id: str,
    grok_bin: str,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    started = _utc_now()
    exe = shutil.which(grok_bin) or grok_bin
    argv = [
        exe,
        "--cwd",
        str(task["project_root"]),
        "--output-format",
        "json",
        "--permission-mode",
        "plan",
        "--sandbox",
        "read-only",
        "--max-turns",
        str(task["max_turns"]),
        "--no-subagents",
        "--disable-web-search",
        "--single",
        build_prompt(task),
    ]
    if "--always-approve" in argv or "bypassPermissions" in argv:
        raise GuardError("LEGACY_ARGV_UNSAFE", "unsafe legacy argv")
    run = _run_owned_process(
        argv, Path(str(task["project_root"])), float(task["timeout_seconds"]), cancel_event
    )
    if run.get("timedOut"):
        return _base_receipt(job_id, "legacy", started, status="failed", blocked_reason="LEGACY_TIMEOUT")
    summary = ""
    try:
        payload = json.loads(str(run.get("stdout") or ""))
        if isinstance(payload, dict):
            summary = str(payload.get("text") or payload.get("summary") or "")
    except json.JSONDecodeError:
        summary = str(run.get("stdout") or "")[:4_000]
    receipt = _base_receipt(
        job_id,
        "legacy",
        started,
        status="cancelled" if cancel_event.is_set() else ("completed" if run.get("returncode") == 0 else "failed"),
        blocked_reason="CANCELLED" if cancel_event.is_set() else (None if run.get("returncode") == 0 else "LEGACY_PROCESS_FAILED"),
    )
    receipt.update({"summary": redact_text(summary)[:16_000], "worktree_path": str(task["project_root"])})
    return receipt


def _verify_or_explain(
    cwd: Path,
    task: Mapping[str, Any],
    cancel_event: threading.Event,
    *,
    write_role: bool,
    changed: Sequence[Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Run the verifier, or say in one word why the receipt has no test result.

    The verifier used to be gated on the agent reaching ``completed``. That
    threw away the answer exactly when the host needed it most: a worker that
    exhausts its turns mid-review stops at ``ACP_STOP_cancelled`` with real
    edits on disk, and an empty ``tests`` list next to those edits is
    indistinguishable from "the tests were not written yet". Whether the agent
    finished its turn and whether its code passes are two different questions,
    and the receipt is supposed to answer the second one.

    An operator cancel is the one case that still skips: the worktree is being
    torn down and spending minutes on pytest would only delay it.
    """
    if not write_role:
        return [], "NOT_A_WRITE_ROLE"
    if cancel_event.is_set():
        return [], "CANCELLED"
    if not changed:
        return [], "NO_CHANGES"
    if not list(task.get("test_commands") or []):
        return [], "NO_TEST_COMMANDS"
    return _run_bridge_tests(cwd, task, cancel_event), None


def _run_bridge_tests(
    cwd: Path,
    task: Mapping[str, Any],
    cancel_event: threading.Event,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in task.get("test_commands", []):
        try:
            argv = validated_test_argv(str(command), cwd)
        except Exception as exc:
            results.append({
                "command": str(command)[:1_000], "passed": False, "returncode": None,
                "output_preview": "", "error": type(exc).__name__, "source": "bridge-verifier",
            })
            continue
        run = _run_owned_process(
            argv,
            cwd,
            min(float(task.get("timeout_seconds") or 1800), 600.0),
            cancel_event,
        )
        output = redact_text(str(run.get("stdout") or "") + str(run.get("stderr") or ""))
        results.append({
            "command": str(command)[:1_000],
            "passed": run.get("returncode") == 0 and not run.get("timedOut") and not run.get("cancelled"),
            "returncode": run.get("returncode"),
            "output_preview": output[-2_000:],
            "source": "bridge-verifier",
        })
    return results


def _path_state(cwd: Path, relative: str) -> tuple[Any, ...]:
    """Bounded content identity used to distinguish this run from stale diff."""
    root = cwd.resolve()
    candidate = (root / str(relative)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return ("escape",)
    if not candidate.exists():
        return ("missing",)
    try:
        stat = candidate.stat()
        if candidate.is_dir():
            return ("dir", stat.st_mtime_ns)
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
        return ("file", stat.st_size, digest.hexdigest())
    except OSError as exc:
        return ("error", type(exc).__name__)


def _resolve_base_sha(
    cwd: Path,
    base_ref: str,
    git_runner: GitRunner,
    *,
    timeout: float = 30.0,
) -> str:
    """Turn a possibly-moving ref into the commit it points at right now.

    `HEAD` is the default base, and it moves the moment anything commits on the
    lane -- after which every diff "against the base" is a diff against the
    work itself, and a finished job reports no_changes. Resolving once, before
    the worker starts, is what makes "since the lane started" mean that.

    Falls back to the literal ref if git cannot answer, which is the behaviour
    that existed before pinning: no new failure mode for a probe this cheap.
    """
    try:
        result = git_runner(["-C", str(cwd), "rev-parse", "--verify", f"{base_ref}^{{commit}}"], None, timeout)
    except Exception:  # noqa: BLE001 - a failed probe must not fail the job
        return base_ref
    sha = str(result.get("stdout") or "").strip()
    if result.get("returncode") == 0 and re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return sha
    return base_ref


def _states_of(cwd: Path, paths: Sequence[Any]) -> dict[str, tuple[Any, ...]]:
    """Bounded content identity for a set of paths, at one moment in time."""
    return {
        str(path).replace("\\", "/"): _path_state(cwd, str(path))
        for path in paths
    }


def _changes_since(
    cwd: Path,
    post_paths: Sequence[str],
    before_states: Mapping[str, tuple[Any, ...]],
) -> list[str]:
    changed: list[str] = []
    for raw in post_paths:
        path = str(raw).replace("\\", "/")
        previous = before_states.get(path)
        if previous is None or _path_state(cwd, path) != previous:
            if path not in changed:
                changed.append(path)
    return changed


def _run_owned_process(
    argv: Sequence[str],
    cwd: Path,
    timeout: float,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    output_cap = 1_000_000
    try:
        proc = subprocess.Popen(
            [str(value) for value in argv], cwd=str(cwd), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": "binary not found", "missing": True, "timedOut": False}
    process_job = _WindowsKillJob(proc)
    deadline = time.monotonic() + max(0.1, timeout)
    cancelled = False
    timed_out = False
    output_limited = threading.Event()
    budget_lock = threading.Lock()
    budget = {"remaining": output_cap}
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def drain(stream: Any, target: list[str]) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if chunk == "":
                    return
                size = len(chunk.encode("utf-8", errors="replace"))
                with budget_lock:
                    remaining = budget["remaining"]
                    if size > remaining:
                        if remaining > 0:
                            target.append(chunk.encode("utf-8")[:remaining].decode("utf-8", errors="ignore"))
                            budget["remaining"] = 0
                        output_limited.set()
                        return
                    budget["remaining"] = remaining - size
                target.append(chunk)
        except (OSError, ValueError):
            return

    readers = [
        threading.Thread(target=drain, args=(proc.stdout, stdout_parts), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, stderr_parts), daemon=True),
    ]
    for reader in readers:
        reader.start()
    while proc.poll() is None:
        if cancel_event.is_set():
            cancelled = True
            _kill_process_tree(proc.pid)
            break
        if output_limited.is_set():
            _kill_process_tree(proc.pid)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_process_tree(proc.pid)
            break
        time.sleep(0.05)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    for reader in readers:
        reader.join(timeout=1)
    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    process_job.close()
    return {
        "args": [str(value) for value in argv], "pid": proc.pid,
        "returncode": 130 if cancelled else (124 if timed_out or output_limited.is_set() else proc.returncode),
        "stdout": stdout or "", "stderr": stderr or "", "timedOut": timed_out,
        "cancelled": cancelled, "output_limited": output_limited.is_set(),
    }


def _base_receipt(
    job_id: str,
    transport: str,
    started: str,
    *,
    status: str,
    blocked_reason: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "job_id": job_id,
        "session_id": None,
        "transport": transport,
        "branch": None,
        "worktree_path": None,
        "changed_files": [],
        "commits": [],
        "diffstat": "",
        "unified_diff": "",
        "tests": [],
        "artifacts": [],
        "findings": [],
        "summary": "",
        "blocked_reason": str(blocked_reason) if blocked_reason else None,
        "started_at": started,
        "finished_at": _utc_now(),
    }


def _present_artifacts(cwd: Path, task: Mapping[str, Any]) -> list[str]:
    present: list[str] = []
    root = cwd.resolve(strict=True)
    for value in task.get("expected_artifacts", []):
        candidate = root / str(value)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file():
            present.append(str(value))
    return present


def _fallback_diffstat(paths: Sequence[str]) -> str:
    if not paths:
        return ""
    preview = ", ".join(str(path) for path in paths[:10])
    return f"{len(paths)} changed file(s), including untracked: {preview}"


def _default_lanes_parent(root: Path) -> Path:
    """Where this job's worktree goes: the operator's pin, else `<root>/.grok/lanes`.

    Inside the project on purpose. A lane holds unmerged work someone is going
    to review, which belongs with the project rather than in a sibling directory
    the operator never asked to have created. The leading dot is what makes it
    safe: pytest skips `.*`, ripgrep and indexers skip hidden, and one
    `.gitignore` line hides it from git. A lane in the visible source tree is
    still refused, because all three would walk it.
    """
    configured = (os.environ.get("GROK_DELEGATE_LANES_PARENT") or "").strip()
    if not configured:
        return in_project_lanes_parent(root)
    candidate = Path(configured).expanduser().resolve()
    if not is_path_inside(candidate, root) or is_hidden_inside(candidate, root):
        return candidate
    raise GuardError(
        "LANES_PARENT_INSIDE_REPO",
        "lanes parent must be outside project_root, or under a dot-directory inside it",
    )


def _lane_name(value: str | None, task: Mapping[str, Any]) -> str:
    raw = value or f"round8-{task['role']}-{task['correlation_id']}"
    slug = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")[:80]
    if not slug:
        slug = "round8-task"
    return normalize_lane(slug)


def _job_id(task: Mapping[str, Any], transport: str, lane: str) -> str:
    material = json.dumps(
        {"task": dict(task), "transport": transport, "lane": lane},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "job-" + hashlib.sha256(material).hexdigest()[:16]


def runtime_status() -> dict[str, Any]:
    from .status import compatibility_report

    with _LOCK:
        cancellable = sorted(_CANCEL_EVENTS)
    return {
        "default_transport": "stdio",
        "auto_behavior": "stdio-only-no-fallback",
        "configured_transports": ["legacy", "stdio", "websocket"],
        "compatibility": compatibility_report(),
        "websocket_mode": (
            "external-loopback-daemon"
            if (os.environ.get("GROK_DELEGATE_WS_ENDPOINT") or "").strip()
            else "managed-per-task-daemon"
        ),
        "concurrency": _CONCURRENCY,
        "max_queued": _MAX_QUEUED,
        "cancellable_jobs": cancellable,
        "server_pid": os.getpid(),
    }


def shutdown_runtime() -> dict[str, Any]:
    """Bounded server-EOF shutdown: signal owned jobs, cancel queued futures."""
    with _LOCK:
        active_ids = list(_CANCEL_EVENTS)
    queued_cancelled = 0
    for job_id in active_ids:
        outcome = cancel_agent_job(job_id)
        if outcome.get("cancelled_while_queued"):
            queued_cancelled += 1
    _EXECUTOR.shutdown(wait=False, cancel_futures=True)
    return {"cancel_signalled": len(active_ids), "queued_terminalized": queued_cancelled}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "TransportRouter",
    "cancel_agent_job",
    "run_task",
    "runtime_status",
    "shutdown_runtime",
    "start_agent_job",
]
