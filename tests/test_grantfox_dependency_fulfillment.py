from copy import deepcopy
import unittest
from unittest.mock import patch

from concierge.grantfox_dependency_fulfillment import (
    GrantFoxDependencyFulfillmentInputError,
    compile_grantfox_dependency_fulfillment,
    verify_dependency_fulfillment_receipt,
)


def dependency_receipt(disposition="DEPENDENCIES_CLEAR"):
    dependencies = [
        {
            "repository_full_name": "Gryd-lock/grydlock-testkit",
            "issue_number": 27,
            "issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/27",
            "state": "closed",
            "observed_at": "2026-09-19T22:01:00Z",
            "snapshot_age_seconds": 60,
            "basis": "Issue #33 explicitly requires #27.",
            "completion": {
                "status": "LANDED",
                "merged_pr_url": "https://github.com/Gryd-lock/grydlock-testkit/pull/127",
                "merge_commit_sha": f"{27:040x}",
                "observed_at": "2026-09-19T22:01:10Z",
            },
        },
        {
            "repository_full_name": "Gryd-lock/grydlock-testkit",
            "issue_number": 4,
            "issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/4",
            "state": "closed",
            "observed_at": "2026-09-19T22:01:00Z",
            "snapshot_age_seconds": 60,
            "basis": "Issue #33 explicitly requires #4.",
            "completion": {
                "status": "LANDED",
                "merged_pr_url": "https://github.com/Gryd-lock/grydlock-testkit/pull/104",
                "merge_commit_sha": f"{4:040x}",
                "observed_at": "2026-09-19T22:01:10Z",
            },
        },
    ]
    return {
        "schema": "grantfox-dependency-readiness-receipt/v1",
        "identity": {"owner": "Gryd-lock", "repo": "grydlock-testkit", "issue_number": 33},
        "dependency_disposition": disposition,
        "source_receipt": {
            "repository_snapshot": {
                "default_branch": "main",
                "commit_sha": "f" * 40,
            }
        },
        "evidence": {"dependencies": dependencies},
    }


def landing(number, *, kind="merged_pull_request", **overrides):
    item = {
        "repository_full_name": "Gryd-lock/grydlock-testkit",
        "issue_number": number,
        "evidence_kind": kind,
        "evidence_url": f"https://github.com/Gryd-lock/grydlock-testkit/pull/{100 + number}",
        "pull_request_number": 100 + number,
        "landed_commit_sha": f"{number:040x}",
        "default_branch": "main",
        "observed_default_branch_head_sha": "f" * 40,
        "contains_landed_commit": True,
        "observed_at": "2026-09-19T22:02:00Z",
        "basis": f"Observed merged PR for prerequisite #{number} on default branch ancestry.",
    }
    if kind == "default_branch_commit":
        item.pop("pull_request_number")
        item["evidence_url"] = (
            "https://github.com/Gryd-lock/grydlock-testkit/commit/"
            f"{item['landed_commit_sha']}"
        )
    item.update(overrides)
    return item


def request(**overrides):
    payload = {
        "schema": "grantfox-dependency-fulfillment/v1",
        "dependency_receipt": dependency_receipt(),
        "landings": [landing(27), landing(4)],
        "evaluated_at": "2026-09-19T22:03:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


class GrantFoxDependencyFulfillmentTests(unittest.TestCase):
    def setUp(self):
        self.verify_patch = patch(
            "concierge.grantfox_dependency_fulfillment.verify_dependency_readiness_receipt",
            return_value=True,
        )
        self.verify_patch.start()

    def tearDown(self):
        self.verify_patch.stop()

    def test_closed_issues_without_landing_evidence_wait(self):
        receipt = compile_grantfox_dependency_fulfillment(request(landings=[]))
        self.assertEqual(receipt["fulfillment_disposition"], "FULFILLMENT_WAIT")
        self.assertEqual(receipt["fulfillment_summary"]["missing_issue_numbers"], [4, 27])
        self.assertIn("PREREQUISITE_LANDING_EVIDENCE_MISSING", receipt["reason_codes"])
        self.assertFalse(receipt["authority"]["implementation_write_authority"])

    def test_exact_landing_evidence_fulfills_without_granting_authority(self):
        receipt = compile_grantfox_dependency_fulfillment(request())
        self.assertEqual(receipt["fulfillment_disposition"], "DEPENDENCIES_FULFILLED")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertTrue(verify_dependency_fulfillment_receipt(receipt))
        self.assertFalse(receipt["authority"]["provider_application_authority"])

    def test_upstream_wait_cannot_be_laundered_by_landings(self):
        receipt = compile_grantfox_dependency_fulfillment(
            request(dependency_receipt=dependency_receipt("DEPENDENCY_WAIT"))
        )
        self.assertEqual(receipt["fulfillment_disposition"], "HOLD")
        self.assertIn("DEPENDENCY_READINESS_NOT_CLEAR", receipt["reason_codes"])

    def test_stale_landing_holds(self):
        stale = landing(27, observed_at="2026-09-19T20:00:00Z")
        receipt = compile_grantfox_dependency_fulfillment(
            request(landings=[stale, landing(4)])
        )
        self.assertEqual(receipt["fulfillment_disposition"], "HOLD")
        self.assertIn("FULFILLMENT_EVIDENCE_STALE", receipt["reason_codes"])

    def test_false_ancestry_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyFulfillmentInputError, "contains_landed_commit must be true"
        ):
            compile_grantfox_dependency_fulfillment(
                request(landings=[landing(27, contains_landed_commit=False), landing(4)])
            )

    def test_undeclared_and_duplicate_landings_are_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyFulfillmentInputError, "undeclared prerequisite"
        ):
            compile_grantfox_dependency_fulfillment(
                request(landings=[landing(27), landing(4), landing(99)])
            )
        with self.assertRaisesRegex(
            GrantFoxDependencyFulfillmentInputError, "duplicate landing"
        ):
            compile_grantfox_dependency_fulfillment(
                request(landings=[landing(27), deepcopy(landing(27)), landing(4)])
            )

    def test_canonical_evidence_url_is_required(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyFulfillmentInputError, "evidence_url must equal"
        ):
            compile_grantfox_dependency_fulfillment(
                request(
                    landings=[
                        landing(27, evidence_url="https://example.com/not-proof"),
                        landing(4),
                    ]
                )
            )

    def test_upstream_pr_swap_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyFulfillmentInputError,
            "must match upstream completion evidence",
        ):
            compile_grantfox_dependency_fulfillment(
                request(landings=[landing(27, pull_request_number=999,
                                           evidence_url="https://github.com/Gryd-lock/grydlock-testkit/pull/999"),
                                  landing(4)])
            )

    def test_upstream_landed_sha_swap_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyFulfillmentInputError,
            "must match upstream completion evidence",
        ):
            compile_grantfox_dependency_fulfillment(
                request(landings=[landing(27, landed_commit_sha="d" * 40), landing(4)])
            )

    def test_default_branch_swap_is_rejected(self):
        with self.assertRaisesRegex(
            GrantFoxDependencyFulfillmentInputError,
            "must equal verified source default branch",
        ):
            compile_grantfox_dependency_fulfillment(
                request(landings=[landing(27, default_branch="release"), landing(4)])
            )

    def test_tamper_breaks_verification(self):
        receipt = compile_grantfox_dependency_fulfillment(request())
        changed = deepcopy(receipt)
        changed["fulfillment_summary"]["landing_evidence_count"] = 0
        self.assertFalse(verify_dependency_fulfillment_receipt(changed))

    def test_invalid_dependency_receipt_is_rejected(self):
        with patch(
            "concierge.grantfox_dependency_fulfillment.verify_dependency_readiness_receipt",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                GrantFoxDependencyFulfillmentInputError, "failed"
            ):
                compile_grantfox_dependency_fulfillment(request())


if __name__ == "__main__":
    unittest.main()
