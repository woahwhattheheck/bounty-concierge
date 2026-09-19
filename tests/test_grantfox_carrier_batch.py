from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.grantfox_carrier_batch import (
    GrantFoxCarrierBatchInputError,
    compile_grantfox_carrier_batch,
    verify_carrier_batch_receipt,
)


def census(number=10, *, carriers=None, observed="2026-09-19T22:40:00Z",
           evaluated="2026-09-19T22:41:00Z"):
    return {
        "schema": "grantfox-carrier-census/v1",
        "canonical_issue_url": f"https://github.com/Acme/repo/issues/{number}",
        "carriers": carriers or [],
        "observed_at": observed,
        "evaluated_at": evaluated,
        "max_snapshot_age_seconds": 900,
    }


def carrier(number=20, *, state="open", process="normal", relation="closes"):
    return {
        "pr_url": f"https://github.com/Acme/repo/pull/{number}",
        "state": state,
        "issue_relation": relation,
        "process_disposition": process,
        "head_sha": ("%040x" % number),
    }


def batch(*snapshots):
    return {"schema": "grantfox-carrier-batch/v1", "snapshots": list(snapshots)}


class GrantFoxCarrierBatchTests(unittest.TestCase):
    def test_mixed_wave_counts_and_suppresses_nonclear(self):
        receipt = compile_grantfox_carrier_batch(
            batch(
                census(14, observed="2026-09-19T22:00:00Z",
                       evaluated="2026-09-19T23:00:01Z"),
                census(10),
                census(12, carriers=[carrier(22, state="closed",
                                             process="closed_unassigned")]),
                census(11, carriers=[carrier(21)]),
                census(13, carriers=[carrier(23, state="closed",
                                             process="unknown")]),
            )
        )
        self.assertEqual(receipt["issue_count"], 5)
        self.assertEqual(
            receipt["counts"],
            {
                "CLEAR_FOR_QUEUE_EVALUATION": 1,
                "REUSE_EXISTING_CARRIER": 1,
                "REAPPLY_WITH_REUSABLE_CARRIER": 1,
                "REVIEW_CLOSED_CARRIER": 1,
                "HOLD": 1,
            },
        )
        self.assertEqual(receipt["summary"]["suppressed_or_review_count"], 4)
        self.assertTrue(verify_carrier_batch_receipt(receipt))

    def test_permutation_stable_and_canonically_sorted(self):
        one = compile_grantfox_carrier_batch(batch(census(13), census(11), census(12)))
        two = compile_grantfox_carrier_batch(batch(census(12), census(13), census(11)))
        self.assertEqual(one, two)
        self.assertEqual(
            [c["identity"]["issue_number"] for c in one["children"]],
            [11, 12, 13],
        )

    def test_duplicate_issue_identity_rejected_case_insensitively(self):
        duplicate = census(10)
        duplicate["canonical_issue_url"] = "https://github.com/acme/REPO/issues/10"
        with self.assertRaisesRegex(
            GrantFoxCarrierBatchInputError, "duplicate canonical issue identity"
        ):
            compile_grantfox_carrier_batch(batch(census(10), duplicate))

    def test_invalid_child_is_indexed(self):
        bad = census(11)
        bad["canonical_issue_url"] = "http://github.com/Acme/repo/issues/11"
        with self.assertRaisesRegex(
            GrantFoxCarrierBatchInputError, r"snapshots\[1\] failed child census"
        ):
            compile_grantfox_carrier_batch(batch(census(10), bad))

    def test_empty_and_oversized_batches_fail_closed(self):
        with self.assertRaisesRegex(GrantFoxCarrierBatchInputError, "must not be empty"):
            compile_grantfox_carrier_batch(batch())
        with self.assertRaisesRegex(GrantFoxCarrierBatchInputError, "at most 500"):
            compile_grantfox_carrier_batch(
                {
                    "schema": "grantfox-carrier-batch/v1",
                    "snapshots": [census(i + 1) for i in range(501)],
                }
            )

    def test_unknown_parent_field_rejected(self):
        payload = batch(census(10))
        payload["surprise"] = True
        with self.assertRaisesRegex(GrantFoxCarrierBatchInputError, "unsupported fields"):
            compile_grantfox_carrier_batch(payload)

    def test_irrelevant_pr_remains_clear_and_is_not_relevant(self):
        receipt = compile_grantfox_carrier_batch(
            batch(census(10, carriers=[carrier(20, relation="none")]))
        )
        self.assertEqual(receipt["counts"]["CLEAR_FOR_QUEUE_EVALUATION"], 1)
        self.assertEqual(receipt["summary"]["relevant_carrier_count"], 0)

    def test_parent_authority_never_upgrades_children(self):
        receipt = compile_grantfox_carrier_batch(batch(census(10)))
        self.assertTrue(receipt["authority"]["advisory_only"])
        for key, value in receipt["authority"].items():
            if key != "advisory_only":
                self.assertFalse(value)

    def test_child_or_summary_tamper_breaks_verification(self):
        receipt = compile_grantfox_carrier_batch(
            batch(census(10), census(11, carriers=[carrier(21)]))
        )
        changed = deepcopy(receipt)
        changed["children"][1]["census"]["carriers"][0]["state"] = "closed"
        self.assertFalse(verify_carrier_batch_receipt(changed))
        changed = deepcopy(receipt)
        changed["summary"]["suppressed_or_review_count"] = 0
        self.assertFalse(verify_carrier_batch_receipt(changed))

    def test_reordering_children_breaks_verification(self):
        receipt = compile_grantfox_carrier_batch(batch(census(10), census(11)))
        changed = deepcopy(receipt)
        changed["children"] = list(reversed(changed["children"]))
        self.assertFalse(verify_carrier_batch_receipt(changed))

    def test_cli_exit_zero_for_clear_and_two_for_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clear_path = Path(tmp) / "clear.json"
            held_path = Path(tmp) / "held.json"
            clear_path.write_text(json.dumps(batch(census(10))), encoding="utf-8")
            held_path.write_text(
                json.dumps(batch(census(10, carriers=[carrier(20)]))),
                encoding="utf-8",
            )
            clear = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.grantfox_carrier_batch",
                    str(clear_path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            held = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.grantfox_carrier_batch",
                    str(held_path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(clear.returncode, 0, clear.stderr)
        self.assertEqual(held.returncode, 2, held.stderr)
        self.assertTrue(verify_carrier_batch_receipt(json.loads(held.stdout)))


if __name__ == "__main__":
    unittest.main()
