# SPDX-License-Identifier: MIT
"""Focused UTC timestamp acceptance boundary for bounty deadline evidence."""
from datetime import datetime, timezone
import unittest

from concierge.bounty_deadline_gate import BountyDeadlineInputError, _parse_timestamp


class StrictUTCDeadlineTest(unittest.TestCase):
    def test_valid_instants(self):
        for raw in (
            "2026-10-09T05:31:12Z",
            "2026-10-09T05:31:12.123Z",
            "2026-10-09T05:31:12.123456Z",
        ):
            with self.subTest(raw=raw):
                value = _parse_timestamp(raw, "issue.observed_at")
                self.assertEqual(value.tzinfo, timezone.utc)
                self.assertIsInstance(value, datetime)

    def test_reject_noncanonical_utc(self):
        for raw in (
            "2026-10-09 05:31:12Z",
            "20261009T053112Z",
            "2026-10-09T05:31Z",
            "2026-10-09T05:31:12.123456789Z",
            "2026-13-09T05:31:12Z",
            "2026-10-09T24:31:12Z",
            "2026-10-09T05:31:62Z",
            "2026-10-09T05:31:12+00:00",
        ):
            with self.subTest(raw=raw), self.assertRaises(BountyDeadlineInputError):
                _parse_timestamp(raw, "deadline_evidence.value")


if __name__ == "__main__":
    unittest.main()
