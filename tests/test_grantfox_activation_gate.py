from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from concierge.grantfox_activation_gate import (
    AUTHORITY,
    GrantFoxActivationInputError,
    compile_activation,
    verify_activation_receipt,
)


def request(queue_disposition="APPLY_ELIGIBLE", source_disposition="SOURCE_ALIGNED",
            continuity_state="DISCOVERED", continuity_disposition="CONTINUE"):
    queue = {
        "schema": "grantfox-queue-gate/v1",
        "disposition": queue_disposition,
        "receipt_sha256": "a" * 64,
        "identity": {
            "owner": "QuickLendX",
            "repo": "quicklendx-frontend",
            "issue_number": 16,
            "actor_login": "woahwhattheheck",
        },
    }
    source = {
        "schema": "grantfox-source-readiness-receipt/v1",
        "source_disposition": source_disposition,
        "source_receipt_sha256": "b" * 64,
        "identity": {"owner": "quicklendx", "repo": "quicklendx-frontend", "issue_number": 16},
        "queue_receipt": deepcopy(queue),
    }
    continuity = {
        "schema": "grantfox-application-continuity/v1",
        "disposition": continuity_disposition,
        "state": continuity_state,
        "receipt_sha256": "c" * 64,
        "identity": {
            "owner": "QUICKLENDX",
            "repo": "quicklendx-frontend",
            "issue_number": 16,
            "actor_login": "WOAHWHATTHEHECK",
        },
        "queue_anchor": {"receipt_sha256": queue["receipt_sha256"]},
    }
    return {
        "schema": "grantfox-activation-gate/v1",
        "queue_receipt": queue,
        "source_receipt": source,
        "continuity_receipt": continuity,
    }


class ActivationGateTests(unittest.TestCase):
    def compile(self, payload):
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            return compile_activation(payload)

    def test_discovered_aligned_can_apply(self):
        receipt = self.compile(request())
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertTrue(verify_activation_receipt(receipt))
        self.assertEqual(receipt["authority"], AUTHORITY)

    def test_source_drift_requires_replan_before_apply(self):
        receipt = self.compile(request(source_disposition="SOURCE_DRIFT_REPLAN"))
        self.assertEqual(receipt["disposition"], "REPLAN_BEFORE_APPLY")

    def test_applied_waits_even_if_point_snapshot_still_says_apply(self):
        receipt = self.compile(request(continuity_state="APPLIED"))
        self.assertEqual(receipt["disposition"], "WAIT_ASSIGNMENT")

    def test_only_assigned_aligned_implementation_snapshot_activates(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            source_disposition="SOURCE_ALIGNED",
            continuity_state="ASSIGNED",
        ))
        self.assertEqual(receipt["disposition"], "IMPLEMENT_ASSIGNED_SCOPE")

    def test_assigned_but_source_drift_refuses_implementation(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            source_disposition="SOURCE_DRIFT_REPLAN",
            continuity_state="ASSIGNED",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_SOURCE")
        self.assertIn("ASSIGNED_SCOPE_SOURCE_NOT_ALIGNED", receipt["reason_codes"])

    def test_assignment_and_queue_contradiction_holds(self):
        receipt = self.compile(request(continuity_state="ASSIGNED"))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("ASSIGNMENT_QUEUE_CONTRADICTION", receipt["reason_codes"])

    def test_submitted_never_regresses_to_implementation(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            continuity_state="SUBMITTED",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("LIFECYCLE_ALREADY_PAST_IMPLEMENTATION", receipt["reason_codes"])

    def test_paid_never_regresses_to_implementation(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            continuity_state="PAID",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")

    def test_continuity_reconcile_dominates(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            continuity_state="ASSIGNED",
            continuity_disposition="HOLD_RECONCILE",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("CONTINUITY_RECONCILE_REQUIRED", receipt["reason_codes"])

    def test_source_hold_dominates(self):
        receipt = self.compile(request(source_disposition="HOLD"))
        self.assertEqual(receipt["disposition"], "HOLD_SOURCE")

    def test_actor_swap_fails_closed(self):
        payload = request()
        payload["continuity_receipt"]["identity"]["actor_login"] = "someone-else"
        with self.assertRaisesRegex(GrantFoxActivationInputError, "actors differ"):
            self.compile(payload)

    def test_issue_swap_fails_closed(self):
        payload = request()
        payload["source_receipt"]["identity"]["issue_number"] = 99
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different issues"):
            self.compile(payload)

    def test_source_queue_digest_swap_fails_closed(self):
        payload = request()
        payload["source_receipt"]["queue_receipt"]["receipt_sha256"] = "d" * 64
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different queue receipt"):
            self.compile(payload)

    def test_continuity_queue_digest_swap_fails_closed(self):
        payload = request()
        payload["continuity_receipt"]["queue_anchor"]["receipt_sha256"] = "d" * 64
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different queue receipt"):
            self.compile(payload)

    def test_unverified_input_is_rejected(self):
        payload = request()
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=False),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            with self.assertRaisesRegex(GrantFoxActivationInputError, "queue_receipt does not verify"):
                compile_activation(payload)

    def test_receipt_digest_detects_tamper(self):
        receipt = self.compile(request())
        receipt["disposition"] = "IMPLEMENT_ASSIGNED_SCOPE"
        self.assertFalse(verify_activation_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
