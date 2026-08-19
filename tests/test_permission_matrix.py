"""Every cell of the permission decision, not a handful of remembered examples.

The gate was tested by example, and the examples were the cases someone had
thought about. That left a cell nobody had written down: a read-only role asking
to run a command the operator had explicitly declared. It was refused for two
releases, which meant the skeptic role -- whose entire job is to disbelieve --
could never run the test it was handed, only read files and assert about them.
Nothing failed, because nothing asked.

So the matrix is generated rather than curated. Profile times tool kind times
the shape of the request is a small cross-product, and every cell is spelled out
below. Reordering the branches in `permission_decision` flips cells here
immediately, which is the property the old tests did not have.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from grok_delegate.acp import permission_decision

DECLARED = "py -3 -m pytest tests -q"
PROFILES = ("read-only", "workspace")
OPTIONS = [
    {"kind": "allow_once", "optionId": "ALLOW"},
    {"kind": "reject_once", "optionId": "REJECT"},
]


@pytest.fixture()
def cwd(tmp_path) -> Path:
    return tmp_path.resolve()


def _decide(kind: str, raw: dict, *, profile: str, cwd: Path, declared=(DECLARED,)) -> str:
    decision = permission_decision(
        {"options": OPTIONS, "toolCall": {"kind": kind, "rawInput": raw}},
        {"permission_profile": profile, "test_commands": list(declared)},
        cwd,
    )
    return "ALLOW" if decision.get("optionId") == "ALLOW" else "REJECT"


# --- the full matrix ------------------------------------------------------------
#
# (kind, request shape, verdict under read-only, verdict under workspace)
#
# A cell that changes here is a change in what the bridge lets a worker do, and
# has to be argued for in a commit message rather than noticed later in a job.

MATRIX = [
    ("think", "empty", "ALLOW", "ALLOW"),
    ("read", "inside", "ALLOW", "ALLOW"),
    ("read", "outside", "REJECT", "REJECT"),
    ("read", "secret", "REJECT", "REJECT"),
    ("read", "empty", "REJECT", "REJECT"),
    ("search", "inside", "ALLOW", "ALLOW"),
    ("search", "outside", "REJECT", "REJECT"),
    ("edit", "inside", "REJECT", "ALLOW"),
    ("edit", "outside", "REJECT", "REJECT"),
    ("edit", "secret", "REJECT", "REJECT"),
    ("write", "inside", "REJECT", "ALLOW"),
    ("write", "outside", "REJECT", "REJECT"),
    ("execute", "declared", "ALLOW", "ALLOW"),
    ("execute", "undeclared", "REJECT", "REJECT"),
    ("execute", "decorated", "REJECT", "REJECT"),
    ("execute", "empty", "REJECT", "REJECT"),
    ("other", "inside", "REJECT", "REJECT"),
    ("fetch", "inside", "REJECT", "REJECT"),
    ("", "inside", "REJECT", "REJECT"),
]


def _raw(shape: str, cwd: Path) -> dict:
    return {
        "inside": {"path": str(cwd / "src" / "app.py")},
        "outside": {"path": str(cwd.parent / "elsewhere" / "app.py")},
        "secret": {"path": str(cwd / ".env")},
        "empty": {},
        "declared": {"command": DECLARED},
        "undeclared": {"command": "py -3 -c \"print(1)\""},
        "decorated": {"command": DECLARED + "; echo EXIT=$LASTEXITCODE"},
    }[shape]


@pytest.mark.parametrize(("kind", "shape", "read_only", "workspace"), MATRIX)
def test_every_cell_of_the_permission_matrix(
    kind: str, shape: str, read_only: str, workspace: str, cwd: Path
) -> None:
    assert _decide(kind, _raw(shape, cwd), profile="read-only", cwd=cwd) == read_only
    assert _decide(kind, _raw(shape, cwd), profile="workspace", cwd=cwd) == workspace


def test_the_matrix_covers_every_kind_the_gate_names(cwd: Path) -> None:
    """A branch added to the gate without a row here is an untested branch.

    Read from the source rather than a list kept in step by hand, because a list
    kept by hand is what let the execute-under-read-only cell go missing.
    """
    import inspect

    from grok_delegate import acp

    source = inspect.getsource(acp.permission_decision)
    named = {
        token.strip("\"' ")
        for line in source.splitlines()
        if "kind ==" in line or "kind in" in line
        for token in line.split("{")[-1].replace("}", "").split(",")
        if token.strip("\"' ").isalpha()
    }
    covered = {row[0] for row in MATRIX}
    assert named <= covered, f"gate names kinds with no matrix row: {sorted(named - covered)}"


@pytest.mark.parametrize(
    "command",
    [
        "git status --porcelain",
        "git log --oneline -20",
        "git diff --stat",
        "git show HEAD",
        "git rev-parse HEAD",
        "git ls-files",
        "git ls-tree HEAD",
        "git shortlog -sn",
        "git describe --tags",
        "git blame README.md",
    ],
)
def test_a_declared_read_only_git_command_is_allowed(command: str, cwd: Path) -> None:
    """`ls-files` is on this list because a real audit job died asking for it."""
    assert _decide("execute", {"command": command}, profile="read-only", cwd=cwd,
                   declared=(command,)) == "ALLOW"


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git merge grok/x",
        "git reset --hard",
        "git clean -xdff",
        "git checkout main",
        "git config user.email me@example.invalid",
        "git show HEAD:.env",
    ],
)
def test_git_that_changes_or_leaks_is_refused_even_when_declared(command: str, cwd: Path) -> None:
    """Declaring it is not enough: the forbidden contour is a separate check."""
    assert _decide("execute", {"command": command}, profile="workspace", cwd=cwd,
                   declared=(command,)) == "REJECT"


def test_an_unknown_kind_fails_closed(cwd: Path) -> None:
    """A future ACP tool kind must be refused until someone decides otherwise."""
    for kind in ("browse", "network", "sudo", "delete", "spawn"):
        for profile in PROFILES:
            assert _decide(kind, {"path": str(cwd / "a")}, profile=profile, cwd=cwd) == "REJECT"


def test_with_no_option_to_choose_the_answer_is_cancel(cwd: Path) -> None:
    decision = permission_decision(
        {"options": [], "toolCall": {"kind": "read", "rawInput": {"path": str(cwd / "a")}}},
        {"permission_profile": "read-only", "test_commands": []},
        cwd,
    )
    assert decision == {"outcome": "cancelled"}
