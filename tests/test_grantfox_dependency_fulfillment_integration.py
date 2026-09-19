import unittest

from concierge.grantfox_dependency_fulfillment import (
    compile_grantfox_dependency_fulfillment,
    verify_dependency_fulfillment_receipt,
)
from concierge.grantfox_dependency_readiness import (
    compile_grantfox_dependency_readiness,
    verify_dependency_readiness_receipt,
)
from concierge.grantfox_queue_gate import compile_grantfox_queue_gate
from concierge.grantfox_source_readiness import compile_grantfox_source_readiness


COMMIT = "5" * 40
BLOB = "a" * 40


def source_receipt():
    queue = compile_grantfox_queue_gate(
        {
            "schema": "grantfox-queue-gate/v1",
            "listing_url": "https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/33",
            "canonical_issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/33",
            "issue_state": "open",
            "actor_login": "woahwhattheheck",
            "assigned_to": None,
            "actor_applied": False,
            "application_count": 1,
            "application_pressure_threshold": 3,
            "linked_pr_urls": [],
            "labels": ["GrantFox OSS", "Maybe Rewarded"],
            "observed_at": "2026-09-19T22:00:00Z",
            "evaluated_at": "2026-09-19T22:00:10Z",
            "max_snapshot_age_seconds": 900,
        }
    )
    return compile_grantfox_source_readiness(
        {
            "schema": "grantfox-source-readiness/v1",
            "queue_receipt": queue,
            "repository_snapshot": {
                "repository_full_name": "Gryd-lock/grydlock-testkit",
                "default_branch": "main",
                "commit_sha": COMMIT,
                "observed_at": "2026-09-19T22:00:20Z",
            },
            "expectations": [
                {
                    "kind": "path",
                    "value": "transactions/README.md",
                    "matches": [
                        {"path": "transactions/README.md", "blob_sha": BLOB}
                    ],
                }
            ],
            "replacements": [],
            "evaluated_at": "2026-09-19T22:00:30Z",
            "max_snapshot_age_seconds": 900,
        }
    )


def dependency_receipt():
    receipt = compile_grantfox_dependency_readiness(
        {
            "schema": "grantfox-dependency-readiness/v1",
            "source_receipt": source_receipt(),
            "dependencies": [
                {
                    "repository_full_name": "Gryd-lock/grydlock-testkit",
                    "issue_number": 27,
                    "issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/27",
                    "state": "closed",
                    "observed_at": "2026-09-19T22:01:00Z",
                    "basis": "Issue #33 requires #27.",
                    "completion": {
                        "status": "LANDED",
                        "merged_pr_url": "https://github.com/Gryd-lock/grydlock-testkit/pull/127",
                        "merge_commit_sha": "b" * 40,
                        "observed_at": "2026-09-19T22:01:10Z",
                    },
                },
                {
                    "repository_full_name": "Gryd-lock/grydlock-testkit",
                    "issue_number": 4,
                    "issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/4",
                    "state": "closed",
                    "observed_at": "2026-09-19T22:01:00Z",
                    "basis": "Issue #33 requires #4.",
                    "completion": {
                        "status": "LANDED",
                        "merged_pr_url": "https://github.com/Gryd-lock/grydlock-testkit/pull/104",
                        "merge_commit_sha": "c" * 40,
                        "observed_at": "2026-09-19T22:01:10Z",
                    },
                },
            ],
            "evaluated_at": "2026-09-19T22:02:00Z",
            "max_snapshot_age_seconds": 900,
        }
    )
    assert verify_dependency_readiness_receipt(receipt)
    return receipt


def landing(number, sha, pr):
    return {
        "repository_full_name": "Gryd-lock/grydlock-testkit",
        "issue_number": number,
        "evidence_kind": "merged_pull_request",
        "evidence_url": f"https://github.com/Gryd-lock/grydlock-testkit/pull/{pr}",
        "pull_request_number": pr,
        "landed_commit_sha": sha,
        "default_branch": "main",
        "observed_default_branch_head_sha": "f" * 40,
        "contains_landed_commit": True,
        "observed_at": "2026-09-19T22:02:10Z",
        "basis": f"Observed PR #{pr} merge commit in current main ancestry.",
    }


class GrantFoxDependencyFulfillmentIntegrationTests(unittest.TestCase):
    def test_real_v1_receipt_hands_off_to_default_branch_fulfillment(self):
        upstream = dependency_receipt()
        self.assertEqual(upstream["dependency_disposition"], "DEPENDENCIES_CLEAR")

        fulfilled = compile_grantfox_dependency_fulfillment(
            {
                "schema": "grantfox-dependency-fulfillment/v1",
                "dependency_receipt": upstream,
                "landings": [
                    landing(27, "b" * 40, 127),
                    landing(4, "c" * 40, 104),
                ],
                "evaluated_at": "2026-09-19T22:03:00Z",
                "max_snapshot_age_seconds": 900,
            }
        )
        self.assertEqual(
            fulfilled["fulfillment_disposition"], "DEPENDENCIES_FULFILLED"
        )
        self.assertTrue(verify_dependency_fulfillment_receipt(fulfilled))

    def test_real_v1_clear_receipt_without_ancestry_evidence_still_waits(self):
        waiting = compile_grantfox_dependency_fulfillment(
            {
                "schema": "grantfox-dependency-fulfillment/v1",
                "dependency_receipt": dependency_receipt(),
                "landings": [],
                "evaluated_at": "2026-09-19T22:03:00Z",
                "max_snapshot_age_seconds": 900,
            }
        )
        self.assertEqual(waiting["fulfillment_disposition"], "FULFILLMENT_WAIT")
        self.assertEqual(
            waiting["fulfillment_summary"]["missing_issue_numbers"], [4, 27]
        )


if __name__ == "__main__":
    unittest.main()
