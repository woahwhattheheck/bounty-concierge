import json
import unittest
from datetime import datetime, timezone

from concierge.cash_close_board import (
    CashCloseEvidenceError,
    CashCloseInputError,
    POLICY_SCHEMA,
    SCHEMA_VERSION,
    compile_cash_close_board,
    render_markdown,
    strict_json_loads,
)

NOW = datetime(2026, 9, 13, 15, 0, 0, tzinfo=timezone.utc)


def digest(char):
    return char * 64


def money(amount="90", currency="USD"):
    return {"amount": amount, "currency": currency}


def ref(kind, value):
    return {"kind": kind, "ref": value}


def event(event_id, kind, at, source_ref, sha, amount=None):
    return {
        "event_id": event_id,
        "kind": kind,
        "at": at,
        "source_ref": source_ref,
        "sha256": sha,
        "amount": amount,
    }


def claim(key, *, advertised=None, refs=None, events=None, owner="Z-Solstice"):
    return {
        "claim_key": key,
        "owner": owner,
        "advertised": advertised,
        "references": refs or [ref("github_pr", "https://github.com/acme/repo/pull/1")],
        "events": events or [],
    }


def manifest(claims):
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "schema": POLICY_SCHEMA,
            "followup_after_hours": 72,
            "escalate_after_hours": 168,
            "ack_verify_after_hours": 24,
        },
        "claims": claims,
    }


class CashCloseBoardTests(unittest.TestCase):
    def compile(self, claims):
        return compile_cash_close_board(manifest(claims), evaluated_at=NOW)

    def test_merge_is_delivery_not_acceptance_or_settlement(self):
        url = "https://github.com/acme/repo/pull/1"
        row = claim(
            "acme:1",
            advertised=money(),
            refs=[ref("github_pr", url)],
            events=[event("merge-1", "WORK_MERGED", "2026-09-10T12:00:00Z", url, digest("a"))],
        )
        result = self.compile([row])["claims"][0]
        self.assertEqual(result["stage"], "DELIVERED")
        self.assertFalse(result["acceptance_verified"])
        self.assertFalse(result["settled"])
        self.assertEqual(result["next_action"], "REQUEST_ACCEPTANCE_OR_PAYOUT_TERMS")

    def test_payment_request_is_not_cash(self):
        url = "https://github.com/acme/repo/pull/1"
        row = claim(
            "acme:1",
            advertised=money(), refs=[ref("github_pr", url)],
            events=[
                event("merge-1", "WORK_MERGED", "2026-09-01T12:00:00Z", url, digest("a")),
                event("request-1", "PAYMENT_REQUEST_SENT", "2026-09-05T12:00:00Z", url, digest("b"), money()),
            ],
        )
        result = self.compile([row])["claims"][0]
        self.assertEqual(result["stage"], "PAYMENT_REQUESTED")
        self.assertEqual(result["next_action"], "ESCALATE_PAYMENT_FOLLOWUP")
        self.assertIsNone(result["received"])
        self.assertFalse(result["settled"])

    def test_sponsor_paid_ack_requires_payment_rail_verification(self):
        thread = "email:thread-7"
        row = claim(
            "rtc:2819", advertised=money("25", "RTC"),
            refs=[ref("email_thread", thread)],
            events=[event("ack-1", "SPONSOR_PAID_ACK", "2026-09-13T10:00:00Z", thread, digest("c"))],
        )
        result = self.compile([row])["claims"][0]
        self.assertEqual(result["stage"], "PAID_ACKNOWLEDGED")
        self.assertEqual(result["next_action"], "VERIFY_PAYMENT_RAIL")
        self.assertFalse(result["settled"])
        self.assertFalse(result["independent_payment_rail_verified"])

    def test_independent_payment_rail_exactly_settles_known_obligation(self):
        issue = "https://github.com/acme/repo/issues/1"
        rail = "rustchain:tx:abc"
        row = claim(
            "rtc:1", advertised=money("25", "RTC"),
            refs=[ref("github_issue", issue), ref("payment_rail", rail)],
            events=[
                event("accepted", "WORK_ACCEPTED", "2026-09-10T12:00:00Z", issue, digest("d"), money("25", "RTC")),
                event("paid", "PAYMENT_RAIL_RECEIVED", "2026-09-11T12:00:00Z", rail, digest("e"), money("25", "RTC")),
            ],
        )
        result = self.compile([row])
        item = result["claims"][0]
        self.assertEqual(item["stage"], "SETTLED")
        self.assertTrue(item["settled"])
        self.assertEqual(item["next_action"], "NONE")
        self.assertEqual(result["summary"]["settled_by_currency"], {"RTC": "25"})

    def test_partial_payment_never_settles(self):
        rail = "stripe:charge:1"
        row = claim(
            "usd:1", advertised=money("90", "USD"), refs=[ref("payment_rail", rail)],
            events=[event("paid-part", "PAYMENT_RAIL_RECEIVED", "2026-09-12T12:00:00Z", rail, digest("f"), money("40", "USD"))],
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "PARTIALLY_SETTLED")
        self.assertEqual(item["remaining"], money("50", "USD"))
        self.assertFalse(item["settled"])

    def test_unpriced_payment_receipt_is_not_full_settlement(self):
        rail = "bank:receipt:1"
        row = claim(
            "unpriced:1", advertised=None, refs=[ref("payment_rail", rail)],
            events=[event("paid", "PAYMENT_RAIL_RECEIVED", "2026-09-12T12:00:00Z", rail, digest("1"), money("10", "USD"))],
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "PAYMENT_RECEIVED_UNPRICED")
        self.assertEqual(item["next_action"], "VERIFY_OBLIGATION_AMOUNT")
        self.assertFalse(item["settled"])

    def test_payment_link_reference_alone_is_not_cash_or_request(self):
        row = claim(
            "link:1", advertised=money("90"), refs=[ref("payment_rail", "https://pay.example/abc")], events=[]
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "IN_PROGRESS")
        self.assertIsNone(item["received"])
        self.assertFalse(item["settled"])

    def test_accepted_without_request_ranks_for_payment_request(self):
        pr = "https://github.com/acme/repo/pull/2"
        row = claim(
            "accepted:2", advertised=money("250"), refs=[ref("github_pr", pr)],
            events=[event("accepted-2", "WORK_ACCEPTED", "2026-09-13T12:00:00Z", pr, digest("2"), money("250"))],
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "ACCEPTED")
        self.assertEqual(item["next_action"], "REQUEST_PAYMENT")
        self.assertEqual(item["sla_status"], "DUE_NOW")

    def test_failed_payment_request_is_route_broken_not_requested(self):
        email = "email:dead-route"
        row = claim(
            "route:1", advertised=money("10"), refs=[ref("email_thread", email)],
            events=[event("request-failed", "PAYMENT_REQUEST_FAILED", "2026-09-13T12:00:00Z", email, digest("3"), money("10"))],
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "PAYMENT_ROUTE_BROKEN")
        self.assertEqual(item["next_action"], "REPAIR_PAYMENT_ROUTE")
        self.assertIsNone(item["requested"])

    def test_successful_request_after_failed_route_clears_route_broken(self):
        email = "email:reroute"
        row = claim(
            "route:2", advertised=money("10"), refs=[ref("email_thread", email)],
            events=[
                event("fail", "PAYMENT_REQUEST_FAILED", "2026-09-10T12:00:00Z", email, digest("4"), money("10")),
                event("sent", "PAYMENT_REQUEST_SENT", "2026-09-11T12:00:00Z", email, digest("5"), money("10")),
            ],
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "PAYMENT_REQUESTED")

    def test_external_review_gate_does_not_fake_acceptance(self):
        pr = "https://github.com/acme/repo/pull/3"
        row = claim(
            "gate:3", advertised=money("250"), refs=[ref("github_pr", pr)],
            events=[
                event("delivered", "WORK_DELIVERED", "2026-09-12T10:00:00Z", pr, digest("6")),
                event("gate", "EXTERNAL_REVIEW_REQUIRED", "2026-09-12T11:00:00Z", pr, digest("7")),
            ],
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "BLOCKED_EXTERNAL_REVIEW")
        self.assertFalse(item["acceptance_verified"])

    def test_accepted_amount_can_narrow_advertised_obligation(self):
        pr = "https://github.com/acme/repo/pull/4"
        rail = "bank:4"
        row = claim(
            "renegotiated:4", advertised=money("100"), refs=[ref("github_pr", pr), ref("payment_rail", rail)],
            events=[
                event("accept", "WORK_ACCEPTED", "2026-09-10T10:00:00Z", pr, digest("8"), money("80")),
                event("rail", "PAYMENT_RAIL_RECEIVED", "2026-09-11T10:00:00Z", rail, digest("9"), money("80")),
            ],
        )
        item = self.compile([row])["claims"][0]
        self.assertEqual(item["stage"], "SETTLED")
        self.assertEqual(item["accepted"], money("80"))

    def test_request_cannot_exceed_known_obligation(self):
        pr = "https://github.com/acme/repo/pull/5"
        row = claim(
            "overask:5", advertised=money("90"), refs=[ref("github_pr", pr)],
            events=[event("ask", "PAYMENT_REQUEST_SENT", "2026-09-11T10:00:00Z", pr, digest("a"), money("100"))],
        )
        with self.assertRaises(CashCloseEvidenceError):
            self.compile([row])

    def test_payment_receipt_cannot_exceed_known_obligation(self):
        rail = "bank:5"
        row = claim(
            "overpay:5", advertised=money("90"), refs=[ref("payment_rail", rail)],
            events=[event("rail", "PAYMENT_RAIL_RECEIVED", "2026-09-11T10:00:00Z", rail, digest("b"), money("100"))],
        )
        with self.assertRaises(CashCloseEvidenceError):
            self.compile([row])

    def test_conflicting_currency_fails_closed(self):
        pr = "https://github.com/acme/repo/pull/6"
        row = claim(
            "fx:6", advertised=money("90", "USD"), refs=[ref("github_pr", pr)],
            events=[event("ask", "PAYMENT_REQUEST_SENT", "2026-09-11T10:00:00Z", pr, digest("c"), money("90", "RTC"))],
        )
        with self.assertRaises(CashCloseEvidenceError):
            self.compile([row])

    def test_cross_claim_evidence_reuse_fails_closed(self):
        one = claim(
            "one:1", refs=[ref("github_pr", "p1")],
            events=[event("one", "WORK_DELIVERED", "2026-09-11T10:00:00Z", "p1", digest("d"))],
        )
        two = claim(
            "two:2", refs=[ref("github_pr", "p2")],
            events=[event("two", "WORK_DELIVERED", "2026-09-11T10:00:00Z", "p2", digest("d"))],
        )
        with self.assertRaises(CashCloseEvidenceError):
            self.compile([one, two])

    def test_duplicate_claim_key_casefold_fails_closed(self):
        with self.assertRaises(CashCloseEvidenceError):
            self.compile([claim("Case:1"), claim("case:1")])

    def test_future_event_fails_closed(self):
        row = claim(
            "future:1", refs=[ref("github_pr", "p")],
            events=[event("future", "WORK_DELIVERED", "2026-09-14T00:00:00Z", "p", digest("e"))],
        )
        with self.assertRaises(CashCloseEvidenceError):
            self.compile([row])

    def test_source_ref_must_be_in_claim_references(self):
        row = claim(
            "source:1", refs=[ref("github_pr", "p")],
            events=[event("bad-source", "WORK_DELIVERED", "2026-09-11T10:00:00Z", "other", digest("f"))],
        )
        with self.assertRaises(CashCloseEvidenceError):
            self.compile([row])

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(CashCloseInputError):
            strict_json_loads('{"schema_version":1,"schema_version":1}')

    def test_float_json_is_rejected(self):
        with self.assertRaises(CashCloseInputError):
            strict_json_loads('{"x":1.5}')

    def test_amount_event_rules_fail_closed(self):
        p = "p"
        with self.assertRaises(CashCloseInputError):
            self.compile([claim(
                "missing:1", refs=[ref("github_pr", p)],
                events=[event("ask", "PAYMENT_REQUEST_SENT", "2026-09-11T10:00:00Z", p, digest("1"), None)],
            )])
        with self.assertRaises(CashCloseInputError):
            self.compile([claim(
                "forbidden:1", refs=[ref("github_pr", p)],
                events=[event("merge", "WORK_MERGED", "2026-09-11T10:00:00Z", p, digest("2"), money("1"))],
            )])

    def test_board_is_deterministic_and_priority_sorted(self):
        accepted_ref = "a"
        delivered_ref = "d"
        accepted = claim(
            "z-accepted", advertised=money("5"), refs=[ref("github_pr", accepted_ref)],
            events=[event("accept-z", "WORK_ACCEPTED", "2026-09-12T10:00:00Z", accepted_ref, digest("3"), money("5"))],
        )
        delivered = claim(
            "a-delivered", advertised=money("5"), refs=[ref("github_pr", delivered_ref)],
            events=[event("deliver-a", "WORK_DELIVERED", "2026-09-12T10:00:00Z", delivered_ref, digest("4"))],
        )
        first = self.compile([delivered, accepted])
        second = self.compile([accepted, delivered])
        self.assertEqual(first, second)
        self.assertEqual([row["claim_key"] for row in first["claims"]], ["z-accepted", "a-delivered"])
        self.assertEqual(len(first["receipt_sha256"]), 64)

    def test_markdown_states_settlement_law_and_escapes_table_text(self):
        row = claim("safe:1", owner="owner|pipe")
        board = self.compile([row])
        text = render_markdown(board)
        self.assertIn("independent payment-rail evidence", text)
        self.assertIn("owner\\|pipe", text)
        self.assertIn("Authority ceiling", text)

    def test_summary_partitions_currencies_without_fx_conversion(self):
        usd = claim("usd:1", advertised=money("90", "USD"))
        rtc = claim("rtc:1", advertised=money("25", "RTC"), refs=[ref("payment_rail", "rail")], events=[
            event("rtc-paid", "PAYMENT_RAIL_RECEIVED", "2026-09-12T10:00:00Z", "rail", digest("5"), money("25", "RTC"))
        ])
        summary = self.compile([usd, rtc])["summary"]
        self.assertEqual(summary["advertised_unsettled_by_currency"], {"USD": "90"})
        self.assertEqual(summary["settled_by_currency"], {"RTC": "25"})

    def test_authority_ceiling_remains_read_derive_only(self):
        authority = self.compile([])["authority"]
        self.assertFalse(authority["outbound_contact"])
        self.assertFalse(authority["wallet_mutation"])
        self.assertFalse(authority["provider_mutation"])
        self.assertFalse(authority["payment_initiation"])
        self.assertFalse(authority["accounting_revenue_recognition"])
        self.assertTrue(authority["settled_requires_independent_payment_rail"])


if __name__ == "__main__":
    unittest.main()
