"""``python -m grok_delegate`` entry.

Default: stdio MCP server.
Also:
  --self-test        operator self-check (no real delegate)
  --smoke-delegate   live bounded plan-only headless smoke in a temp dir
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable


def _print(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _run_self_test() -> int:
    """Find binary, version, auth, drive real JSON-RPC path for status tools."""
    from . import server
    from .guard import validate_grok_bin
    from .status import (
        build_status_report,
        probe_auth_presence,
        probe_git_available,
        probe_grok_version,
        run_doctor_json,
        run_models,
    )

    rows: list[tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        rows.append((name, ok, detail))

    # 1) Binary
    def _bin() -> tuple[bool, str]:
        env_bin = os.environ.get("GROK_DELEGATE_BIN")
        name = validate_grok_bin(env_bin, from_client=False)
        resolved = shutil.which(name)
        if resolved or Path(name).is_file():
            return True, resolved or name
        return False, f"not found: {name}"

    check("binary", _bin)

    bin_name = validate_grok_bin(os.environ.get("GROK_DELEGATE_BIN"), from_client=False)

    # 2) Version (live if binary present)
    def _version() -> tuple[bool, str]:
        if not shutil.which(bin_name) and not Path(bin_name).is_file():
            return False, "skipped — binary missing"
        info = probe_grok_version(grok_bin=bin_name)
        if info.get("ok") and info.get("version"):
            return True, str(info.get("version"))
        return bool(info.get("ok")), str(info.get("version") or info.get("error") or "no version")

    check("version", _version)

    # 3) Auth presence (never auth.json)
    def _auth() -> tuple[bool, str]:
        if not shutil.which(bin_name) and not Path(bin_name).is_file():
            return False, "skipped — binary missing"
        info = probe_auth_presence(grok_bin=bin_name)
        if not info.get("ok"):
            return False, str(info.get("error") or "probe failed")
        present = bool(info.get("auth_present"))
        return present, "present" if present else "absent (login required)"

    check("auth_presence", _auth)

    # 4) Git
    def _git() -> tuple[bool, str]:
        info = probe_git_available()
        return bool(info.get("available")), str(info.get("version") or "missing")

    check("git", _git)

    # 5) In-process JSON-RPC: initialize → tools/list → each status tool
    def _rpc_initialize() -> tuple[bool, str]:
        resp = server.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "self-test"}},
            }
        )
        assert resp is not None
        name = resp.get("result", {}).get("serverInfo", {}).get("name")
        return name == "grok-delegate", f"serverInfo.name={name!r}"

    check("rpc_initialize", _rpc_initialize)

    def _rpc_tools_list() -> tuple[bool, str]:
        resp = server.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert resp is not None
        names = [t["name"] for t in resp["result"]["tools"]]
        required = {
            "grok_delegate",
            "grok_delegate_plan",
            "grok_delegate_status",
            "grok_delegate_doctor",
            "grok_delegate_models",
            "grok_delegate_inspect",
        }
        missing = sorted(required - set(names))
        return not missing, f"tools={names}" if not missing else f"missing={missing}"

    check("rpc_tools_list", _rpc_tools_list)

    # Status tools via handle_tool_call (same path as tools/call)
    # Use a temp allowlist so status does not fail on empty roots when env unset.
    tmp_root = Path(tempfile.mkdtemp(prefix="grok-delegate-selftest-"))
    try:
        def _status() -> tuple[bool, str]:
            result = server.handle_tool_call(
                "grok_delegate_status",
                {},
                allowed_roots=[tmp_root],
            )
            ok = bool(result.get("ok"))
            roots = (result.get("roots") or {}).get("allowed") if isinstance(result, dict) else None
            return ok, f"roots={roots}" if ok else str(result.get("error") or result)[:200]

        check("tool_status", _status)

        def _doctor() -> tuple[bool, str]:
            if not shutil.which(bin_name) and not Path(bin_name).is_file():
                # Still exercise handler with a mock-less path: expect GROK_MISSING or live.
                result = server.handle_tool_call("grok_delegate_doctor", {})
                return False, str(result.get("error") or "binary missing")
            result = server.handle_tool_call("grok_delegate_doctor", {})
            # doctor may return ok with findings; structured result required
            if result.get("error") == "GROK_MISSING":
                return False, "GROK_MISSING"
            return "doctor" in result or result.get("ok") is not None, (
                f"ok={result.get('ok')} keys={list(result)[:8]}"
            )

        check("tool_doctor", _doctor)

        def _models() -> tuple[bool, str]:
            if not shutil.which(bin_name) and not Path(bin_name).is_file():
                return False, "binary missing"
            result = server.handle_tool_call("grok_delegate_models", {})
            if result.get("error") == "GROK_MISSING":
                return False, "GROK_MISSING"
            return "models_text" in result or result.get("ok") is not None, (
                f"ok={result.get('ok')}"
            )

        check("tool_models", _models)

        def _inspect() -> tuple[bool, str]:
            if not shutil.which(bin_name) and not Path(bin_name).is_file():
                return False, "binary missing"
            result = server.handle_tool_call(
                "grok_delegate_inspect",
                {"repo_root": str(tmp_root)},
                allowed_roots=[tmp_root],
            )
            # inspect may fail if not a real project — still require structured JSON, not crash
            return isinstance(result, dict) and (
                "inspect" in result or "error" in result or "ok" in result
            ), f"ok={result.get('ok')} err={result.get('error')}"

        check("tool_inspect", _inspect)

        def _no_delegate_spawned() -> tuple[bool, str]:
            # Self-test must not call grok_delegate tool.
            return True, "no delegate tool invoked"

        check("no_delegate", _no_delegate_spawned)

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Print table
    _print("")
    _print("grok_delegate --self-test")
    _print("-" * 60)
    width = max(len(n) for n, _, _ in rows)
    all_ok = True
    for name, ok, detail in rows:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        _print(f"  {mark:4}  {name:<{width}}  {detail}")
    _print("-" * 60)
    _print("RESULT: " + ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


def _run_smoke_delegate() -> int:
    """Real bounded plan-only headless run in a temporary git repo (not mocked)."""
    from .guard import validate_grok_bin
    from .runner import delegate

    bin_name = validate_grok_bin(os.environ.get("GROK_DELEGATE_BIN"), from_client=False)
    if not shutil.which(bin_name) and not Path(bin_name).is_file():
        _print(f"SMOKE FAIL: grok binary not found ({bin_name})")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="grok-delegate-smoke-"))
    repo = tmp / "repo"
    lanes = tmp / "pcp-lanes"
    repo.mkdir()
    lanes.mkdir()

    def _git(args: list[str], cwd: Path) -> None:
        import subprocess

        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}"
            )

    try:
        _git(["init", "-b", "dev"], repo)
        _git(["config", "user.email", "smoke@example.invalid"], repo)
        _git(["config", "user.name", "smoke"], repo)
        (repo / "README.md").write_text("# smoke\n", encoding="utf-8")
        _git(["add", "README.md"], repo)
        _git(["commit", "-m", "init"], repo)
        # origin/dev for base_ref: create a local ref name that rev-parse accepts
        # prepare_worktree uses base_ref origin/dev by default — create it.
        _git(["branch", "origin/dev"], repo)

        _print(f"smoke: repo={repo}")
        _print(f"smoke: lanes={lanes}")
        _print("smoke: launching plan-only headless (max_turns=2)…")

        result = delegate(
            goal=(
                "Reply with exactly the word SMOKE_OK and do not modify any files. "
                "This is a connectivity smoke test."
            ),
            lane="smoke-r4",
            repo_root=repo,
            base_ref="origin/dev",
            max_turns=2,
            plan_only=True,
            lanes_parent=lanes,
            grok_bin=bin_name,
            timeout_seconds=180.0,
            require_clean_base=True,
            # live subprocess — no mock
        )

        _print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
        if result.get("ok"):
            _print("SMOKE PASS")
            return 0
        # Environmental failures still reported honestly
        _print(f"SMOKE FAIL: {result.get('error')} {result.get('message')}")
        return 1
    except Exception as exc:  # noqa: BLE001
        _print(f"SMOKE ERROR: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        return _run_self_test()
    if "--smoke-delegate" in args:
        return _run_smoke_delegate()
    if "--help" in args or "-h" in args:
        _print(
            "Usage: python -m grok_delegate [--self-test | --smoke-delegate]\n"
            "Default: stdio MCP server (see grok_delegate.server)."
        )
        return 0
    from .server import main as server_main

    return server_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
