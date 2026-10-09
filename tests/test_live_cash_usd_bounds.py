# SPDX-License-Identifier: MIT
"""Focused USD parsing regression for live bounty admission."""

import unittest
from decimal import Decimal

from concierge.bounty_live_cash_admission import (
    LiveCashAdmissionError, _decimal, _format_decimal,
)


class USDParsingRegression(unittest.TestCase):
    def test_normal_tokens(self):
        self.assertEqual(_format_decimal(_decimal("1,234.50", "USD")), "1234.5")
        self.assertEqual(_format_decimal(_decimal("15.00", "USD")), "15")

    def test_reject_invalid_or_unbounded_tokens(self):
        for text in ("1e100000", "1,,000", "9" * 80, "1000000000001"):
            with self.subTest(text=text), self.assertRaises(LiveCashAdmissionError):
                _decimal(text, "USD")
        with self.assertRaises(LiveCashAdmissionError):
            _format_decimal(Decimal("1e100000"))


if __name__ == "__main__":
    unittest.main()
