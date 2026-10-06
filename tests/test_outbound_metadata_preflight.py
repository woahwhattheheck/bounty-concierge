# SPDX-License-Identifier: MIT
"""Focused checks for outbound metadata preflight."""

import unittest

from concierge.outbound_metadata_preflight import (
    MetadataPreflightError,
    inspect_outbound_metadata,
)


class OutboundMetadataPreflightTests(unittest.TestCase):
    def test_clean_technical_metadata_is_clear(self):
        self.assertEqual(
            inspect_outbound_metadata({
                "title": "fix: preserve connector retry semantics",
                "body": "Updates the provider integration and keeps the existing API contract.",
            }),
            [],
        )

    def test_generated_footer_and_coauthor_are_blocked(self):
        result = inspect_outbound_metadata({
            "body": "Implementation notes.\nGenerated with AI\nCo-Authored-By: Example <x@example.com>"
        })
        self.assertEqual(
            [(item.line, item.code) for item in result],
            [(2, "GENERATED_ATTRIBUTION"), (3, "COAUTHOR_TRAILER")],
        )

    def test_harness_and_internal_lane_are_blocked(self):
        result = inspect_outbound_metadata({
            "body": "Sol-Recovery-0027 / cloud harness"
        })
        self.assertEqual(
            {item.code for item in result},
            {"HARNESS_BYLINE", "INTERNAL_LANE"},
        )

    def test_internal_source_branch_is_blocked(self):
        result = inspect_outbound_metadata({
            "body": "Exact source: owner/repo:sol56/fix-thing at abc123."
        })
        self.assertEqual([item.code for item in result], ["INTERNAL_BRANCH_PREFIX"])

    def test_claimant_credit_is_blocked(self):
        result = inspect_outbound_metadata({
            "body": "GrantFox claimant on #404: @example."
        })
        self.assertEqual([item.code for item in result], ["CREDIT_BYLINE"])

    def test_plain_technical_content_is_not_rewritten_or_echoed(self):
        body = "Adds a provider option and preserves the public API."
        self.assertEqual(inspect_outbound_metadata({"body": body}), [])

    def test_unknown_field_fails_closed(self):
        with self.assertRaises(MetadataPreflightError):
            inspect_outbound_metadata({"body": "ok", "unexpected": "value"})


if __name__ == "__main__":
    unittest.main()
