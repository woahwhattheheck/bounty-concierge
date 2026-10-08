"""Focused canonical-source identity regression for bounty value routing."""
import unittest

from concierge.bounty_value_router import (
    BountyValueRoutingInputError,
    compile_bounty_value_routing,
)


def request(*urls: str) -> dict:
    return {
        "schema": "bounty-value-routing/v1",
        "policy": {
            "schema": "bounty-value-routing-policy/v1",
            "max_evidence_age_seconds": 3600,
            "routes": {"main_queue": "main", "pile_10_49": "pile"},
            "assets": {"USD": {"active_floor": "50", "pile_floor": "10"}},
        },
        "evaluated_at": "2026-10-08T09:00:00Z",
        "candidates": [
            {
                "work_id": f"work-{index}",
                "canonical_source_url": url,
                "reward_evidence": [],
            }
            for index, url in enumerate(urls)
        ],
    }


class BountyValueRouterIdentityTest(unittest.TestCase):
    def test_same_github_issue_variants_are_rejected_as_duplicates(self):
        with self.assertRaises(BountyValueRoutingInputError):
            compile_bounty_value_routing(
                request(
                    "https://github.com/TokenJunkieLabs/Commons/issues/123",
                    "https://www.github.com/tokenjunkielabs/commons/issues/123/",
                )
            )

    def test_non_github_urls_keep_exact_string_identity(self):
        receipt = compile_bounty_value_routing(
            request(
                "https://example.com/Issue/123",
                "https://example.com/issue/123",
            )
        )
        self.assertEqual(len(receipt["candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
