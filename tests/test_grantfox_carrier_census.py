from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.grantfox_carrier_census import (
    GrantFoxCarrierCensusInputError,
    compile_grantfox_carrier_census,
    verify_carrier_census_receipt,
)


def request(**overrides):
    payload = {
        "schema": "grantfox-carrier-census/v1",
        "canonical_issue_url": "https://github.com/Stellar-VaultLink/invofi/issues/149",
        "carriers": [],
        "observed_at": "2026-09-19T22:30:00Z",
        "evaluated_at": "2026-09-19T22:32:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


def carrier(**overrides):
    payload = {
        "pr_url": "https://github.com/Stellar-VaultLink/invofi/pull/347",
        "state": "open",
        "issue_relation": "closes",
        "process_disposition": "normal",
        "head_sha": "53fe2690f70123456789abcdef0123456789abcd",
    }
    payload.update(overrides)
    return payload


class GrantFoxCarrierCensusTests(unittest.TestCase):
    def test_clear_when_no_relevant_carrier_exists(self):
        receipt = compile_grantfox_carrier_census(request())
        self.assertEqual(receipt["disposition"], "CLEAR_FOR_QUEUE_EVALUATION")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertFalse(receipt["authority"]["implementation_write_authority"])

    def test_open_carrier_suppresses_duplicate_build(self):
        receipt = compile_grantfox_carrier_census(request(carriers=[carrier()]))
        self.assertEqual(receipt["disposition"], "REUSE_EXISTING_CARRIER")
        self.assertIn("ACTIVE_OR_MERGED_CARRIER_PRESENT", receipt["reason_codes"])

    def test_merged_carrier_suppresses_duplicate_build(self):
        receipt = compile_grantfox_carrier_census(request(carriers=[carrier(state="merged")]))
        self.assertEqual(receipt["disposition"], "REUSE_EXISTING_CARRIER")

    def test_process_closed_unassigned_carrier_is_reusable_after_assignment(self):
        receipt = compile_grantfox_carrier_census(
            request(carriers=[carrier(state="closed", process_disposition="closed_unassigned")])
        )
        self.assertEqual(receipt["disposition"], "REAPPLY_WITH_REUSABLE_CARRIER")
        self.assertIn("PROCESS_CLOSED_REUSABLE_CARRIER_PRESENT", receipt["reason_codes"])

    def test_other_closed_carrier_requires_review(self):
        receipt = compile_grantfox_carrier_census(
            request(carriers=[carrier(state="closed", process_disposition="unknown")])
        )
        self.assertEqual(receipt["disposition"], "REVIEW_CLOSED_CARRIER")

    def test_nonreferencing_pr_does_not_block(self):
        receipt = compile_grantfox_carrier_census(
            request(carriers=[carrier(issue_relation="none")])
        )
        self.assertEqual(receipt["disposition"], "CLEAR_FOR_QUEUE_EVALUATION")
        self.assertEqual(receipt["census"]["observed_pr_count"], 1)
        self.assertEqual(receipt["census"]["relevant_carrier_count"], 0)

    def test_stale_census_holds_even_when_empty(self):
        receipt = compile_grantfox_carrier_census(
            request(evaluated_at="2026-09-19T23:00:01Z", max_snapshot_age_seconds=1800)
        )
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("CARRIER_CENSUS_STALE", receipt["reason_codes"])

    def test_cross_repo_pr_is_rejected(self):
        with self.assertRaisesRegex(GrantFoxCarrierCensusInputError, "canonical issue repository"):
            compile_grantfox_carrier_census(
                request(carriers=[carrier(pr_url="https://github.com/Other/invofi/pull/347")])
            )

    def test_duplicate_pr_is_rejected(self):
        with self.assertRaisesRegex(GrantFoxCarrierCensusInputError, "duplicate"):
            compile_grantfox_carrier_census(request(carriers=[carrier(), carrier()]))

    def test_url_aliases_queries_ports_and_encoded_paths_are_rejected(self):
        hostile = [
            "https://github.com/Stellar-VaultLink/invofi/issues/149?x=1",
            "https://github.com:443/Stellar-VaultLink/invofi/issues/149",
            "https://user@github.com/Stellar-VaultLink/invofi/issues/149",
            "https://github.com/Stellar-VaultLink/invofi/issues/%31%34%39",
        ]
        for url in hostile:
            with self.subTest(url=url):
                with self.assertRaises(GrantFoxCarrierCensusInputError):
                    compile_grantfox_carrier_census(request(canonical_issue_url=url))

    def test_closed_unassigned_requires_closed_state(self):
        with self.assertRaisesRegex(GrantFoxCarrierCensusInputError, "requires state=closed"):
            compile_grantfox_carrier_census(
                request(carriers=[carrier(state="open", process_disposition="closed_unassigned")])
            )

    def test_unknown_request_and_carrier_fields_fail_closed(self):
        with self.assertRaisesRegex(GrantFoxCarrierCensusInputError, "unsupported fields"):
            compile_grantfox_carrier_census(request(extra="nope"))
        bad = carrier()
        bad["surprise"] = True
        with self.assertRaisesRegex(GrantFoxCarrierCensusInputError, "unsupported fields"):
            compile_grantfox_carrier_census(request(carriers=[bad]))

    def test_receipt_is_deterministic_and_tamper_evident(self):
        first = compile_grantfox_carrier_census(request(carriers=[carrier()]))
        second = compile_grantfox_carrier_census(request(carriers=[carrier()]))
        self.assertEqual(first, second)
        self.assertTrue(verify_carrier_census_receipt(first))
        changed = deepcopy(first)
        changed["census"]["carriers"][0]["state"] = "closed"
        self.assertFalse(verify_carrier_census_receipt(changed))

    def test_cli_clear_is_zero_and_suppression_is_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            clear_path = Path(tmp) / "clear.json"
            hold_path = Path(tmp) / "hold.json"
            clear_path.write_text(json.dumps(request()), encoding="utf-8")
            hold_path.write_text(json.dumps(request(carriers=[carrier()])), encoding="utf-8")
            clear = subprocess.run(
                [sys.executable, "-m", "concierge.grantfox_carrier_census", str(clear_path), "--json"],
                text=True,
                capture_output=True,
                check=False,
            )
            hold = subprocess.run(
                [sys.executable, "-m", "concierge.grantfox_carrier_census", str(hold_path), "--json"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(clear.returncode, 0, clear.stderr)
            self.assertEqual(hold.returncode, 2, hold.stderr)
            self.assertTrue(verify_carrier_census_receipt(json.loads(hold.stdout)))


if __name__ == "__main__":
    unittest.main()
