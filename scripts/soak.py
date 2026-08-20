"""Drive every role through the bridge for real, and say plainly what broke.

The suite proves units. This proves the product: a scratch repository, a live
Grok CLI, and one job per role, judged by the receipt rather than by anything
the agent said about itself. It exists because three separate failures this
month were invisible to a green suite and obvious within thirty seconds of
actually running a job -- a skeptic that could not run its own pytest, a poll
that truncated the record it read, a refusal that killed a job instead of
bending it.

Grok side is deliberately not economised: the worker is cheap and the point is
to load it until something bends. The host side is the opposite -- every poll is
bounded and measured, because host context is the resource this bridge exists to
protect.

    py -3 scripts/soak.py                 # every role
    py -3 scripts/soak.py --only skeptic  # one of them
    py -3 scripts/soak.py --turns 60      # lean harder

Exit code is 0 only when every row passes. Evidence lands in
Service/Audits/soak-<stamp>.json so a later run can be compared to this one.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import server  # noqa: E402

POLL_SECONDS = 5.0
POLL_BUDGET_CHARS = 16_384


@dataclass
class Row:
    """One role, one job, one verdict."""

    role: str
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    #: Things worth reporting that are not failures. Kept apart from
    #: `reasons` because `passed` is computed from that list, and a note
    #: appended there would fail a row for being informative.
    notes: list[str] = field(default_factory=list)
    receipt: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    max_poll_chars: int = 0

    def fail(self, why: str) -> None:
        self.reasons.append(why)
        self.passed = False


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)


def seed_repo() -> Path:
    """A small real repository: something to read, change, and test."""
    root = Path(tempfile.mkdtemp(prefix="grok-soak-")).resolve()
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    (root / ".grok-mcp.json").write_text('{"preset": "max"}', encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=soak@local", "-c", "user.name=soak", "commit", "-q", "-m", "seed")
    return root


def call(tool: str, args: dict, root: Path) -> dict:
    return server.handle_tool_call(tool, args, allowed_roots=[root])


def run_job(tool: str, task: dict, root: Path, lane: str, row: Row, timeout_s: float) -> dict:
    """Start a job and poll it to completion, measuring what each poll costs."""
    started = time.monotonic()
    body = call(tool, {"task": task, "lane": lane}, root)
    if not body.get("ok"):
        row.fail(f"start refused: {body.get('error')} {str(body.get('message'))[:120]}")
        return body
    job_id = body["job_id"]

    while True:
        time.sleep(POLL_SECONDS)
        polled = call("grok_agent_poll", {"job_id": job_id, "limit": 5}, root)
        row.max_poll_chars = max(row.max_poll_chars, len(json.dumps(polled, default=str)))
        state = polled.get("state")
        if state in {"done", "error", "cancelled"}:
            row.elapsed_s = time.monotonic() - started
            return polled
        if time.monotonic() - started > timeout_s:
            call("grok_agent_cancel", {"job_id": job_id}, root)
            row.fail(f"still running after {timeout_s:.0f}s")
            row.elapsed_s = time.monotonic() - started
            return polled


# --- what each role has to prove ------------------------------------------------


def check_common(row: Row, polled: dict) -> dict:
    receipt = polled.get("result") or {}
    row.receipt = {
        k: receipt.get(k)
        for k in ("status", "blocked_reason", "changed_files", "tests", "tests_skipped_reason",
                  "lane_commit", "stop_reason", "verifier_touched_files",
                  "foreign_changed_files", "worker_written_files")
    }
    if polled.get("error"):
        row.fail(f"transport error: {polled['error']}")
    if receipt.get("blocked_reason") == "ACP_STOP_cancelled":
        # Judge the substance, not the string. `ACP_STOP_cancelled` is what a
        # gate refusal that killed the turn looks like -- and also what simply
        # running out of turns looks like, which is a documented success:
        # the verifier still runs and the lane still commits
        # (`agent_runtime.py:675`). These jobs are small and get forty turns, so
        # exhaustion here would itself be news; but a row that fails must say
        # which of the two happened rather than assuming.
        verified = [t for t in (receipt.get("tests") or [])
                    if t.get("source") == "bridge-verifier"]
        committed = bool((receipt.get("lane_commit") or {}).get("sha"))
        if verified and committed:
            row.notes.append("stopped on turns, but verified and committed")
        else:
            row.fail("the turn died and left nothing verified or committed behind it")
    if receipt.get("blocked_reason") == "ACP_OUTPUT_LIMIT":
        row.fail("output cap truncated the job instead of bounding it")
    if row.max_poll_chars > POLL_BUDGET_CHARS:
        row.fail(f"a single poll cost the host {row.max_poll_chars} chars")
    return receipt


def soak_read_only(role: str, tool: str, root: Path, turns: int) -> Row:
    """consult and skeptic: must read, must be able to run a declared command."""
    row = Row(role=role)
    polled = run_job(
        tool,
        {
            "objective": (
                "Read app.py and tests/test_app.py. Then RUN the declared test command and "
                "report its exact exit status. Answer in one short paragraph naming what add() "
                "does and whether the suite passed."
            ),
            "project_root": str(root),
            "correlation_id": f"soak-{role}",
            "role": role,
            "permission_profile": "read-only",
            "test_commands": ["py -3 -m pytest tests -q"],
            "max_turns": turns,
        },
        root,
        f"soak-{role}",
        row,
        timeout_s=420,
    )
    receipt = check_common(row, polled)
    if receipt.get("status") not in {"completed", "no_changes"}:
        row.fail(f"status={receipt.get('status')!r}, expected a finished read-only turn")
    if receipt.get("tests_skipped_reason") != "NOT_A_WRITE_ROLE":
        row.fail(f"tests_skipped_reason={receipt.get('tests_skipped_reason')!r}")
    if (receipt.get("lane_commit") or {}).get("committed"):
        row.fail("a read-only role committed something")
    summary = str(receipt.get("summary") or polled.get("result", {}).get("summary") or "")
    if len(summary) < 40:
        row.fail("no usable text came back; a read-only role's answer IS its output")
    row.passed = not row.reasons
    return row


def soak_write(role: str, tool: str, root: Path, turns: int) -> Row:
    """execute and fix: must change a file, get verified, and land on a lane."""
    row = Row(role=role)
    target = "app.py"
    polled = run_job(
        tool,
        {
            "objective": (
                f"Add a function `mul(a, b)` returning a * b to {target}, and a test for it in "
                "tests/test_app.py. Keep the existing add() untouched. Then run the declared "
                "test command."
            ),
            "project_root": str(root),
            "correlation_id": f"soak-{role}",
            "role": role,
            "permission_profile": "workspace",
            "expected_artifacts": [target, "tests/test_app.py"],
            "test_commands": ["py -3 -m pytest tests -q"],
            "base_ref": "HEAD",
            "max_turns": turns,
        },
        root,
        f"soak-{role}",
        row,
        timeout_s=900,
    )
    receipt = check_common(row, polled)
    if receipt.get("status") != "completed":
        row.fail(f"status={receipt.get('status')!r} blocked={receipt.get('blocked_reason')!r}")
    if not receipt.get("changed_files"):
        row.fail("nothing changed, so there is nothing to accept")
    tests = receipt.get("tests") or []
    bridge_run = [t for t in tests if t.get("source") == "bridge-verifier"]
    if not bridge_run:
        row.fail("no bridge-verifier test result: the receipt rests on the agent's word")
    elif any(not t.get("passed") or t.get("returncode") != 0 for t in bridge_run):
        row.fail(f"verifier saw a red suite: {bridge_run}")
    if not (receipt.get("lane_commit") or {}).get("sha"):
        row.fail("the lane was not committed, so the work is not reviewable")
    if receipt.get("verifier_touched_files"):
        row.fail(f"the verifier wrote files: {receipt['verifier_touched_files']}")
    row.passed = not row.reasons
    return row


ROLES = {
    "consult": lambda root, turns: soak_read_only("consult", "grok_agent_consult", root, turns),
    "skeptic": lambda root, turns: soak_read_only("skeptic", "grok_agent_review", root, turns),
    "execute": lambda root, turns: soak_write("execute", "grok_agent_execute", root, turns),
    "fix": lambda root, turns: soak_write("fix", "grok_agent_fix", root, turns),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", choices=sorted(ROLES), default=None)
    parser.add_argument("--turns", type=int, default=40, help="worker turn budget per job")
    args = parser.parse_args(argv)

    status = server.handle_tool_call("grok_agent_status", {})
    legacy = status.get("legacy") or {}
    if not (legacy.get("auth") or {}).get("present"):
        print("no Grok CLI session: run `grok login` first", file=sys.stderr)
        return 2
    print(f"bridge {legacy.get('server', {}).get('version')} · CLI "
          f"{legacy.get('grok', {}).get('version')} · turns {args.turns}\n")

    rows: list[Row] = []
    for role in (args.only or list(ROLES)):
        root = seed_repo()  # a clean repository per role: no shared state to blame
        print(f"-- {role}: running against {root}", flush=True)
        try:
            row = ROLES[role](root, args.turns)
        except Exception as exc:  # noqa: BLE001 -- a crash here is a finding, not a stack trace
            row = Row(role=role)
            row.fail(f"harness raised {type(exc).__name__}: {exc}")
        rows.append(row)
        print(f"   {'PASS' if row.passed else 'FAIL'} in {row.elapsed_s:.0f}s"
              f" · worst poll {row.max_poll_chars} chars", flush=True)
        for reason in row.reasons:
            print(f"     - {reason}", flush=True)
        for note in row.notes:
            print(f"     . {note}", flush=True)

    print("\n" + "=" * 66)
    for row in rows:
        print(f"  {'PASS' if row.passed else 'FAIL'}  {row.role:<9}"
              f" {row.elapsed_s:>6.0f}s  poll<={row.max_poll_chars:>6}  "
              f"{'; '.join(row.reasons)[:70]}")
    print("=" * 66)

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out = _ROOT / "Service" / "Audits" / f"soak-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "bridge": legacy.get("server", {}).get("version"),
                "cli": legacy.get("grok", {}).get("version"),
                "turns": args.turns,
                "rows": [
                    {"role": r.role, "passed": r.passed, "reasons": r.reasons,
                     "elapsed_s": round(r.elapsed_s, 1), "max_poll_chars": r.max_poll_chars,
                     "notes": r.notes,
                     "receipt": r.receipt}
                    for r in rows
                ],
            },
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    print(f"evidence: {out.relative_to(_ROOT)}")
    return 0 if all(r.passed for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
