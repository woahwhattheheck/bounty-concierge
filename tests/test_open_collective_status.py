# SPDX-License-Identifier: MIT
"""One focused regression for Open Collective payment-history status drift."""
import unittest

from concierge.open_collective_status import ExpenseProofError, assess_expense_page


def page(invoice, current, activities):
    lines = [
        f"# Invoice #{invoice} to Appium",
        "#### Contribution payout",
        current,
        f"Invoice #{invoice}",
        "### Expense Details",
        "Total amount",
        "More actions",
    ]
    for when, kind in activities:
        lines.extend(["By member", f"on {when}", f"Expense {kind}"])
    lines.extend(["##### Collective balance", "Current Fiscal Host", "Open Source Collective"])
    return "\n".join(lines)


class OpenCollectiveStatusTest(unittest.TestCase):
    observed = "2026-10-09T08:11:00Z"

    def assess(self, invoice, status, activities):
        return assess_expense_page(
            page(invoice, status, activities),
            expense_url=f"https://opencollective.com/appium/expenses/{invoice}",
            observed_at=self.observed,
        )

    def test_paid_in_history_but_latest_approved(self):
        record = self.assess(347817, "Approved", [
            ("September 29, 2026", "approved"),
            ("September 29, 2026", "processing"),
            ("September 29, 2026", "paid"),
            ("October 6, 2026", "marked as incomplete"),
            ("October 8, 2026", "approved"),
        ])
        self.assertFalse(record["paid_now_on_provider_page"])
        self.assertEqual(record["historical_paid_events"], 1)
        self.assertEqual(record["current_status"], "APPROVED")
        self.assertEqual(record["latest_transition"]["date"], "2026-10-08")
        self.assertEqual(len(record["source_sha256"]), 64)

    def test_current_paid_remains_eligible_as_page_evidence_only(self):
        record = self.assess(347815, "Paid", [
            ("September 28, 2026", "approved"),
            ("September 29, 2026", "processing"),
            ("September 29, 2026", "paid"),
        ])
        self.assertTrue(record["paid_now_on_provider_page"])
        self.assertEqual(record["current_status"], "PAID")

    def test_partial_or_stale_conflicting_page_fails_closed(self):
        stale = page(347817, "Paid", [
            ("September 29, 2026", "paid"),
            ("October 8, 2026", "approved"),
        ])
        with self.assertRaises(ExpenseProofError):
            assess_expense_page(
                stale,
                expense_url="https://opencollective.com/appium/expenses/347817",
                observed_at=self.observed,
            )
        with self.assertRaises(ExpenseProofError):
            assess_expense_page(
                "Invoice #347817\nPaid", 
                expense_url="https://opencollective.com/appium/expenses/347817",
                observed_at=self.observed,
            )


if __name__ == "__main__":
    unittest.main()
