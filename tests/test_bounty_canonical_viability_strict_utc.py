"""Focused strict UTC timestamp regression for canonical bounty evidence admission."""

import unittest

from concierge.bounty_canonical_viability import (
    BountyCanonicalViabilityInputError,
    _time,
)


class CanonicalBountyUtcTest(unittest.TestCase):
    def test_accepted_strict_utc_forms(self):
        for value in (
            "2026-10-09T05:53:00Z",
            "2026-10-09T05:53:00.1Z",
            "2026-10-09T05:53:00.123456Z",
        ):
            with self.subTest(value=value):
                parsed = _time(value, "observed_at")
                self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_noncanonical_or_impossible_forms_are_rejected(self):
        for value in (
            "2026-10-09 05:53:00Z",         # space delimiter
            "20261009T05:53:00Z",           # basic ISO date
            "2026-10-09T05:53Z",            # missing seconds
            "2026-10-09T05:53:00.1234567Z", # beyond microsecond precision
            "2026-10-09T05:53:00+00:00",    # offset rather than Z
            "2026-10-09T05:53:00.123xZ",    # nonliteral fraction separator
            "2026-10-09T05:53:00,123Z",     # comma fraction
            "2026-02-30T05:53:00Z",         # impossible date
            "2026-10-09T25:53:00Z",         # impossible hour
        ):
            with self.subTest(value=value):
                with self.assertRaises(BountyCanonicalViabilityInputError):
                    _time(value, "observed_at")


if __name__ == "__main__":
    unittest.main()
