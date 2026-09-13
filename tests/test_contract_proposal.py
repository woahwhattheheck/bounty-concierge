import copy
import hashlib
import json
import unittest
from unittest import mock

from concierge.contract_proposal import (
    BRIEF_VERSION,
    ContractProposalInputError,
    build_external_contract_proposal,
    verify_external_contract_proposal,
)
from concierge.contract_qualification import ContractQualificationInputError


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture(route="MANUAL_OWNER"):
    snapshot = {
        "schema_version": "bounty-concierge.external-contract-qualification/v1",
        "listing": {},
        "required_claims": [
            {"claim_id": "python", "claim_type": "EXPERIENCE", "mandatory": True},
            {"claim_id": "api", "claim_type": "TOOL", "mandatory": True},
            {"claim_id": "availability", "claim_type": "AVAILABILITY", "mandatory": False},
        ],
        "evidence": [],
        "platform": {},
        "bid": {},
    }
    receipt = {
        "disposition": "ACTIONABLE",
        "bid_ready": True,
        "submitted": False,
        "canonical_source_url": "https://example.test/contracts/42",
        "source_digest": digest("source"),
        "qualification_digest": digest("qualification"),
        "native_currency": "USD",
        "bid": {"currency": "USD", "amount": "2400", "delivery_days": 14},
        "submission_route": route,
        "evidence_bindings": [
            {"claim_id": "python", "evidence_id": "ev-python", "evidence_type": "MERGED_PR", "evidence_digest": digest("python"), "scope": "EXACT"},
            {"claim_id": "api", "evidence_id": "ev-api", "evidence_type": "REPO_COMMIT", "evidence_digest": digest("api"), "scope": "EXACT"},
            {"claim_id": "availability", "evidence_id": "ev-adjacent", "evidence_type": "CURRENT_AVAILABILITY", "evidence_digest": digest("adjacent"), "scope": "ADJACENT"},
        ],
    }
    brief = {
        "schema_version": BRIEF_VERSION,
        "proposal_id": "proposal-42",
        "headline": "Ship the requested API automation with evidence-backed delivery.",
        "cover_note": "I can deliver the scoped automation in the qualified 14-day window.",
        "proposal_claims": [
            {"claim_id": "python", "statement": "Relevant Python delivery experience is evidenced below.", "evidence_ids": ["ev-python"]},
            {"claim_id": "api", "statement": "Relevant API tooling is evidenced below.", "evidence_ids": ["ev-api"]},
        ],
        "deliverables": [
            {"deliverable_id": "implementation", "description": "Production implementation", "acceptance_note": "Meets the agreed functional scope"},
            {"deliverable_id": "tests", "description": "Regression coverage", "acceptance_note": "Focused tests pass"},
            {"deliverable_id": "handoff", "description": "Operator handoff notes", "acceptance_note": "Runbook is complete"},
        ],
        "milestones": [
            {"milestone_id": "m1", "title": "Implementation and tests", "due_day": 10, "deliverable_ids": ["tests", "implementation"]},
            {"milestone_id": "m2", "title": "Handoff", "due_day": 14, "deliverable_ids": ["handoff"]},
        ],
    }
    return snapshot, receipt, brief


class ContractProposalTests(unittest.TestCase):
    def verification(self, receipt, disposition="ACTIONABLE"):
        return {
            "valid": True,
            "qualification_digest": receipt["qualification_digest"],
            "current_disposition": disposition,
            "verified_as_of": "2026-09-13T13:00:00Z",
        }

    def build(self, snapshot, receipt, brief):
        with mock.patch(
            "concierge.contract_proposal.verify_contract_qualification_receipt",
            return_value=self.verification(receipt),
        ):
            return build_external_contract_proposal(
                snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z"
            )

    def test_builds_owner_review_packet_without_external_authority(self):
        snapshot, receipt, brief = fixture()
        result = self.build(snapshot, receipt, brief)
        self.assertEqual(result["status"], "READY_FOR_OWNER_REVIEW")
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["submitted"])
        self.assertEqual(result["bid"], receipt["bid"])
        self.assertEqual(result["submission_route"], "MANUAL_OWNER")
        self.assertTrue(all(value is False for value in result["authority"].values()))
        self.assertTrue(verify_external_contract_proposal(result)["valid"])

    def test_connected_route_still_requires_owner_review(self):
        snapshot, receipt, brief = fixture("CONNECTED_ACTION")
        result = self.build(snapshot, receipt, brief)
        self.assertEqual(result["submission_route"], "CONNECTED_ACTION")
        self.assertEqual(result["status"], "READY_FOR_OWNER_REVIEW")
        self.assertTrue(result["human_review_required"])

    def test_deterministic_under_claim_and_deliverable_reordering(self):
        snapshot, receipt, brief = fixture()
        first = self.build(snapshot, receipt, brief)
        other = copy.deepcopy(brief)
        other["proposal_claims"].reverse()
        other["deliverables"].reverse()
        second = self.build(snapshot, receipt, other)
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])
        self.assertEqual(first, second)

    def test_missing_mandatory_claim_is_rejected(self):
        snapshot, receipt, brief = fixture()
        brief["proposal_claims"] = brief["proposal_claims"][:1]
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "omits mandatory"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_unknown_evidence_is_rejected(self):
        snapshot, receipt, brief = fixture()
        brief["proposal_claims"][0]["evidence_ids"] = ["not-qualified"]
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "absent from qualification"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_cross_claim_evidence_is_rejected(self):
        snapshot, receipt, brief = fixture()
        brief["proposal_claims"][0]["evidence_ids"] = ["ev-api"]
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "different claim_id"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_adjacent_evidence_cannot_support_proposal_claim(self):
        snapshot, receipt, brief = fixture()
        brief["proposal_claims"].append({"claim_id": "availability", "statement": "Available", "evidence_ids": ["ev-adjacent"]})
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "only EXACT"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_duplicate_proposal_claim_id_is_rejected(self):
        snapshot, receipt, brief = fixture()
        brief["proposal_claims"].append(copy.deepcopy(brief["proposal_claims"][0]))
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "exactly once"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_unassigned_deliverable_is_rejected(self):
        snapshot, receipt, brief = fixture()
        brief["milestones"][0]["deliverable_ids"] = ["implementation"]
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "every deliverable"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_duplicate_deliverable_assignment_is_rejected(self):
        snapshot, receipt, brief = fixture()
        brief["milestones"][1]["deliverable_ids"] = ["handoff", "tests"]
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "only one milestone"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_milestone_cannot_exceed_qualified_delivery_window(self):
        snapshot, receipt, brief = fixture()
        brief["milestones"][1]["due_day"] = 15
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "bounded positive integer"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_milestone_days_must_increase(self):
        snapshot, receipt, brief = fixture()
        brief["milestones"][1]["due_day"] = 9
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "strictly increasing"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_nonactionable_qualification_is_rejected(self):
        snapshot, receipt, brief = fixture()
        receipt["disposition"] = "HOLD"
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt, "HOLD")):
            with self.assertRaisesRegex(ContractProposalInputError, "not ACTIONABLE"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_previously_submitted_qualification_is_rejected(self):
        snapshot, receipt, brief = fixture()
        receipt["submitted"] = True
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "submitted=false"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_verifier_failure_is_fail_closed(self):
        snapshot, receipt, brief = fixture()
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", side_effect=ContractQualificationInputError("stale")):
            with self.assertRaisesRegex(ContractProposalInputError, "qualification verification failed"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_current_hold_is_fail_closed(self):
        snapshot, receipt, brief = fixture()
        verification = self.verification(receipt, "HOLD")
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=verification):
            with self.assertRaisesRegex(ContractProposalInputError, "current-actionable"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_verifier_digest_mismatch_is_fail_closed(self):
        snapshot, receipt, brief = fixture()
        verification = self.verification(receipt)
        verification["qualification_digest"] = digest("other")
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=verification):
            with self.assertRaisesRegex(ContractProposalInputError, "digest mismatch"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_bid_currency_mismatch_is_rejected(self):
        snapshot, receipt, brief = fixture()
        receipt["native_currency"] = "EUR"
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "currency"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_unknown_brief_field_is_rejected(self):
        snapshot, receipt, brief = fixture()
        brief["secret_rate"] = "9999"
        with mock.patch("concierge.contract_proposal.verify_contract_qualification_receipt", return_value=self.verification(receipt)):
            with self.assertRaisesRegex(ContractProposalInputError, "shape mismatch"):
                build_external_contract_proposal(snapshot, receipt, brief, as_of="2026-09-13T13:00:00Z")

    def test_packet_tampering_is_detected(self):
        snapshot, receipt, brief = fixture()
        result = self.build(snapshot, receipt, brief)
        result["bid"]["amount"] = "9999"
        with self.assertRaisesRegex(ContractProposalInputError, "digest mismatch"):
            verify_external_contract_proposal(result)

    def test_packet_authority_escalation_is_rejected_even_if_rehashed(self):
        snapshot, receipt, brief = fixture()
        result = self.build(snapshot, receipt, brief)
        result["authority"]["marketplace_submission"] = True
        core = {k: v for k, v in result.items() if k != "packet_sha256"}
        result["packet_sha256"] = digest(json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        with self.assertRaisesRegex(ContractProposalInputError, "exceeds assembly authority"):
            verify_external_contract_proposal(result)

    def test_packet_self_integrity_does_not_claim_external_authority(self):
        snapshot, receipt, brief = fixture()
        result = self.build(snapshot, receipt, brief)
        verified = verify_external_contract_proposal(result)
        self.assertTrue(verified["valid"])
        self.assertFalse(verified["submitted"])
        self.assertNotIn("work_awarded", verified)
        self.assertNotIn("payment_received", verified)


if __name__ == "__main__":
    unittest.main()
