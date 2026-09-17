import hashlib
import hmac
import json
import unittest

from concierge import reward_settlement_certifier as certifier
from concierge.certified_settlement_collections_queue import (
    CONTACT_SCHEMA,
    CollectionsQueueError,
    CollectionsQueueVerificationError,
    canonical,
    compile_bytes,
    compile_queue,
    loads_strict,
    verify_bytes,
)

KEY = b"unit-test-settlement-trust-key-32bytes"
GEN = "2026-09-16T12:00:00Z"
AS_OF = "2026-09-16T13:00:00Z"


def src(sid, authority, observed=GEN):
    return {
        "source_id": sid,
        "source_ref": f"ref:{sid}",
        "source_sha256": hashlib.sha256(sid.encode()).hexdigest(),
        "observed_at": observed,
        "authority": authority,
    }


def event(case, suffix, kind, authority, **extra):
    return {
        "event_id": f"{case}-{suffix}",
        "kind": kind,
        "source": src(f"{case}-{suffix}-src", authority),
        **extra,
    }


def case(case_id, events):
    return {
        "case_id": case_id,
        "work": {
            "repo": "woahwhattheheck/example",
            "pr": int(case_id.split("-")[-1]),
            "merge_commit_sha": hashlib.sha1(case_id.encode()).hexdigest(),
            "merged_at": "2026-09-16T11:00:00Z",
            "source": src(f"{case_id}-work-src", "REPOSITORY"),
        },
        "events": events,
    }


def document(cases):
    return {
        "schema": "bounty-concierge/reward-settlement-input/v1",
        "generated_at": GEN,
        "cases": cases,
    }


def registry_for(doc, omit_source_ids=()):
    omit = set(omit_source_ids)
    sources = []
    seen = set()
    for c in doc["cases"]:
        for source in [c["work"]["source"]] + [e["source"] for e in c["events"]]:
            if source["source_id"] in omit or source["source_id"] in seen:
                continue
            sources.append(source)
            seen.add(source["source_id"])
    body = {
        "schema": "bounty-concierge/reward-settlement-trusted-sources/v1",
        "generated_at": GEN,
        "sources": sources,
    }
    return {
        **body,
        "signature_hmac_sha256": hmac.new(KEY, certifier.canonical(body), hashlib.sha256).hexdigest(),
    }


def build(doc, omit_source_ids=()):
    registry = registry_for(doc, omit_source_ids)
    certificate = certifier.certify(doc, registry, KEY)
    return registry, certificate


def compile_one(c, omit_source_ids=(), *, as_of=AS_OF, max_age=7200, routes=None):
    doc = document([c])
    reg, cert = build(doc, omit_source_ids)
    queue = compile_queue(
        doc,
        reg,
        cert,
        key=KEY,
        as_of=as_of,
        max_evidence_age_seconds=max_age,
        contact_routes=routes,
    )
    return queue["records"][0], doc, reg, cert


class CollectionsQueueTests(unittest.TestCase):
    def test_advertised_routes_to_award_followup(self):
        c = case("case-1", [event("case-1", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=1000, currency="USD")])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "AWARD_FOLLOWUP_CANDIDATE")
        self.assertFalse(row["commercial_facts"]["conversion_performed"])

    def test_award_routes_to_ticket_followup(self):
        c = case("case-2", [event("case-2", "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=2500, currency="USD")])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "PAYOUT_TICKET_FOLLOWUP_CANDIDATE")
        self.assertTrue(row["commercial_facts"]["sponsor_award_certified"])

    def test_ticket_routes_to_rail_followup(self):
        c = case("case-3", [
            event("case-3", "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=2500, currency="USD"),
            event("case-3", "ticket", "PAYOUT_TICKET", "PROVIDER", ticket_id="ticket-3"),
        ])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "PAYOUT_RAIL_FOLLOWUP_CANDIDATE")

    def test_rail_routes_to_transfer_followup(self):
        c = case("case-4", [
            event("case-4", "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=2500, currency="USD"),
            event("case-4", "ticket", "PAYOUT_TICKET", "PROVIDER", ticket_id="ticket-4"),
            event("case-4", "rail", "PAYOUT_RAIL", "PROVIDER", rail_ref="rail-4"),
        ])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "TRANSFER_PENDING_FOLLOWUP_CANDIDATE")

    def test_pending_transfer_stays_transfer_followup(self):
        c = case("case-5", [
            event("case-5", "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=2500, currency="USD"),
            event("case-5", "transfer", "TRANSFER", "BANK", transfer_id="tx-5", status="PENDING", direction="INCOMING", amount_minor=2500, currency="USD"),
        ])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "TRANSFER_PENDING_FOLLOWUP_CANDIDATE")

    def test_nonterminal_payout_without_certified_award_needs_trust(self):
        ticket = event("case-21", "ticket", "PAYOUT_TICKET", "PROVIDER", ticket_id="ticket-21")
        c = case("case-21", [ticket])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")

    def test_confirmed_transfer_is_settled_not_revenue(self):
        c = case("case-6", [event("case-6", "transfer", "TRANSFER", "BANK", transfer_id="tx-6", status="CONFIRMED", direction="INCOMING", amount_minor=9000, currency="USD")])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "SETTLED")
        self.assertFalse(row["authority"]["recognize_accounting_revenue"])
        self.assertEqual(row["commercial_facts"]["certified_paid_by_currency"], {"USD": 9000})

    def test_certified_closure_is_terminal(self):
        c = case("case-7", [event("case-7", "close", "CLOSURE", "SPONSOR", reason="NO_REWARD")])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "CLOSED_NO_REWARD")

    def test_untrusted_stronger_event_needs_trust_evidence(self):
        ad = event("case-8", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=1000, currency="USD")
        award = event("case-8", "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=1000, currency="USD")
        c = case("case-8", [ad, award])
        row, *_ = compile_one(c, omit_source_ids={award["source"]["source_id"]})
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertFalse(row["commercial_facts"]["sponsor_award_certified"])

    def test_merge_only_needs_money_road_evidence(self):
        row, *_ = compile_one(case("case-9", []))
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")

    def test_certified_ineligible_blocks_followup(self):
        c = case("case-10", [
            event("case-10", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=5000, currency="USD"),
            event("case-10", "elig", "ELIGIBILITY", "PROVIDER", decision="INELIGIBLE"),
        ])
        row, *_ = compile_one(c)
        self.assertEqual(row["queue_state"], "HOLD_CONTRADICTION")

    def test_stale_nonterminal_needs_refresh_but_terminal_paid_stays_settled(self):
        ad = case("case-11", [event("case-11", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=1000, currency="USD")])
        row, *_ = compile_one(ad, as_of="2026-09-16T18:00:00Z", max_age=3600)
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        paid = case("case-12", [event("case-12", "tx", "TRANSFER", "BANK", transfer_id="tx-12", status="CONFIRMED", direction="INCOMING", amount_minor=10, currency="USD")])
        row2, *_ = compile_one(paid, as_of="2026-09-16T18:00:00Z", max_age=3600)
        self.assertEqual(row2["queue_state"], "SETTLED")
        self.assertFalse(row2["evidence"]["source_current"])

    def test_noncash_unit_is_preserved_without_conversion(self):
        c = case("case-13", [event("case-13", "award", "SPONSOR_AWARD", "SPONSOR", amount_minor=3, currency="RTC")])
        row, *_ = compile_one(c)
        self.assertEqual(row["commercial_facts"]["sponsor_award"]["currency"], "RTC")
        self.assertNotIn("USD", row["commercial_facts"]["certified_paid_by_currency"])
        self.assertFalse(row["commercial_facts"]["conversion_performed"])

    def test_contact_routes_are_evidence_only(self):
        c = case("case-14", [event("case-14", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=1000, currency="USD")])
        routes = {
            "schema": CONTACT_SCHEMA,
            "generated_at": GEN,
            "routes": [{
                "case_id": "case-14", "route_id": "route-14", "channel": "EMAIL",
                "target_ref": "sponsor@example.test", "source_ref": "ref:route-14",
                "source_sha256": "a" * 64, "observed_at": GEN,
                "valid_until": "2026-09-17T12:00:00Z",
            }],
        }
        row, *_ = compile_one(c, routes=routes)
        self.assertEqual(row["contact_routes"]["state"], "CURRENT")
        self.assertFalse(row["contact_routes"]["route_presence_grants_send_authority"])
        self.assertFalse(row["authority"]["send_outbound"])

    def test_expired_contact_route_is_stale(self):
        c = case("case-15", [event("case-15", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=1000, currency="USD")])
        routes = {
            "schema": CONTACT_SCHEMA, "generated_at": GEN,
            "routes": [{
                "case_id": "case-15", "route_id": "route-15", "channel": "FORM",
                "target_ref": "form:15", "source_ref": "ref:route-15",
                "source_sha256": "b" * 64, "observed_at": GEN,
                "valid_until": "2026-09-16T12:30:00Z",
            }],
        }
        row, *_ = compile_one(c, routes=routes)
        self.assertEqual(row["contact_routes"]["state"], "STALE")
        self.assertEqual(row["contact_routes"]["current_route_ids"], [])

    def test_unknown_contact_case_fails_closed(self):
        c = case("case-16", [])
        routes = {"schema": CONTACT_SCHEMA, "generated_at": GEN, "routes": [{
            "case_id": "other-99", "route_id": "route-x", "channel": "OTHER",
            "target_ref": "target:x", "source_ref": "ref:x", "source_sha256": "c" * 64,
            "observed_at": GEN, "valid_until": None,
        }]}
        with self.assertRaises(CollectionsQueueError):
            compile_one(c, routes=routes)

    def test_tampered_certificate_is_rejected(self):
        c = case("case-17", [event("case-17", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=1000, currency="USD")])
        doc = document([c]); reg, cert = build(doc)
        cert = json.loads(json.dumps(cert))
        cert["records"][0]["certified_settlement_state"] = "PAID_CERTIFIED"
        with self.assertRaises(CollectionsQueueError):
            compile_queue(doc, reg, cert, key=KEY, as_of=AS_OF, max_evidence_age_seconds=7200)

    def test_compile_verify_is_byte_exact_and_tamper_detecting(self):
        c = case("case-18", [event("case-18", "ad", "ADVERTISED_BOUNTY", "OFFICIAL_OFFER", amount_minor=1000, currency="USD")])
        doc = document([c]); reg, cert = build(doc)
        settlement_raw = canonical(doc); registry_raw = canonical(reg); certificate_raw = canonical(cert)
        artifacts = compile_bytes(settlement_raw, registry_raw, certificate_raw, key=KEY, as_of=AS_OF, max_evidence_age_seconds=7200)
        verify_bytes(settlement_raw, registry_raw, certificate_raw, *artifacts, key=KEY, as_of=AS_OF, max_evidence_age_seconds=7200)
        with self.assertRaises(CollectionsQueueVerificationError):
            verify_bytes(settlement_raw, registry_raw, certificate_raw, artifacts[0] + b" ", artifacts[1], artifacts[2], key=KEY, as_of=AS_OF, max_evidence_age_seconds=7200)

    def test_duplicate_json_and_bool_policy_fail_closed(self):
        with self.assertRaises(CollectionsQueueError):
            loads_strict(b'{"x":1,"x":2}')
        c = case("case-19", []); doc = document([c]); reg, cert = build(doc)
        with self.assertRaises(CollectionsQueueError):
            compile_queue(doc, reg, cert, key=KEY, as_of=AS_OF, max_evidence_age_seconds=True)

    def test_as_of_before_generations_fails_closed(self):
        c = case("case-20", []); doc = document([c]); reg, cert = build(doc)
        with self.assertRaises(CollectionsQueueError):
            compile_queue(doc, reg, cert, key=KEY, as_of="2026-09-16T11:59:59Z", max_evidence_age_seconds=7200)


if __name__ == "__main__":
    unittest.main()
