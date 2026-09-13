from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from concierge.submission_packet import (
    SubmissionPacketInputError,
    build_submission_packet,
    format_summary,
)


SOURCE = "https://github.com/acme/widgets/issues/17"
PR = "https://github.com/acme/widgets/pull/23"
HEAD = "a" * 40
EVIDENCE_SHA = "b" * 64


def portfolio(source: str = SOURCE):
    return {
        "schema": "qualified-opportunity-portfolio/v1",
        "selected_count": 1,
        "selected": [
            {
                "canonical_source_url": source,
                "advertised_reward_usd": "500",
                "authority": {
                    "reward": "advertised_only",
                    "revenue": "not_earned_or_settled_by_this_receipt",
                },
            }
        ],
        "authority": {"cash_claim": False},
    }


def evidence(source: str = SOURCE):
    return {
        "canonical_source_url": source,
        "pull_request_url": PR,
        "head_sha": HEAD,
        "changed_paths": ["src/fix.py", "tests/test_fix.py"],
        "allowed_paths": ["src/fix.py", "tests/test_fix.py"],
        "tests": [
            {"command": "python -m unittest tests.test_fix -v", "outcome": "PASS"},
            {"command": "python -m compileall src", "outcome": "PASS"},
        ],
        "evidence_sha256": EVIDENCE_SHA,
        "acceptance_checks": [
            {"criterion_id": "AC-1", "status": "PASS"},
            {"criterion_id": "AC-2", "status": "PASS"},
        ],
    }


class SubmissionPacketTests(unittest.TestCase):
    def test_ready_packet_binds_evidence_without_claiming_cash(self):
        result = build_submission_packet(portfolio(), [evidence()])
        self.assertEqual(result["ready_count"], 1)
        self.assertEqual(result["hold_count"], 0)
        packet = result["packets"][0]
        self.assertEqual(packet["disposition"], "READY_FOR_HUMAN_SUBMISSION")
        self.assertEqual(packet["evidence"]["pull_request_repo"], "acme/widgets")
        self.assertEqual(packet["evidence"]["pull_request_number"], 23)
        self.assertEqual(packet["evidence"]["head_sha"], HEAD)
        self.assertFalse(packet["authority"]["cash_claim"])
        self.assertEqual(packet["authority"]["submission"], "human_only")
        self.assertFalse(result["authority"]["external_post_performed"])
        self.assertRegex(packet["packet_sha256"], r"^[0-9a-f]{64}$")

    def test_packet_hash_is_deterministic_across_path_input_order(self):
        a = evidence()
        b = evidence()
        b["changed_paths"] = list(reversed(b["changed_paths"]))
        b["allowed_paths"] = list(reversed(b["allowed_paths"]))
        first = build_submission_packet(portfolio(), [a])["packets"][0]["packet_sha256"]
        second = build_submission_packet(portfolio(), [b])["packets"][0]["packet_sha256"]
        self.assertEqual(first, second)

    def test_missing_evidence_holds_instead_of_fabricating(self):
        result = build_submission_packet(portfolio(), [])
        packet = result["packets"][0]
        self.assertEqual(packet["disposition"], "HOLD")
        self.assertEqual(packet["reason_codes"], ["EVIDENCE_MISSING"])
        self.assertIsNone(packet["evidence"])

    def test_changed_path_outside_allowlist_holds(self):
        value = evidence()
        value["changed_paths"].append("secrets.txt")
        result = build_submission_packet(portfolio(), [value])
        self.assertEqual(
            result["packets"][0]["reason_codes"],
            ["CHANGED_PATH_OUTSIDE_ALLOWLIST"],
        )

    def test_nonpassing_test_holds(self):
        for outcome in ("FAIL", "PENDING", "CANCELLED"):
            with self.subTest(outcome=outcome):
                value = evidence()
                value["tests"][0]["outcome"] = outcome
                result = build_submission_packet(portfolio(), [value])
                self.assertIn("TESTS_NOT_ALL_PASS", result["packets"][0]["reason_codes"])

    def test_incomplete_acceptance_holds(self):
        for status in ("FAIL", "UNKNOWN"):
            with self.subTest(status=status):
                value = evidence()
                value["acceptance_checks"][0]["status"] = status
                result = build_submission_packet(portfolio(), [value])
                self.assertIn(
                    "ACCEPTANCE_CHECKS_NOT_ALL_PASS",
                    result["packets"][0]["reason_codes"],
                )

    def test_strict_github_identity_rejects_aliases_and_cross_shape(self):
        bad_sources = [
            "http://github.com/acme/widgets/issues/17",
            "https://github.com/acme/widgets/issues/017",
            "https://github.com/acme/widgets/issues/17?x=1",
            "https://user@github.com/acme/widgets/issues/17",
            "https://github.com:bad/acme/widgets/issues/17",
            "https://github.com:443/acme/widgets/issues/17",
            "https://github.com/acme/widgets/pull/17",
            "https://github.com/./widgets/issues/17",
            "https://github.com/acme/../issues/17",
        ]
        for source in bad_sources:
            with self.subTest(source=source), self.assertRaises(SubmissionPacketInputError):
                build_submission_packet(portfolio(source), [])

    def test_strict_pr_identity_rejects_query_fragment_wrong_shape_and_dot_aliases(self):
        bad = [
            "https://github.com/acme/widgets/pull/023",
            "https://github.com/acme/widgets/pull/23#discussion",
            "https://github.com/acme/widgets/issues/23",
            "https://github.com/./widgets/pull/23",
            "https://github.com/acme/../pull/23",
        ]
        for pr in bad:
            with self.subTest(pr=pr):
                value = evidence()
                value["pull_request_url"] = pr
                with self.assertRaises(SubmissionPacketInputError):
                    build_submission_packet(portfolio(), [value])

    def test_pr_repository_must_match_selected_issue_repository(self):
        value = evidence()
        value["pull_request_url"] = "https://github.com/other/project/pull/23"
        with self.assertRaisesRegex(
            SubmissionPacketInputError,
            "pull request repository must match canonical source repository",
        ):
            build_submission_packet(portfolio(), [value])

    def test_pr_repository_identity_match_is_case_insensitive(self):
        source = "https://github.com/Acme/Widgets/issues/17"
        value = evidence(source)
        value["pull_request_url"] = "https://github.com/acme/widgets/pull/23"
        result = build_submission_packet(portfolio(source), [value])
        self.assertEqual(result["ready_count"], 1)
        self.assertEqual(result["packets"][0]["evidence"]["pull_request_repo"], "acme/widgets")

    def test_head_and_digest_must_be_exact_lowercase_hex(self):
        variants = [
            ("head_sha", "A" * 40),
            ("head_sha", "a" * 39),
            ("evidence_sha256", "B" * 64),
            ("evidence_sha256", "b" * 63),
        ]
        for field, bad in variants:
            with self.subTest(field=field, bad=bad):
                value = evidence()
                value[field] = bad
                with self.assertRaises(SubmissionPacketInputError):
                    build_submission_packet(portfolio(), [value])

    def test_path_traversal_absolute_backslash_and_duplicates_refused(self):
        variants = ["../secret", "/etc/passwd", "src\\fix.py", "./src/fix.py", "src/../fix.py"]
        for bad in variants:
            with self.subTest(bad=bad):
                value = evidence()
                value["changed_paths"] = [bad]
                with self.assertRaises(SubmissionPacketInputError):
                    build_submission_packet(portfolio(), [value])
        value = evidence()
        value["allowed_paths"].append(value["allowed_paths"][0])
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(portfolio(), [value])

    def test_duplicate_or_extra_evidence_fails_closed(self):
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(portfolio(), [evidence(), evidence()])
        other = evidence("https://github.com/acme/widgets/issues/18")
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(portfolio(), [other])

    def test_portfolio_authority_must_be_exact(self):
        bad = portfolio()
        bad["authority"]["cash_claim"] = True
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(bad, [evidence()])
        bad = portfolio()
        bad["selected"][0]["authority"]["revenue"] = "earned"
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(bad, [evidence()])

    def test_selected_count_and_duplicate_sources_are_bound(self):
        bad = portfolio()
        bad["selected_count"] = 2
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(bad, [evidence()])
        bad = portfolio()
        bad["selected"].append(copy.deepcopy(bad["selected"][0]))
        bad["selected_count"] = 2
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(bad, [evidence()])

    def test_raw_issue_text_and_undeclared_evidence_fields_cannot_leak(self):
        p = portfolio()
        p["selected"][0]["raw_issue_body"] = "SECRET ISSUE BODY"
        result = build_submission_packet(p, [evidence()])
        self.assertNotIn("SECRET ISSUE BODY", repr(result))
        value = evidence()
        value["raw_issue_body"] = "SECRET"
        with self.assertRaises(SubmissionPacketInputError):
            build_submission_packet(portfolio(), [value])

    def test_summary_disclaims_external_post_and_cash(self):
        result = build_submission_packet(portfolio(), [evidence()])
        text = format_summary(result)
        self.assertIn("external_post=false", text)
        self.assertIn("cash_claim=false", text)

    def test_cli_ready_zero_and_hold_two(self):
        with tempfile.TemporaryDirectory() as td:
            ready_path = os.path.join(td, "ready.json")
            hold_path = os.path.join(td, "hold.json")
            with open(ready_path, "w", encoding="utf-8") as handle:
                json.dump({"portfolio": portfolio(), "evidence": [evidence()]}, handle)
            with open(hold_path, "w", encoding="utf-8") as handle:
                json.dump({"portfolio": portfolio(), "evidence": []}, handle)
            ready = subprocess.run(
                [sys.executable, "-m", "concierge.submission_packet", ready_path, "--summary"],
                cwd=os.path.dirname(os.path.dirname(__file__)),
                text=True,
                capture_output=True,
            )
            hold = subprocess.run(
                [sys.executable, "-m", "concierge.submission_packet", hold_path, "--summary"],
                cwd=os.path.dirname(os.path.dirname(__file__)),
                text=True,
                capture_output=True,
            )
            self.assertEqual(ready.returncode, 0, ready.stderr)
            self.assertEqual(hold.returncode, 2, hold.stderr)
            self.assertIn("ready=1", ready.stdout)
            self.assertIn("hold=1", hold.stdout)


if __name__ == "__main__":
    unittest.main()
