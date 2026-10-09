"""One focused behavior check for the offline claim-gap report."""

import copy
import unittest
from datetime import datetime, timezone

from concierge.claim_gap_audit import SCHEMA, audit


NOW = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
ACTOR = {"login": "woahwhattheheck", "id": 293286387}


def snapshot():
    return {
        "schema": SCHEMA,
        "actor": ACTOR,
        "observed_at": NOW.isoformat(),
        "pull_requests": [{
            "repo": "Centurylong/sanctifier",
            "number": 1024,
            "state": "open",
            "head_sha": "a" * 40,
            "author": ACTOR,
            "body": (
                "Closes #336\n"
                "/claim Centurylong/sanctifier#336\n"
                "I request eligible bounty compensation."
            ),
        }],
        "issues": [{
            "repo": "Centurylong/sanctifier",
            "number": 336,
            "pr_number": 1024,
            "state": "open",
            "comments_complete": True,
            "comments": [],
        }],
    }


class ClaimGapAuditCase(unittest.TestCase):
    def test_missing_original_issue_claim_is_actionable(self):
        result = audit(snapshot(), now=NOW)
        row = result["items"][0]
        self.assertEqual(row["state"], "ACTIONABLE_GAPS")
        self.assertEqual(row["gaps"], ["MISSING_ORIGINAL_ISSUE_CLAIM"])
        self.assertEqual(result["side_effects"], "NONE")

    def test_matching_author_comment_closes_gap(self):
        data = snapshot()
        data["issues"][0]["comments"] = [{
            "author": ACTOR, "body": "/claim #336\nI request payout review.",
        }]
        result = audit(data, now=NOW)
        self.assertEqual(result["items"][0]["state"], "CLAIMS_PRESENT")
        self.assertEqual(result["summary"]["already_claimed"], 1)

    def test_other_author_cannot_satisfy_original_claim(self):
        data = snapshot()
        data["issues"][0]["comments"] = [{
            "author": {"login": "other", "id": 99},
            "body": "/claim #336",
        }]
        self.assertEqual(
            audit(data, now=NOW)["items"][0]["gaps"],
            ["MISSING_ORIGINAL_ISSUE_CLAIM"],
        )

    def test_incomplete_evidence_and_stale_capture_hold(self):
        data = snapshot()
        data["issues"][0]["comments_complete"] = False
        self.assertEqual(
            audit(data, now=NOW)["items"][0]["state"],
            "HOLD_PARTIAL_ISSUE_TIMELINE",
        )
        later = NOW.replace(day=11)
        self.assertEqual(
            audit(data, now=later)["items"][0]["state"],
            "HOLD_STALE_CAPTURE",
        )


if __name__ == "__main__":
    unittest.main()
