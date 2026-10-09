# SPDX-License-Identifier: MIT
"""Focused PR identity / portal roster / payout-truth regression."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from concierge.portal_registration_gap import audit, PortalRegistrationError, SCHEMA

AT = datetime(2026, 10, 9, 5, 45, tzinfo=timezone.utc)
T = "2026-10-09T05:43:00Z"


def example():
    return {"schema": SCHEMA, "cases": [{
        "platform": "issuehunt", "repo": "egoist/bili", "issue": 183,
        "claimant": "woahwhattheheck",
        "github": {"pr_url": "https://github.com/egoist/bili/pull/642",
                   "head_sha": "a" * 40, "state": "open",
                   "observed_at": T, "author": "woahwhattheheck"},
        "portal": {"source_url": "https://oss.issuehunt.io/r/egoist/bili/issues/183",
                   "observed_at": T, "complete": True,
                   "registered_pr_urls": ["https://oss.issuehunt.io/r/egoist/bili/pull/635"]},
        "settlement": None
    }]}


class PortalGapFocused(unittest.TestCase):
    def test_exact_original_pr_registration_and_deduplication(self):
        src = example()
        result = audit(src, as_of=AT)
        self.assertEqual(result["summary"]["GITHUB_SUBMITTED_PORTAL_NOT_REGISTERED"], 1)
        self.assertEqual(result["cases"][0]["other_registered_count"], 1)
        self.assertEqual(len(result["work_orders"]), 1)
        self.assertEqual(result["work_orders"][0]["operation_id"], audit(src, as_of=AT)["work_orders"][0]["operation_id"])
        src["cases"][0]["portal"]["registered_pr_urls"].append("https://oss.issuehunt.io/r/egoist/bili/pull/642")
        result = audit(src, as_of=AT)
        self.assertEqual(result["cases"][0]["status"], "PORTAL_REGISTERED_UNAWARDED")
        self.assertEqual(result["work_orders"], [])

    def test_incomplete_stale_or_different_author_fail_closed(self):
        src = example()
        src["cases"][0]["portal"]["complete"] = False
        self.assertEqual(audit(src, as_of=AT)["cases"][0]["status"], "UNKNOWN")
        src = example()
        src["cases"][0]["portal"]["observed_at"] = "2026-10-01T05:00:00Z"
        self.assertEqual(audit(src, as_of=AT)["work_orders"], [])
        src = example()
        src["cases"][0]["github"]["author"] = "some-other-author"
        with self.assertRaises(PortalRegistrationError):
            audit(src, as_of=AT)
        src = example()
        src["cases"][0]["portal"]["source_url"] = "https://oss.issuehunt.io.evil.org/r/egoist/bili/issues/183"
        with self.assertRaises(PortalRegistrationError):
            audit(src, as_of=AT)

    def test_independent_settlement_proof_same_pr_and_duplicate_identity(self):
        src = example()
        identity = {"platform": "issuehunt", "repo": "egoist/bili", "issue": 183,
                    "claimant": "woahwhattheheck", "pr_url": "https://github.com/egoist/bili/pull/642"}
        src["cases"][0]["settlement"] = {"status": "AWARDED", "identity": identity,
                                           "receipt_sha256": "b" * 64, "verified_at": T}
        self.assertEqual(audit(src, as_of=AT)["cases"][0]["status"], "AWARDED")
        src["cases"][0]["settlement"]["status"] = "PAID"
        self.assertEqual(audit(src, as_of=AT)["cases"][0]["status"], "PAID")
        src["cases"][0]["settlement"]["verified_at"] = "2026-09-01T05:43:00Z"
        self.assertEqual(audit(src, as_of=AT)["cases"][0]["status"], "UNKNOWN")
        src["cases"][0]["settlement"]["verified_at"] = T
        src["cases"][0]["settlement"]["identity"]["pr_url"] = "https://github.com/egoist/bili/pull/635"
        with self.assertRaises(PortalRegistrationError):
            audit(src, as_of=AT)
        src = example()
        src["cases"].append(deepcopy(src["cases"][0]))
        with self.assertRaises(PortalRegistrationError):
            audit(src, as_of=AT)


if __name__ == "__main__":
    unittest.main()
