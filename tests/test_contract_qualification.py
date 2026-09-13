from __future__ import annotations

import copy
import unittest

from concierge.contract_qualification import (
    ContractQualificationInputError,
    SCHEMA_VERSION,
    qualify_external_contract,
    verify_contract_qualification_receipt,
)

NOW = "2026-09-13T10:30:00Z"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


def snapshot() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "listing": {
            "url": "https://market.example/projects/40694775",
            "state": "OPEN",
            "observed_at": "2026-09-13T10:20:00Z",
            "closes_at": "2026-09-14T10:30:00Z",
            "source_digest": DIGEST_A,
            "currency": "USD",
            "min_amount": "150",
            "max_amount": "400",
        },
        "required_claims": [
            {"claim_id": "n8n-exp", "claim_type": "EXPERIENCE", "mandatory": True},
            {"claim_id": "graph-exp", "claim_type": "TOOL", "mandatory": True},
            {"claim_id": "llm-http", "claim_type": "EXPERIENCE", "mandatory": True},
            {"claim_id": "handoff", "claim_type": "AVAILABILITY", "mandatory": True},
            {"claim_id": "portfolio", "claim_type": "PORTFOLIO", "mandatory": False},
        ],
        "evidence": [],
        "platform": {
            "submission_route": "MANUAL_OWNER",
            "requirements_complete": True,
            "fees_known": True,
            "account_ready": True,
            "required_balance": "20",
            "available_balance": "20",
            "kyc_status": "SATISFIED",
        },
        "bid": {"currency": "USD", "amount": "250", "delivery_days": 3},
    }


def evidence(
    evidence_id: str,
    claim_id: str,
    evidence_type: str,
    digest: str,
    *,
    scope: str = "EXACT",
    observed_at: str = "2026-09-13T10:15:00Z",
) -> dict:
    return {
        "evidence_id": evidence_id,
        "claim_id": claim_id,
        "evidence_type": evidence_type,
        "evidence_ref": f"https://evidence.example/{evidence_id}",
        "evidence_digest": digest,
        "observed_at": observed_at,
        "scope": scope,
    }


def make_actionable(value: dict) -> dict:
    value = copy.deepcopy(value)
    value["evidence"] = [
        evidence("e1", "n8n-exp", "REPO_COMMIT", DIGEST_B),
        evidence("e2", "graph-exp", "MERGED_PR", DIGEST_C),
        evidence("e3", "llm-http", "PROVIDER_RECEIPT", DIGEST_D),
        evidence("e4", "handoff", "CURRENT_AVAILABILITY", DIGEST_E),
    ]
    return value


class ExternalContractQualificationTests(unittest.TestCase):
    def test_missing_mandatory_screening_claims_hold(self):
        result = qualify_external_contract(snapshot(), as_of=NOW)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["bid_ready"])
        self.assertFalse(result["submitted"])
        self.assertEqual(
            result["unproven_claim_ids"],
            ["graph-exp", "handoff", "llm-http", "n8n-exp"],
        )
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_adjacent_generic_evidence_cannot_satisfy_specific_experience(self):
        value = snapshot()
        value["evidence"] = [
            evidence("generic-api", "graph-exp", "REPO_COMMIT", DIGEST_B, scope="ADJACENT")
        ]
        result = qualify_external_contract(value, as_of=NOW)
        self.assertIn("ADJACENT_EVIDENCE:graph-exp", result["reason_codes"])
        self.assertIn("graph-exp", result["unproven_claim_ids"])

    def test_all_exact_claims_ready_for_owner_submission_but_never_submitted(self):
        result = qualify_external_contract(make_actionable(snapshot()), as_of=NOW)
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertEqual(result["status"], "READY_FOR_OWNER_SUBMISSION")
        self.assertTrue(result["bid_ready"])
        self.assertTrue(result["owner_action_required"])
        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason_codes"], [])
        self.assertEqual(result["native_currency"], "USD")
        self.assertEqual(result["bid"]["amount"], "250")

    def test_connected_route_still_does_not_claim_submission(self):
        value = make_actionable(snapshot())
        value["platform"]["submission_route"] = "CONNECTED_ACTION"
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["status"], "READY_FOR_CONNECTED_SUBMISSION")
        self.assertFalse(result["owner_action_required"])
        self.assertFalse(result["submitted"])
        self.assertFalse(result["authority"]["marketplace_submission"])

    def test_closed_listing_rejects(self):
        value = make_actionable(snapshot())
        value["listing"]["state"] = "CLOSED"
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "REJECT")
        self.assertIn("LISTING_CLOSED", result["reason_codes"])

    def test_deadline_passed_rejects_even_if_state_says_open(self):
        value = make_actionable(snapshot())
        value["listing"]["closes_at"] = "2026-09-13T10:29:59Z"
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "REJECT")
        self.assertIn("LISTING_DEADLINE_PASSED", result["reason_codes"])

    def test_unknown_or_stale_listing_holds(self):
        value = make_actionable(snapshot())
        value["listing"]["state"] = "UNKNOWN"
        value["listing"]["observed_at"] = "2026-09-13T09:00:00Z"
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("LISTING_STATE_UNKNOWN", result["reason_codes"])
        self.assertIn("LISTING_OBSERVATION_STALE", result["reason_codes"])

    def test_platform_money_and_kyc_unknown_hold(self):
        value = make_actionable(snapshot())
        value["platform"].update({
            "requirements_complete": False,
            "fees_known": False,
            "account_ready": False,
            "required_balance": "20",
            "available_balance": "0",
            "kyc_status": "UNKNOWN",
        })
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "HOLD")
        for code in (
            "PLATFORM_REQUIREMENTS_INCOMPLETE",
            "PLATFORM_FEES_UNKNOWN",
            "PLATFORM_ACCOUNT_NOT_READY",
            "PLATFORM_BALANCE_INSUFFICIENT",
            "PLATFORM_KYC_UNKNOWN",
        ):
            self.assertIn(code, result["reason_codes"])

    def test_bid_must_use_native_currency_and_actual_normalized_range(self):
        value = make_actionable(snapshot())
        value["bid"]["currency"] = "AUD"
        value["bid"]["amount"] = "750"
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("BID_CURRENCY_MISMATCH", result["reason_codes"])
        self.assertIn("BID_AMOUNT_OUTSIDE_LISTING_RANGE", result["reason_codes"])

    def test_non_usd_budget_is_preserved_without_conversion(self):
        value = make_actionable(snapshot())
        value["listing"].update({"currency": "AUD", "min_amount": "250", "max_amount": "1500"})
        value["bid"].update({"currency": "AUD", "amount": "900"})
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["native_currency"], "AUD")
        self.assertEqual(result["bid"]["currency"], "AUD")
        self.assertEqual(result["bid"]["amount"], "900")
        self.assertEqual(result["disposition"], "ACTIONABLE")

    def test_stale_availability_evidence_holds_only_availability_claim(self):
        value = make_actionable(snapshot())
        for row in value["evidence"]:
            if row["claim_id"] == "handoff":
                row["observed_at"] = "2026-09-12T09:00:00Z"
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertEqual(result["unproven_claim_ids"], ["handoff"])
        self.assertIn("AVAILABILITY_EVIDENCE_STALE:handoff", result["reason_codes"])

    def test_wrong_evidence_type_does_not_satisfy_portfolio_claim(self):
        value = make_actionable(snapshot())
        for claim in value["required_claims"]:
            if claim["claim_id"] == "portfolio":
                claim["mandatory"] = True
        value["evidence"].append(evidence("portfolio-attest", "portfolio", "OWNER_ATTESTATION", DIGEST_A))
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("EVIDENCE_TYPE_MISMATCH:portfolio", result["reason_codes"])
        self.assertIn("portfolio", result["unproven_claim_ids"])

    def test_optional_unproven_claim_does_not_block(self):
        result = qualify_external_contract(make_actionable(snapshot()), as_of=NOW)
        self.assertNotIn("portfolio", result["unproven_claim_ids"])
        self.assertEqual(result["disposition"], "ACTIONABLE")

    def test_duplicate_evidence_id_is_invalid_input(self):
        value = make_actionable(snapshot())
        value["evidence"].append(copy.deepcopy(value["evidence"][0]))
        with self.assertRaisesRegex(ContractQualificationInputError, "duplicate evidence_id"):
            qualify_external_contract(value, as_of=NOW)

    def test_evidence_claim_mismatch_is_invalid_input(self):
        value = make_actionable(snapshot())
        value["evidence"][0]["claim_id"] = "does-not-exist"
        with self.assertRaisesRegex(ContractQualificationInputError, "unknown claim_id"):
            qualify_external_contract(value, as_of=NOW)

    def test_future_evidence_is_invalid_input(self):
        value = make_actionable(snapshot())
        value["evidence"][0]["observed_at"] = "2026-09-13T10:31:00Z"
        with self.assertRaisesRegex(ContractQualificationInputError, "from the future"):
            qualify_external_contract(value, as_of=NOW)

    def test_receipt_is_content_addressed_and_source_change_invalidates_it(self):
        value = make_actionable(snapshot())
        first = qualify_external_contract(value, as_of=NOW)
        second = qualify_external_contract(copy.deepcopy(value), as_of=NOW)
        self.assertEqual(first["qualification_digest"], second["qualification_digest"])
        self.assertTrue(verify_contract_qualification_receipt(value, first, as_of=NOW)["valid"])
        changed = copy.deepcopy(value)
        changed["listing"]["source_digest"] = DIGEST_B
        with self.assertRaisesRegex(ContractQualificationInputError, "receipt mismatch"):
            verify_contract_qualification_receipt(changed, first, as_of=NOW)

    def test_claim_or_evidence_change_invalidates_receipt(self):
        value = make_actionable(snapshot())
        receipt = qualify_external_contract(value, as_of=NOW)
        changed = copy.deepcopy(value)
        changed["evidence"][0]["evidence_digest"] = DIGEST_A
        with self.assertRaisesRegex(ContractQualificationInputError, "receipt mismatch"):
            verify_contract_qualification_receipt(changed, receipt, as_of=NOW)

    def test_log_safe_receipt_omits_evidence_refs_and_listing_prompt_text(self):
        value = make_actionable(snapshot())
        value["evidence"][0]["evidence_ref"] = "PRIVATE-PORTFOLIO-REFERENCE"
        result = qualify_external_contract(value, as_of=NOW)
        self.assertNotIn("PRIVATE-PORTFOLIO-REFERENCE", repr(result))
        self.assertFalse(any("prompt" in key for key in result))

    def test_optional_bad_evidence_does_not_poison_actionable_decision(self):
        value = make_actionable(snapshot())
        value["evidence"].append(evidence(
            "optional-adjacent", "portfolio", "OWNER_ATTESTATION", DIGEST_A, scope="ADJACENT"
        ))
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertNotIn("ADJACENT_EVIDENCE:portfolio", result["reason_codes"])

    def test_exact_evidence_wins_when_same_claim_also_has_adjacent_evidence(self):
        value = make_actionable(snapshot())
        value["evidence"].append(evidence(
            "graph-generic", "graph-exp", "REPO_COMMIT", DIGEST_A, scope="ADJACENT"
        ))
        result = qualify_external_contract(value, as_of=NOW)
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertNotIn("ADJACENT_EVIDENCE:graph-exp", result["reason_codes"])

    def test_actionable_receipt_cannot_be_replayed_after_listing_becomes_stale(self):
        value = make_actionable(snapshot())
        receipt = qualify_external_contract(value, as_of=NOW)
        with self.assertRaisesRegex(ContractQualificationInputError, "no longer current-actionable"):
            verify_contract_qualification_receipt(value, receipt, as_of="2026-09-13T11:00:01Z")

    def test_verification_time_cannot_precede_receipt(self):
        value = make_actionable(snapshot())
        receipt = qualify_external_contract(value, as_of=NOW)
        with self.assertRaisesRegex(ContractQualificationInputError, "precedes qualification"):
            verify_contract_qualification_receipt(value, receipt, as_of="2026-09-13T10:29:59Z")

    def test_uppercase_digest_is_rejected_as_noncanonical(self):
        value = make_actionable(snapshot())
        value["listing"]["source_digest"] = "A" * 64
        with self.assertRaisesRegex(ContractQualificationInputError, "lowercase sha256"):
            qualify_external_contract(value, as_of=NOW)

    def test_log_injection_identifiers_are_rejected(self):
        value = make_actionable(snapshot())
        value["required_claims"][0]["claim_id"] = "n8n-exp\nINJECTED"
        with self.assertRaisesRegex(ContractQualificationInputError, "control characters|log-safe identifier"):
            qualify_external_contract(value, as_of=NOW)

    def test_pathological_decimal_exponent_is_rejected_before_formatting(self):
        value = make_actionable(snapshot())
        value["bid"]["amount"] = "1e999999"
        with self.assertRaisesRegex(ContractQualificationInputError, "magnitude"):
            qualify_external_contract(value, as_of=NOW)

    def test_claim_and_evidence_collection_bounds_fail_closed(self):
        value = snapshot()
        value["required_claims"] = [
            {"claim_id": f"claim-{index}", "claim_type": "OTHER", "mandatory": False}
            for index in range(101)
        ]
        with self.assertRaisesRegex(ContractQualificationInputError, "required_claims exceeds"):
            qualify_external_contract(value, as_of=NOW)

        value = snapshot()
        value["evidence"] = [
            evidence(f"e-{index}", "portfolio", "PORTFOLIO_ARTIFACT", DIGEST_A)
            for index in range(501)
        ]
        with self.assertRaisesRegex(ContractQualificationInputError, "evidence exceeds"):
            qualify_external_contract(value, as_of=NOW)

    def test_malformed_timestamp_and_money_fail_closed(self):
        value = make_actionable(snapshot())
        value["listing"]["observed_at"] = "2026-09-13 10:20:00"
        with self.assertRaises(ContractQualificationInputError):
            qualify_external_contract(value, as_of=NOW)
        value = make_actionable(snapshot())
        value["bid"]["amount"] = 250.0
        with self.assertRaisesRegex(ContractQualificationInputError, "exact decimal"):
            qualify_external_contract(value, as_of=NOW)


if __name__ == "__main__":
    unittest.main()
