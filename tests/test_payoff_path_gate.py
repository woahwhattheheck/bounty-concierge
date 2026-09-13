from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy

from concierge.payoff_path_gate_core import (
    PACKET_SCHEMA, PayoffPathError, compile_gate, verify_gate, _digest,
    load_strict_json, main,
)

AS_OF = "2026-09-13T15:00:00.000Z"
LATER = "2026-09-13T16:00:00.000Z"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def path_for():
    return {
        "mechanism": "BOUNTY",
        "value": {"kind": "FIXED", "currency": "USD", "amount_minor": 9000},
        "source": {"canonical_url": "https://example.com/opportunity/42", "evidence_ref": "source:terms-v3", "evidence_sha256": SHA_A, "observed_at_utc": "2026-09-13T14:00:00.000Z", "max_age_days": 7},
        "conversion": {"event": "SUBMIT_WORK", "due_at_utc": "2026-09-20T12:00:00.000Z", "evidence_ref": "conversion:deadline-v1", "evidence_sha256": SHA_B},
    }


def policy(gen=1, cap=60, predecessor=None, supersedes=None, observed="2026-09-13T14:00:00.000Z"):
    return {
        "policy_id": f"owner-cap-{gen}", "generation": gen, "cap_minutes": cap,
        "observed_at_utc": observed, "evidence_ref": f"owner:cap-{gen}",
        "evidence_sha256": SHA_C if gen == 1 else SHA_D,
        "predecessor_policy_sha256": predecessor,
        "supersedes_receipt_sha256": supersedes,
    }


def event(event_id="effort-1", minutes=30, observed="2026-09-13T14:30:00.000Z", work_id="work-1", opportunity_id="opp-42"):
    return {
        "event_id": event_id, "work_id": work_id, "opportunity_id": opportunity_id,
        "minutes": minutes, "observed_at_utc": observed,
        "evidence_ref": f"effort:{event_id}", "evidence_sha256": SHA_A,
    }


def item(pol=None, events=None, work_id="work-1", opportunity_id="opp-42"):
    return {
        "work_id": work_id, "opportunity_id": opportunity_id,
        "started_at_utc": "2026-09-13T13:00:00.000Z",
        "budget_policy": pol or policy(), "effort_events": list(events or [event(work_id=work_id, opportunity_id=opportunity_id)]),
        "payoff_path": path_for(),
    }


def doc(row=None, predecessor=None):
    return {"schema": "payoff-path-work/v2", "predecessor_receipt": predecessor, "work_items": [row or item()]}


class ContinuityTests(unittest.TestCase):
    def test_spend_is_derived_from_events_not_snapshot(self):
        packet, markdown, receipt = compile_gate(doc(), AS_OF)
        row = packet["results"][0]
        self.assertEqual(30, row["free_work_spent_minutes"])
        self.assertEqual(30, row["free_work_remaining_minutes"])
        self.assertEqual(1, row["effort_event_count"])
        self.assertTrue(verify_gate(doc(), packet, markdown, receipt, AS_OF))

    def test_legacy_snapshot_schema_fails_closed(self):
        legacy = {"schema": "payoff-path-work/v1", "work_items": []}
        with self.assertRaisesRegex(PayoffPathError, "legacy caller-authored spend snapshots"):
            compile_gate(legacy, AS_OF)

    def test_exact_replay_is_idempotent_and_event_order_invariant(self):
        row = item(events=[event("b", 10), event("a", 20)])
        first = compile_gate(doc(row), AS_OF)
        row2 = item(events=[event("a", 20), event("b", 10)])
        second = compile_gate(doc(row2), AS_OF)
        self.assertEqual(first, second)
        self.assertEqual(first, compile_gate(doc(row), AS_OF))

    def test_event_omission_and_mutation_fail_against_prior_receipt(self):
        _, _, prior = compile_gate(doc(), AS_OF)
        omitted = item(events=[])
        omitted["effort_events"] = []
        with self.assertRaisesRegex(PayoffPathError, "omitted prior effort event"):
            compile_gate(doc(omitted, prior), LATER)
        changed = item(events=[event("effort-1", 29, observed="2026-09-13T14:30:00.000Z")])
        with self.assertRaisesRegex(PayoffPathError, "mutated prior effort event"):
            compile_gate(doc(changed, prior), LATER)

    def test_same_generation_cap_increase_and_tighten_fail(self):
        _, _, prior = compile_gate(doc(), AS_OF)
        for cap in (90, 20):
            changed = item(pol=policy(cap=cap))
            with self.subTest(cap=cap), self.assertRaisesRegex(PayoffPathError, "same-generation"):
                compile_gate(doc(changed, prior), LATER)

    def test_explicit_successor_cap_can_reopen_prior_stop_visibly(self):
        stopped_doc = doc(item(pol=policy(cap=30), events=[event(minutes=30)]))
        stopped_packet, _, prior = compile_gate(stopped_doc, AS_OF)
        self.assertEqual("STOP_UNPAID_WORK", stopped_packet["results"][0]["state"])
        prior_policy_sha = stopped_packet["results"][0]["budget_policy_sha256"]
        new_policy = policy(2, 90, prior_policy_sha, _digest(prior), observed="2026-09-13T15:30:00.000Z")
        successor = doc(item(pol=new_policy, events=[event(minutes=30)]), prior)
        packet, _, _ = compile_gate(successor, LATER)
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", packet["results"][0]["state"])
        self.assertIn("OWNER_CAP_GENERATION_EXPLICITLY_REOPENED_PREVIOUS_STOP", packet["results"][0]["reasons"])

    def test_prior_stop_cannot_reopen_by_spend_reset(self):
        stopped_doc = doc(item(pol=policy(cap=30), events=[event(minutes=30)]))
        _, _, prior = compile_gate(stopped_doc, AS_OF)
        reset = item(pol=policy(cap=30), events=[])
        reset["effort_events"] = []
        with self.assertRaisesRegex(PayoffPathError, "omitted prior effort event"):
            compile_gate(doc(reset, prior), LATER)

    def test_policy_generation_cannot_skip_or_forge_predecessor(self):
        packet, _, prior = compile_gate(doc(), AS_OF)
        prior_policy_sha = packet["results"][0]["budget_policy_sha256"]
        bad_skip = item(pol=policy(3, 90, prior_policy_sha, _digest(prior), observed="2026-09-13T15:30:00.000Z"))
        with self.assertRaisesRegex(PayoffPathError, "generation"):
            compile_gate(doc(bad_skip, prior), LATER)
        bad_link = item(pol=policy(2, 90, "0"*64, _digest(prior), observed="2026-09-13T15:30:00.000Z"))
        with self.assertRaisesRegex(PayoffPathError, "predecessor mismatch"):
            compile_gate(doc(bad_link, prior), LATER)

    def test_new_event_cannot_backdate_prior_receipt(self):
        _, _, prior = compile_gate(doc(), AS_OF)
        row = item(events=[event(), event("effort-2", 5, observed="2026-09-13T14:59:59.000Z")])
        with self.assertRaisesRegex(PayoffPathError, "backdates"):
            compile_gate(doc(row, prior), LATER)

    def test_cross_work_event_and_receipt_transplants_fail(self):
        with self.assertRaisesRegex(PayoffPathError, "identity does not match"):
            compile_gate(doc(item(events=[event(work_id="other")])), AS_OF)
        _, _, prior = compile_gate(doc(), AS_OF)
        transplanted = item(opportunity_id="opp-99", events=[event(opportunity_id="opp-99")])
        with self.assertRaisesRegex(PayoffPathError, "cross-opportunity"):
            compile_gate(doc(transplanted, prior), LATER)

    def test_prior_work_cannot_disappear(self):
        _, _, prior = compile_gate(doc(), AS_OF)
        empty = {"schema": "payoff-path-work/v2", "predecessor_receipt": prior, "work_items": []}
        with self.assertRaisesRegex(PayoffPathError, "omitted prior work"):
            compile_gate(empty, LATER)

    def test_new_event_advances_spend_and_can_exhaust(self):
        _, _, prior = compile_gate(doc(), AS_OF)
        successor = item(events=[event(), event("effort-2", 30, observed="2026-09-13T15:30:00.000Z")])
        packet, _, receipt = compile_gate(doc(successor, prior), LATER)
        self.assertEqual(60, packet["results"][0]["free_work_spent_minutes"])
        self.assertEqual("STOP_UNPAID_WORK", packet["results"][0]["state"])
        self.assertTrue(receipt["continuity"][0]["terminal_stop"])

    def test_predecessor_receipt_tamper_breaks_successor_policy_link(self):
        packet, _, prior = compile_gate(doc(), AS_OF)
        prior_policy_sha = packet["results"][0]["budget_policy_sha256"]
        linked_digest = _digest(prior)
        new_policy = policy(2, 90, prior_policy_sha, linked_digest, observed="2026-09-13T15:30:00.000Z")
        tampered = deepcopy(prior)
        tampered["markdown_sha256"] = "0"*64
        with self.assertRaisesRegex(PayoffPathError, "exact predecessor receipt"):
            compile_gate(doc(item(pol=new_policy), tampered), LATER)

    def test_future_effort_holds_not_ready(self):
        row = item(events=[event(observed="2026-09-13T16:00:00.000Z")])
        packet, _, _ = compile_gate(doc(row), AS_OF)
        self.assertEqual("HOLD_STALE_OR_INVALID", packet["results"][0]["state"])
        self.assertIn("EFFORT_EVENT_FROM_FUTURE", packet["results"][0]["reasons"])

    def test_policy_history_is_complete_and_cannot_be_rewritten(self):
        packet1, _, receipt1 = compile_gate(doc(), AS_OF)
        p1 = packet1["results"][0]["budget_policy_sha256"]
        pol2 = policy(2, 90, p1, _digest(receipt1), observed="2026-09-13T15:30:00.000Z")
        packet2, _, receipt2 = compile_gate(doc(item(pol=pol2), receipt1), LATER)
        history = receipt2["continuity"][0]["policy_history"]
        self.assertEqual([1, 2], [entry["generation"] for entry in history])
        self.assertEqual(p1, history[0]["policy_sha256"])
        self.assertEqual(packet2["results"][0]["budget_policy_sha256"], history[1]["policy_sha256"])
        forged = deepcopy(receipt2)
        forged["continuity"][0]["policy_history"] = forged["continuity"][0]["policy_history"][1:]
        pol3 = policy(3, 120, packet2["results"][0]["budget_policy_sha256"], _digest(forged), observed="2026-09-13T16:30:00.000Z")
        with self.assertRaisesRegex(PayoffPathError, "contiguous generations"):
            compile_gate(doc(item(pol=pol3), forged), "2026-09-13T17:00:00.000Z")

    def test_successor_evidence_must_be_strictly_after_predecessor_receipt(self):
        packet, _, prior = compile_gate(doc(), AS_OF)
        prior_policy_sha = packet["results"][0]["budget_policy_sha256"]
        same_time_policy = policy(2, 90, prior_policy_sha, _digest(prior), observed=AS_OF)
        with self.assertRaisesRegex(PayoffPathError, "predates predecessor"):
            compile_gate(doc(item(pol=same_time_policy), prior), LATER)
        same_time_event = item(events=[event(), event("effort-2", 5, observed=AS_OF)])
        with self.assertRaisesRegex(PayoffPathError, "backdates"):
            compile_gate(doc(same_time_event, prior), LATER)


class PreservedSemanticAndCliTests(unittest.TestCase):
    def test_all_supported_mechanisms_remain_owner_review_only(self):
        cases = [
            ("BOUNTY", "SUBMIT_WORK", "FIXED", "USD", 9000),
            ("COMPETITION_PRIZE", "ENTER_COMPETITION", "POOL", "USD", 100000),
            ("PAID_OFFER_OR_PILOT", "SEND_PAID_OFFER", "NEGOTIATED", None, None),
            ("PRIME_SUBCONTRACT", "SECURE_TEAMING", "NEGOTIATED", None, None),
            ("REFERRAL_COMMISSION", "COMPLETE_REFERRAL", "UNSPECIFIED_BY_SOURCE", None, None),
            ("SPONSOR_OR_GRANT", "APPLY_FOR_GRANT", "POOL", "USD", 500000),
        ]
        rows = []
        for i, (mechanism, conversion_event, kind, currency, amount) in enumerate(cases):
            p = path_for()
            p["mechanism"] = mechanism
            p["conversion"]["event"] = conversion_event
            p["value"] = {"kind": kind, "currency": currency, "amount_minor": amount}
            work_id = f"work-{i}"
            opp_id = f"opp-{i}"
            rows.append({
                "work_id": work_id,
                "opportunity_id": opp_id,
                "started_at_utc": "2026-09-13T13:00:00.000Z",
                "budget_policy": policy(),
                "effort_events": [event(f"effort-{i}", 1, work_id=work_id, opportunity_id=opp_id)],
                "payoff_path": p,
            })
        document = {"schema": "payoff-path-work/v2", "predecessor_receipt": None, "work_items": rows}
        packet, markdown, _ = compile_gate(document, AS_OF)
        self.assertEqual(PACKET_SCHEMA, packet["schema"])
        self.assertEqual(6, packet["summary"]["READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW"])
        self.assertEqual("OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION", packet["authority"])
        for banned in ("payment received", "award confirmed", "revenue recognized"):
            self.assertNotIn(banned, markdown.lower())

    def test_source_freshness_and_conversion_deadline_still_fail_closed(self):
        stale = item()
        stale["payoff_path"]["source"]["observed_at_utc"] = "2026-08-01T00:00:00.000Z"
        stale["payoff_path"]["source"]["max_age_days"] = 7
        packet, _, _ = compile_gate(doc(stale), AS_OF)
        self.assertIn("SOURCE_EVIDENCE_STALE", packet["results"][0]["reasons"])
        expired = item()
        expired["payoff_path"]["conversion"]["due_at_utc"] = "2026-09-13T14:59:59.000Z"
        packet, _, _ = compile_gate(doc(expired), AS_OF)
        self.assertIn("CONVERSION_DEADLINE_EXPIRED", packet["results"][0]["reasons"])

    def test_no_path_holds_and_exhausted_cap_wins(self):
        no_path = item()
        no_path["payoff_path"] = None
        packet, _, _ = compile_gate(doc(no_path), AS_OF)
        self.assertEqual("HOLD_NO_PAYOFF_PATH", packet["results"][0]["state"])
        stopped = item(pol=policy(cap=20), events=[event(minutes=30)])
        stopped["payoff_path"] = None
        packet, _, _ = compile_gate(doc(stopped), AS_OF)
        self.assertEqual("STOP_UNPAID_WORK", packet["results"][0]["state"])

    def test_wrong_event_invented_value_and_bad_url_rejected(self):
        wrong = item()
        wrong["payoff_path"]["conversion"]["event"] = "ENTER_COMPETITION"
        with self.assertRaisesRegex(PayoffPathError, "does not match"):
            compile_gate(doc(wrong), AS_OF)
        invented = item()
        invented["payoff_path"]["value"] = {"kind": "NEGOTIATED", "currency": "USD", "amount_minor": 1}
        with self.assertRaisesRegex(PayoffPathError, "invented numeric value"):
            compile_gate(doc(invented), AS_OF)
        bad_url = item()
        bad_url["payoff_path"]["source"]["canonical_url"] = "http://example.com/opportunity/42"
        with self.assertRaises(PayoffPathError):
            compile_gate(doc(bad_url), AS_OF)

    def test_duplicate_json_keys_and_non_finite_values_rejected(self):
        with self.assertRaisesRegex(PayoffPathError, "duplicate JSON key"):
            load_strict_json('{"schema":"payoff-path-work/v2","schema":"x","predecessor_receipt":null,"work_items":[]}')
        with self.assertRaisesRegex(PayoffPathError, "non-finite"):
            load_strict_json('{"x":NaN}')

    def test_packet_markdown_and_receipt_tamper_rejected(self):
        document = doc()
        packet, markdown, receipt = compile_gate(document, AS_OF)
        bad = deepcopy(packet)
        bad["results"][0]["free_work_remaining_minutes"] += 1
        with self.assertRaises(PayoffPathError):
            verify_gate(document, bad, markdown, receipt, AS_OF)
        with self.assertRaises(PayoffPathError):
            verify_gate(document, packet, markdown + "tamper", receipt, AS_OF)
        bad_receipt = deepcopy(receipt)
        bad_receipt["packet_sha256"] = "0" * 64
        with self.assertRaises(PayoffPathError):
            verify_gate(document, packet, markdown, bad_receipt, AS_OF)

    def test_previous_ready_expires_at_verification_time(self):
        document = doc()
        packet, markdown, receipt = compile_gate(document, AS_OF)
        with self.assertRaisesRegex(PayoffPathError, "no longer current"):
            verify_gate(document, packet, markdown, receipt, "2026-09-21T00:00:00.000Z")

    def test_cli_has_no_as_of_override(self):
        with self.assertRaises(SystemExit):
            main(["compile", "--input", "x", "--packet", "p", "--markdown", "m", "--receipt", "r", "--as-of", AS_OF])

    def test_cli_round_trip_and_exclusive_outputs(self):
        document = doc()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.json"
            source.write_text(json.dumps(document), encoding="utf-8")
            packet = root / "packet.json"
            markdown = root / "review.md"
            receipt = root / "receipt.json"
            self.assertEqual(0, main(["compile", "--input", str(source), "--packet", str(packet), "--markdown", str(markdown), "--receipt", str(receipt)]))
            self.assertEqual(0, main(["verify", "--input", str(source), "--packet", str(packet), "--markdown", str(markdown), "--receipt", str(receipt)]))
            with self.assertRaises(SystemExit):
                main(["compile", "--input", str(source), "--packet", str(packet), "--markdown", str(markdown), "--receipt", str(receipt)])


if __name__ == "__main__":
    unittest.main()
