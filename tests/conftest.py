"""Keep a test run out of the operator's real state directory.

Job records are persisted by default now, so a test that boots a server would
otherwise write into `%LOCALAPPDATA%\\grok-delegate\\jobs` -- the same directory
a real session uses. Point the whole run at a temporary directory instead: the
default is exercised by `tests/test_jobs_persistence.py`, which asks
`resolve_jobs_dir` directly rather than by letting a server write.
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_jobs_dir() -> "object":
    # ignore_cleanup_errors because a server subprocess started by a test may
    # still hold a job file open when the session ends, and on Windows an open
    # handle makes the directory unremovable -- which turned a green run into
    # a teardown ERROR and a non-zero exit.
    with tempfile.TemporaryDirectory(
        prefix="grok-delegate-tests-jobs-", ignore_cleanup_errors=True
    ) as tmp:
        previous = os.environ.get("GROK_DELEGATE_JOBS_DIR")
        os.environ["GROK_DELEGATE_JOBS_DIR"] = tmp
        try:
            yield tmp
        finally:
            if previous is None:
                os.environ.pop("GROK_DELEGATE_JOBS_DIR", None)
            else:
                os.environ["GROK_DELEGATE_JOBS_DIR"] = previous
