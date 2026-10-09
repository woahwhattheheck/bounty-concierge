# SPDX-License-Identifier: MIT
"""Focused compensation-request versus settlement-status regressions."""

import unittest

from concierge.mova_factory import MovaFactoryError, _claim


class ClaimReceiptStatusTests(unittest.TestCase):
    def test_affirmative_claim_retains_unpaid_status_verbatim(self):
        for text in (
            "I request the bounty payment. No payment has been received yet.",
            "/claim #42\nNo payout has yet been confirmed.",
            "No reward has been awarded; I claim the bounty.",
        ):
            with self.subTest(text=text):
                value = {"required": True, "text": text}
                self.assertEqual(_claim(value), value)

    def test_actual_waivers_remain_rejected(self):
        for text in (
            "I request the bounty. No payment is requested.",
            "I request the bounty. No payment has been received yet; I waive payment.",
            "I am not claiming payment.",
        ):
            with self.subTest(text=text):
                with self.assertRaises(MovaFactoryError):
                    _claim({"required": True, "text": text})

    def test_status_is_not_itself_a_compensation_request(self):
        for text in (
            "No payment has been received.",
            "Please review my claim. No payment has been received yet.",
        ):
            with self.subTest(text=text):
                with self.assertRaises(MovaFactoryError):
                    _claim({"required": True, "text": text})
        with self.assertRaises(MovaFactoryError):
            _claim({"required": False, "text": "I request the bounty payment."})


if __name__ == "__main__":
    unittest.main()
