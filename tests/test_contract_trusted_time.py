from __future__ import annotations

import unittest
from unittest import mock

from concierge.contract_proposal import (
    BRIEF_VERSION,
    ContractProposalInputError,
    build_external_contract_proposal_current,
    main as proposal_main,
)
from concierge.contract_qualification import (
    ContractQualificationInputError,
    SCHEMA_VERSION,
    qualify_external_contract,
    qualify_external_contract_current,
    main as qualification_main,
    verify_contract_qualification_receipt,
    verify_contract_qualification_receipt_current,
)

QUALIFIED_AT = "2026-09-13T10:30:00Z"
CURRENT_AT = "2026-09-13T11:00:00Z"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def actionable_snapshot() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "listing": {
            "url": "https://market.example/contracts/time-fence",
            "state": "OPEN",
            "observed_at": "2026-09-13T10:20:00Z",
            "closes_at": "2026-09-13T10:45:00Z",
            "source_digest": DIGEST_A,
            "currency": "USD",
            "min_amount": "100",
            "max_amount": "500",
        },
        "required_claims": [
            {"claim_id": "python", "claim_type": "EXPERIENCE", "mandatory": True}
        ],
        "evidence": [
            {
                "evidence_id": "ev-python",
                "claim_id": "python",
                "evidence_type": "REPO_COMMIT",
                "evidence_ref": "https://evidence.example/ev-python",
                "evidence_digest": DIGEST_B,
                "observed_at": "2026-09-13T10:15:00Z",
                "scope": "EXACT",
            }
        ],
        "platform": {
            "submission_route": "MANUAL_OWNER",
            "requirements_complete": True,
            "fees_known": True,
            "account_ready": True,
            "required_balance": "0",
            "available_balance": "0",
            "kyc_status": "SATISFIED",
        },
        "bid": {"currency": "USD", "amount": "250", "delivery_days": 3},
    }


def proposal_brief() -> dict:
    return {
        "schema_version": BRIEF_VERSION,
        "proposal_id": "time-fence-proposal",
        "headline": "Evidence-backed contract delivery",
        "cover_note": "Owner review is required before any external action.",
        "proposal_claims": [
            {
                "claim_id": "python",
                "statement": "Relevant Python delivery evidence is bound to this qualification.",
                "evidence_ids": ["ev-python"],
            }
        ],
        "deliverables": [
            {
                "deliverable_id": "implementation",
                "description": "Implement the qualified scope",
                "acceptance_note": "Qualified scope is satisfied",
            }
        ],
        "milestones": [
            {
                "milestone_id": "m1",
                "title": "Implementation",
                "due_day": 3,
                "deliverable_ids": ["implementation"],
            }
        ],
    }


class ContractTrustedCurrentTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = actionable_snapshot()
        self.receipt = qualify_external_contract(self.snapshot, as_of=QUALIFIED_AT)
        self.assertEqual(self.receipt["disposition"], "ACTIONABLE")

    def test_current_qualification_uses_process_time_and_rejects_expired_listing(self):
        with mock.patch(
            "concierge.contract_qualification.trusted_utc_now", return_value=CURRENT_AT
        ):
            result = qualify_external_contract_current(self.snapshot)
        self.assertEqual(result["disposition"], "REJECT")
        self.assertIn("LISTING_DEADLINE_PASSED", result["reason_codes"])
        self.assertIn("LISTING_OBSERVATION_STALE", result["reason_codes"])

    def test_current_receipt_verification_cannot_resurrect_historical_actionable_state(self):
        with mock.patch(
            "concierge.contract_qualification.trusted_utc_now", return_value=CURRENT_AT
        ):
            with self.assertRaisesRegex(
                ContractQualificationInputError, "no longer current-actionable"
            ):
                verify_contract_qualification_receipt_current(self.snapshot, self.receipt)

    def test_explicit_trusted_verifier_path_remains_deterministic(self):
        verified = verify_contract_qualification_receipt(
            self.snapshot, self.receipt, as_of=QUALIFIED_AT
        )
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["current_disposition"], "ACTIONABLE")

    def test_current_proposal_assembly_refuses_expired_qualification(self):
        with mock.patch("concierge.contract_proposal.trusted_utc_now", return_value=CURRENT_AT):
            with self.assertRaisesRegex(
                ContractProposalInputError, "no longer current-actionable"
            ):
                build_external_contract_proposal_current(
                    self.snapshot, self.receipt, proposal_brief()
                )

    def test_qualification_cli_has_no_caller_as_of_escape_hatch(self):
        with self.assertRaises(SystemExit) as raised:
            qualification_main(["snapshot.json", "--as-of", QUALIFIED_AT])
        self.assertEqual(raised.exception.code, 2)

    def test_proposal_cli_has_no_caller_as_of_escape_hatch(self):
        with self.assertRaises(SystemExit) as raised:
            proposal_main(
                [
                    "snapshot.json",
                    "receipt.json",
                    "brief.json",
                    "--as-of",
                    QUALIFIED_AT,
                ]
            )
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
