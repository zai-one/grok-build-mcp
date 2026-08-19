"""Unit tests for grok_delegate.verdict (R7-C structured lane verdict).

Every scenario listed in Service/Archive/GOAL-ROUND7-AUTONOMY.md R7-C. Pure unit tests —
no subprocess, no real git, no real grok. Diff inputs are plain dicts shaped
like runner.collect_diff.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import verdict as V  # noqa: E402


def _valid_payload(**overrides: object) -> dict:
    """Minimal valid lane-verdict object; overrides replace keys."""
    base: dict = {
        "files_written": ["grok_delegate/verdict.py"],
        "committed": True,
        "tests_added": 1,
        "gates_run": False,
        "self_skeptic_findings": [],
        "blocked_reason": None,
        "summary": "delivered verdict module",
    }
    base.update(overrides)
    return base


def _diff(*, changed: list[str] | None = None, commits: list[str] | None = None) -> dict:
    return {
        "ok": True,
        "changed_files": list(changed or []),
        "diffstat": "",
        "commits": list(commits or []),
    }


class ParseLaneVerdictTests(unittest.TestCase):
    """parse_lane_verdict: tolerant, never raises."""

    def test_valid_json_object_parses_ok(self) -> None:
        """Well-formed JSON → ok with normalized verdict."""
        payload = _valid_payload()
        raw = json.dumps(payload, ensure_ascii=False)
        result = V.parse_lane_verdict(raw)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["verdict"]["files_written"], payload["files_written"])
        self.assertEqual(result["verdict"]["committed"], True)
        self.assertEqual(result["verdict"]["tests_added"], 1)
        self.assertEqual(result["verdict"]["summary"], "delivered verdict module")
        self.assertIsNone(result["verdict"]["blocked_reason"])

    def test_malformed_json_is_verdict_missing_never_raises(self) -> None:
        """Broken JSON text → VERDICT_MISSING; parse_lane_verdict never raises."""
        try:
            result = V.parse_lane_verdict("{not valid json,,,")
        except Exception as exc:  # pragma: no cover - failure path for the assertion
            self.fail(f"parse_lane_verdict raised {type(exc).__name__}: {exc}")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "VERDICT_MISSING")
        self.assertIsNone(result["verdict"])
        self.assertIsNone(result["field"])

    def test_absent_stdout_is_verdict_missing(self) -> None:
        """None input → VERDICT_MISSING (absent verdict)."""
        result = V.parse_lane_verdict(None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "VERDICT_MISSING")
        self.assertIsNone(result["verdict"])
        self.assertIn("absent", (result.get("message") or "").lower())

    def test_empty_stdout_is_verdict_missing(self) -> None:
        """Empty / whitespace-only stdout → VERDICT_MISSING."""
        for raw in ("", "   ", "\n\t  \n"):
            with self.subTest(raw=repr(raw)):
                result = V.parse_lane_verdict(raw)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "VERDICT_MISSING")
                self.assertIsNone(result["verdict"])

    def test_non_dict_json_array_is_verdict_missing(self) -> None:
        """JSON array is not a verdict object → VERDICT_MISSING."""
        result = V.parse_lane_verdict(json.dumps([_valid_payload()]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "VERDICT_MISSING")
        self.assertIsNone(result["verdict"])

    def test_non_dict_json_string_is_verdict_missing(self) -> None:
        """JSON string scalar is not a verdict object → VERDICT_MISSING."""
        result = V.parse_lane_verdict(json.dumps("just a string"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "VERDICT_MISSING")
        self.assertIsNone(result["verdict"])

    def test_non_dict_json_number_is_verdict_missing(self) -> None:
        """JSON number scalar is not a verdict object → VERDICT_MISSING."""
        result = V.parse_lane_verdict("42")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "VERDICT_MISSING")
        self.assertIsNone(result["verdict"])

    def test_extra_unknown_fields_are_tolerated(self) -> None:
        """Unknown keys survive parse (bounded); required shape still ok."""
        payload = _valid_payload(
            lane_id="r7c-verdict",
            notes=["extra", "list"],
            nested={"k": "v"},
        )
        result = V.parse_lane_verdict(json.dumps(payload, ensure_ascii=False))
        self.assertTrue(result["ok"], msg=result)
        verdict = result["verdict"]
        self.assertEqual(verdict["lane_id"], "r7c-verdict")
        self.assertEqual(verdict["notes"], ["extra", "list"])
        self.assertEqual(verdict["nested"], {"k": "v"})
        # Required fields still present and normalized.
        self.assertEqual(verdict["committed"], True)
        self.assertEqual(verdict["tests_added"], 1)

    def test_missing_required_field_is_verdict_invalid_naming_field(self) -> None:
        """Object missing a required key → VERDICT_INVALID with field named."""
        for field in V.REQUIRED_VERDICT_FIELDS:
            with self.subTest(field=field):
                payload = _valid_payload()
                del payload[field]
                result = V.parse_lane_verdict(json.dumps(payload))
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "VERDICT_INVALID")
                self.assertEqual(result["field"], field)
                self.assertIsNone(result["verdict"])
                self.assertIn(field, result.get("message") or "")

    def test_tests_added_negative_is_verdict_invalid(self) -> None:
        """tests_added < 0 → VERDICT_INVALID naming tests_added."""
        result = V.parse_lane_verdict(json.dumps(_valid_payload(tests_added=-1)))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "VERDICT_INVALID")
        self.assertEqual(result["field"], "tests_added")
        self.assertIsNone(result["verdict"])

    def test_tests_added_non_int_is_verdict_invalid(self) -> None:
        """tests_added must be int (not float/string/bool) → VERDICT_INVALID."""
        for bad in (1.5, "2", True, None, [1]):
            with self.subTest(bad=bad):
                result = V.parse_lane_verdict(json.dumps(_valid_payload(tests_added=bad)))
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "VERDICT_INVALID")
                self.assertEqual(result["field"], "tests_added")
                self.assertIsNone(result["verdict"])

    def test_oversize_verdict_input_is_bounded_not_crashed(self) -> None:
        """Input longer than MAX_VERDICT_INPUT_CHARS is truncated; never crashes."""
        # Build a payload whose JSON exceeds the input bound. Truncation yields
        # incomplete JSON → VERDICT_MISSING (safe fail-closed), never an exception.
        huge_summary = "x" * (V.MAX_VERDICT_INPUT_CHARS + 5_000)
        payload = _valid_payload(summary=huge_summary)
        raw = json.dumps(payload)
        self.assertGreater(len(raw), V.MAX_VERDICT_INPUT_CHARS)
        try:
            result = V.parse_lane_verdict(raw)
        except Exception as exc:  # pragma: no cover
            self.fail(f"oversize input crashed: {type(exc).__name__}: {exc}")
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)
        self.assertIn(result["reason"], ("VERDICT_MISSING", "VERDICT_INVALID", None))
        # If somehow parse survived, summary must still be bounded.
        if result.get("ok") and result.get("verdict"):
            summary = result["verdict"]["summary"]
            self.assertLessEqual(
                len(summary),
                V.MAX_VERDICT_STRING_CHARS + len("…(truncated)"),
            )

    def test_oversize_string_fields_are_bounded_after_parse(self) -> None:
        """Post-parse string bounds apply without raising."""
        long_finding = "f" * (V.MAX_VERDICT_STRING_CHARS + 100)
        long_summary = "s" * (V.MAX_VERDICT_STRING_CHARS + 50)
        # Keep total under input bound so parse succeeds and field bounds apply.
        payload = _valid_payload(
            summary=long_summary,
            self_skeptic_findings=[long_finding],
            files_written=["ok.py"],
            committed=False,
            tests_added=0,
        )
        result = V.parse_lane_verdict(json.dumps(payload))
        self.assertTrue(result["ok"], msg=result)
        summary = result["verdict"]["summary"]
        finding = result["verdict"]["self_skeptic_findings"][0]
        self.assertTrue(summary.endswith("…(truncated)"))
        self.assertTrue(finding.endswith("…(truncated)"))
        self.assertLessEqual(len(summary), V.MAX_VERDICT_STRING_CHARS + len("…(truncated)"))
        self.assertLessEqual(len(finding), V.MAX_VERDICT_STRING_CHARS + len("…(truncated)"))

    def test_non_ascii_summary_and_findings_round_trip_utf8(self) -> None:
        """Non-ASCII text in summary/findings is preserved (UTF-8, no crash)."""
        summary = "Готово: verdict + тесты — 日本語 ✓"
        findings = ["не ASCII finding", "emoji 🔥 ok", "café résumé"]
        payload = _valid_payload(
            summary=summary,
            self_skeptic_findings=findings,
            committed=False,
            files_written=[],
            tests_added=0,
        )
        raw = json.dumps(payload, ensure_ascii=False)
        # Also exercise the bytes path (UTF-8).
        result_str = V.parse_lane_verdict(raw)
        result_bytes = V.parse_lane_verdict(raw.encode("utf-8"))
        for result in (result_str, result_bytes):
            self.assertTrue(result["ok"], msg=result)
            self.assertEqual(result["verdict"]["summary"], summary)
            self.assertEqual(result["verdict"]["self_skeptic_findings"], findings)

    def test_mapping_input_parses_without_re_dump(self) -> None:
        """Pre-parsed mapping is validated directly (CLI already decoded JSON)."""
        result = V.parse_lane_verdict(_valid_payload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"]["committed"], True)


class ReconcileVerdictTests(unittest.TestCase):
    """reconcile_verdict: trust git over prose."""

    def test_valid_verdict_matching_git_reality_is_ok(self) -> None:
        """Claim matches collect_diff → status ok."""
        payload = _valid_payload(
            files_written=["grok_delegate/verdict.py", "tests/test_verdict.py"],
            committed=True,
            tests_added=12,
        )
        parsed = V.parse_lane_verdict(payload)
        self.assertTrue(parsed["ok"])
        diff = _diff(
            changed=["grok_delegate/verdict.py", "tests/test_verdict.py"],
            commits=["abc1234 feat(verdict): R7-C structured lane verdict"],
        )
        rec = V.reconcile_verdict(parsed, diff)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["status"], "ok")
        self.assertIsNone(rec["reason"])
        self.assertEqual(
            rec["changed_files"],
            ["grok_delegate/verdict.py", "tests/test_verdict.py"],
        )
        self.assertEqual(len(rec["commits"]), 1)

    def test_files_written_claim_with_empty_git_is_verdict_unsupported(self) -> None:
        """files_written non-empty while changed_files AND commits empty → unsupported."""
        payload = _valid_payload(
            files_written=["grok_delegate/verdict.py"],
            committed=False,
            tests_added=0,
        )
        diff = _diff(changed=[], commits=[])
        rec = V.reconcile_verdict(payload, diff)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["status"], "VERDICT_UNSUPPORTED")
        self.assertEqual(rec["reason"], "VERDICT_UNSUPPORTED")
        self.assertEqual(rec["field"], "files_written")
        self.assertEqual(rec["changed_files"], [])
        self.assertEqual(rec["commits"], [])

    def test_committed_true_with_zero_commits_is_verdict_unsupported(self) -> None:
        """committed:true but git reports zero commits → VERDICT_UNSUPPORTED."""
        # Dirty tree alone is not enough — commits list must be non-empty.
        payload = _valid_payload(
            files_written=["grok_delegate/verdict.py"],
            committed=True,
            tests_added=1,
        )
        diff = _diff(changed=["grok_delegate/verdict.py"], commits=[])
        rec = V.reconcile_verdict(payload, diff)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["status"], "VERDICT_UNSUPPORTED")
        self.assertEqual(rec["reason"], "VERDICT_UNSUPPORTED")
        self.assertEqual(rec["field"], "committed")
        self.assertEqual(rec["commits"], [])

    def test_blocked_reason_surfaces_as_blocked_even_when_files_changed(self) -> None:
        """Non-empty blocked_reason wins even when git shows real work."""
        payload = _valid_payload(
            files_written=["grok_delegate/verdict.py"],
            committed=True,
            blocked_reason="GATE_FAILED",
            summary="stopped after gate failure",
        )
        diff = _diff(
            changed=["grok_delegate/verdict.py"],
            commits=["def5678 wip: partial"],
        )
        rec = V.reconcile_verdict(payload, diff)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["status"], "blocked")
        self.assertEqual(rec["reason"], "GATE_FAILED")
        self.assertIn("GATE_FAILED", rec.get("message") or "")
        # Git reality still echoed for the driver.
        self.assertEqual(rec["changed_files"], ["grok_delegate/verdict.py"])
        self.assertEqual(len(rec["commits"]), 1)

    def test_reconcile_accepts_parse_result_shape(self) -> None:
        """reconcile_verdict accepts parse_lane_verdict output, not only raw payload."""
        parsed = V.parse_lane_verdict(
            _valid_payload(
                files_written=["a.py"],
                committed=True,
            )
        )
        rec = V.reconcile_verdict(
            parsed,
            _diff(changed=["a.py"], commits=["cafebabe"]),
        )
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["status"], "ok")

    def test_reconcile_propagates_parse_failure(self) -> None:
        """Failed parse result passed to reconcile keeps VERDICT_MISSING/INVALID."""
        bad = V.parse_lane_verdict("{broken")
        rec = V.reconcile_verdict(bad, _diff())
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["reason"], "VERDICT_MISSING")
        self.assertEqual(rec["status"], "VERDICT_MISSING")


class SchemaContractTests(unittest.TestCase):
    """LANE_VERDICT_SCHEMA must stay aligned with parse_lane_verdict enforcement."""

    def test_lane_verdict_schema_required_matches_enforced_fields(self) -> None:
        """Schema required keys cannot drift from REQUIRED_VERDICT_FIELDS / parse."""
        self.assertIsInstance(V.LANE_VERDICT_SCHEMA, dict)
        schema_required = V.LANE_VERDICT_SCHEMA.get("required")
        self.assertIsInstance(schema_required, list)
        self.assertEqual(set(schema_required), set(V.REQUIRED_VERDICT_FIELDS))
        self.assertEqual(list(schema_required), list(V.REQUIRED_VERDICT_FIELDS))

        # Every schema-required key is actually enforced by parse (missing → INVALID).
        for field in schema_required:
            with self.subTest(field=field):
                payload = _valid_payload()
                del payload[field]
                result = V.parse_lane_verdict(payload)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "VERDICT_INVALID")
                self.assertEqual(result["field"], field)

        # And every enforced field is advertised in the schema.
        for field in V.REQUIRED_VERDICT_FIELDS:
            self.assertIn(field, V.LANE_VERDICT_SCHEMA.get("properties") or {})

    def test_default_lane_json_schema_opt_out(self) -> None:
        """default_lane_json_schema returns schema by default; None when opted out."""
        self.assertIs(V.default_lane_json_schema(), V.LANE_VERDICT_SCHEMA)
        self.assertIs(V.default_lane_json_schema(opt_out=False), V.LANE_VERDICT_SCHEMA)
        self.assertIsNone(V.default_lane_json_schema(opt_out=True))


if __name__ == "__main__":
    unittest.main()


class VerdictWiringInDelegateTests(unittest.TestCase):
    """Integrator wiring (R7-C): delegate must reconcile the lane's claim with git.

    The unit tests above prove the pure rules; these prove the runner actually applies
    them, which is the point — a lane must not be able to self-certify success.
    """

    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.lanes = Path(tempfile.mkdtemp())

    def _delegate(self, verdict_json: str, *, git_has_work: bool):
        from grok_delegate import runner as runner_mod

        def fake_subprocess(args, cwd, timeout):
            return {
                "args": args,
                "returncode": 0,
                "stdout": verdict_json,
                "stderr": "",
                "timedOut": False,
            }

        def fake_git(args, cwd, timeout):
            argv = [str(a) for a in args]
            out = ""
            if "--version" in argv:
                out = "git version 2.45.0\n"
            elif "rev-parse" in argv:
                out = "abc123\n"
            elif git_has_work and "--name-only" in argv:
                out = "grok_delegate/x.py\n"
            elif git_has_work and "log" in argv:
                out = "abc1234 feat: real work\n"
            elif git_has_work and "--stat" in argv:
                out = " grok_delegate/x.py | 3 +++\n 1 file changed\n"
            if "worktree" in argv and "add" in argv:
                lanes_prefix = str(self.lanes)
                for token in argv:
                    if token.startswith(lanes_prefix):
                        Path(token).mkdir(parents=True, exist_ok=True)
                        break
            return {
                "args": argv,
                "returncode": 0,
                "stdout": out,
                "stderr": "",
                "timedOut": False,
            }

        return runner_mod.delegate(
            goal="Do the slice and report a verdict.",
            lane=f"verdict-wiring-{abs(hash(verdict_json)) % 10000}",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=3,
            git_runner=fake_git,
            subprocess_runner=fake_subprocess,
            which=lambda n: "/mock/grok",
        )

    _TRUTHFUL = json.dumps(
        {
            "files_written": ["grok_delegate/x.py"],
            "committed": True,
            "tests_added": 2,
            "gates_run": False,
            "self_skeptic_findings": [],
            "blocked_reason": None,
            "summary": "did the work",
        }
    )

    _LYING = json.dumps(
        {
            "files_written": ["grok_delegate/never_written.py"],
            "committed": True,
            "tests_added": 9,
            "gates_run": True,
            "self_skeptic_findings": [],
            "blocked_reason": None,
            "summary": "claims work that does not exist",
        }
    )

    def test_truthful_verdict_is_surfaced(self) -> None:
        result = self._delegate(self._TRUTHFUL, git_has_work=True)
        self.assertTrue(result.get("ok"), msg=result.get("message"))
        self.assertEqual(result.get("verdict_status"), "ok")
        self.assertEqual(result["verdict"]["tests_added"], 2)

    def test_lying_verdict_is_unsupported(self) -> None:
        result = self._delegate(self._LYING, git_has_work=False)
        self.assertEqual(
            result.get("verdict_status"),
            "VERDICT_UNSUPPORTED",
            msg="a claim git cannot confirm must not pass as success",
        )

    def test_absent_verdict_is_missing_not_a_crash(self) -> None:
        result = self._delegate("no json at all, just prose", git_has_work=True)
        self.assertEqual(result.get("verdict_status"), "VERDICT_MISSING")
        self.assertTrue(result.get("ok"), "a missing verdict must not fail the delegation")

    def test_schema_is_requested_by_default(self) -> None:
        from grok_delegate import guard, verdict as verdict_mod

        argv = guard.build_grok_argv(
            "goal",
            "C:/lanes/wt",
            guard.build_permission_profile(),
            5,
            json_schema=verdict_mod.default_lane_json_schema(),
        )
        self.assertIn("--json-schema", argv)
