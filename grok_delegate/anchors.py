"""Goal anchor extraction and worktree pre-validation (R7-B).

Why: fabricated path citations silently kill executor sessions — the model
searches for a non-existent file, fails, and ends its turn. Extracting
path-like tokens from the goal and checking them against the worktree lets
the runner fail fast when *every* anchor is fictional, and surface partial
misses when only some are. Pure stdlib; Claude wires this into runner later.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Bound how many anchors we surface — prevents a novel-length goal from
# producing an unbounded checklist. Truncation is deterministic (first N).
MAX_ANCHORS = 64

# Reject absurdly long tokens (paste accidents, minified blobs).
MAX_ANCHOR_LEN = 256

# Line-number suffix on citations like ``src/foo.ts:123`` (strip for exists()).
_LINE_SUFFIX_RE = re.compile(r":(\d+)$")

# URLs must never become anchors (``https://example.com/a/b.ts`` looks path-like).
_URL_RE = re.compile(r"https?://[^\s\)\]>`'\"\,;]+", re.IGNORECASE)

# Markdown link target: [label](target) — capture target only.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Backticked span — capture interior.
_BACKTICK_RE = re.compile(r"`([^`]+)`")

# Path-like token: requires at least one / or \ separator so prose like
# ``e.g. version 1.2`` never matches. Allows globs (*), Windows drives,
# relative and absolute forms, optional :line suffix.
_PATH_TOKEN_RE = re.compile(
    r"(?:"
    r"[A-Za-z]:[/\\]"  # Windows drive absolute
    r"|[/\\]"  # Unix absolute or UNC-ish
    r"|\.{1,2}[/\\]"  # ./ or ../
    r"|[\w.-]+[/\\]"  # relative component + separator
    r")"
    r"(?:[\w.$*+\[\]-]+[/\\])*"  # further components (glob-safe)
    r"[\w.$*+\[\]-]+"  # final component
    r"(?::\d+)?"  # optional line suffix
)


def extract_goal_anchors(goal: str) -> list[str]:
    """Pull path-like tokens from *goal* text, bounded and de-duplicated.

    Why: the runner needs a cheap, pure pre-check before spawn. Matching is
    intentionally strict (separator required) so URLs and dotted prose stay out.
    """
    if not goal or not goal.strip():
        return []

    # Mask URLs so path-shaped URL tails cannot be harvested.
    masked = _URL_RE.sub(lambda m: " " * len(m.group(0)), goal)

    found: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        cleaned = token.strip().strip("\"'<>.,;")
        if not cleaned or len(cleaned) > MAX_ANCHOR_LEN:
            return
        if not _looks_like_anchor(cleaned):
            return
        # Stable key: normalize slashes for de-dupe only; keep original form.
        key = cleaned.replace("\\", "/")
        if key in seen:
            return
        seen.add(key)
        found.append(cleaned)

    # Prefer explicit markup (links, backticks) then bare tokens in the mask.
    for match in _MD_LINK_RE.finditer(masked):
        target = match.group(1).strip()
        # Markdown can wrap angles: [x](<path>) or titles: [x](path "title")
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        target = target.split()[0] if target else target
        _add(target)

    for match in _BACKTICK_RE.finditer(masked):
        _add(match.group(1))

    for match in _PATH_TOKEN_RE.finditer(masked):
        _add(match.group(0))

    return found[:MAX_ANCHORS]


def validate_goal_anchors(goal: str, worktree: str | Path) -> dict[str, Any]:
    """Check extracted anchors against *worktree* filesystem only.

    Returns ``{ok, missing, checked}``. ``ok`` is True when nothing is missing
    (including the empty-goal case). Absolute paths are reported in
    ``checked`` but never treated as in-worktree hits. Existence is pure
    filesystem — a path living only on another git branch counts as missing.
    """
    anchors = extract_goal_anchors(goal)
    if not anchors:
        return {"ok": True, "missing": [], "checked": []}

    root = Path(worktree)
    checked: list[str] = []
    missing: list[str] = []

    for raw in anchors:
        checked.append(raw)
        if not _anchor_present(raw, root):
            missing.append(raw)

    return {
        "ok": len(missing) == 0,
        "missing": missing,
        "checked": checked,
    }


def _looks_like_anchor(token: str) -> bool:
    """True when *token* is path-shaped (separator or glob), not a URL/prose."""
    if _URL_RE.match(token):
        return False
    if "://" in token:
        return False
    # Require a path separator or a glob star so ``e.g.`` / ``1.2`` stay out.
    if "/" not in token and "\\" not in token:
        return False
    # Must match the path token grammar (guards odd markdown debris).
    if not _PATH_TOKEN_RE.fullmatch(token):
        # Allow bare glob roots like ``src/**`` that fullmatch might still hit;
        # if the regex rejects, drop it.
        return False
    return True


def _strip_line_suffix(token: str) -> str:
    return _LINE_SUFFIX_RE.sub("", token)


def _is_absolute_anchor(token: str) -> bool:
    """Absolute / drive-letter / UNC — never resolve as relative to worktree."""
    if token.startswith("/") or token.startswith("\\"):
        return True
    if re.match(r"^[A-Za-z]:[/\\]", token):
        return True
    try:
        if Path(token).is_absolute():
            return True
    except (OSError, ValueError):
        pass
    return False


def _anchor_present(raw: str, root: Path) -> bool:
    """Filesystem presence check for one extracted anchor token."""
    token = _strip_line_suffix(raw.strip())
    if not token:
        return False
    if _is_absolute_anchor(token):
        # Reported to caller via checked/missing; never a worktree hit.
        return False

    # Normalize separators for pathlib on all platforms.
    rel = token.replace("\\", "/")

    if any(ch in rel for ch in "*?["):
        return _glob_present(root, rel)

    candidate = root / rel
    try:
        return candidate.exists()
    except OSError:
        return False


def _glob_present(root: Path, pattern: str) -> bool:
    """True if *pattern* matches at least one path under *root*."""
    try:
        # pathlib.Path.glob treats pattern relative to the Path receiver.
        # Do not resolve outside root: pattern is relative by construction
        # (absolute anchors already rejected).
        if pattern.startswith("/"):
            return False
        matches = root.glob(pattern)
        for _ in matches:
            return True
        # On case-insensitive Windows, pathlib glob is already case-folded.
        # If nothing matched and we are on a case-insensitive FS, still False
        # for truly absent trees.
        return False
    except (OSError, ValueError):
        return False


def _case_fold_exists(root: Path, rel: str) -> bool:
    """Optional helper: case-insensitive walk (unused on Windows Path.exists)."""
    # Kept for clarity/tests on platforms where exists() is case-sensitive.
    # Walk components and pick a case-insensitive match at each level.
    parts = Path(rel).parts
    cur = root
    for part in parts:
        if any(ch in part for ch in "*?["):
            return False
        try:
            if not cur.is_dir():
                return False
            names = {e.name: e for e in cur.iterdir()}
        except OSError:
            return False
        if part in names:
            cur = names[part]
            continue
        lowered = {k.lower(): v for k, v in names.items()}
        hit = lowered.get(part.lower())
        if hit is None:
            return False
        cur = hit
    try:
        return cur.exists()
    except OSError:
        return False


# Silence unused-import lint if os is only needed for platform tests later.
_ = os
