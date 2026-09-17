# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import hmac
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from concierge.certified_settlement_collections_queue import (
    CONTACT_SCHEMA,
    QUEUE_SCHEMA,
    QueueError,
    build_queue,
    verify_queue,
)
from concierge.reward_settlement_certifier import INPUT_SCHEMA, REGISTRY_SCHEMA, canonical

KEY = b"test-secret-key-material-32-bytes!!"
H = "a" * 64
AS_OF = "2026-09-17T00:40:00Z"


def src(sid, authority="PROVIDER", when="2026-09-17T00:05:00Z", ref=None, sha=H):
    return {
        "source_id": sid,
        "source_ref": ref or f"fixture://{sid}",
        "source_sha256": sha,
        "observed_at": when,
        "authority": authority,
    }


def award(sid="award", amount=1000, currency="USD", when="2026-09-17T00:02:00Z"):
    return {"event_id": sid, "kind": "SPONSOR_AWARD", "source": src(sid, "SPONSOR", when), "amount_minor": amount, "currency": currency}


def advertised(sid="advertised", amount=1200, currency="USD"):
    return {"event_id": sid, "kind": "ADVERTISED_BOUNTY", "source": src(sid, "OFFICIAL_OFFER", "2026-09-17T00:01:00Z"), "amount_minor": amount, "currency": currency}


def ticket(sid="ticket"):
    return {"event_id": sid, "kind": "PAYOUT_TICKET", "source": src(sid, "PROVIDER", "2026-09-17T00:03:00Z"), "ticket_id": f"ticket-{sid}"}


def rail(sid="rail"):
    return {"event_id": sid, "kind": "PAYOUT_RAIL", "source": src(sid, "OPERATOR_CAPTURE", "2026-09-17T00:04:00Z"), "rail_ref": f"rail-{sid}"}


def transfer(sid="transfer", status="PENDING", amount=1000, currency="USD", when="2026-09-17T00:05:00Z"):
    return {
        "event_id": sid,
        "kind": "TRANSFER",
        "source": src(sid, "PROVIDER", when),
        "transfer_id": "transfer-1",
        "status": status,
        "direction": "INCOMING",
        "amount_minor": amount,
        "currency": currency,
    }


def closure(sid="close"):
    return {"event_id": sid, "kind": "CLOSURE", "source": src(sid, "SPONSOR", "2026-09-17T00:06:00Z"), "reason": "NO_REWARD"}


def doc(events=None):
    return {
        "schema": INPUT_SCHEMA,
        "generated_at": "2026-09-17T00:30:00Z",
        "cases": [{
            "case_id": "case-1",
            "work": {
                "repo": "example/repo",
                "pr": 1,
                "merge_commit_sha": "1" * 40,
                "merged_at": "2026-09-16T23:00:00Z",
                "source": src("merge", "REPOSITORY", "2026-09-17T00:00:00Z"),
            },
            "events": events or [],
        }],
    }


def signed_registry(sources, key=KEY):
    body = {"schema": REGISTRY_SCHEMA, "generated_at": "2026-09-17T00:30:00Z", "sources": sources}
    return {**body, "signature_hmac_sha256": hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}


def registry_for(d, *, trusted_event_ids=None, extra_sources=None):
    sources = [d["cases"][0]["work"]["source"]]
    for event in d["cases"][0]["events"]:
        if trusted_event_ids is None or event["event_id"] in trusted_event_ids:
            sources.append(event["source"])
    sources.extend(extra_sources or [])
    return signed_registry(sources)


class CertifiedSettlementCollectionsQueueTests(unittest.TestCase):
    def compile(self, events, *, trusted_event_ids=None, as_of=AS_OF, contacts=None, max_age=604800, extra_sources=None):
        d = doc(events)
        r = registry_for(d, trusted_event_ids=trusted_event_ids, extra_sources=extra_sources)
        return d, r, build_queue(d, r, KEY, as_of=as_of, contacts=contacts, max_evidence_age_seconds=max_age)

    def test_all_required_progression_partitions(self):
        cases = [
            ([award()], "AWARD_FOLLOWUP_CANDIDATE"),
            ([award(), ticket()], "PAYOUT_TICKET_FOLLOWUP_CANDIDATE"),
            ([award(), ticket(), rail()], "PAYOUT_RAIL_FOLLOWUP_CANDIDATE"),
            ([award(), ticket(), rail(), transfer()], "TRANSFER_PENDING_FOLLOWUP_CANDIDATE"),
            ([award(), transfer(status="CONFIRMED")], "SETTLED"),
            ([closure()], "CLOSED_NO_REWARD"),
            ([advertised()], "NEEDS_TRUST_EVIDENCE"),
        ]
        for events, expected in cases:
            with self.subTest(expected=expected):
                _, _, q = self.compile(events)
                self.assertEqual(q["records"][0]["partition"], expected)

    def test_untrusted_later_declared_state_demotes_to_trust_evidence(self):
        _, _, q = self.compile([award(), ticket()], trusted_event_ids={"award"})
        row = q["records"][0]
        self.assertEqual(row["certified_settlement_state"], "SPONSOR_AWARD_CERTIFIED")
        self.assertEqual(row["partition"], "NEEDS_TRUST_EVIDENCE")
        self.assertIn("DECLARED_LATER_SETTLEMENT_STATE_NOT_TRUST_BOUND", row["reason_codes"])

    def test_failed_transfer_routes_to_rail_review_not_paid(self):
        _, _, q = self.compile([award(), ticket(), rail(), transfer(status="FAILED")])
        row = q["records"][0]
        self.assertEqual(row["partition"], "PAYOUT_RAIL_FOLLOWUP_CANDIDATE")
        self.assertIn("CERTIFIED_TRANSFER_FAILED_RECHECK_RAIL", row["reason_codes"])
        self.assertEqual(row["amount_evidence"]["paid_certified_by_currency"], {})

    def test_stale_candidate_requires_refresh_but_terminal_paid_does_not_demote(self):
        stale_as_of = "2026-10-01T00:40:00Z"
        _, _, q = self.compile([award()], as_of=stale_as_of, max_age=60)
        self.assertEqual(q["records"][0]["partition"], "NEEDS_TRUST_EVIDENCE")
        self.assertIn("CERTIFIED_EVENT_MISSING_OR_STALE", q["records"][0]["reason_codes"])
        _, _, paid = self.compile([award(), transfer(status="CONFIRMED")], as_of=stale_as_of, max_age=60)
        self.assertEqual(paid["records"][0]["partition"], "SETTLED")

    def test_contact_route_must_be_separately_trust_bound_and_never_authorizes_send(self):
        contact_source = src("contact", "SPONSOR", "2026-09-17T00:07:00Z", "fixture://sponsor/contact")
        contacts = {
            "schema": CONTACT_SCHEMA,
            "generated_at": "2026-09-17T00:30:00Z",
            "routes": [{
                "case_id": "case-1",
                "route_id": "route-1",
                "route_kind": "SPONSOR_ISSUE",
                "source_id": "contact",
                "source_ref": "fixture://sponsor/contact",
                "observed_at": "2026-09-17T00:07:00Z",
            }],
        }
        _, _, q = self.compile([award()], contacts=contacts, extra_sources=[contact_source])
        row = q["records"][0]
        self.assertEqual(row["partition"], "AWARD_FOLLOWUP_CANDIDATE")
        self.assertEqual(row["contact_routes"][0]["route_id"], "route-1")
        self.assertTrue(row["contact_routes"][0]["reference_only"])
        self.assertFalse(row["next_action"]["send_authorized"])
        self.assertFalse(q["truth"]["contact_route_authorizes_send"])

    def test_untrusted_or_mismatched_contact_route_holds_and_is_not_surfaced(self):
        contact_source = src("contact", "SPONSOR", "2026-09-17T00:07:00Z", "fixture://sponsor/contact")
        contacts = {
            "schema": CONTACT_SCHEMA,
            "generated_at": "2026-09-17T00:30:00Z",
            "routes": [{
                "case_id": "case-1", "route_id": "route-1", "route_kind": "SPONSOR_EMAIL",
                "source_id": "contact", "source_ref": "fixture://wrong", "observed_at": "2026-09-17T00:07:00Z",
            }],
        }
        _, _, q = self.compile([award()], contacts=contacts, extra_sources=[contact_source])
        row = q["records"][0]
        self.assertEqual(row["partition"], "HOLD_CONTRADICTION")
        self.assertIn("CONTACT_ROUTE_SOURCE_MISMATCH", row["reason_codes"])
        self.assertEqual(row["contact_routes"], [])

    def test_duplicate_contact_route_id_holds(self):
        s1 = src("contact-a", "SPONSOR", "2026-09-17T00:07:00Z", "fixture://contact/a")
        s2 = src("contact-b", "SPONSOR", "2026-09-17T00:08:00Z", "fixture://contact/b")
        contacts = {
            "schema": CONTACT_SCHEMA,
            "generated_at": "2026-09-17T00:30:00Z",
            "routes": [
                {"case_id":"case-1","route_id":"dup","route_kind":"SPONSOR_ISSUE","source_id":"contact-a","source_ref":"fixture://contact/a","observed_at":"2026-09-17T00:07:00Z"},
                {"case_id":"case-1","route_id":"dup","route_kind":"SPONSOR_EMAIL","source_id":"contact-b","source_ref":"fixture://contact/b","observed_at":"2026-09-17T00:08:00Z"},
            ],
        }
        _, _, q = self.compile([award()], contacts=contacts, extra_sources=[s1, s2])
        row = q["records"][0]
        self.assertEqual(row["partition"], "HOLD_CONTRADICTION")
        self.assertIn("DUPLICATE_CONTACT_ROUTE_ID", row["reason_codes"])

    def test_noncash_unit_is_preserved_without_usd_conversion(self):
        _, _, q = self.compile([award(currency="PTS"), transfer(status="CONFIRMED", currency="PTS")])
        row = q["records"][0]
        self.assertEqual(row["amount_evidence"]["awarded"], {"amount_minor": 1000, "currency": "PTS", "source_ids": ["award"]})
        self.assertEqual(row["amount_evidence"]["paid_certified_by_currency"], {"PTS": 1000})
        self.assertFalse(row["amount_evidence"]["silent_conversion_performed"])
        self.assertNotIn("USD", q["aggregates"]["certified_paid_by_currency"])

    def test_authority_and_revenue_are_hard_false(self):
        _, _, q = self.compile([award(), ticket()])
        self.assertEqual(q["aggregates"]["recognized_revenue_by_currency"], {})
        self.assertTrue(all(value is False for value in q["authority"].values()))
        self.assertFalse(q["truth"]["merge_creates_receivable"])
        self.assertFalse(q["truth"]["advertised_creates_award"])

    def test_receipt_binds_predecessor_projections_and_is_deterministic(self):
        d = doc([award(), ticket()]); r = registry_for(d)
        a = build_queue(d, r, KEY, as_of=AS_OF)
        b = build_queue(copy.deepcopy(d), copy.deepcopy(r), KEY, as_of=AS_OF)
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], QUEUE_SCHEMA)
        self.assertEqual(len(a["source_binding"]["certification_receipt_sha256"]), 64)
        self.assertEqual(len(a["source_binding"]["settlement_ledger_projection_sha256"]), 64)
        self.assertTrue(verify_queue(d, r, KEY, a, as_of=AS_OF))

    def test_queue_tamper_fails_verifier(self):
        d = doc([award()]); r = registry_for(d); q = build_queue(d, r, KEY, as_of=AS_OF)
        q["records"][0]["partition"] = "SETTLED"
        self.assertFalse(verify_queue(d, r, KEY, q, as_of=AS_OF))

    def test_as_of_cannot_predate_source_generation(self):
        d = doc([award()]); r = registry_for(d)
        with self.assertRaisesRegex(QueueError, "may not predate"):
            build_queue(d, r, KEY, as_of="2026-09-17T00:00:00Z")

    def test_invalid_age_bound_rejected(self):
        d = doc([award()]); r = registry_for(d)
        for bad in (-1, True, 1.5):
            with self.subTest(bad=bad), self.assertRaises(QueueError):
                build_queue(d, r, KEY, as_of=AS_OF, max_evidence_age_seconds=bad)

    def test_cli_requires_key_and_refuses_overwrite(self):
        d = doc([award()]); r = registry_for(d)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); ip=root/"in.json"; rp=root/"registry.json"; qp=root/"queue.json"; mp=root/"queue.md"
            ip.write_bytes(canonical(d)); rp.write_bytes(canonical(r))
            env=dict(os.environ); env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1])
            cmd=[sys.executable,"-m","concierge.certified_settlement_collections_queue","--input",str(ip),"--registry",str(rp),"--as-of",AS_OF,"--queue",str(qp),"--markdown",str(mp)]
            no_key=subprocess.run(cmd,env=env,text=True,capture_output=True); self.assertNotEqual(no_key.returncode,0)
            env["REWARD_SETTLEMENT_TRUST_KEY"]=KEY.decode()
            good=subprocess.run(cmd,env=env,text=True,capture_output=True); self.assertEqual(good.returncode,0,good.stderr)
            again=subprocess.run(cmd,env=env,text=True,capture_output=True); self.assertNotEqual(again.returncode,0)


if __name__ == "__main__":
    unittest.main()
