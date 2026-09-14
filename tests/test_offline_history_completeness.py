from __future__ import annotations

import unittest

from concierge import _revenue_settlement_base as base
from concierge import revenue_settlement as settlement


class OfflineHistoryCompletenessTests(unittest.TestCase):
    @staticmethod
    def canonical(*, transactions, total):
        return {
            "ok": True,
            "miner_id": "alice",
            "transactions": transactions,
            "total": total,
        }

    def test_exact_complete_provider_snapshot_remains_accepted(self):
        rows = [{"type": "transfer_in"}]
        captured, wallet = settlement._history_capture(
            self.canonical(transactions=rows, total=1),
            wallet="alice",
        )
        self.assertEqual(captured, rows)
        self.assertEqual(wallet, "alice")

    def test_incomplete_first_page_is_rejected(self):
        with self.assertRaisesRegex(
            settlement.RevenueSettlementInputError,
            "wallet metadata was invalid",
        ):
            settlement._history_capture(
                self.canonical(transactions=[{}], total=2),
                wallet="alice",
            )

    def test_total_smaller_than_rows_is_rejected(self):
        with self.assertRaisesRegex(
            settlement.RevenueSettlementInputError,
            "wallet metadata was invalid",
        ):
            settlement._history_capture(
                self.canonical(transactions=[{}], total=0),
                wallet="alice",
            )

    def test_boolean_negative_and_oversize_totals_are_rejected(self):
        for total in (True, -1, settlement._MAX_ITEMS + 1):
            with self.subTest(total=total):
                with self.assertRaisesRegex(
                    settlement.RevenueSettlementInputError,
                    "wallet metadata was invalid",
                ):
                    settlement._history_capture(
                        self.canonical(transactions=[], total=total),
                        wallet="alice",
                    )

    def test_direct_capture_row_count_is_bounded(self):
        count = settlement._MAX_ITEMS + 1
        with self.assertRaisesRegex(
            settlement.RevenueSettlementInputError,
            "wallet metadata was invalid",
        ):
            settlement._history_capture(
                self.canonical(transactions=[{}] * count, total=count),
                wallet="alice",
            )

    def test_normalized_operator_capture_is_unchanged(self):
        rows = [{"type": "transfer_in"}]
        captured, wallet = settlement._history_capture(
            {
                "schema_version": 1,
                "source": "rustchain_wallet_history",
                "wallet": "alice",
                "items": rows,
            },
            wallet="alice",
        )
        self.assertEqual(captured, rows)
        self.assertEqual(wallet, "alice")

    def test_historical_base_import_and_public_module_share_guard(self):
        self.assertIs(base, settlement._base)
        self.assertIs(base._history_capture, settlement._history_capture)


if __name__ == "__main__":
    unittest.main()
