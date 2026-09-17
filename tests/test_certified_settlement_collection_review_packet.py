from __future__ import annotations
import copy
import unittest

from concierge.certified_settlement_collection_review_packet import (
    FALSE_AUTHORITY, PACKET_SCHEMA, QUEUE_SCHEMA, ROUTES_SCHEMA,
    ReviewPacketError, _sha, build_review_packet, canonical, compile_bytes, loads_strict,
)


def queue_fixture(state="AWARD_FOLLOWUP_CANDIDATE"):
    body = {
        "schema": QUEUE_SCHEMA,
        "input_generated_at": "2026-09-17T00:30:00Z",
        "trusted_registry_generated_at": "2026-09-17T00:30:00Z",
        "as_of": "2026-09-17T01:00:00Z",
        "freshness_seconds": 3600,
        "certificate_receipt_sha256": "a" * 64,
        "trusted_registry_body_sha256": "b" * 64,
        "records": [{
            "case_id": "case-1", "work": {"repo": "x/y", "pr": 1}, "queue_state": state,
            "reason_codes": ["TEST"], "money": {"basis": "SPONSOR_AWARD", "amount_minor": 100, "currency": "USD", "unit": "minor"},
            "event_age_seconds": 30,
        }],
        "aggregates": {"record_count": 1, "queue_state_counts": {}},
        "authority": copy.deepcopy(FALSE_AUTHORITY),
    }
    return {**body, "receipt_sha256": _sha(body)}


def routes(*rows):
    return {"schema": ROUTES_SCHEMA, "as_of": "2026-09-17T01:00:00Z", "routes": list(rows)}


def route(disposition="AVAILABLE", route_id="r1"):
    return {
        "case_id": "case-1", "route_id": route_id, "kind": "EMAIL", "route_ref": "opaque-route",
        "disposition": disposition, "source_ref": "evidence://route", "source_sha256": "c" * 64,
        "observed_at": "2026-09-17T00:55:00Z",
    }


class ReviewPacketTests(unittest.TestCase):
    def test_available_route_yields_reasoned_action_without_authority(self):
        packet = build_review_packet(queue_fixture(), routes(route()))
        self.assertEqual(packet["schema"], PACKET_SCHEMA)
        row = packet["records"][0]
        self.assertEqual(row["route_status"], "AVAILABLE")
        self.assertEqual(row["next_action_code"], "REVIEW_AWARD_STATUS")
        self.assertFalse(row["outbound_authorized"])
        self.assertEqual(packet["authority"], FALSE_AUTHORITY)

    def test_dnr_blocks_followup(self):
        row = build_review_packet(queue_fixture(), routes(route("DNR")))["records"][0]
        self.assertEqual(row["next_action_code"], "HOLD_DNR")
        self.assertIn("do not send", row["next_action"])

    def test_available_plus_dnr_holds(self):
        packet = build_review_packet(queue_fixture(), routes(route("AVAILABLE", "r1"), route("DNR", "r2")))
        self.assertEqual(packet["records"][0]["route_status"], "HOLD_CONFLICT")
        self.assertEqual(packet["records"][0]["next_action_code"], "HOLD")

    def test_missing_route_is_find_route_for_followup(self):
        row = build_review_packet(queue_fixture(), routes())["records"][0]
        self.assertEqual(row["route_status"], "NONE")
        self.assertEqual(row["next_action_code"], "FIND_ROUTE")

    def test_terminal_states_never_become_followup(self):
        for state in ("SETTLED", "CLOSED_NO_REWARD"):
            row = build_review_packet(queue_fixture(state), routes(route()))["records"][0]
            self.assertEqual(row["next_action_code"], "NO_FOLLOWUP")

    def test_needs_evidence_stays_evidence_refresh(self):
        row = build_review_packet(queue_fixture("NEEDS_TRUST_EVIDENCE"), routes(route()))["records"][0]
        self.assertEqual(row["next_action_code"], "REFRESH_EVIDENCE")

    def test_hold_stays_hold(self):
        row = build_review_packet(queue_fixture("HOLD_CONTRADICTION"), routes(route()))["records"][0]
        self.assertEqual(row["next_action_code"], "HOLD")

    def test_ticket_rail_transfer_actions(self):
        expected = {
            "PAYOUT_TICKET_FOLLOWUP_CANDIDATE": "REVIEW_PAYOUT_TICKET",
            "PAYOUT_RAIL_FOLLOWUP_CANDIDATE": "REVIEW_PAYOUT_RAIL",
            "TRANSFER_PENDING_FOLLOWUP_CANDIDATE": "REVIEW_TRANSFER_STATUS",
        }
        for state, action in expected.items():
            row = build_review_packet(queue_fixture(state), routes(route()))["records"][0]
            self.assertEqual(row["next_action_code"], action)

    def test_queue_tamper_rejected(self):
        q = queue_fixture(); q["records"][0]["queue_state"] = "SETTLED"
        with self.assertRaises(ReviewPacketError):
            build_review_packet(q, routes())

    def test_authority_amplification_rejected(self):
        q = queue_fixture(); q["authority"]["send_outbound"] = True
        body = {k: v for k, v in q.items() if k != "receipt_sha256"}; q["receipt_sha256"] = _sha(body)
        with self.assertRaises(ReviewPacketError):
            build_review_packet(q, routes())

    def test_unknown_case_route_rejected(self):
        r = route(); r["case_id"] = "missing"
        with self.assertRaises(ReviewPacketError):
            build_review_packet(queue_fixture(), routes(r))

    def test_future_route_rejected(self):
        r = route(); r["observed_at"] = "2026-09-17T02:00:00Z"
        with self.assertRaises(ReviewPacketError):
            build_review_packet(queue_fixture(), routes(r))

    def test_duplicate_route_rejected(self):
        r = route()
        with self.assertRaises(ReviewPacketError):
            build_review_packet(queue_fixture(), routes(r, copy.deepcopy(r)))

    def test_as_of_mismatch_rejected(self):
        rd = routes(); rd["as_of"] = "2026-09-17T01:01:00Z"
        with self.assertRaises(ReviewPacketError):
            build_review_packet(queue_fixture(), rd)

    def test_compile_bytes_deterministic(self):
        q = queue_fixture(); rd = routes(route())
        a = compile_bytes(canonical(q), canonical(rd)); b = compile_bytes(canonical(q), canonical(rd))
        self.assertEqual(a, b)
        self.assertTrue(a.endswith(b"\n"))

    def test_strict_duplicate_json_rejected(self):
        with self.assertRaises(ReviewPacketError):
            loads_strict(b'{"x":1,"x":2}')

    def test_packet_receipt_binds_routes(self):
        q = queue_fixture(); a = build_review_packet(q, routes(route("AVAILABLE")))
        b = build_review_packet(q, routes(route("DNR")))
        self.assertNotEqual(a["receipt_sha256"], b["receipt_sha256"])


if __name__ == "__main__": unittest.main()
