import unittest
from unittest.mock import patch

from concierge.grantfox_activation_gate import (
    GrantFoxActivationInputError,
    compile_activation,
)
from tests.test_grantfox_activation_gate import request


class GrantFoxActivationFulfillmentTests(unittest.TestCase):
    def compile_fixture(self, payload):
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_dependency_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_dependency_fulfillment_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            return compile_activation(payload)

    def test_missing_landing_evidence_waits(self):
        receipt = self.compile_fixture(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            fulfillment_disposition="FULFILLMENT_WAIT",
            continuity_state="ASSIGNED",
        ))
        self.assertEqual(receipt["disposition"], "WAIT_DEPENDENCIES")
        self.assertIn(
            "PREREQUISITE_LANDING_EVIDENCE_MISSING", receipt["reason_codes"]
        )

    def test_fulfillment_hold_blocks_implementation(self):
        receipt = self.compile_fixture(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            fulfillment_disposition="HOLD",
            continuity_state="ASSIGNED",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_DEPENDENCIES")
        self.assertIn("DEPENDENCY_FULFILLMENT_HOLD", receipt["reason_codes"])

    def test_fulfillment_must_embed_exact_dependency_receipt(self):
        payload = request()
        payload["fulfillment_receipt"]["dependency_receipt"][
            "dependency_receipt_sha256"
        ] = "f" * 64
        with self.assertRaisesRegex(
            GrantFoxActivationInputError, "different dependency receipt"
        ):
            self.compile_fixture(payload)

    def test_fulfillment_issue_identity_must_match(self):
        payload = request()
        payload["fulfillment_receipt"]["identity"]["issue_number"] = 99
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different issues"):
            self.compile_fixture(payload)

    def test_nonempty_dependencies_require_fulfillment_receipt(self):
        payload = request()
        payload.pop("fulfillment_receipt")
        payload["dependency_receipt"]["evidence"] = {
            "dependencies": [{"issue_number": 7}]
        }
        with self.assertRaisesRegex(
            GrantFoxActivationInputError,
            "fulfillment_receipt is required when prerequisites are declared",
        ):
            self.compile_fixture(payload)

    def test_failed_fulfillment_verification_is_rejected(self):
        payload = request()
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_dependency_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_dependency_fulfillment_receipt", return_value=False),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            with self.assertRaisesRegex(
                GrantFoxActivationInputError, "fulfillment_receipt does not verify"
            ):
                compile_activation(payload)


if __name__ == "__main__":
    unittest.main()
