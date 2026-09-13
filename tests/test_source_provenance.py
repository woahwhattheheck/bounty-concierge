from __future__ import annotations

import unittest

from concierge.source_provenance import (
    ProvenanceInputError,
    verify_source_provenance,
)


def snapshot(**overrides):
    issue_url = "https://github.com/acme/widgets/issues/17"
    value = {
        "listing_url": issue_url,
        "reward_evidence_urls": [issue_url],
        "canonical_audit": {
            "repo": "acme/widgets",
            "number": 17,
            "issue_url": issue_url,
            "issue_state": "open",
            "open_pr_count": 0,
            "stale_listing_signal": False,
            "search_truncated": False,
        },
    }
    value.update(overrides)
    return value


class SourceProvenanceTests(unittest.TestCase):
    def test_first_party_canonical_issue_is_actionable(self):
        result = verify_source_provenance(snapshot())
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["signals"]["listing_relation"], "FIRST_PARTY")
        self.assertEqual(
            result["use_source_url"],
            "https://github.com/acme/widgets/issues/17",
        )

    def test_external_mirror_is_canonicalized_when_reward_is_first_party(self):
        result = verify_source_provenance(
            snapshot(listing_url="https://bounties.example/tasks/widgets-17?ref=feed")
        )
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertEqual(
            result["signals"]["listing_relation"], "MIRROR_CANONICALIZED"
        )
        self.assertEqual(
            result["use_source_url"],
            "https://github.com/acme/widgets/issues/17",
        )

    def test_external_reward_only_holds_dispatch(self):
        result = verify_source_provenance(
            snapshot(
                listing_url="https://github.com.evil.example/acme/widgets/issues/17",
                reward_evidence_urls=[
                    "https://bounties.example/tasks/widgets-17#reward"
                ],
            )
        )
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn(
            "CANONICAL_REWARD_EVIDENCE_MISSING",
            result["reason_codes"],
        )
        self.assertEqual(result["signals"]["ignored_reward_evidence_count"], 1)

    def test_canonical_comment_anchor_is_valid_reward_evidence(self):
        result = verify_source_provenance(
            snapshot(
                reward_evidence_urls=[
                    "https://github.com/acme/widgets/issues/17#issuecomment-123456"
                ]
            )
        )
        self.assertTrue(result["dispatch"])
        self.assertEqual(
            result["signals"]["canonical_reward_evidence_count"], 1
        )

    def test_query_bearing_reward_url_is_not_canonical_authority(self):
        result = verify_source_provenance(
            snapshot(
                reward_evidence_urls=[
                    "https://github.com/acme/widgets/issues/17?redirect=mirror"
                ]
            )
        )
        self.assertEqual(result["disposition"], "HOLD")
        self.assertEqual(
            result["signals"]["canonical_reward_evidence_count"], 0
        )

    def test_null_canonical_audit_booleans_fail_closed(self):
        for field in ("stale_listing_signal", "search_truncated"):
            with self.subTest(field=field):
                value = snapshot()
                value["canonical_audit"] = dict(value["canonical_audit"])
                value["canonical_audit"][field] = None
                with self.assertRaisesRegex(ProvenanceInputError, "must be boolean"):
                    verify_source_provenance(value)

    def test_incomplete_canonical_audit_holds_standalone_dispatch(self):
        for missing in ("stale_listing_signal", "search_truncated"):
            with self.subTest(missing=missing):
                value = snapshot()
                value["canonical_audit"] = dict(value["canonical_audit"])
                value["canonical_audit"].pop(missing)
                result = verify_source_provenance(value)
                self.assertEqual(result["disposition"], "HOLD")
                self.assertFalse(result["dispatch"])
                self.assertIn("CANONICAL_AUDIT_INCOMPLETE", result["reason_codes"])
                self.assertFalse(result["signals"]["canonical_source_verified"])

    def test_mismatched_canonical_issue_url_fails_closed(self):
        value = snapshot()
        value["canonical_audit"] = dict(value["canonical_audit"])
        value["canonical_audit"]["issue_url"] = (
            "https://github.com/acme/widgets/issues/18"
        )
        with self.assertRaisesRegex(
            ProvenanceInputError, "does not match"
        ):
            verify_source_provenance(value)

    def test_closed_issue_rejects_even_with_reward_evidence(self):
        value = snapshot()
        value["canonical_audit"] = dict(value["canonical_audit"])
        value["canonical_audit"]["issue_state"] = "closed"
        result = verify_source_provenance(value)
        self.assertEqual(result["disposition"], "REJECT")
        self.assertFalse(result["dispatch"])
        self.assertIn("CANONICAL_ISSUE_NOT_OPEN", result["reason_codes"])

    def test_truncated_or_stale_canonical_audit_holds(self):
        for field in ("search_truncated", "stale_listing_signal"):
            with self.subTest(field=field):
                value = snapshot()
                value["canonical_audit"] = dict(value["canonical_audit"])
                value["canonical_audit"][field] = True
                result = verify_source_provenance(value)
                self.assertEqual(result["disposition"], "HOLD")
                self.assertFalse(result["dispatch"])

    def test_missing_canonical_audit_holds_without_echoing_listing(self):
        result = verify_source_provenance(
            {
                "listing_url": "https://example.test/private-source-name",
                "canonical_audit": None,
            }
        )
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIsNone(result["use_source_url"])
        self.assertNotIn("private-source-name", repr(result))

    def test_non_https_or_credential_bearing_urls_are_rejected(self):
        hostile = (
            "http://github.com/acme/widgets/issues/17",
            "https://user:pass@github.com/acme/widgets/issues/17",
            "https://github.com:444/acme/widgets/issues/17",
        )
        for listing_url in hostile:
            with self.subTest(listing_url=listing_url):
                with self.assertRaises(ProvenanceInputError):
                    verify_source_provenance(snapshot(listing_url=listing_url))

    def test_non_list_reward_evidence_is_rejected(self):
        with self.assertRaisesRegex(
            ProvenanceInputError, "reward_evidence_urls must be a list"
        ):
            verify_source_provenance(snapshot(reward_evidence_urls="not-a-list"))


if __name__ == "__main__":
    unittest.main()
