"""Versioned task and receipt contracts for the MCP -> Grok transports.

The runtime intentionally validates without a third-party JSON Schema engine so
the local MCP server remains a stdlib-only install.  The equivalent published
schemas live under ``schemas/`` and contract tests keep the two surfaces aligned.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .guard import (
    GuardError,
    configured_max_turns,
    configured_model,
    configured_reasoning_effort,
    looks_like_secret_path,
    path_in_allowlist,
)
from .economy import apply_task_economy_defaults

TASK_SCHEMA_ID = "grok-task-packet.v1"
RECEIPT_SCHEMA_ID = "grok-work-receipt.v1"
EVENT_SCHEMA_ID = "grok-agent-event.v1"

ROLES = frozenset({"consult", "execute", "skeptic", "fix"})
TRANSPORTS = frozenset({"legacy", "stdio", "websocket", "auto"})
PERMISSION_PROFILES = frozenset({"read-only", "workspace"})
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

MAX_OBJECTIVE = 12_000
MAX_ITEM = 2_000
MAX_ITEMS = 64
MAX_CORRELATION = 128
MAX_PATH = 1_024
MAX_EVENTS = 64
MAX_EVENT_TEXT = 2_000

_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TASK_FIELDS = frozenset(
    {
        "schema_version",
        "objective",
        "role",
        "project_root",
        "base_ref",
        "model",
        "reasoning_effort",
        "permission_profile",
        "max_turns",
        "timeout_seconds",
        "inputs",
        "constraints",
        "acceptance_criteria",
        "expected_artifacts",
        "test_commands",
        "mount_paths",
        "review_lane",
        "correlation_id",
    }
)


def validate_transport(value: Any) -> str:
    transport = str(value or "stdio").strip().lower()
    if transport not in TRANSPORTS:
        raise GuardError(
            "TRANSPORT_INVALID",
            f"transport must be one of {sorted(TRANSPORTS)}",
        )
    # Round 8 auto is intentionally not a cascading fallback.
    return "stdio" if transport == "auto" else transport


def validate_task_packet(
    value: Mapping[str, Any] | None,
    *,
    allowed_roots: Sequence[Path | str],
    forced_role: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GuardError("TASK_PACKET_INVALID", "task must be a JSON object")
    value = apply_task_economy_defaults(dict(value))
    unknown = sorted(set(value) - _TASK_FIELDS)
    if unknown:
        raise GuardError(
            "TASK_PACKET_UNKNOWN_FIELDS",
            f"unknown task fields: {', '.join(unknown)}",
        )
    supplied_schema = value.get("schema_version")
    if supplied_schema is not None and supplied_schema != TASK_SCHEMA_ID:
        raise GuardError("TASK_SCHEMA_VERSION_INVALID", f"schema_version must be {TASK_SCHEMA_ID}")

    objective = _bounded_string(value.get("objective"), "objective", MAX_OBJECTIVE)
    supplied_role = str(value.get("role") or "").strip().lower()
    if forced_role and supplied_role and supplied_role != forced_role:
        raise GuardError(
            "TASK_ROLE_MISMATCH",
            f"this tool requires role={forced_role}, got role={supplied_role}",
        )
    role = str(forced_role or supplied_role).strip().lower()
    if role not in ROLES:
        raise GuardError("TASK_ROLE_INVALID", f"role must be one of {sorted(ROLES)}")

    raw_root = _bounded_string(value.get("project_root"), "project_root", MAX_PATH)
    try:
        root = Path(raw_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GuardError("PROJECT_ROOT_INVALID", f"cannot resolve project_root: {exc}") from exc
    if not root.is_dir():
        raise GuardError("PROJECT_ROOT_INVALID", "project_root must be a directory")
    allow = [Path(p).expanduser().resolve() for p in allowed_roots]
    if not path_in_allowlist(root, allow):
        raise GuardError(
            "PROJECT_ROOT_UNTRUSTED",
            "project_root must resolve exactly to an allowlisted root",
        )

    expected_profile = "read-only" if role in {"consult", "skeptic"} else "workspace"
    profile = str(value.get("permission_profile") or expected_profile).strip().lower()
    if profile not in PERMISSION_PROFILES:
        raise GuardError(
            "PERMISSION_PROFILE_INVALID",
            f"permission_profile must be one of {sorted(PERMISSION_PROFILES)}",
        )
    if profile != expected_profile:
        raise GuardError(
            "PERMISSION_PROFILE_ROLE_MISMATCH",
            f"role {role} requires permission_profile={expected_profile}",
        )

    # The operator's budget stands in for the contract default, so raising it does
    # not depend on GROK_DELEGATE_ECONOMY also being on.
    max_turns = _bounded_int(
        value.get("max_turns", configured_max_turns(os.environ) or 40), "max_turns", 1, 60
    )
    timeout = _bounded_int(value.get("timeout_seconds", 1800), "timeout_seconds", 1, 3600)
    model = _optional_string(value.get("model"), "model", 128) or configured_model(os.environ)
    effort = (
        _optional_string(value.get("reasoning_effort"), "reasoning_effort", 16)
        or configured_reasoning_effort(os.environ)
        or "high"
    ).lower()
    if effort not in REASONING_EFFORTS:
        raise GuardError(
            "REASONING_EFFORT_INVALID",
            f"reasoning_effort must be one of {sorted(REASONING_EFFORTS)}",
        )
    # HEAD, not a branch name: "master" was wrong the moment a repo renamed its
    # default branch, and the job then failed preflight with BASE_UNREACHABLE for
    # a reason that had nothing to do with the task. Branching from whatever the
    # checkout is actually on is both correct and always reachable.
    base_ref = _optional_string(value.get("base_ref"), "base_ref", 256) or "HEAD"
    correlation = _bounded_string(value.get("correlation_id"), "correlation_id", MAX_CORRELATION)
    if not _CORRELATION_RE.fullmatch(correlation):
        raise GuardError(
            "CORRELATION_ID_INVALID",
            "correlation_id must contain only letters, digits, dot, colon, underscore or dash",
        )

    artifacts = _bounded_string_list(value.get("expected_artifacts", []), "expected_artifacts")
    if role in {"execute", "fix"} and not artifacts:
        raise GuardError(
            "EXPECTED_ARTIFACTS_REQUIRED",
            "execute and fix tasks require at least one expected_artifacts entry",
        )
    normalized_artifacts: list[str] = []
    for item in artifacts:
        candidate = Path(item)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise GuardError(
                "EXPECTED_ARTIFACT_INVALID",
                "expected_artifacts entries must be relative and must not contain '..'",
            )
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise GuardError("EXPECTED_ARTIFACT_ESCAPE", "expected artifact escapes project_root") from exc
        normalized_artifacts.append(candidate.as_posix())

    mounts = _bounded_string_list(value.get("mount_paths", []), "mount_paths")
    normalized_mounts: list[str] = []
    for item in mounts:
        candidate = Path(item)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise GuardError(
                "MOUNT_PATH_INVALID",
                "mount_paths entries must be relative to project_root and must not contain '..'",
            )
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise GuardError("MOUNT_PATH_ESCAPE", "mount path escapes project_root") from exc
        if looks_like_secret_path(candidate):
            # The whole reason a lane is built from a git ref is that
            # gitignored credentials are not in it. Mounting one back would
            # undo that in the one place the bridge still controls.
            raise GuardError(
                "MOUNT_PATH_FORBIDDEN",
                f"mount_paths must not name a credential file: {candidate.as_posix()}",
            )
        normalized_mounts.append(candidate.as_posix())

    review_lane = _optional_string(value.get("review_lane"), "review_lane", 96)
    if review_lane and role in {"execute", "fix"}:
        # A write role gets a lane of its own; asking it to stand in another
        # one would mean two workers in a single worktree.
        raise GuardError(
            "REVIEW_LANE_NOT_FOR_WRITE_ROLE",
            "review_lane is for read-only roles; a write role is given its own lane",
        )

    test_commands = _bounded_string_list(value.get("test_commands", []), "test_commands")
    if role in {"execute", "fix"} and not test_commands:
        raise GuardError(
            "TEST_COMMANDS_REQUIRED",
            "execute and fix tasks require at least one bridge-verifiable test_commands entry",
        )

    return {
        "schema_version": TASK_SCHEMA_ID,
        "objective": objective,
        "role": role,
        "project_root": str(root),
        "base_ref": base_ref,
        "model": model,
        "reasoning_effort": effort,
        "permission_profile": profile,
        "max_turns": max_turns,
        "timeout_seconds": timeout,
        "inputs": _bounded_string_list(value.get("inputs", []), "inputs"),
        "constraints": _bounded_string_list(value.get("constraints", []), "constraints"),
        "acceptance_criteria": _bounded_string_list(
            value.get("acceptance_criteria", []), "acceptance_criteria"
        ),
        "expected_artifacts": normalized_artifacts,
        "test_commands": test_commands,
        "mount_paths": normalized_mounts,
        "review_lane": review_lane,
        "correlation_id": correlation,
    }


def objective_hash(objective: str) -> str:
    return hashlib.sha256(objective.encode("utf-8")).hexdigest()


def build_prompt(task: Mapping[str, Any]) -> str:
    lines = [
        str(task["objective"]),
        "",
        "Role: " + str(task["role"]),
        "Permission profile: " + str(task["permission_profile"]),
        "Work only inside the supplied cwd. Never push, merge, change credentials, or use --always-approve.",
    ]
    for label, key in (
        ("Inputs", "inputs"),
        ("Constraints", "constraints"),
        ("Acceptance criteria", "acceptance_criteria"),
        ("Expected artifacts", "expected_artifacts"),
        ("Test commands", "test_commands"),
    ):
        values = list(task.get(key) or [])
        if values:
            lines.append("")
            lines.append(label + ":")
            lines.extend(f"- {value}" for value in values)
    if task.get("test_commands"):
        lines.extend(
            [
                "",
                # Live capture: asked to "report the exit code", the agent ran
                # `python -m pytest -q; echo EXIT_CODE=$LASTEXITCODE`. Permission
                # is an exact match against the list above, so the decorated
                # command was denied and the turns spent on it were wasted.
                "Run each test command exactly as written above, with nothing appended.",
                "The permission gate matches the text literally and denies anything else.",
                # Twice in a row a live job died here: the agent needed a number
                # it could not read off disk, wrote an ad-hoc script, was denied,
                # and stopped -- producing nothing at all after minutes of paid
                # work. Being told the rule was not enough; it also needed a
                # legal move for the case the rule forbids.
                "Those are the only commands available to you. Reading and searching files"
                " is always allowed; every other command is denied, and the denial can end"
                " your turn.",
                "If you need something you cannot get by reading or searching, do not try a"
                " command anyway. Write down what you would have run and why, and finish the"
                " rest of the work without it. A partial result on disk beats a denied"
                " command and nothing.",
            ]
        )
    if task["permission_profile"] == "read-only":
        lines.extend(
            [
                "",
                # A live capture on the operator's machine shows what this
                # paragraph is buying. The worker asked to edit a file, the
                # bridge answered `reject_once` -- the correct, non-fatal answer
                # -- and the CLI sent `session/cancel` anyway. The turn ended on
                # `stopReason: cancelled` with nothing delivered but an opening
                # sentence, while the CLI's own log recorded a perfectly
                # successful inference. No gate change can prevent that; the
                # only defence is the worker never reaching for the write.
                "You cannot create, modify, move or delete any file in this session, and you"
                " must not try. A write is refused, and this CLI ends the whole turn on a"
                " refused write -- you would deliver nothing at all.",
                "Your answer in this message IS the deliverable. Nothing you leave on disk"
                " will be collected, so put the whole result here.",
            ]
        )

    if task["role"] in {"execute", "fix"}:
        lines.extend(
            [
                "",
                "Make the requested file change now. Run the relevant bounded test before finishing.",
                "A summary without a real filesystem diff and acceptance evidence is not success.",
                # The bridge commits whatever is left in the lane once the job
                # ends, so stopping early costs review time, never the work.
                "Leave the work on disk; the bridge commits the lane branch when the job ends.",
            ]
        )
    return "\n".join(lines)


#: Windows and macOS resolve `Expected.txt` and `expected.txt` to one file, so a
#: receipt that compares the two as strings reports a single file under two names
#: -- once as a missing artifact and once as somebody else's change. Comparison
#: folds case where the filesystem does; the reasons still quote what was written.
_FOLD_CASE = os.name == "nt" or sys.platform == "darwin"


def _fold_path(value: Any) -> str:
    text = str(value).replace("\\", "/").rstrip("/")
    return text.casefold() if _FOLD_CASE else text


def finalize_receipt(receipt: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(receipt)
    out["schema_version"] = RECEIPT_SCHEMA_ID
    out["objective_hash"] = objective_hash(str(task["objective"]))
    out.setdefault("changed_files", [])
    out.setdefault("full_changed_files", list(out["changed_files"]))
    out.setdefault("commits", [])
    out.setdefault("diffstat", "")
    out.setdefault("unified_diff", "")
    out.setdefault("tests", [])
    out.setdefault("tests_skipped_reason", None)
    out.setdefault("verifier_touched_files", [])
    out.setdefault("worker_written_files", [])
    out.setdefault("foreign_changed_files", [])
    out.setdefault("artifacts", [])
    out.setdefault("findings", [])
    out.setdefault("summary", "")
    out.setdefault("blocked_reason", None)

    status = str(out.get("status") or "failed")
    if out.get("worker_alive_after_shutdown") is True:
        status = "failed"
        out["blocked_reason"] = "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"
        out["error_code"] = "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"
        out["error"] = "WORKER_STILL_ALIVE_AFTER_SHUTDOWN"
    if task["role"] in {"execute", "fix"} and status == "completed":
        expected_set = {_fold_path(path) for path in task.get("expected_artifacts", [])}
        # Judged on its own, before anything short-circuits. `full_changed_files`
        # answers "what is in this lane", which is a different question from
        # "what did this run do" -- and it is the one that decides whether the
        # branch is safe to merge.
        # Only what the permission gate let the worker write counts as the
        # worker's. Everything else in the tree belongs to somebody else: a test
        # run's __pycache__, or -- observed on a real machine -- another MCP
        # server the CLI has configured writing a log relative to its working
        # directory, which happens to be the lane. Blaming the worker for those
        # blocked every execute job with UNEXPECTED_CHANGED_FILES for files it
        # never touched, and a gate that cries wolf is a gate people switch off.
        # An empty list means the transport reported nothing, so fall back to
        # judging everything rather than silently accepting everything.
        authored = {_fold_path(path) for path in out.get("worker_written_files") or []}
        unexpected_changes = [
            str(path).replace("\\", "/").rstrip("/")
            for path in out["full_changed_files"]
            if _fold_path(path) not in expected_set
            and (not authored or _fold_path(path) in authored)
        ]
        foreign = sorted(
            str(path).replace("\\", "/").rstrip("/")
            for path in out["full_changed_files"]
            if _fold_path(path) not in expected_set and authored and _fold_path(path) not in authored
        )
        # Reported, never hidden: the operator still has to know the lane is not
        # only their work before they merge it.
        out["foreign_changed_files"] = foreign
        if not out["changed_files"]:
            status = "no_changes"
            out["blocked_reason"] = "EXECUTE_NO_CHANGES"
            # A run that changed nothing can still be sitting on files nobody
            # asked for. Reporting only "nothing happened" let the bridge commit
            # them to the lane anyway, and the next run -- whose base is that
            # commit -- returned a clean receipt with them still on the branch.
            if unexpected_changes:
                status = "blocked"
                out["blocked_reason"] = "UNEXPECTED_CHANGED_FILES: " + ", ".join(unexpected_changes)
        else:
            present = {_fold_path(path) for path in out["artifacts"]}
            missing = [p for p in task.get("expected_artifacts", []) if _fold_path(p) not in present]
            if missing:
                status = "blocked"
                out["blocked_reason"] = "EXPECTED_ARTIFACT_MISSING: " + ", ".join(missing)
            touched = {_fold_path(changed) for changed in out["changed_files"]}
            unchanged_artifacts = [
                p for p in task.get("expected_artifacts", []) if _fold_path(p) not in touched
            ]
            if unchanged_artifacts:
                status = "blocked"
                out["blocked_reason"] = "EXPECTED_ARTIFACT_NOT_CHANGED: " + ", ".join(unchanged_artifacts)
            if unexpected_changes:
                status = "blocked"
                out["blocked_reason"] = "UNEXPECTED_CHANGED_FILES: " + ", ".join(unexpected_changes)
            # The verifier runs after the worker and its writes land in the same
            # tree, so an artifact a test created reads exactly like delivered
            # work. Accepting that would let the test suite certify itself.
            self_written = sorted(
                str(path).replace("\\", "/").rstrip("/")
                for path in out["verifier_touched_files"]
                if _fold_path(path) in expected_set
            )
            if self_written:
                status = "blocked"
                out["blocked_reason"] = "ARTIFACT_WRITTEN_BY_VERIFIER: " + ", ".join(self_written)
            expected_tests = list(task.get("test_commands", []))
            valid_tests = [
                test for test in out["tests"]
                if isinstance(test, Mapping)
                and str(test.get("command") or "") in expected_tests
                and test.get("passed") is True
                and test.get("returncode") == 0
                and test.get("source") == "bridge-verifier"
            ]
            if len({test["command"] for test in valid_tests}) != len(set(expected_tests)):
                status = "blocked"
                out["blocked_reason"] = "TEST_EVIDENCE_MISSING"
            if any(
                isinstance(test, Mapping)
                and str(test.get("command") or "") in expected_tests
                # A run that never happened is not a run that failed. `passed`
                # is now absent when the verifier was cancelled or timed out,
                # and treating an absent boolean as False would turn "we never
                # got to it" into a red suite -- the very confusion the
                # outcome field was added to end.
                and test.get("outcome", "failed" if not test.get("passed") else "passed")
                == "failed"
                for test in out["tests"]
            ):
                status = "failed"
                out["blocked_reason"] = "TEST_FAILED"
            # Work the operator cannot review is not delivered work. The gate
            # judged artifacts and tests and never looked at whether the lane
            # commit succeeded, so a COMMIT_FAILED -- a rejecting hook, a
            # read-only checkout -- came back `completed` with `sha: None` and
            # nothing on the branch to merge.
            commit = out.get("lane_commit")
            if not isinstance(commit, Mapping):
                # An absent field is not an exemption. `{}` was caught and a
                # missing key was not, so a transport that never fills this in
                # -- `transport: legacy` is exactly one -- came back `completed`
                # with nothing on any branch to merge. The gate must not depend
                # on some other gate happening to catch the same receipt.
                #
                # Last, and only when nothing else objected: "no commit" is the
                # least informative thing that can be wrong with a receipt, and
                # it must not displace a reason that names the actual defect.
                if status == "completed":
                    status = "blocked"
                    out["blocked_reason"] = "LANE_COMMIT_MISSING: NOT_REPORTED"
            elif not commit.get("sha"):
                reason = str(commit.get("reason") or "UNKNOWN")
                if reason not in {"NOT_A_WRITE_ROLE"}:
                    status = "blocked"
                    out["blocked_reason"] = "LANE_COMMIT_MISSING: " + reason
    out["status"] = status
    # jobs.start_job owns transport-independent lifecycle state and keys it from
    # this boolean.  A terminal receipt without it was incorrectly persisted as
    # state=error even when every evidence gate passed.
    out["ok"] = status == "completed"
    # The finalized mapping is also the live MCP response.  Sanitize the whole
    # object here (not only when it is later persisted) so paths, findings, and
    # mapping keys cannot leak credential-shaped material to the caller.
    return redact_value(out)


def bounded_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively bounded, JSON-safe event with secret-looking fields removed."""
    deny_keys = {
        "secret", "token", "authorization", "signature", "api_key", "apikey",
        "password", "passwd", "cookie", "setcookie", "accesstoken", "refreshtoken",
        "clientsecret", "privatekey", "credential", "credentials",
    }

    def clean(value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "<TRUNCATED_DEPTH>"
        if isinstance(value, Mapping):
            return {
                redact_text(str(k))[:MAX_EVENT_TEXT]: clean(v, depth + 1)
                for k, v in value.items()
                if re.sub(r"[^a-z0-9]", "", str(k).lower()) not in deny_keys
            }
        if isinstance(value, (list, tuple)):
            return [clean(v, depth + 1) for v in value[:64]]
        if isinstance(value, (set, frozenset)):
            cleaned = [clean(v, depth + 1) for v in list(value)[:64]]
            return sorted(cleaned, key=str)
        if isinstance(value, str):
            return redact_text(value)[:MAX_EVENT_TEXT]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return redact_text(str(value))[:MAX_EVENT_TEXT]

    return clean(dict(event))


#: The name beside the value. Every one of these was found leaking by a skeptic
#: reading real files a worker is allowed to read: `SECRET_KEY=` in a Django
#: settings module, `AWS_SECRET_ACCESS_KEY=` in a compose file, `"api_key"` and
#: `"client_secret"` in JSON. The old list wanted `secret` to stand alone, so a
#: key that merely *contained* it walked straight through.
#: Deciding "is this key a secret" in Python instead of in the pattern. The
#: first version wrapped this alternation in `[A-Za-z0-9_.-]*` on both sides,
#: which is two unbounded quantifiers around an alternation -- catastrophic
#: backtracking. One line of a diff took 34ms and the test suite went from two
#: minutes to two hours. `redact_text` runs on every event and every line of
#: stderr, so this is the hot path, and a regex that can blow up on input a
#: worker chooses does not belong in it.
_SECRET_WORDS = re.compile(
    r"(?:secret|passwd|password|api[_-]?key|apikey|access[_-]?key|private[_-]?key"
    r"|access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?token|token"
    r"|credential|cookie|server[_-]?key)",
    re.IGNORECASE,
)
#: Bounded, and it cannot overlap what follows it, so matching stays linear.
_KEY = r"[A-Za-z0-9_.\[\]-]{1,64}"


def _looks_secret(name: str) -> bool:
    return bool(_SECRET_WORDS.search(name))


def _redact_assignments(text: str, pattern: re.Pattern[str], build) -> str:
    """Substitute, but do not let an innocent key hide a guilty one behind it.

    `re.sub` resumes after the whole match, so `URL: ws://host?server-key=x`
    matched key=`URL` with the entire address as its value, decided `URL` is not
    a secret, and skipped past `server-key=` without ever looking at it. Here a
    key that is not a secret costs only its own length, and scanning continues
    inside what it would have swallowed.
    """
    out: list[str] = []
    pos = 0
    while pos <= len(text):
        match = pattern.search(text, pos)
        if match is None:
            break
        key = match.group("key")
        if _looks_secret(key):
            out.append(text[pos:match.start()])
            out.append(build(match))
            pos = match.end()
        else:
            # Step past the name only, so the value stays available to the scan.
            resume = match.start("key") + len(key)
            out.append(text[pos:resume])
            pos = resume
    out.append(text[pos:])
    return "".join(out)

#: Each pattern carries its own replacement, because they no longer share a
#: shape: the quoted one has to put the quotes back.
#: `Authorization: Basic dXNlcjpwYXNz` used to lose only the word `Basic`,
#: leaving base64 of user:password in the clear. Kept apart from the two below
#: because the header name is fixed, so there is no key to judge.
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*)"
    r"(?:bearer|basic|digest|token|negotiate|apikey)?\s*(?:[^\s,;}\]]+)"
)

#: `.netrc` writes credentials with a space and no delimiter --
#: `machine host login user password hunter2` -- so every assignment pattern
#: above walks straight past it. It matters more than the format's age suggests:
#: the outbound redactor is the *only* defence the bridge actually has against a
#: secret in the working directory, because this CLI never asks the gate about a
#: read (Service/Research/2026-08-20-read-gate-reachability.md).
#:
#: Gated on the file's own marker word rather than applied to every `password`
#: in every diff, and deliberately not redacting `login`: a username is not the
#: secret, and replacing it would make ordinary prose unreadable for nothing.
_NETRC_MARKER = re.compile(r"(?im)^\s*(?:machine\s+\S+|default\s*$)")
_NETRC_PASSWORD = re.compile(r"(?i)(\bpassword\s+)(\S+)")

_SECRET_TEXT_PATTERNS = (
    # Quoted value: to the closing quote, so a passphrase does not survive from
    # its second word onwards.
    (
        re.compile(r"([\"']?)(?P<key>" + _KEY + r")\1(\s*[:=]\s*)(?!//)([\"'])[^\"'\n]{3,}\4"),
        lambda m: f"{m.group(1)}{m.group('key')}{m.group(1)}{m.group(3)}{m.group(4)}<REDACTED>{m.group(4)}",
    ),
    # Bare value: stops at a delimiter, never at a space alone. Parentheses are
    # excluded and eight characters are required so the redactor stops eating
    # code -- `token: str = ""` became `token: <REDACTED> = ""` and
    # `password_hash = bcrypt(x)` lost its call, and a redactor that mangles the
    # diff is one people turn off. A quoted value is still caught from three
    # characters up by the pattern above.
    (
        # `(?!//)` stops a URL scheme reading as an assignment: in
        # `ws://host?server-key=x` the key matched `ws` and its value swallowed
        # the rest of the URL, so the real `server-key=` was never examined.
        re.compile(r"(?P<key>" + _KEY + r")(\s*[:=]\s*)(?!//)([^\s\"'&,;}\]()\n]{8,})"),
        lambda m: f"{m.group('key')}{m.group(2)}<REDACTED>",
    ),
)

#: Vendor keys shaped `sk_live_…` / `pk_test_…`: an underscore where the older
#: list assumed a dash, which is how a Stripe secret key read as ordinary text.
_VENDOR_UNDERSCORE_KEY = re.compile(
    r"(?<![A-Za-z0-9])[a-z]{2,4}_(?:live|test|prod)_[A-Za-z0-9]{16,}",
    re.IGNORECASE | re.ASCII,
)

#: Credentials that carry their own prefix, so no `key=` context is needed to
#: recognise them. Only xai- and sk- were listed, which meant the bridge could
#: quote a GitHub token or a database URL from a file the worker was allowed to
#: read -- a docker-compose.yml is not a secret file, and the password in it is.
_BARE_CREDENTIAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?:xai|sk)-[A-Za-z0-9._-]{12,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|(?:AKIA|ASIA|AIDA|AROA)[A-Z0-9]{12,}"
    r"|AIza[A-Za-z0-9_-]{30,}"
    r"|ya29\.[A-Za-z0-9._-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"|glpat-[A-Za-z0-9._-]{16,}"
    r"|npm_[A-Za-z0-9]{30,}"
    r")",
    re.IGNORECASE | re.ASCII,
)

#: A password living in a URL rather than beside an `=`. The scheme and user are
#: kept, because a receipt that says only <REDACTED> is harder to act on than one
#: that says which service leaked.
#: The user may be empty (`redis://:pass@host`), and the password may itself
#: contain an `@` -- the old pattern stopped at the first one and leaked the
#: tail. Anchored so the match ends at the *last* `@` before the host.
_URL_USERINFO_PATTERN = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]{1,31}://)([^\s:/@]{0,128}):([^\s/]{1,256})@(?=[^\s/@]*(?:[/\s?#]|$))",
)

#: The same prefixes, wrapped onto the next line. A terminal breaking a long key
#: put the prefix and the body in different strings, and both halves then read as
#: ordinary text -- the joined form was redacted, the wrapped one was not.
_WRAPPED_CREDENTIAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:xai|sk|gh[pousr]|glpat|npm)[-_][ \t]*\r?\n[ \t]*[A-Za-z0-9._-]{12,}",
    re.IGNORECASE | re.ASCII,
)
_PEM_PRIVATE_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]{0,64}PRIVATE KEY-----[\s\S]*?"
    r"-----END [^-\r\n]{0,64}PRIVATE KEY-----",
    re.IGNORECASE,
)
_PEM_PRIVATE_MARKER_PATTERN = re.compile(
    r"-----(?:BEGIN|END) [^-\r\n]{0,64}(?:PRIVATE KEY|SECRET|ENCRYPTED)[^-\r\n]{0,64}-----",
    re.IGNORECASE,
)
_PEM_UNTERMINATED_PRIVATE_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]{0,64}PRIVATE KEY-----[\s\S]*\Z",
    re.IGNORECASE,
)


_EXTRA_SECRET_NEEDLES: list[str] = []
_MIN_SECRET_NEEDLE = 8


def register_secret_needle(value: str) -> None:
    """Remember an operator secret so receipts and logs cannot echo it.

    HTTP bearers are CSPRNG hex, not ``xai-`` / ``sk-`` prefixes, so the
    pattern redactor would otherwise leave them intact.
    """
    text = str(value).strip()
    if len(text) < _MIN_SECRET_NEEDLE:
        return
    if text not in _EXTRA_SECRET_NEEDLES:
        _EXTRA_SECRET_NEEDLES.append(text)


def reset_secret_needles_for_tests() -> None:
    _EXTRA_SECRET_NEEDLES.clear()


def redact_text(value: str) -> str:
    out = str(value)
    for needle in _EXTRA_SECRET_NEEDLES:
        out = out.replace(needle, "<REDACTED>")
    env_token = os.environ.get("GROK_DELEGATE_HTTP_TOKEN", "").strip()
    if len(env_token) >= _MIN_SECRET_NEEDLE:
        out = out.replace(env_token, "<REDACTED>")
    out = _PEM_PRIVATE_BLOCK_PATTERN.sub("<REDACTED_PEM_PRIVATE_KEY>", out)
    out = _PEM_UNTERMINATED_PRIVATE_BLOCK_PATTERN.sub("<REDACTED_PEM_PRIVATE_KEY>", out)
    out = _PEM_PRIVATE_MARKER_PATTERN.sub("<REDACTED_PEM_MARKER>", out)
    out = _WRAPPED_CREDENTIAL_PATTERN.sub("<REDACTED>", out)
    out = _BARE_CREDENTIAL_PATTERN.sub("<REDACTED>", out)
    out = _URL_USERINFO_PATTERN.sub(r"\1\2:<REDACTED>@", out)
    out = _VENDOR_UNDERSCORE_KEY.sub("<REDACTED>", out)
    out = _AUTHORIZATION_PATTERN.sub(r"\1<REDACTED>", out)
    if _NETRC_MARKER.search(out):
        out = _NETRC_PASSWORD.sub(r"\1<REDACTED>", out)
    for pattern, build in _SECRET_TEXT_PATTERNS:
        out = _redact_assignments(out, pattern, build)
    return out


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "<TRUNCATED_DEPTH>"
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            raw_key = str(key)
            safe_key = redact_text(raw_key)[:MAX_EVENT_TEXT]
            normalized = re.sub(r"[^a-z0-9]", "", raw_key.lower())
            if normalized in {
                "secret", "token", "authorization", "signature", "apikey", "password",
                "passwd", "cookie", "setcookie", "accesstoken", "refreshtoken",
                "clientsecret", "privatekey", "credential", "credentials",
            }:
                out[safe_key] = "<REDACTED>"
            else:
                out[safe_key] = redact_value(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_value(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        cleaned = [redact_value(item, depth + 1) for item in value]
        return sorted(cleaned, key=str)
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def _bounded_string(value: Any, field: str, cap: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > cap or any(c in text for c in ("\x00",)):
        raise GuardError("TASK_PACKET_BOUNDS", f"{field} exceeds its bound or contains NUL")
    return text


def _optional_string(value: Any, field: str, cap: int) -> str | None:
    if value is None or value == "":
        return None
    return _bounded_string(value, field, cap)


def _bounded_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be an array")
    if len(value) > MAX_ITEMS:
        raise GuardError("TASK_PACKET_BOUNDS", f"{field} exceeds {MAX_ITEMS} entries")
    return [_bounded_string(item, f"{field}[]", MAX_ITEM) for item in value]


def _bounded_int(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GuardError("TASK_PACKET_INVALID", f"{field} must be an integer") from exc
    if result < low or result > high:
        raise GuardError("TASK_PACKET_BOUNDS", f"{field} must be between {low} and {high}")
    return result


__all__ = [
    "EVENT_SCHEMA_ID",
    "MAX_EVENTS",
    "RECEIPT_SCHEMA_ID",
    "ROLES",
    "TASK_SCHEMA_ID",
    "TRANSPORTS",
    "bounded_event",
    "build_prompt",
    "finalize_receipt",
    "objective_hash",
    "redact_text",
    "redact_value",
    "register_secret_needle",
    "reset_secret_needles_for_tests",
    "validate_task_packet",
    "validate_transport",
]
