from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.currency_partitioned_ranker import rank_partitioned_opportunities


class DuplicateCanonicalSourceAuthorityTests(unittest.TestCase):
    def test_malformed_duplicate_cannot_make_valid_sibling_survive(self):
        source = "https://github.com/acme/widgets/issues/17"

        def intake(snapshot, **_):
            signals = {
                "advertised_reward_usd": [],
                "live_label_reward_usd": [],
                "advertised_reward_rtc": ["30"],
                "live_label_reward_rtc": ["30"],
            }
            return {
                "disposition": "ACTIONABLE",
                "dispatch": True,
                "canonical_source_url": source,
                "reason_codes": [],
                "qualification": {
                    "disposition": "ACTIONABLE",
                    "dispatch": True,
                    "signals": signals,
                },
                "provenance": {
                    "disposition": "ACTIONABLE",
                    "dispatch": True,
                    "signals": {},
                },
            }

        candidates = [
            {
                "snapshot": {"title": "python parser bounty"},
                "estimated_effort_hours": "0",
                "estimated_win_probability": "0.5",
            },
            {
                "snapshot": {"title": "python parser bounty"},
                "estimated_effort_hours": "2",
                "estimated_win_probability": "0.5",
            },
        ]

        with (
            patch(
                "concierge.currency_partitioned_ranker.qualify_revenue_intake",
                side_effect=intake,
            ),
            patch(
                "concierge.currency_partitioned_ranker.match_skills",
                return_value=1.0,
            ),
        ):
            result = rank_partitioned_opportunities(candidates, ["python"])

        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            [row["reason_code"] for row in result["excluded"]],
            ["ESTIMATE_INVALID", "DUPLICATE_CANONICAL_SOURCE"],
        )


if __name__ == "__main__":
    unittest.main()
