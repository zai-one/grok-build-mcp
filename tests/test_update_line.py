"""The update block also has to fit on one status line.

Hosts that do not parse the structured receipt still need the three answers:
available, up to date, unknown. The line is a terminal row, so it stays
under 120 characters.
"""

from __future__ import annotations

from grok_delegate.updater import format_update_line

LOCAL = "a" * 40
REMOTE = "b" * 40


def test_available_names_how_far_behind() -> None:
    line = format_update_line(
        {
            "available": True,
            "behind": 3,
            "local_sha": LOCAL,
            "remote_sha": REMOTE,
        }
    )
    assert line == f"update available: 3 commits behind ({LOCAL[:7]} -> {REMOTE[:7]})"
    assert "\n" not in line
    assert len(line) < 120


def test_matching_heads_read_as_up_to_date() -> None:
    line = format_update_line(
        {
            "available": False,
            "behind": None,
            "local_sha": LOCAL,
            "remote_sha": LOCAL,
            "reason": None,
        }
    )
    assert line == "bridge is up to date"
    assert "\n" not in line
    assert len(line) < 120


def test_unknown_carries_the_reason() -> None:
    line = format_update_line(
        {
            "available": False,
            "reason": "REMOTE_UNREACHABLE",
        }
    )
    assert line == "update state unknown: REMOTE_UNREACHABLE"
    assert "\n" not in line
    assert len(line) < 120
