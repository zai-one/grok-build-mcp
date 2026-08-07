"""Host-reported project directory as an allowlist source.

An MCP host knows which directory the user actually opened — Claude Code exports
it to the spawned server as ``CLAUDE_PROJECT_DIR``. Adopting it removes the chore
of listing every project by hand, but it also means a root can enter the
allowlist without the operator writing it down. So the behaviour is opt-in, and
the tests that matter most here are the ones asserting it stays *off*.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grok_delegate import server
from grok_delegate.guard import (
    HOST_PROJECT_DIR_ENV,
    TRUST_HOST_ROOTS_ENV,
    host_provided_roots,
    trust_host_roots_enabled,
)


class TrustFlagTests(unittest.TestCase):
    """The opt-in itself, before any path handling."""

    def test_absent_flag_means_off(self) -> None:
        self.assertFalse(trust_host_roots_enabled({}))

    def test_accepted_spellings(self) -> None:
        for raw in ("1", "true", "TRUE", "yes", "on", " on "):
            with self.subTest(raw=raw):
                self.assertTrue(trust_host_roots_enabled({TRUST_HOST_ROOTS_ENV: raw}))

    def test_rejected_spellings(self) -> None:
        """Anything unrecognised must read as off, never as on."""
        for raw in ("0", "false", "no", "off", "", "maybe", "2"):
            with self.subTest(raw=raw):
                self.assertFalse(trust_host_roots_enabled({TRUST_HOST_ROOTS_ENV: raw}))


class HostProvidedRootsTests(unittest.TestCase):
    def test_no_roots_without_opt_in(self) -> None:
        """The whole point: a host-set variable alone grants nothing."""
        env = {HOST_PROJECT_DIR_ENV: r"D:\some\project"}
        self.assertEqual(host_provided_roots(env), [])

    def test_root_returned_when_opted_in(self) -> None:
        env = {TRUST_HOST_ROOTS_ENV: "1", HOST_PROJECT_DIR_ENV: r"D:\some\project"}
        self.assertEqual(host_provided_roots(env), [r"D:\some\project"])

    def test_opt_in_without_host_variable_is_empty(self) -> None:
        """Opting in must not invent a root when the host said nothing."""
        self.assertEqual(host_provided_roots({TRUST_HOST_ROOTS_ENV: "1"}), [])
        self.assertEqual(
            host_provided_roots({TRUST_HOST_ROOTS_ENV: "1", HOST_PROJECT_DIR_ENV: "   "}),
            [],
        )

    def test_surrounding_quotes_are_stripped(self) -> None:
        env = {TRUST_HOST_ROOTS_ENV: "1", HOST_PROJECT_DIR_ENV: '"D:\\quoted\\project"'}
        self.assertEqual(host_provided_roots(env), [r"D:\quoted\project"])


class LoadAllowedRootsTests(unittest.TestCase):
    """Integration with the allowlist the resolver actually consults."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.host_dir = Path(self._tmp.name) / "host"
        self.explicit_dir = Path(self._tmp.name) / "explicit"
        self.host_dir.mkdir()
        self.explicit_dir.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_host_root_ignored_while_opt_in_is_off(self) -> None:
        roots = server.load_allowed_roots(env={HOST_PROJECT_DIR_ENV: str(self.host_dir)})
        self.assertEqual(roots, [])

    def test_host_root_alone_forms_the_allowlist(self) -> None:
        roots = server.load_allowed_roots(
            env={TRUST_HOST_ROOTS_ENV: "1", HOST_PROJECT_DIR_ENV: str(self.host_dir)}
        )
        self.assertEqual(roots, [self.host_dir.resolve()])

    def test_host_root_widens_rather_than_replaces(self) -> None:
        """An explicit list must survive; the host root is added, not swapped in."""
        roots = server.load_allowed_roots(
            env={
                "GROK_DELEGATE_ALLOWED_ROOTS": str(self.explicit_dir),
                TRUST_HOST_ROOTS_ENV: "1",
                HOST_PROJECT_DIR_ENV: str(self.host_dir),
            }
        )
        self.assertEqual(roots, [self.explicit_dir.resolve(), self.host_dir.resolve()])

    def test_no_duplicate_when_host_repeats_an_explicit_root(self) -> None:
        roots = server.load_allowed_roots(
            env={
                "GROK_DELEGATE_ALLOWED_ROOTS": str(self.host_dir),
                TRUST_HOST_ROOTS_ENV: "1",
                HOST_PROJECT_DIR_ENV: str(self.host_dir),
            }
        )
        self.assertEqual(roots, [self.host_dir.resolve()])

    def test_single_pin_fallback_still_applies(self) -> None:
        """The REPO_ROOT pin fires on an empty list; the host root must not mask it."""
        roots = server.load_allowed_roots(
            env={
                "GROK_DELEGATE_REPO_ROOT": str(self.explicit_dir),
                TRUST_HOST_ROOTS_ENV: "1",
                HOST_PROJECT_DIR_ENV: str(self.host_dir),
            }
        )
        self.assertEqual(roots, [self.explicit_dir.resolve(), self.host_dir.resolve()])

    def test_injection_still_wins_outright(self) -> None:
        """Explicit injection is a closed set — the environment cannot extend it."""
        roots = server.load_allowed_roots(
            injected=[self.explicit_dir],
            env={TRUST_HOST_ROOTS_ENV: "1", HOST_PROJECT_DIR_ENV: str(self.host_dir)},
        )
        self.assertEqual(roots, [self.explicit_dir.resolve()])


class ResolveTrustedRepoRootTests(unittest.TestCase):
    """The gate a tool call actually passes through."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.host_dir = Path(self._tmp.name) / "host"
        self.host_dir.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self._saved: dict[str, str | None] = {}

    def _set_env(self, **values: str | None) -> None:
        import os

        for key, value in values.items():
            self._saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        import os

        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_host_root_accepted_end_to_end(self) -> None:
        self._set_env(
            GROK_DELEGATE_ALLOWED_ROOTS=None,
            GROK_DELEGATE_REPO_ROOT=None,
            **{TRUST_HOST_ROOTS_ENV: "1", HOST_PROJECT_DIR_ENV: str(self.host_dir)},
        )
        resolved = server.resolve_trusted_repo_root({"repo_root": str(self.host_dir)})
        self.assertEqual(resolved, self.host_dir.resolve())

    def test_still_fails_closed_without_opt_in(self) -> None:
        self._set_env(
            GROK_DELEGATE_ALLOWED_ROOTS=None,
            GROK_DELEGATE_REPO_ROOT=None,
            **{TRUST_HOST_ROOTS_ENV: None, HOST_PROJECT_DIR_ENV: str(self.host_dir)},
        )
        with self.assertRaises(Exception) as ctx:
            server.resolve_trusted_repo_root({"repo_root": str(self.host_dir)})
        self.assertEqual(getattr(ctx.exception, "code", None), "ALLOWED_ROOTS_EMPTY")

    def test_sibling_of_host_root_is_not_trusted(self) -> None:
        """Membership is exact equality, and opting in must not soften that."""
        sibling = self.host_dir.parent / "neighbour"
        sibling.mkdir()
        self._set_env(
            GROK_DELEGATE_ALLOWED_ROOTS=None,
            GROK_DELEGATE_REPO_ROOT=None,
            **{TRUST_HOST_ROOTS_ENV: "1", HOST_PROJECT_DIR_ENV: str(self.host_dir)},
        )
        with self.assertRaises(Exception):
            server.resolve_trusted_repo_root({"repo_root": str(sibling)})


if __name__ == "__main__":
    unittest.main()
