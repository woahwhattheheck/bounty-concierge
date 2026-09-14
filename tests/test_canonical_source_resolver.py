from __future__ import annotations

import unittest

from concierge.canonical_source_resolver import (
    CanonicalSourceInputError,
    resolve_canonical_source,
)


class CanonicalSourceResolverTests(unittest.TestCase):
    def test_direct_github_issue_listing_is_authoritative(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://github.com/acme/widgets/issues/17",
                "body": "Related: https://github.com/acme/widgets/issues/99",
            }
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["canonical_repo"], "acme/widgets")
        self.assertEqual(result["issue_number"], 17)
        self.assertEqual(result["signals"]["resolution_basis"], "LISTING_URL")
        self.assertTrue(result["signals"]["listing_is_canonical"])

    def test_external_mirror_resolves_one_explicit_source_url(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/tasks/widget-17?feed=hot",
                "source_urls": [
                    "https://docs.example/widget-17",
                    "https://github.com/acme/widgets/issues/17#issuecomment-123",
                ],
            }
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(
            result["canonical_issue_url"],
            "https://github.com/acme/widgets/issues/17",
        )
        self.assertEqual(
            result["signals"]["resolution_basis"], "EXPLICIT_SOURCE_URL"
        )
        self.assertEqual(result["signals"]["ignored_source_url_count"], 1)

    def test_explicit_source_urls_override_untrusted_listing_text(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/tasks/widget-17",
                "source_urls": ["https://github.com/acme/widgets/issues/17"],
                "body": "Also see https://github.com/other/noise/issues/999",
            }
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["canonical_repo"], "acme/widgets")
        self.assertEqual(result["issue_number"], 17)

    def test_case_aliases_collapse_to_one_github_identity(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/tasks/widget-17",
                "source_urls": [
                    "https://github.com/Acme/Widgets/issues/17",
                    "https://github.com/acme/widgets/issues/17",
                ],
            }
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["canonical_repo"], "acme/widgets")
        self.assertEqual(result["signals"]["candidate_count"], 1)

    def test_external_mirror_can_resolve_full_issue_url_from_text(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/tasks/widget-17",
                "title": "Widget bounty",
                "body": "Canonical work item: https://github.com/acme/widgets/issues/17",
            }
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["signals"]["resolution_basis"], "LISTING_TEXT")
        self.assertEqual(result["canonical_repo"], "acme/widgets")

    def test_external_mirror_can_resolve_qualified_issue_reference(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/tasks/widget-17",
                "body": "Canonical: acme/widgets#17",
            }
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["issue_number"], 17)

    def test_text_reference_decorations_do_not_resolve(self):
        hostile = (
            "https://github.com/acme/widgets/issues/17evil",
            "https://github.com/acme/widgets/issues/17?redirect=mirror",
            "https://github.com/acme/widgets/issues/17/extra",
            "https://github.com/acme/widgets/issues/17.evil",
            "https://github.com/acme/widgets/issues/17..evil",
            "https://github.com/acme/widgets/issues/17.#fragment",
            "acme/widgets#17evil",
            "acme/widgets#17-extra",
            "acme/widgets#17.evil",
            "acme/widgets#17..evil",
            "evilhttps://github.com/acme/widgets/issues/17",
            "foo/https://github.com/acme/widgets/issues/17",
            "evil/acme/widgets#17",
            "/acme/widgets#17",
        )
        for body in hostile:
            with self.subTest(body=body):
                result = resolve_canonical_source(
                    {
                        "listing_url": "https://bounties.example/tasks/hostile",
                        "body": body,
                    }
                )
                self.assertFalse(result["resolved"])
                self.assertEqual(
                    result["reason_codes"], ["CANONICAL_SOURCE_MISSING"]
                )

    def test_unicode_token_contamination_does_not_resolve(self):
        hostile = (
            "éhttps://github.com/acme/widgets/issues/17",
            "https://github.com/acme/widgets/issues/17é",
            "αacme/widgets#17",
            "acme/widgets#17α",
            "\u0301https://github.com/acme/widgets/issues/17",
            "acme/widgets#17\u0301",
            "\u200dhttps://github.com/acme/widgets/issues/17",
            "acme/widgets#17\u200d",
            "·https://github.com/acme/widgets/issues/17",
            "https://github.com/acme/widgets/issues/17·",
            "·acme/widgets#17",
            "acme/widgets#17·",
        )
        for body in hostile:
            with self.subTest(body=body):
                result = resolve_canonical_source(
                    {
                        "listing_url": "https://bounties.example/tasks/unicode-hostile",
                        "body": body,
                    }
                )
                self.assertFalse(result["resolved"])
                self.assertEqual(
                    result["reason_codes"], ["CANONICAL_SOURCE_MISSING"]
                )

    def test_clean_text_references_after_punctuation_still_resolve(self):
        clean = (
            "Canonical (https://github.com/acme/widgets/issues/17).",
            "Canonical [acme/widgets#17].",
            "Canonical: https://github.com/acme/widgets/issues/17.",
            "Canonical: acme/widgets#17.",
            "Canonical — https://github.com/acme/widgets/issues/17",
            "Canonical — acme/widgets#17",
        )
        for body in clean:
            with self.subTest(body=body):
                result = resolve_canonical_source(
                    {
                        "listing_url": "https://bounties.example/tasks/clean",
                        "body": body,
                    }
                )
                self.assertTrue(result["resolved"])
                self.assertEqual(result["canonical_repo"], "acme/widgets")
                self.assertEqual(result["issue_number"], 17)

    def test_multiple_explicit_sources_hold_instead_of_guessing(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/tasks/ambiguous",
                "source_urls": [
                    "https://github.com/acme/widgets/issues/17",
                    "https://github.com/acme/widgets/issues/18",
                ],
            }
        )
        self.assertFalse(result["resolved"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertEqual(result["reason_codes"], ["CANONICAL_SOURCE_AMBIGUOUS"])
        self.assertEqual(result["signals"]["candidate_count"], 2)

    def test_multiple_text_sources_hold_instead_of_guessing(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/tasks/ambiguous",
                "body": (
                    "Either https://github.com/acme/widgets/issues/17 "
                    "or acme/widgets#18"
                ),
            }
        )
        self.assertFalse(result["resolved"])
        self.assertEqual(result["reason_codes"], ["CANONICAL_SOURCE_AMBIGUOUS"])

    def test_missing_source_holds_without_echoing_listing_text(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://bounties.example/private-task-name",
                "body": "private customer prose",
            }
        )
        self.assertFalse(result["resolved"])
        self.assertEqual(result["reason_codes"], ["CANONICAL_SOURCE_MISSING"])
        self.assertNotIn("private-task-name", repr(result))
        self.assertNotIn("private customer prose", repr(result))

    def test_lookalike_github_host_does_not_resolve(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://github.com.evil.example/acme/widgets/issues/17",
                "body": "no canonical source here",
            }
        )
        self.assertFalse(result["resolved"])
        self.assertEqual(result["reason_codes"], ["CANONICAL_SOURCE_MISSING"])

    def test_query_bearing_direct_github_url_is_not_silently_authoritative(self):
        result = resolve_canonical_source(
            {
                "listing_url": "https://github.com/acme/widgets/issues/17?redirect=mirror",
            }
        )
        self.assertFalse(result["resolved"])
        self.assertEqual(result["reason_codes"], ["CANONICAL_SOURCE_MISSING"])

    def test_structurally_unsafe_urls_fail_closed(self):
        bad = (
            "http://bounties.example/task/17",
            "https://user:pass@bounties.example/task/17",
            "https://bounties.example:444/task/17",
        )
        for listing_url in bad:
            with self.subTest(listing_url=listing_url):
                with self.assertRaises(CanonicalSourceInputError):
                    resolve_canonical_source({"listing_url": listing_url})

    def test_non_list_source_urls_are_rejected(self):
        with self.assertRaisesRegex(
            CanonicalSourceInputError, "source_urls must be a list"
        ):
            resolve_canonical_source(
                {
                    "listing_url": "https://bounties.example/task/17",
                    "source_urls": "https://github.com/acme/widgets/issues/17",
                }
            )


if __name__ == "__main__":
    unittest.main()
