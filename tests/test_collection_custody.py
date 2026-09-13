import copy
import unittest

from concierge.collection_request import compile_collection_request
from concierge.collection_custody import (
    CollectionCustodyError,
    empty_ledger,
    evaluate_request,
    record_dispatch,
    record_follow_up,
    record_sponsor_event,
    register_request,
    strict_json_loads,
    supersede_with_payment_request,
    verify_ledger,
)

NOW = "2026-09-13T14:40:00Z"
T0 = "2026-09-13T12:00:00Z"
T1 = "2026-09-13T12:10:00Z"
T2 = "2026-09-13T12:20:00Z"
T3 = "2026-09-13T12:30:00Z"
T4 = "2026-09-13T12:40:00Z"
A, B, C = "a" * 64, "b" * 64, "c" * 64


def payload(kind="NONE", evidence=None, amount="90", currency="USD", route="https://pay.example/a"):
    return {
        "schema": "bounty-collection-request-input/v1",
        "sponsor_name": "Sponsor Team",
        "work": {
            "repo": "acme/widget",
            "pr": 42,
            "canonical_url": "https://github.com/acme/widget/pull/42",
            "head_sha": "1" * 40,
            "state": "MERGED",
            "advertised_amount": amount,
            "currency": currency,
        },
        "payout_route": {"type": "PAYMENT_LINK", "value": route},
        "acceptance": {
            "kind": kind,
            "evidence_ref": None if kind == "NONE" else "https://sponsor.example/evidence/42",
            "evidence_sha256": None if kind == "NONE" else evidence,
        },
    }


def register(packet=None, ledger=None, rid="req-001", eid="evt-reg-001", when=T0, route_key="sponsor-route-001"):
    return register_request(
        empty_ledger() if ledger is None else ledger,
        event_id=eid,
        occurred_at=when,
        request_id=rid,
        packet=compile_collection_request(payload()) if packet is None else packet,
        route_class="email",
        route_key=route_key,
        as_of=NOW,
    )


def dispatch(reg, rid="req-001", eid="evt-send-001", when=T1, receipt="provider-receipt-001"):
    return record_dispatch(
        reg["ledger"],
        event_id=eid,
        occurred_at=when,
        request_id=rid,
        request_digest=reg["request_digest"],
        route_class="email",
        provider_receipt_key=receipt,
        evidence_sha256=A,
        as_of=NOW,
    )


def sponsor(sent, reg, status="accepted", evidence=B, when=T2):
    return record_sponsor_event(
        sent["ledger"],
        event_id="evt-sponsor-001",
        occurred_at=when,
        request_id="req-001",
        request_digest=reg["request_digest"],
        status=status,
        provider_event_key="provider-event-001",
        evidence_sha256=evidence,
        as_of=NOW,
    )


class CollectionCustodyTests(unittest.TestCase):
    def test_packet_route_is_not_send_evidence(self):
        reg = register()
        out = evaluate_request(reg["ledger"], "req-001", as_of=NOW, follow_up_after_seconds=3600)
        self.assertEqual((out["send_disposition"], out["outbound_event_count"]), ("READY_TO_SEND", 0))
        self.assertFalse(out["authority"]["external_send_performed_by_module"])

    def test_dispatch_receipt_advances_state_but_is_not_authenticated(self):
        reg = register(); sent = dispatch(reg)
        out = evaluate_request(sent["ledger"], "req-001", as_of="2026-09-13T12:30:00Z", follow_up_after_seconds=3600)
        self.assertEqual(out["lifecycle_state"], "AWAITING_SPONSOR")
        self.assertTrue(out["dispatch_evidence_recorded"])
        self.assertFalse(out["authority"]["provider_receipt_authenticated_by_module"])

    def test_silence_becomes_follow_up_due_only_after_real_dispatch(self):
        reg = register()
        before = evaluate_request(reg["ledger"], "req-001", as_of=NOW, follow_up_after_seconds=3600)
        self.assertEqual(before["lifecycle_state"], "READY_TO_SEND")
        sent = dispatch(reg)
        after = evaluate_request(sent["ledger"], "req-001", as_of="2026-09-13T13:10:00Z", follow_up_after_seconds=3600)
        self.assertEqual(after["lifecycle_state"], "FOLLOW_UP_DUE")
        self.assertFalse(after["authority"]["acceptance_inferred_from_silence"])

    def test_follow_up_requires_dispatch_and_resets_baseline(self):
        reg = register()
        with self.assertRaises(CollectionCustodyError) as ctx:
            record_follow_up(reg["ledger"], event_id="evt-fu-001", occurred_at=T1, request_id="req-001", request_digest=reg["request_digest"], route_class="email", provider_receipt_key="provider-follow-001", evidence_sha256=B, as_of=NOW)
        self.assertEqual(ctx.exception.code, "FOLLOW_UP_BEFORE_DISPATCH")
        sent = dispatch(reg)
        followed = record_follow_up(sent["ledger"], event_id="evt-fu-001", occurred_at=T2, request_id="req-001", request_digest=reg["request_digest"], route_class="email", provider_receipt_key="provider-follow-001", evidence_sha256=B, as_of=NOW)
        out = evaluate_request(followed["ledger"], "req-001", as_of="2026-09-13T12:30:00Z", follow_up_after_seconds=3600)
        self.assertEqual(out["lifecycle_state"], "AWAITING_SPONSOR")

    def test_duplicate_primary_dispatch_fails(self):
        reg = register(); sent = dispatch(reg)
        with self.assertRaises(CollectionCustodyError) as ctx:
            record_dispatch(sent["ledger"], event_id="evt-send-002", occurred_at=T2, request_id="req-001", request_digest=reg["request_digest"], route_class="email", provider_receipt_key="provider-receipt-002", evidence_sha256=B, as_of=NOW)
        self.assertEqual(ctx.exception.code, "MULTIPLE_PRIMARY_DISPATCH")

    def test_global_provider_reference_reuse_fails(self):
        first = register(); sent = dispatch(first, receipt="provider-shared-001")
        second_packet = compile_collection_request(payload(kind="SPONSOR_ACCEPTED", evidence=B))
        second = register(packet=second_packet, ledger=sent["ledger"], rid="req-002", eid="evt-reg-002", when=T2, route_key="sponsor-route-002")
        with self.assertRaises(CollectionCustodyError) as ctx:
            dispatch(second, rid="req-002", eid="evt-send-002", when=T3, receipt="provider-shared-001")
        self.assertEqual(ctx.exception.code, "EXTERNAL_REFERENCE_REUSE")

    def test_sponsor_event_before_dispatch_fails(self):
        reg = register()
        with self.assertRaises(CollectionCustodyError) as ctx:
            sponsor({"ledger": reg["ledger"]}, reg)
        self.assertEqual(ctx.exception.code, "SPONSOR_EVENT_BEFORE_DISPATCH")

    def test_assessment_cannot_claim_payment_pending(self):
        reg = register(); sent = dispatch(reg)
        with self.assertRaises(CollectionCustodyError) as ctx:
            sponsor(sent, reg, status="payment_pending")
        self.assertEqual(ctx.exception.code, "ASSESSMENT_STATUS_AUTHORITY_VIOLATION")

    def test_assessment_acceptance_requires_new_payment_request(self):
        reg = register(); accepted = sponsor(dispatch(reg), reg)
        out = evaluate_request(accepted["ledger"], "req-001", as_of=NOW, follow_up_after_seconds=3600)
        self.assertEqual(out["lifecycle_state"], "PAYMENT_REQUEST_REQUIRED")
        self.assertFalse(out["authority"]["payment_inferred"])

    def test_acceptance_upgrade_binds_exact_evidence_and_work(self):
        reg = register(); accepted = sponsor(dispatch(reg), reg)
        pay_packet = compile_collection_request(payload(kind="SPONSOR_ACCEPTED", evidence=B))
        pay = register(packet=pay_packet, ledger=accepted["ledger"], rid="req-002", eid="evt-reg-002", when=T3)
        linked = supersede_with_payment_request(pay["ledger"], event_id="evt-link-001", occurred_at=T4, old_request_id="req-001", old_request_digest=reg["request_digest"], new_request_id="req-002", new_request_digest=pay["request_digest"], evidence_sha256=B, as_of=NOW)
        self.assertEqual(evaluate_request(linked["ledger"], "req-001", as_of=NOW, follow_up_after_seconds=3600)["lifecycle_state"], "SUPERSEDED")
        self.assertEqual(evaluate_request(linked["ledger"], "req-002", as_of=NOW, follow_up_after_seconds=3600)["send_disposition"], "READY_TO_SEND")

    def test_wrong_acceptance_evidence_blocks_upgrade(self):
        reg = register(); accepted = sponsor(dispatch(reg), reg)
        pay_packet = compile_collection_request(payload(kind="SPONSOR_ACCEPTED", evidence=C))
        pay = register(packet=pay_packet, ledger=accepted["ledger"], rid="req-002", eid="evt-reg-002", when=T3)
        with self.assertRaises(CollectionCustodyError) as ctx:
            supersede_with_payment_request(pay["ledger"], event_id="evt-link-001", occurred_at=T4, old_request_id="req-001", old_request_digest=reg["request_digest"], new_request_id="req-002", new_request_digest=pay["request_digest"], evidence_sha256=B, as_of=NOW)
        self.assertEqual(ctx.exception.code, "SUPERSESSION_ACCEPTANCE_EVIDENCE_MISMATCH")

    def test_upgrade_cannot_silently_redirect_payout(self):
        reg = register(); accepted = sponsor(dispatch(reg), reg)
        pay_packet = compile_collection_request(payload(kind="SPONSOR_ACCEPTED", evidence=B, route="https://pay.example/other"))
        pay = register(packet=pay_packet, ledger=accepted["ledger"], rid="req-002", eid="evt-reg-002", when=T3)
        with self.assertRaises(CollectionCustodyError) as ctx:
            supersede_with_payment_request(pay["ledger"], event_id="evt-link-001", occurred_at=T4, old_request_id="req-001", old_request_digest=reg["request_digest"], new_request_id="req-002", new_request_digest=pay["request_digest"], evidence_sha256=B, as_of=NOW)
        self.assertEqual(ctx.exception.code, "SUPERSESSION_PAYOUT_ROUTE_MISMATCH")

    def test_parallel_active_generation_cannot_dispatch(self):
        reg = register(); sent = dispatch(reg)
        pay_packet = compile_collection_request(payload(kind="SPONSOR_ACCEPTED", evidence=B))
        second = register(packet=pay_packet, ledger=sent["ledger"], rid="req-002", eid="evt-reg-002", when=T2, route_key="sponsor-route-002")
        self.assertEqual(evaluate_request(second["ledger"], "req-002", as_of=NOW, follow_up_after_seconds=3600)["lifecycle_state"], "LINK_GENERATION_REQUIRED")
        with self.assertRaises(CollectionCustodyError) as ctx:
            dispatch(second, rid="req-002", eid="evt-send-002", when=T3, receipt="provider-receipt-002")
        self.assertEqual(ctx.exception.code, "PARALLEL_ACTIVE_REQUEST")

    def test_direct_payment_packet_can_reach_payment_pending_and_settlement_evidence(self):
        packet = compile_collection_request(payload(kind="SPONSOR_ACCEPTED", evidence=B, amount="25", currency="RTC"))
        reg = register(packet=packet); sent = dispatch(reg)
        pending = sponsor(sent, reg, status="payment_pending")
        self.assertEqual(evaluate_request(pending["ledger"], "req-001", as_of=NOW, follow_up_after_seconds=3600)["lifecycle_state"], "PAYMENT_PENDING")
        reg2 = register(packet=packet, rid="req-010", eid="evt-reg-010", route_key="route-010")
        sent2 = dispatch(reg2, rid="req-010", eid="evt-send-010", receipt="receipt-010")
        paid = record_sponsor_event(sent2["ledger"], event_id="evt-sponsor-010", occurred_at=T2, request_id="req-010", request_digest=reg2["request_digest"], status="paid_evidence_ready", provider_event_key="event-010", evidence_sha256=C, as_of=NOW)
        out = evaluate_request(paid["ledger"], "req-010", as_of=NOW, follow_up_after_seconds=3600)
        self.assertEqual(out["lifecycle_state"], "SETTLEMENT_EVIDENCE_READY")
        self.assertEqual((out["advertised_amount"], out["currency"]), ("25", "RTC"))
        self.assertFalse(out["authority"]["cash_recognized"])

    def test_terminal_decision_blocks_followup(self):
        reg = register(); accepted = sponsor(dispatch(reg), reg)
        with self.assertRaises(CollectionCustodyError) as ctx:
            record_follow_up(accepted["ledger"], event_id="evt-fu-001", occurred_at=T3, request_id="req-001", request_digest=reg["request_digest"], route_class="email", provider_receipt_key="provider-follow-001", evidence_sha256=C, as_of=NOW)
        self.assertEqual(ctx.exception.code, "FOLLOW_UP_AFTER_SPONSOR_DECISION")

    def test_packet_tamper_rejected(self):
        packet = compile_collection_request(payload()); packet["body"] = "legally due now"
        with self.assertRaises(CollectionCustodyError) as ctx:
            register(packet=packet)
        self.assertEqual(ctx.exception.code, "PACKET_VERIFICATION_FAILED")

    def test_ledger_tamper_rejected(self):
        reg = register(); ledger = copy.deepcopy(reg["ledger"]); ledger["events"][0]["currency"] = "RTC"
        with self.assertRaises(CollectionCustodyError) as ctx:
            verify_ledger(ledger, as_of=NOW)
        self.assertEqual(ctx.exception.code, "LEDGER_DIGEST_MISMATCH")

    def test_rehashed_invalid_pr_still_fails_semantics(self):
        import concierge.collection_custody as cc
        reg = register(); ledger = copy.deepcopy(reg["ledger"]); event = ledger["events"][0]
        event["pr_url"] = "https://evil.example/not-github"
        fields = ("packet_sha256", "pr_url", "head_sha", "disposition", "advertised_amount", "currency", "acceptance_kind", "acceptance_evidence_sha256", "payout_route_sha256", "route_class", "route_key")
        event["request_digest"] = cc._digest({key: event[key] for key in fields})
        ledger["ledger_sha256"] = cc._ledger_digest(ledger["events"])
        with self.assertRaises(CollectionCustodyError) as ctx:
            verify_ledger(ledger, as_of=NOW)
        self.assertEqual(ctx.exception.code, "PR_URL_INVALID")

    def test_future_and_inverted_time_rejected(self):
        with self.assertRaises(CollectionCustodyError) as future:
            register(when="2026-09-14T00:00:00Z")
        self.assertEqual(future.exception.code, "FUTURE_EVENT")
        reg = register(when=T1); sent = dispatch(reg, when=T2)
        with self.assertRaises(CollectionCustodyError) as inverted:
            sponsor(sent, reg, when=T1)
        self.assertEqual(inverted.exception.code, "TIMESTAMP_INVERSION")

    def test_route_and_opaque_provider_fences(self):
        reg = register()
        with self.assertRaises(CollectionCustodyError) as route:
            record_dispatch(reg["ledger"], event_id="evt-send-001", occurred_at=T1, request_id="req-001", request_digest=reg["request_digest"], route_class="github_comment", provider_receipt_key="provider-receipt-001", evidence_sha256=A, as_of=NOW)
        self.assertEqual(route.exception.code, "ROUTE_CLASS_MISMATCH")
        with self.assertRaises(CollectionCustodyError) as opaque:
            dispatch(reg, receipt="person@example.com")
        self.assertEqual(opaque.exception.code, "ID_NOT_OPAQUE")

    def test_strict_json_rejects_duplicate_keys_and_nan(self):
        with self.assertRaises(CollectionCustodyError) as dup:
            strict_json_loads('{"a":1,"a":2}')
        self.assertEqual(dup.exception.code, "DUPLICATE_JSON_KEY")
        with self.assertRaises(CollectionCustodyError) as nan:
            strict_json_loads('{"a":NaN}')
        self.assertEqual(nan.exception.code, "NONFINITE_JSON_CONSTANT")

    def test_verify_ledger_and_receipt_are_deterministic(self):
        reg = register(); sent = dispatch(reg)
        verified = verify_ledger(sent["ledger"], as_of=NOW)
        self.assertTrue(verified["valid"])
        self.assertEqual((verified["event_count"], verified["request_count"]), (2, 1))
        first = evaluate_request(sent["ledger"], "req-001", as_of=NOW, follow_up_after_seconds=3600)
        second = evaluate_request(sent["ledger"], "req-001", as_of=NOW, follow_up_after_seconds=3600)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
