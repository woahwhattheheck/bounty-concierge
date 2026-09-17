# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import hmac
import unittest

from concierge.certified_settlement_collection_queue import build_queue
from concierge.reward_settlement_certifier import REGISTRY_SCHEMA, canonical

KEY = b"collection-queue-truth-chain-key-32b"
GENERATED = "2026-09-16T12:00:00Z"
AS_OF = "2026-09-16T13:00:00Z"


def source(source_id: str, authority: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "source_ref": f"synthetic:{source_id}",
        "source_sha256": hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
        "observed_at": GENERATED,
        "authority": authority,
    }


def event(case_id: str, suffix: str, kind: str, authority: str, **fields):
    return {
        "event_id": f"{case_id}-{suffix}",
        "kind": kind,
        "source": source(f"{case_id}-{suffix}-source", authority),
        **fields,
    }


def document(case_id: str, events: list[dict]) -> dict:
    return {
        "schema": "bounty-concierge/reward-settlement-input/v1",
        "generated_at": GENERATED,
        "cases": [
            {
                "case_id": case_id,
                "work": {
                    "repo": "example/project",
                    "pr": 77,
                    "merge_commit_sha": "a" * 40,
                    "merged_at": "2026-09-16T11:00:00Z",
                    "source": source(f"{case_id}-work-source", "REPOSITORY"),
                },
                "events": events,
            }
        ],
    }


def registry(doc: dict, omit_source_ids=frozenset()) -> dict:
    sources = []
    for case in doc["cases"]:
        candidates = [case["work"]["source"], *[row["source"] for row in case["events"]]]
        sources.extend(row for row in candidates if row["source_id"] not in omit_source_ids)
    body = {"schema": REGISTRY_SCHEMA, "generated_at": GENERATED, "sources": sources}
    return {
        **body,
        "signature_hmac_sha256": hmac.new(KEY, canonical(body), hashlib.sha256).hexdigest(),
    }


def one_row(doc: dict, *, omit_source_ids=frozenset()) -> dict:
    queue = build_queue(
        doc,
        registry(doc, omit_source_ids),
        KEY,
        as_of=AS_OF,
        freshness_seconds=7200,
    )
    assert len(queue["records"]) == 1
    return queue["records"][0]


class CertifiedSettlementCollectionQueueTruthChainTests(unittest.TestCase):
    def test_certified_ticket_without_certified_award_is_not_collection_ready(self):
        case_id = "ticket-no-award"
        doc = document(case_id, [
            event(case_id, "ticket", "PAYOUT_TICKET", "PROVIDER", ticket_id="ticket-77"),
        ])
        row = one_row(doc)
        self.assertEqual(row["certified_settlement_state"], "PAYOUT_TICKET_CERTIFIED")
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertEqual(row["reason_codes"], ["NO_CERTIFIED_SPONSOR_AWARD"])

    def test_certified_rail_without_certified_award_is_not_collection_ready(self):
        case_id = "rail-no-award"
        doc = document(case_id, [
            event(case_id, "ticket", "PAYOUT_TICKET", "PROVIDER", ticket_id="ticket-77"),
            event(case_id, "rail", "PAYOUT_RAIL", "PROVIDER", rail_ref="rail-77"),
        ])
        row = one_row(doc)
        self.assertEqual(row["certified_settlement_state"], "PAYOUT_RAIL_CERTIFIED")
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertEqual(row["reason_codes"], ["NO_CERTIFIED_SPONSOR_AWARD"])

    def test_pending_transfer_without_certified_award_is_not_collection_ready(self):
        case_id = "transfer-no-award"
        doc = document(case_id, [
            event(
                case_id,
                "transfer",
                "TRANSFER",
                "BANK",
                transfer_id="transfer-77",
                status="PENDING",
                direction="INCOMING",
                amount_minor=5000,
                currency="USD",
            ),
        ])
        row = one_row(doc)
        self.assertEqual(row["certified_settlement_state"], "TRANSFER_EVIDENCE_CERTIFIED")
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertEqual(row["reason_codes"], ["NO_CERTIFIED_SPONSOR_AWARD"])

    def test_certified_ineligible_blocks_nonterminal_reward_followup(self):
        case_id = "award-ineligible"
        doc = document(case_id, [
            event(case_id, "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=12000, currency="USD"),
            event(case_id, "eligibility", "ELIGIBILITY", "PROVIDER", decision="INELIGIBLE"),
        ])
        row = one_row(doc)
        self.assertEqual(row["certified_settlement_state"], "SPONSOR_AWARD_CERTIFIED")
        self.assertEqual(row["queue_state"], "HOLD_CONTRADICTION")
        self.assertEqual(row["reason_codes"], ["CERTIFIED_INELIGIBLE"])

    def test_certified_eligible_award_preserves_existing_progression(self):
        case_id = "award-eligible"
        doc = document(case_id, [
            event(case_id, "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=12000, currency="USD"),
            event(case_id, "eligibility", "ELIGIBILITY", "PROVIDER", decision="ELIGIBLE"),
        ])
        row = one_row(doc)
        self.assertEqual(row["queue_state"], "AWARD_FOLLOWUP_CANDIDATE")
        self.assertEqual(row["reason_codes"], ["CERTIFIED_AWARD_WITHOUT_PAYOUT_TICKET"])
        self.assertTrue(row["money"]["certified"])

    def test_untrusted_ineligible_assertion_does_not_override_certified_award(self):
        case_id = "award-untrusted-ineligible"
        ineligible = event(case_id, "eligibility", "ELIGIBILITY", "PROVIDER", decision="INELIGIBLE")
        doc = document(case_id, [
            event(case_id, "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=12000, currency="USD"),
            ineligible,
        ])
        row = one_row(doc, omit_source_ids={ineligible["source"]["source_id"]})
        self.assertEqual(row["queue_state"], "AWARD_FOLLOWUP_CANDIDATE")

    def test_certified_terminal_payment_remains_terminal_even_with_old_ineligible_fact(self):
        case_id = "paid-with-ineligible"
        doc = document(case_id, [
            event(case_id, "eligibility", "ELIGIBILITY", "PROVIDER", decision="INELIGIBLE"),
            event(
                case_id,
                "transfer",
                "TRANSFER",
                "BANK",
                transfer_id="transfer-77",
                status="CONFIRMED",
                direction="INCOMING",
                amount_minor=5000,
                currency="USD",
            ),
        ])
        row = one_row(doc)
        self.assertEqual(row["queue_state"], "SETTLED")
        self.assertEqual(row["reason_codes"], ["CERTIFIED_CONFIRMED_TRANSFER"])

    def test_declared_money_exposes_certification_provenance(self):
        case_id = "award-untrusted-money"
        award = event(case_id, "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=12000, currency="USD")
        doc = document(case_id, [award])
        row = one_row(doc, omit_source_ids={award["source"]["source_id"]})
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertEqual(row["money"]["basis"], "SPONSOR_AWARD")
        self.assertEqual(row["money"]["source_id"], award["source"]["source_id"])
        self.assertFalse(row["money"]["certified"])


if __name__ == "__main__":
    unittest.main()
