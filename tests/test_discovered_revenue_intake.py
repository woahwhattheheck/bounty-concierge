from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.discovered_revenue_intake import qualify_discovered_revenue_intake


class DiscoveredRevenueIntakeTests(unittest.TestCase):
    @patch("concierge.discovered_revenue_intake.qualify_live_revenue_intake")
    def test_resolved_mirror_is_forwarded_to_authoritative_live_intake(self, live):
        live.return_value = {
            "disposition": "ACTIONABLE",
            "dispatch": True,
            "canonical_source_url": "https://github.com/acme/widgets/issues/17",
            "reason_codes": [],
            "reasons": [],
        }
        listing = {
            "listing_url": "https://bounties.example/tasks/widget-17",
            "source_urls": ["https://github.com/acme/widgets/issues/17"],
        }

        result = qualify_discovered_revenue_intake(listing, token="secret")

        self.assertTrue(result["dispatch"])
        live.assert_called_once_with(
            "acme/widgets",
            17,
            listing_url="https://bounties.example/tasks/widget-17",
            token="secret",
            max_pages=10,
            saturation_threshold=4,
        )
        self.assertEqual(
            result["resolver"]["signals"]["resolution_basis"],
            "EXPLICIT_SOURCE_URL",
        )

    @patch("concierge.discovered_revenue_intake.qualify_live_revenue_intake")
    def test_direct_canonical_listing_still_runs_live_intake(self, live):
        live.return_value = {
            "disposition": "HOLD",
            "dispatch": False,
            "canonical_source_url": "https://github.com/acme/widgets/issues/17",
            "reason_codes": ["QUALIFICATION:CLAIM_PRESSURE_HIGH"],
            "reasons": [],
        }
        listing = {"listing_url": "https://github.com/acme/widgets/issues/17"}

        result = qualify_discovered_revenue_intake(listing)

        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        live.assert_called_once()
        self.assertTrue(result["resolver"]["signals"]["listing_is_canonical"])

    @patch("concierge.discovered_revenue_intake.qualify_live_revenue_intake")
    def test_ambiguous_mirror_holds_without_live_dispatch_read(self, live):
        result = qualify_discovered_revenue_intake(
            {
                "listing_url": "https://bounties.example/tasks/ambiguous",
                "source_urls": [
                    "https://github.com/acme/widgets/issues/17",
                    "https://github.com/acme/widgets/issues/18",
                ],
            }
        )

        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertEqual(
            result["reason_codes"],
            ["RESOLVER:CANONICAL_SOURCE_AMBIGUOUS"],
        )
        self.assertIsNone(result["canonical_source_url"])
        live.assert_not_called()

    @patch("concierge.discovered_revenue_intake.qualify_live_revenue_intake")
    def test_missing_canonical_source_holds_without_live_dispatch_read(self, live):
        result = qualify_discovered_revenue_intake(
            {
                "listing_url": "https://bounties.example/tasks/no-source",
                "body": "reward advertised here but no canonical work item",
            }
        )

        self.assertFalse(result["dispatch"])
        self.assertEqual(
            result["reason_codes"],
            ["RESOLVER:CANONICAL_SOURCE_MISSING"],
        )
        live.assert_not_called()

    @patch("concierge.discovered_revenue_intake.qualify_live_revenue_intake")
    def test_left_decorated_text_holds_without_live_dispatch_read(self, live):
        hostile = (
            "evilhttps://github.com/acme/widgets/issues/17",
            "foo/https://github.com/acme/widgets/issues/17",
            "evil/acme/widgets#17",
        )
        for body in hostile:
            with self.subTest(body=body):
                result = qualify_discovered_revenue_intake(
                    {
                        "listing_url": "https://bounties.example/tasks/hostile",
                        "body": body,
                    }
                )
                self.assertFalse(result["dispatch"])
                self.assertEqual(result["disposition"], "HOLD")
                self.assertEqual(
                    result["reason_codes"],
                    ["RESOLVER:CANONICAL_SOURCE_MISSING"],
                )
        live.assert_not_called()

    @patch("concierge.discovered_revenue_intake.qualify_live_revenue_intake")
    def test_unicode_contaminated_text_holds_without_live_dispatch_read(self, live):
        hostile = (
            "éhttps://github.com/acme/widgets/issues/17",
            "https://github.com/acme/widgets/issues/17é",
            "αacme/widgets#17",
            "acme/widgets#17α",
            "\u0301https://github.com/acme/widgets/issues/17",
            "acme/widgets#17\u0301",
            "\u200dhttps://github.com/acme/widgets/issues/17",
            "acme/widgets#17\u200d",
        )
        for body in hostile:
            with self.subTest(body=body):
                result = qualify_discovered_revenue_intake(
                    {
                        "listing_url": "https://bounties.example/tasks/unicode-hostile",
                        "body": body,
                    }
                )
                self.assertFalse(result["dispatch"])
                self.assertEqual(result["disposition"], "HOLD")
                self.assertEqual(
                    result["reason_codes"],
                    ["RESOLVER:CANONICAL_SOURCE_MISSING"],
                )
        live.assert_not_called()

    @patch("concierge.discovered_revenue_intake.qualify_live_revenue_intake")
    def test_session_and_tighter_threshold_are_forwarded(self, live):
        live.return_value = {
            "disposition": "REJECT",
            "dispatch": False,
            "canonical_source_url": "https://github.com/acme/widgets/issues/17",
            "reason_codes": ["QUALIFICATION:CANONICAL_ISSUE_NOT_OPEN"],
            "reasons": [],
        }
        session = object()

        qualify_discovered_revenue_intake(
            {"listing_url": "https://github.com/acme/widgets/issues/17"},
            session=session,
            max_pages=3,
            saturation_threshold=2,
        )

        live.assert_called_once_with(
            "acme/widgets",
            17,
            listing_url="https://github.com/acme/widgets/issues/17",
            token=None,
            max_pages=3,
            saturation_threshold=2,
            session=session,
        )


if __name__ == "__main__":
    unittest.main()
