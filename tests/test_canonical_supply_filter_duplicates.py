# SPDX-License-Identifier: MIT
"""Focused canonical-target marketplace duplicate regression coverage."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from tools.canonical_supply_filter import evaluate


NOW = datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc)


def issue(resource: str, **overrides):
    result = {
        "resource": resource,
        "issue_state": "open",
        "reward_state": "available",
        "repository_archived": False,
        "reward_scope": "issue",
        "assignees": [],
        "claims": [],
        "same_scope_prs": [],
    }
    result.update(overrides)
    return result


def evaluate_items(items, *, captured="2026-10-08T22:00:00Z"):
    return evaluate(
        {"schema": "canonical-supply-snapshot/v1", "captured_at": captured, "items": items},
        owner="tokenjunkielabs",
        max_age_seconds=900,
        now=NOW,
    )


class CanonicalSupplyDuplicateTest(unittest.TestCase):
    def test_same_issue_only_one_new_build(self):
        result = evaluate_items([
            issue("go-gitea/gitea#1872"),
            issue("GO-GITEA/GITEA#01872"),
            issue("go-gitea/gitea#4898"),
        ])
        rows = result["items"]
        self.assertEqual([x["canonical_decision"] for x in rows], [
            "BUILD_ALLOWED", "PRUNE", "BUILD_ALLOWED",
        ])
        self.assertEqual([x["new_build_allowed"] for x in rows], [True, False, True])
        self.assertIn("canonical_primary_row:0", rows[1]["decision_reasons"])
        self.assertEqual(result["counts"]["BUILD_ALLOWED"], 2)

    def test_conflicting_duplicate_evidence_blocks_every_alias(self):
        result = evaluate_items([
            issue("go-gitea/gitea#1872"),
            issue("GO-GITEA/gitea#1872", issue_state="closed"),
        ])
        self.assertEqual([x["canonical_decision"] for x in result["items"]], [
            "VERIFY_REQUIRED", "VERIFY_REQUIRED",
        ])
        self.assertTrue(all(not x["build_allowed"] for x in result["items"]))

    def test_stale_duplicate_rows_remain_unverified(self):
        result = evaluate_items([
            issue("go-gitea/gitea#1872"), issue("go-gitea/gitea#1872"),
        ], captured="2026-10-08T20:00:00Z")
        self.assertEqual([x["canonical_decision"] for x in result["items"]], [
            "VERIFY_REQUIRED", "VERIFY_REQUIRED",
        ])


if __name__ == "__main__":
    unittest.main()
