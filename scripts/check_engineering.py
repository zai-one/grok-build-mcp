#!/usr/bin/env python3
"""Standalone offline check; the canonical copy also syncs its generated bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

BEGIN = "<!-- mcp-standard:begin "
END = "<!-- mcp-standard:end -->"
LOCK = "docs/engineering/standard.json"
CORE = "docs/engineering/core.md"
MAX_BYTES = 4096
MAX_LINES = 30
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template")
PRIVATE_PATHS = (
    "AGENTS.local.md",
    "CLAUDE.local.md",
    "docs/engineering/private/",
    "docs/engineering/local/",
    ".tasks/",
    "outputs/",
    "audit-screenshots/",
    ".claude/settings.local.json",
    ".claude/worktrees/",
    ".codex/config.local.toml",
    ".mcp.local.json",
    "mcp-client.json",
    "mcp-client.toml",
    ".env",
    ".env.*",
)
PRIVATE_INDEX_PATHS = (*PRIVATE_PATHS, *(f":(exclude){path}" for path in ENV_EXAMPLES))


def check_private_index(root: Path) -> None:
    """Ignore rules do not protect files already added to the Git index."""
    if not (root / ".git").exists():
        return
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required to verify private paths in the index")
    required = {f"/{path}" for path in PRIVATE_PATHS} | {f"!/{path}" for path in ENV_EXAMPLES}
    if not required.issubset(safe_path(root, ".gitignore").read_text().splitlines()):
        raise ValueError(
            "root .gitignore must explicitly protect private workspace paths and keep env examples public"
        )
    try:
        result = subprocess.run(  # noqa: S603 -- fixed read-only Git command, no shell
            [git, "-C", str(root), "ls-files", "-z", "--", *PRIVATE_INDEX_PATHS],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("cannot verify private paths in Git index") from exc
    if result.returncode:
        raise ValueError("cannot verify private paths in Git index")
    if result.stdout:
        raise ValueError("private workspace files are tracked; remove them from the index before publishing")
    samples = [
        path + "privacy-check" if path.endswith("/") else path.replace("*", "privacy-check")
        for path in PRIVATE_PATHS
    ]
    expected = b"\0".join(path.encode() for path in samples) + b"\0"
    try:
        ignored = subprocess.run(  # noqa: S603 -- fixed read-only Git command, no shell
            [git, "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
            input=expected,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("cannot verify private ignore rules") from exc
    if ignored.returncode or ignored.stdout != expected:
        raise ValueError("private workspace paths must remain ignored")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts:
        raise ValueError(f"unsafe bundle path: {relative}")
    path = root / part
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"symlink or escaping bundle path: {relative}")
    return path


def read(path: Path) -> bytes:
    return path.read_bytes()


def block(core: bytes, version: str) -> str:
    return f"{BEGIN}version={version} sha256={digest(core)} -->\n{core.decode().rstrip()}\n{END}"


def split_agents(text: str) -> tuple[str, str, str]:
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError("AGENTS.md must contain exactly one standard block")
    start, finish = text.index(BEGIN), text.index(END) + len(END)
    if finish < start:
        raise ValueError("invalid standard marker order")
    return text[:start], text[start:finish], text[finish:]


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix=".mcp-standard-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_lock(root: Path) -> dict:
    data = json.loads(read(safe_path(root, LOCK)))
    if data.get("schema_version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("invalid standard manifest")
    if not re.fullmatch(r"\d+\.\d+\.\d+", data.get("version", "")):
        raise ValueError("invalid standard version")
    required = {
        CORE,
        "scripts/check_engineering.py",
        *(f"docs/engineering/{name}.md" for name in ("contracts", "safety", "releases")),
    }
    if set(data["files"]) != required:
        raise ValueError("incomplete or unexpected standard files")
    return data


def check(root: Path) -> dict:
    check_private_index(root)
    lock = load_lock(root)
    for relative, expected in lock["files"].items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"invalid hash: {relative}")
        if digest(read(safe_path(root, relative))) != expected:
            raise ValueError(f"standard drift: {relative}")
    agents = read(safe_path(root, "AGENTS.md"))
    if len(agents) > MAX_BYTES:
        raise ValueError(f"AGENTS.md exceeds {MAX_BYTES} UTF-8 bytes")
    text = agents.decode("utf-8")
    if len(text.splitlines()) > MAX_LINES:
        raise ValueError(f"AGENTS.md exceeds {MAX_LINES} lines")
    if split_agents(text)[1] != block(read(safe_path(root, CORE)), lock["version"]):
        raise ValueError("AGENTS.md generated block differs from its standard")
    if read(safe_path(root, "CLAUDE.md")) != b"@AGENTS.md\n":
        raise ValueError("CLAUDE.md must import only the short AGENTS.md")
    for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
        if "://" not in link and not safe_path(root, link).is_file():
            raise ValueError(f"broken AGENTS.md link: {link}")
    return {"version": lock["version"], "agents_bytes": len(agents), "agents_lines": len(text.splitlines())}


def canonical_bundle(source: Path) -> tuple[dict[str, bytes], str]:
    version = (source / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("invalid canonical VERSION")
    files = {CORE: read(source / "core.md"), "scripts/check_engineering.py": read(source / "engineering.py")}
    files.update(
        {
            f"docs/engineering/{name}.md": read(source / "guides" / f"{name}.md")
            for name in ("contracts", "safety", "releases")
        }
    )
    return files, version


def sync(root: Path, source: Path, apply: bool = False) -> list[str]:
    """Preserve local bytes; recover interrupted updates only from exact old/new bytes."""
    check_private_index(root)
    old = load_lock(root)
    files, version = canonical_bundle(source)
    current_agents = read(safe_path(root, "AGENTS.md")).decode()
    previous, current_block, local = split_agents(current_agents)
    agents = (previous + block(files[CORE], version) + local).encode()
    if len(agents) > MAX_BYTES:
        raise ValueError("updated AGENTS.md would exceed byte budget")
    if len(agents.decode().splitlines()) > MAX_LINES:
        raise ValueError("updated AGENTS.md would exceed line budget")
    lock = {
        "schema_version": 1,
        "version": version,
        "files": {name: digest(body) for name, body in files.items()},
    }
    expected = {
        **files,
        "AGENTS.md": agents,
        LOCK: (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode(),
    }
    # A changed bundle under the same version is a publication error, not an update.
    if version == old["version"] and lock["files"] != old["files"]:
        raise ValueError("canonical content changed without a VERSION bump")
    if tuple(map(int, version.split("."))) < tuple(map(int, old["version"].split("."))):
        raise ValueError("standard downgrade is forbidden")
    # A process interruption may leave old and new bundle files with the old lock.
    # Accept only these two exact versions, never arbitrary local edits.
    for name, body in files.items():
        if digest(read(safe_path(root, name))) not in {old["files"][name], digest(body)}:
            raise ValueError(f"standard drift: {name}")
    old_header = f"{BEGIN}version={old['version']} sha256={old['files'][CORE]} -->\n"
    old_body = current_block.removeprefix(old_header).removesuffix(f"\n{END}")
    valid_old = (
        current_block.startswith(old_header) and digest((old_body + "\n").encode()) == old["files"][CORE]
    )
    if not valid_old and current_block != block(files[CORE], version):
        raise ValueError("AGENTS.md generated block differs from old and new standard")
    if read(safe_path(root, "CLAUDE.md")) != b"@AGENTS.md\n":
        raise ValueError("CLAUDE.md must import only the short AGENTS.md")
    changed = [name for name, body in expected.items() if read(safe_path(root, name)) != body]
    if apply:
        for name in changed:
            atomic_write(safe_path(root, name), expected[name])
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="offline local consistency check (default)")
    parser.add_argument("--sync", action="store_true", help="compare against the canonical bundle")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--apply", action="store_true", help="apply an explicitly requested canonical update")
    args = parser.parse_args()
    try:
        if args.apply and not args.sync:
            raise ValueError("--apply requires --sync")
        if args.sync:
            changed = sync(args.root, args.source, args.apply)
            result = {"version": load_lock(args.root)["version"], "changed": changed}
            if changed and not args.apply:
                print(json.dumps(result))
                return 1
            result.update(check(args.root))
        else:
            result = check(args.root)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL MCP standard: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
