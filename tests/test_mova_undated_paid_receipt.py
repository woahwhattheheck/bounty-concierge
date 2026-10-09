# SPDX-License-Identifier: MIT
"""Focused evidence semantics for vetted paid sponsors with undisclosed payment dates."""

from datetime import datetime, timedelta, timezone
import unittest

from concierge.mova_factory import MovaFactoryError, _qualified_paid_repository


class UndatedPaidReceiptTest(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.receipt = {
            "payer": "Archestra",
            "recipient": "Excellencedev",
            "currency": "USD",
            "amount": "75",
            "merged_pr_url": "https://github.com/archestra-ai/archestra/pull/4690",
            "payment_evidence_url": "https://algora.io/claims/yDgieQvf98Su9TKC",
            "payment_status": "PAID",
            "paid_at_precision": "not_published",
        }
        self.policy = {
            "schema_version": 1,
            "policy_id": "REPO-ELIGIBILITY-20261009-BRYCE-01",
            "default_status": "HOLD_UNVERIFIED",
            "activity_max_age_days": 90,
            "observed_at": now.isoformat(),
            "repositories": {
                "archestra-ai/archestra": {
                    "status": "QUALIFIED_ACTIVE_PAID",
                    "maintainer_activity": {
                        "kind": "merge",
                        "actor": "xdrdak",
                        "event_at": now.isoformat(),
                        "evidence_url": "https://github.com/archestra-ai/archestra/pull/8622",
                    },
                    "paid_merge_history": [self.receipt],
                }
            },
        }

    def qualifies(self):
        return _qualified_paid_repository("archestra-ai/archestra", self.policy)

    def test_verified_paid_without_published_date_is_admitted(self):
        result = self.qualifies()
        self.assertEqual(result["paid_merge_receipt_count"], 1)

    def test_undated_claim_without_paid_status_is_rejected(self):
        self.receipt.pop("payment_status")
        with self.assertRaisesRegex(MovaFactoryError, "PAID status"):
            self.qualifies()

    def test_approved_is_not_necessarily_paid(self):
        self.receipt["payment_status"] = "APPROVED"
        with self.assertRaisesRegex(MovaFactoryError, "must be PAID"):
            self.qualifies()

    def test_undated_paid_receipt_without_date_provenance_is_rejected(self):
        self.receipt.pop("paid_at_precision")
        with self.assertRaisesRegex(MovaFactoryError, "unknown-date provenance"):
            self.qualifies()

    def test_dated_paid_history_remains_compatible(self):
        self.receipt.pop("payment_status")
        self.receipt.pop("paid_at_precision")
        self.receipt["paid_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertEqual(self.qualifies()["status"], "QUALIFIED_ACTIVE_PAID")

    def test_future_payment_date_is_rejected(self):
        self.receipt["paid_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        with self.assertRaisesRegex(MovaFactoryError, "future"):
            self.qualifies()


if __name__ == "__main__":
    unittest.main()
