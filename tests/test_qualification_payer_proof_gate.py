# SPDX-License-Identifier: MIT
"""Focused regressions: advertised rewards cannot bypass same-payer paid-merge proof."""
from datetime import datetime, timedelta, timezone
import unittest

from concierge.bounty_qualification import qualify_dispatch
from concierge.payer_paid_merge_gate import evaluate_payer_history


def candidate(repo="Acme/Plugin"):
    return {
        "repository_full_name": repo,
        "title": "Bounty $70: Fix existing behavior",
        "body": "The sponsor advertises a $70 bounty.",
        "labels": ["bounty"],
        "canonical_audit": {
            "repository_full_name": repo,
            "issue_state": "open",
            "open_pr_count": 0,
            "stale_listing_signal": False,
            "search_truncated": False,
        },
    }


def owner_registry(*, observed=None, state="QUALIFIED_ACTIVE_PAID", history=True):
    now = datetime.now(timezone.utc)
    observed = observed or now - timedelta(minutes=15)
    return {
        "schema_version": 1,
        "activity_max_age_days": 30,
        "observed_at": observed.isoformat(),
        "repositories": {
            "acme/plugin": {
                "status": state,
                "maintainer_activity": {"event_at": (now - timedelta(days=1)).isoformat()},
                "paid_merge_history": [
                    {
                        "payer": "Acme / Open Collective",
                        "recipient": "ContributorX",
                        "merged_pr_url": "https://github.com/acme/plugin/pull/14",
                        "payment_evidence_url": "https://opencollective.com/acme/expenses/81",
                    }
                ] if history else [],
            }
        },
    }


class PaidPayerProofDispatchTest(unittest.TestCase):
    def test_advertised_reward_cannot_dispatch_without_curated_registry(self):
        result = qualify_dispatch(candidate())
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn("PAYER_REGISTRY_MISSING_OR_INVALID", result["reason_codes"])

    def test_curated_paid_history_remains_hold_without_exact_task_authorization(self):
        result = qualify_dispatch(candidate(), payer_registry=owner_registry())
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn("EXACT_TASK_PREFLIGHT_REQUIRED", result["reason_codes"])
        self.assertTrue(result["signals"]["same_payer_paid_merge_gate"]["historical_paid_merge_proof"])
        self.assertFalse(result["signals"]["same_payer_paid_merge_gate"]["payment_to_our_claimant_verified"])

    def test_paid_platform_reputation_not_substitute_for_same_payer_receipt(self):
        result = qualify_dispatch(candidate(), payer_registry=owner_registry(history=False))
        self.assertIn("SAME_PAYER_PAID_MERGE_UNVERIFIED", result["reason_codes"])
        self.assertEqual(result["disposition"], "HOLD")

    def test_paid_record_for_different_repository_does_not_transfer(self):
        result = qualify_dispatch(candidate("Elsewhere/Unrelated"), payer_registry=owner_registry())
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("SAME_PAYER_PAID_MERGE_UNVERIFIED", result["reason_codes"])

    def test_inactive_31_day_maintainer_is_not_green(self):
        registry = owner_registry()
        registry["repositories"]["acme/plugin"]["maintainer_activity"]["event_at"] = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat()
        result = qualify_dispatch(candidate(), payer_registry=registry)
        self.assertIn("PAID_PAYER_MAINTAINER_INACTIVE", result["reason_codes"])
        self.assertFalse(result["dispatch"])

    def test_outdated_activity_policy_cannot_pass(self):
        registry = owner_registry()
        registry["activity_max_age_days"] = 90
        result = qualify_dispatch(candidate(), payer_registry=registry)
        self.assertIn("PAYER_ACTIVITY_POLICY_MISMATCH", result["reason_codes"])

    def test_stale_owner_registry_is_not_passed_as_live(self):
        stale = owner_registry(observed=datetime.now(timezone.utc) - timedelta(days=5))
        result = qualify_dispatch(candidate(), payer_registry=stale)
        self.assertIn("PAYER_REGISTRY_STALE", result["reason_codes"])

    def test_source_identity_and_fake_receipt_host_fail_closed(self):
        mismatched = candidate()
        mismatched["canonical_audit"]["repository_full_name"] = "attacker/other"
        self.assertEqual(qualify_dispatch(mismatched, payer_registry=owner_registry())["disposition"], "HOLD")
        altered = owner_registry()
        altered["repositories"]["acme/plugin"]["paid_merge_history"][0]["payment_evidence_url"] = (
            "https://opencollective.com.evil.example/expense/81"
        )
        verdict = evaluate_payer_history(candidate(), altered)
        self.assertFalse(verdict["eligible"])
        self.assertEqual(verdict["reason_code"], "SAME_PAYER_PAID_MERGE_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
