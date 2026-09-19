from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.grantfox_queue_batch import (
    GrantFoxBatchInputError,
    compile_grantfox_queue_batch,
    verify_batch_receipt,
)


def child(number=33, **overrides):
    payload = {
        "schema": "grantfox-queue-gate/v1",
        "listing_url": (
            f"https://contribute.grantfox.xyz/org/Gryd-lock/"
            f"repo/grydlock-testkit/issue/{number}"
        ),
        "canonical_issue_url": (
            f"https://github.com/Gryd-lock/grydlock-testkit/issues/{number}"
        ),
        "issue_state": "open",
        "actor_login": "woahwhattheheck",
        "assigned_to": None,
        "actor_applied": False,
        "application_count": 0,
        "application_pressure_threshold": 3,
        "linked_pr_urls": [],
        "labels": ["Maybe Rewarded", "GrantFox OSS"],
        "observed_at": "2026-09-19T21:50:00Z",
        "evaluated_at": "2026-09-19T21:51:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


def batch(*snapshots):
    return {"schema": "grantfox-queue-batch/v1", "snapshots": list(snapshots)}


class GrantFoxQueueBatchTests(unittest.TestCase):
    def test_mixed_batch_preserves_child_dispositions_and_counts(self):
        receipt = compile_grantfox_queue_batch(
            batch(
                child(36, linked_pr_urls=["https://github.com/acme/repo/pull/7"]),
                child(33),
                child(35, actor_applied=True),
                child(34, actor_applied=True, assigned_to="woahwhattheheck"),
            )
        )
        self.assertEqual(receipt["issue_count"], 4)
        self.assertEqual(
            receipt["counts"],
            {
                "APPLY_ELIGIBLE": 1,
                "WAIT_ASSIGNMENT": 1,
                "IMPLEMENTATION_ELIGIBLE": 1,
                "HOLD": 1,
            },
        )
        self.assertTrue(verify_batch_receipt(receipt))

    def test_output_is_permutation_stable(self):
        one = compile_grantfox_queue_batch(batch(child(35), child(33), child(34)))
        two = compile_grantfox_queue_batch(batch(child(34), child(35), child(33)))
        self.assertEqual(one, two)
        self.assertEqual(
            [entry["identity"]["issue_number"] for entry in one["children"]],
            [33, 34, 35],
        )

    def test_duplicate_canonical_identity_is_rejected_case_insensitively(self):
        duplicate = child(
            33,
            listing_url=(
                "https://contribute.grantfox.xyz/org/gryd-lock/"
                "repo/GRYDLOCK-TESTKIT/issue/33"
            ),
            canonical_issue_url=(
                "https://github.com/gryd-lock/GRYDLOCK-TESTKIT/issues/33"
            ),
        )
        with self.assertRaisesRegex(
            GrantFoxBatchInputError, "duplicate canonical issue identity"
        ):
            compile_grantfox_queue_batch(batch(child(33), duplicate))

    def test_empty_and_oversized_batches_fail_closed(self):
        with self.assertRaisesRegex(GrantFoxBatchInputError, "must not be empty"):
            compile_grantfox_queue_batch(batch())
        with self.assertRaisesRegex(GrantFoxBatchInputError, "at most 500"):
            compile_grantfox_queue_batch(
                {"schema": "grantfox-queue-batch/v1", "snapshots": [child(i + 1) for i in range(501)]}
            )

    def test_child_gate_error_is_indexed_and_propagated(self):
        bad = child(34, canonical_issue_url="https://github.com/Gryd-lock/grydlock-testkit/issues/999")
        with self.assertRaisesRegex(
            GrantFoxBatchInputError, r"snapshots\[1\] failed child gate"
        ):
            compile_grantfox_queue_batch(batch(child(33), bad))

    def test_batch_authority_never_upgrades_child_advice(self):
        receipt = compile_grantfox_queue_batch(batch(child(33)))
        self.assertEqual(
            receipt["authority"],
            {
                "advisory_only": True,
                "provider_application_authority": False,
                "implementation_write_authority": False,
                "submission_authority": False,
                "payment_or_wallet_authority": False,
            },
        )

    def test_reward_summary_never_claims_award_or_payment(self):
        receipt = compile_grantfox_queue_batch(
            batch(child(33), child(34, labels=["GrantFox OSS"]))
        )
        self.assertEqual(receipt["reward_summary"]["possible_discretionary_count"], 1)
        self.assertEqual(receipt["reward_summary"]["explicit_award_count"], 0)
        self.assertEqual(receipt["reward_summary"]["verified_payment_count"], 0)

    def test_child_tamper_breaks_parent_verification_even_with_parent_digest_reused(self):
        receipt = compile_grantfox_queue_batch(batch(child(33), child(34)))
        changed = deepcopy(receipt)
        changed["children"][0]["provider_snapshot"]["application_count"] = 99
        self.assertFalse(verify_batch_receipt(changed))

    def test_count_or_bucket_tamper_breaks_verification(self):
        receipt = compile_grantfox_queue_batch(batch(child(33), child(34, actor_applied=True)))
        changed = deepcopy(receipt)
        changed["counts"]["HOLD"] = 99
        self.assertFalse(verify_batch_receipt(changed))
        changed = deepcopy(receipt)
        changed["issues_by_disposition"]["APPLY_ELIGIBLE"] = []
        self.assertFalse(verify_batch_receipt(changed))

    def test_child_order_tamper_breaks_verification(self):
        receipt = compile_grantfox_queue_batch(batch(child(33), child(34)))
        changed = deepcopy(receipt)
        changed["children"] = list(reversed(changed["children"]))
        self.assertFalse(verify_batch_receipt(changed))

    def test_cli_round_trip(self):
        payload = batch(child(33), child(34, actor_applied=True))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.grantfox_queue_batch",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertTrue(verify_batch_receipt(receipt))
        self.assertEqual(receipt["issue_count"], 2)


if __name__ == "__main__":
    unittest.main()
