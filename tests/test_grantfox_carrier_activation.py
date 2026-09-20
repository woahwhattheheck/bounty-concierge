from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch
import unittest

from concierge.grantfox_carrier_activation import (
    GrantFoxCarrierActivationInputError,
    compile_carrier_activation,
    verify_carrier_activation_receipt,
)
from concierge.grantfox_carrier_census import compile_grantfox_carrier_census


UTC = timezone.utc
BASE_NOW = datetime(2026, 9, 20, 1, 45, 0, tzinfo=UTC)


def activation(**overrides):
    payload = {
        "schema": "grantfox-deadline-activation-receipt/v1",
        "disposition": "APPLY_ELIGIBLE",
        "advisory_next_action": "APPLY_THROUGH_PROVIDER_ROUTE",
        "reason_codes": [],
        "identity": {
            "owner": "stellar-vaultlink",
            "repo": "invofi",
            "issue_number": 149,
            "actor_login": "woahwhattheheck",
        },
        "receipt_sha256": "a" * 64,
    }
    payload.update(overrides)
    return payload


def census_request(**overrides):
    payload = {
        "schema": "grantfox-carrier-census/v1",
        "canonical_issue_url": "https://github.com/Stellar-VaultLink/invofi/issues/149",
        "carriers": [],
        "observed_at": "2026-09-20T01:44:00Z",
        "evaluated_at": "2026-09-20T01:44:30Z",
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


def request(carrier_receipt, activation_receipt=None):
    return {
        "schema": "grantfox-carrier-activation/v1",
        "deadline_activation_receipt": activation_receipt or activation(),
        "carrier_census_receipt": carrier_receipt,
    }


class GrantFoxCarrierActivationTests(unittest.TestCase):
    def compile(self, carrier_receipt, *, activation_receipt=None, now=BASE_NOW):
        with patch(
            "concierge.grantfox_carrier_activation.verify_deadline_activation_receipt",
            return_value=True,
        ):
            return compile_carrier_activation(
                request(carrier_receipt, activation_receipt),
                now=now,
            )

    def test_clear_census_preserves_actionable_activation(self):
        receipt = self.compile(compile_grantfox_carrier_census(census_request()))
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertEqual(receipt["carrier"]["current_disposition"], "CLEAR_FOR_QUEUE_EVALUATION")
        self.assertEqual(receipt["reason_codes"], [])

    def test_open_or_merged_carrier_suppresses_fresh_dispatch(self):
        for state in ("open", "merged"):
            with self.subTest(state=state):
                census = compile_grantfox_carrier_census(
                    census_request(carriers=[carrier(state=state)])
                )
                receipt = self.compile(census)
                self.assertEqual(receipt["disposition"], "HOLD_EXISTING_CARRIER")
                self.assertIn("ACTIVE_OR_MERGED_CARRIER_PRESENT", receipt["reason_codes"])
                self.assertEqual(
                    receipt["advisory_next_action"],
                    "REVIEW_EXISTING_CARRIER_BEFORE_NEW_WORK",
                )

    def test_process_closed_carrier_routes_to_reuse_after_assignment(self):
        census = compile_grantfox_carrier_census(
            census_request(
                carriers=[
                    carrier(
                        state="closed",
                        process_disposition="closed_unassigned",
                    )
                ]
            )
        )
        receipt = self.compile(census)
        self.assertEqual(receipt["disposition"], "HOLD_REUSABLE_CARRIER")
        self.assertIn(
            "PROCESS_CLOSED_REUSABLE_CARRIER_PRESENT", receipt["reason_codes"]
        )

    def test_other_closed_carrier_routes_to_review(self):
        census = compile_grantfox_carrier_census(
            census_request(
                carriers=[carrier(state="closed", process_disposition="unknown")]
            )
        )
        receipt = self.compile(census)
        self.assertEqual(receipt["disposition"], "HOLD_CLOSED_CARRIER_REVIEW")
        self.assertIn("CLOSED_CARRIER_REVIEW_REQUIRED", receipt["reason_codes"])

    def test_carrier_census_is_re_evaluated_at_composition_time(self):
        census = compile_grantfox_carrier_census(
            census_request(
                observed_at="2026-09-20T01:43:00Z",
                evaluated_at="2026-09-20T01:43:30Z",
                max_snapshot_age_seconds=60,
            )
        )
        receipt = self.compile(census, now=BASE_NOW)
        self.assertEqual(receipt["disposition"], "HOLD_CARRIER_CENSUS")
        self.assertEqual(receipt["carrier"]["current_disposition"], "HOLD")
        self.assertIn("CARRIER_CENSUS_STALE", receipt["reason_codes"])

    def test_mismatched_issue_identity_fails_closed(self):
        census = compile_grantfox_carrier_census(
            census_request(
                canonical_issue_url="https://github.com/Stellar-VaultLink/invofi/issues/150"
            )
        )
        with self.assertRaisesRegex(
            GrantFoxCarrierActivationInputError, "different issues"
        ):
            self.compile(census)

    def test_upstream_hold_stays_hold_even_with_clear_carrier_census(self):
        census = compile_grantfox_carrier_census(census_request())
        held = activation(
            disposition="HOLD_DEADLINE",
            advisory_next_action="REFRESH_DEADLINE_EVIDENCE",
        )
        receipt = self.compile(census, activation_receipt=held)
        self.assertEqual(receipt["disposition"], "HOLD_UPSTREAM")
        self.assertIn(
            "GRANTFOX_DEADLINE_ACTIVATION_NOT_ACTIONABLE", receipt["reason_codes"]
        )

    def test_receipt_is_tamper_evident(self):
        census = compile_grantfox_carrier_census(census_request())
        receipt = self.compile(census)
        changed = deepcopy(receipt)
        changed["carrier"]["active_or_merged_count"] = 99
        self.assertFalse(verify_carrier_activation_receipt(changed, now=BASE_NOW))

    def test_verifier_rejects_actionable_receipt_after_census_ages_out(self):
        census = compile_grantfox_carrier_census(
            census_request(
                observed_at="2026-09-20T01:44:00Z",
                evaluated_at="2026-09-20T01:44:10Z",
                max_snapshot_age_seconds=90,
            )
        )
        composed_at = datetime(2026, 9, 20, 1, 44, 30, tzinfo=UTC)
        receipt = self.compile(census, now=composed_at)
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        later = datetime(2026, 9, 20, 1, 45, 31, tzinfo=UTC)
        with patch(
            "concierge.grantfox_carrier_activation.verify_deadline_activation_receipt",
            return_value=True,
        ):
            self.assertFalse(
                verify_carrier_activation_receipt(receipt, now=later)
            )


if __name__ == "__main__":
    unittest.main()
