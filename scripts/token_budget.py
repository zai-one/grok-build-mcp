#!/usr/bin/env python3
"""ZAI.ONE MCP standard: token-budget and Agent Skills checks (dependency-free).

Same file in every zai-one MCP repo (copy, no shared dependency).

Checks
  * skills/<name>/SKILL.md: frontmatter per
    https://agentskills.io/specification - name 1-64 [a-z0-9-], no "--", equals the
    folder; description 1..1024 (standard: <=300 recommended); compatibility <=500;
    metadata str->str; body <= 80 lines; relative links resolve.
  * Optional --manifest JSON {"instructions": str, "tools": [{"name", "description",
    "inputSchema"?}]}: instructions <= 1200 chars, each description <= 300 chars,
    first sentence <= 120 chars, whole tools/list <= --max-tools-kb.

Usage
  python scripts/token_budget.py [--root .] [--manifest tools.json] [--strict]
Exit code 1 on any error. Warnings (soft limits) fail only with --strict.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")

LIMITS = {
    "instructions": 1200,
    "tool_description": 300,
    "tool_first_sentence": 120,
    "skill_description_hard": 1024,
    "skill_description_soft": 300,
    "skill_compatibility": 500,
    "skill_body_lines": 80,
    "tools_kb": 24,
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the YAML subset used by SKILL.md: scalars and one nested map level."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' frontmatter")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("frontmatter is not closed with '---'")
    head, body = text[4:end], text[end + 4 :].lstrip("\n")
    data: dict[str, object] = {}
    parent: str | None = None
    block: str | None = None  # key of a folded (>) or literal (|) scalar being read
    block_lines: list[str] = []
    style = ">"

    def close_block() -> None:
        if block is not None:
            joiner = " " if style == ">" else "\n"
            data[block] = joiner.join(block_lines).strip()

    for raw in head.split("\n"):
        if block is not None:
            if raw.startswith((" ", "\t")) or not raw.strip():
                block_lines.append(raw.strip())
                continue
            close_block()
            block, block_lines = None, []
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw.startswith((" ", "\t"))
        key, sep, value = raw.strip().partition(":")
        if not sep:
            raise ValueError(f"bad frontmatter line: {raw!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if indented and parent is not None:
            nested = data.setdefault(parent, {})
            assert isinstance(nested, dict)
            nested[key.strip()] = value
            continue
        parent = None
        if value in (">", ">-", "|", "|-"):
            block, block_lines, style = key.strip(), [], value[0]
            continue
        if value == "":
            parent = key.strip()
            data[parent] = {}
        else:
            data[key.strip()] = value
    close_block()
    return data, body


def first_sentence(text: str) -> str:
    match = re.search(r"[.!?](\s|$)", text)
    return text[: match.start() + 1] if match else text


def check_skill(path: Path, report: Report) -> None:
    rel = path.as_posix()
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        report.err(f"{rel}: {exc}")
        return
    unknown = sorted(set(meta) - ALLOWED_KEYS)
    if unknown:
        report.err(f"{rel}: unknown frontmatter keys {unknown}; put extras under metadata")
    name = str(meta.get("name", ""))
    folder = path.parent.name
    if not NAME_RE.match(name) or "--" in name:
        report.err(f"{rel}: name {name!r} must be 1-64 chars [a-z0-9-], no leading/trailing/double '-'")
    if name != folder:
        report.err(f"{rel}: name {name!r} must equal folder {folder!r}")
    desc = str(meta.get("description", ""))
    if not desc:
        report.err(f"{rel}: description is required")
    elif len(desc) > LIMITS["skill_description_hard"]:
        report.err(f"{rel}: description {len(desc)} > {LIMITS['skill_description_hard']}")
    elif len(desc) > LIMITS["skill_description_soft"]:
        report.warn(f"{rel}: description {len(desc)} > {LIMITS['skill_description_soft']} (standard)")
    comp = meta.get("compatibility")
    if comp is not None and len(str(comp)) > LIMITS["skill_compatibility"]:
        report.err(f"{rel}: compatibility > {LIMITS['skill_compatibility']}")
    md = meta.get("metadata")
    if md is not None and not isinstance(md, dict):
        report.err(f"{rel}: metadata must be a map of strings")
    lines = body.rstrip("\n").count("\n") + 1
    if lines > LIMITS["skill_body_lines"]:
        report.warn(f"{rel}: body {lines} lines > {LIMITS['skill_body_lines']}; move detail to references/")
    for target in LINK_RE.findall(body):
        if "://" in target or target.startswith("mailto:"):
            continue
        if not (path.parent / target).exists():
            report.err(f"{rel}: broken link {target}")


def find_skills(root: Path) -> list[Path]:
    patterns = ("skills/*/SKILL.md",)
    found: list[Path] = []
    for pattern in patterns:
        found.extend(sorted(root.glob(pattern)))
    return found


def check_manifest(manifest: dict, report: Report, label: str = "manifest") -> None:
    instructions = manifest.get("instructions") or ""
    if len(instructions) > LIMITS["instructions"]:
        report.err(f"{label}: instructions {len(instructions)} > {LIMITS['instructions']} chars")
    tools = manifest.get("tools") or []
    for tool in tools:
        name = tool.get("name", "?")
        desc = tool.get("description") or ""
        if not desc:
            report.err(f"{label}: tool {name} has no description")
            continue
        if len(desc) > LIMITS["tool_description"]:
            report.err(f"{label}: tool {name} description {len(desc)} > {LIMITS['tool_description']}")
        head = first_sentence(desc)
        if len(head) > LIMITS["tool_first_sentence"]:
            report.warn(f"{label}: tool {name} first sentence {len(head)} > {LIMITS['tool_first_sentence']}")
    size_kb = len(json.dumps(tools, ensure_ascii=False, separators=(",", ":")).encode()) / 1024
    if size_kb > LIMITS["tools_kb"]:
        report.err(f"{label}: tools/list {size_kb:.1f} KB > {LIMITS['tools_kb']} KB")


def run(root: Path, manifest_path: Path | None = None) -> Report:
    report = Report()
    for skill in find_skills(root):
        check_skill(skill, report)
    if manifest_path is not None:
        check_manifest(json.loads(manifest_path.read_text(encoding="utf-8")), report, manifest_path.name)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", type=Path)
    ap.add_argument("--manifest", type=Path, help="JSON with instructions + tools (tools/list shape)")
    ap.add_argument("--max-tools-kb", type=float, default=LIMITS["tools_kb"])
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args(argv)
    LIMITS["tools_kb"] = args.max_tools_kb
    report = run(args.root, args.manifest)
    for msg in report.warnings:
        print(f"warning: {msg}")
    for msg in report.errors:
        print(f"error: {msg}")
    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    print("token budget: " + ("FAIL" if failed else "ok"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
