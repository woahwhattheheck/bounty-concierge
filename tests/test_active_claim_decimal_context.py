# SPDX-License-Identifier: MIT
from __future__ import annotations

from decimal import ROUND_DOWN, getcontext, setcontext
import unittest
from unittest import mock

from concierge import active_claim_portfolio as acp
from tests.test_active_claim_portfolio import (
    NOW,
    availability,
    live_candidate,
    policy,
    qualification,
    result,
)


class ActiveClaimDecimalContextTests(unittest.TestCase):
    def setUp(self):
        self.clock = mock.patch.object(acp, "_utc_now", return_value=NOW)
        self.qual = mock.patch.object(
            acp, "_qualify_live_authority", return_value=qualification(amount="90.01")
        )
        self.avail = mock.patch.object(
            acp, "_inspect_live_availability", return_value=availability()
        )
        self.clock.start()
        self.q = self.qual.start()
        self.a = self.avail.start()

    def tearDown(self):
        mock.patch.stopall()

    def test_live_usd_binding_ignores_ambient_precision_and_rounding(self):
        saved = getcontext().copy()
        try:
            context = getcontext()
            context.prec = 2
            context.rounding = ROUND_DOWN

            forged = live_candidate()
            forged["reward_minor"] = 9000
            forged_receipt = acp.compile_live_active_claim_portfolio(
                [forged], [], policy()
            )
            forged_row = result(forged_receipt)
            self.assertEqual(forged_row["disposition"], "CONFLICT_HOLD")
            self.assertIn(
                "LIVE_REWARD_AMOUNT_MISMATCH", forged_row["reason_codes"]
            )

            exact = live_candidate()
            exact["reward_minor"] = 9001
            exact_receipt = acp.compile_live_active_claim_portfolio(
                [exact], [], policy()
            )
            self.assertEqual(
                result(exact_receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM"
            )
        finally:
            setcontext(saved)

    def test_extreme_reward_exponent_fails_closed_without_expansion(self):
        self.q.return_value = qualification(amount="1e999999999999")
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn(
            "LIVE_REWARD_AMOUNT_UNREPRESENTABLE", row["reason_codes"]
        )

    def test_trailing_zero_usd_signal_stays_exact(self):
        self.q.return_value = qualification(amount="90.010")
        candidate = live_candidate()
        candidate["reward_minor"] = 9001
        receipt = acp.compile_live_active_claim_portfolio(
            [candidate], [], policy()
        )
        self.assertEqual(
            result(receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM"
        )


if __name__ == "__main__":
    unittest.main()
