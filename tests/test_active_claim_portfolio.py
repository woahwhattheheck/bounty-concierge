# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from concierge import active_claim_portfolio as acp


AS_OF = "2026-09-13T12:00:00Z"
ZERO = "0" * 64
ONE = "1" * 64


def qualification():
    return {
        "disposition": "ACTIONABLE",
        "dispatch": True,
        "reason_codes": [],
        "signals": {"canonical_audit_complete": True},
    }


def availability(repo="owner/repo", number=1):
    return {
        "schema": "bounty-availability/v1",
        "repo": repo,
        "number": number,
        "disposition": "CLEAR",
        "dispatch": True,
        "reason_code": None,
        "issue_state": "open",
        "signal_codes": [],
        "evidence": [],
        "authority": {"effect": "new_work_dispatch_only"},
    }


def candidate(
    repo="owner/repo",
    number=1,
    sponsor="sponsor-a",
    worker="worker-a",
    observed="2026-09-13T11:30:00Z",
    reward_currency="RTC",
    reward_minor=25,
):
    return {
        "repo": repo,
        "number": number,
        "sponsor_key": sponsor,
        "worker_id": worker,
        "observed_at": observed,
        "reward_currency": reward_currency,
        "reward_minor": reward_minor,
        "qualification": qualification(),
        "availability": availability(repo, number),
    }


def policy(**overrides):
    base = {
        "version": "policy-v1",
        "max_active_claims_total": 4,
        "max_active_claims_per_worker": 2,
        "max_active_claims_per_sponsor": 2,
        "sponsor_overrides": {},
        "max_claim_age_seconds": 3600,
    }
    base.update(overrides)
    return base


def generation(cand):
    as_of_dt = datetime.strptime(AS_OF, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return acp._normalize_candidate(cand, as_of_dt)["generation_sha256"]


def claim_event(
    cand,
    event_id="evt-1",
    worker=None,
    sponsor=None,
    state="CLAIMED",
    event_at="2026-09-13T11:45:00Z",
    opportunity_digest=None,
    predecessor=None,
):
    if worker is None:
        worker = cand["worker_id"]
    if sponsor is None:
        sponsor = cand["sponsor_key"]
    if opportunity_digest is None:
        opportunity_digest = generation(cand)
    pred_id = predecessor["event_id"] if predecessor is not None else None
    pred_digest = acp._sha256_json(predecessor) if predecessor is not None else None
    return {
        "event_id": event_id,
        "repo": cand["repo"],
        "number": cand["number"],
        "sponsor_key": sponsor,
        "worker_id": worker,
        "opportunity_digest": opportunity_digest,
        "state": state,
        "event_at": event_at,
        "predecessor_event_id": pred_id,
        "predecessor_event_digest": pred_digest,
        "evidence_ref": "internal:%s" % event_id,
        "evidence_sha256": ONE,
    }


def row(receipt, opportunity_id="owner/repo#1"):
    return next(item for item in receipt["results"] if item["opportunity_id"] == opportunity_id)


class ActiveClaimPortfolioTests(unittest.TestCase):
    def test_clean_candidate_is_ready(self):
        receipt = acp.compile_active_claim_portfolio([candidate()], [], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM")
        self.assertEqual(receipt["capacity"]["active_claims_total"], 0)
        self.assertFalse(receipt["authority"]["external_github_claim"])
        self.assertFalse(receipt["authority"]["revenue_recognition"])

    def test_clean_live_claim_is_active_owned(self):
        cand = candidate()
        event = claim_event(cand)
        receipt = acp.compile_active_claim_portfolio([cand], [event], policy(), as_of=AS_OF)
        result = row(receipt)
        self.assertEqual(result["disposition"], "ACTIVE_OWNED")
        self.assertEqual(result["active_worker_id"], "worker-a")
        self.assertEqual(result["claim_age_seconds"], 900)
        self.assertEqual(receipt["capacity"]["active_claims_total"], 1)

    def test_exact_event_replay_is_idempotent(self):
        cand = candidate()
        event = claim_event(cand)
        one = acp.compile_active_claim_portfolio([cand], [event], policy(), as_of=AS_OF)
        two = acp.compile_active_claim_portfolio([cand], [event, dict(event)], policy(), as_of=AS_OF)
        self.assertEqual(row(one)["disposition"], "ACTIVE_OWNED")
        self.assertEqual(row(two)["disposition"], "ACTIVE_OWNED")
        self.assertEqual(two["capacity"]["active_claims_total"], 1)
        self.assertEqual(one, two)

    def test_same_event_id_changed_bytes_conflicts(self):
        cand = candidate()
        first = claim_event(cand)
        changed = dict(first)
        changed["evidence_sha256"] = ZERO
        receipt = acp.compile_active_claim_portfolio(
            [cand], [first, changed], policy(), as_of=AS_OF
        )
        self.assertEqual(row(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("EVENT_ID_REUSE", row(receipt)["reason_codes"])

    def test_two_workers_claiming_same_generation_conflicts(self):
        cand = candidate()
        first = claim_event(cand, event_id="evt-a", worker="worker-a")
        second = claim_event(cand, event_id="evt-b", worker="worker-b")
        receipt = acp.compile_active_claim_portfolio(
            [cand], [first, second], policy(), as_of=AS_OF
        )
        result = row(receipt)
        self.assertEqual(result["disposition"], "CONFLICT_HOLD")
        self.assertIn("CLAIM_ROOT_CARDINALITY", result["reason_codes"])

    def test_duplicate_candidate_assignment_variants_conflict(self):
        a = candidate(worker="worker-a")
        b = candidate(worker="worker-b")
        receipt = acp.compile_active_claim_portfolio([a, b], [], policy(), as_of=AS_OF)
        result = row(receipt)
        self.assertEqual(result["disposition"], "CONFLICT_HOLD")
        self.assertIn("CANDIDATE_VARIANT_CONFLICT", result["reason_codes"])

    def test_release_frees_capacity(self):
        cand = candidate()
        first = claim_event(cand, event_id="evt-a")
        release = claim_event(
            cand,
            event_id="evt-b",
            state="RELEASED",
            event_at="2026-09-13T11:50:00Z",
            predecessor=first,
        )
        receipt = acp.compile_active_claim_portfolio(
            [cand], [release, first], policy(), as_of=AS_OF
        )
        self.assertEqual(row(receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM")
        self.assertEqual(receipt["capacity"]["active_claims_total"], 0)

    def test_completed_internal_frees_capacity_but_is_not_acceptance(self):
        cand = candidate()
        first = claim_event(cand, event_id="evt-a")
        done = claim_event(
            cand,
            event_id="evt-b",
            state="COMPLETED_INTERNAL",
            event_at="2026-09-13T11:50:00Z",
            predecessor=first,
        )
        receipt = acp.compile_active_claim_portfolio([cand], [first, done], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM")
        self.assertFalse(receipt["authority"]["completed_internal_is_sponsor_acceptance"])

    def test_missing_predecessor_holds(self):
        cand = candidate()
        event = claim_event(cand, event_id="evt-b", state="RELEASED")
        event["predecessor_event_id"] = "evt-missing"
        event["predecessor_event_digest"] = ZERO
        receipt = acp.compile_active_claim_portfolio([cand], [event], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("PREDECESSOR_MISSING", row(receipt)["reason_codes"])

    def test_predecessor_digest_tamper_holds(self):
        cand = candidate()
        first = claim_event(cand, event_id="evt-a")
        release = claim_event(cand, event_id="evt-b", state="RELEASED", predecessor=first)
        release["predecessor_event_digest"] = ZERO
        receipt = acp.compile_active_claim_portfolio([cand], [first, release], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("PREDECESSOR_DIGEST_MISMATCH", row(receipt)["reason_codes"])

    def test_claimed_after_claimed_is_state_regression(self):
        cand = candidate()
        first = claim_event(cand, event_id="evt-a")
        second = claim_event(cand, event_id="evt-b", state="CLAIMED", predecessor=first)
        receipt = acp.compile_active_claim_portfolio([cand], [first, second], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("STATE_REGRESSION_OR_INVALID_TRANSITION", row(receipt)["reason_codes"])

    def test_future_event_holds(self):
        cand = candidate()
        future = claim_event(cand, event_at="2026-09-13T12:00:01Z")
        receipt = acp.compile_active_claim_portfolio([cand], [future], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("FUTURE_EVENT", row(receipt)["reason_codes"])

    def test_stale_at_exact_age_boundary(self):
        cand = candidate()
        event = claim_event(cand, event_at="2026-09-13T11:00:00Z")
        receipt = acp.compile_active_claim_portfolio([cand], [event], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "STALE_CLAIM_REVIEW")
        self.assertEqual(row(receipt)["claim_age_seconds"], 3600)

    def test_old_generation_still_consumes_capacity_and_holds_candidate(self):
        old = candidate(observed="2026-09-13T11:20:00Z")
        event = claim_event(old)
        current = candidate(observed="2026-09-13T11:30:00Z")
        receipt = acp.compile_active_claim_portfolio([current], [event], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "UPSTREAM_CHANGED_HOLD")
        self.assertEqual(receipt["capacity"]["active_claims_total"], 1)

    def test_total_capacity_hold(self):
        active_cand = candidate(repo="o/a", number=1, worker="w1", sponsor="s1")
        event = claim_event(active_cand)
        target = candidate(repo="o/b", number=2, worker="w2", sponsor="s2")
        p = policy(max_active_claims_total=1)
        receipt = acp.compile_active_claim_portfolio([target], [event], p, as_of=AS_OF)
        self.assertEqual(row(receipt, "o/b#2")["disposition"], "CAPACITY_HOLD")
        self.assertIn("TOTAL_ACTIVE_CAP_REACHED", row(receipt, "o/b#2")["reason_codes"])

    def test_worker_capacity_hold(self):
        active_cand = candidate(repo="o/a", number=1, worker="w1", sponsor="s1")
        event = claim_event(active_cand)
        target = candidate(repo="o/b", number=2, worker="w1", sponsor="s2")
        p = policy(max_active_claims_per_worker=1)
        receipt = acp.compile_active_claim_portfolio([target], [event], p, as_of=AS_OF)
        self.assertIn("WORKER_ACTIVE_CAP_REACHED", row(receipt, "o/b#2")["reason_codes"])

    def test_sponsor_capacity_hold(self):
        active_cand = candidate(repo="o/a", number=1, worker="w1", sponsor="same")
        event = claim_event(active_cand)
        target = candidate(repo="o/b", number=2, worker="w2", sponsor="same")
        p = policy(max_active_claims_per_sponsor=1)
        receipt = acp.compile_active_claim_portfolio([target], [event], p, as_of=AS_OF)
        self.assertIn("SPONSOR_ACTIVE_CAP_REACHED", row(receipt, "o/b#2")["reason_codes"])

    def test_sponsor_override_is_applied(self):
        active_cand = candidate(repo="o/a", number=1, worker="w1", sponsor="same")
        event = claim_event(active_cand)
        target = candidate(repo="o/b", number=2, worker="w2", sponsor="same")
        p = policy(max_active_claims_per_sponsor=3, sponsor_overrides={"same": 1})
        receipt = acp.compile_active_claim_portfolio([target], [event], p, as_of=AS_OF)
        self.assertIn("SPONSOR_ACTIVE_CAP_REACHED", row(receipt, "o/b#2")["reason_codes"])

    def test_bad_upstream_availability_holds(self):
        cand = candidate()
        cand["availability"]["dispatch"] = False
        receipt = acp.compile_active_claim_portfolio([cand], [], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("AVAILABILITY_NOT_CLEAR", row(receipt)["reason_codes"])

    def test_future_candidate_evidence_holds(self):
        cand = candidate(observed="2026-09-13T12:00:01Z")
        receipt = acp.compile_active_claim_portfolio([cand], [], policy(), as_of=AS_OF)
        self.assertIn("FUTURE_CANDIDATE_EVIDENCE", row(receipt)["reason_codes"])

    def test_bool_cannot_alias_availability_number(self):
        cand = candidate(number=1)
        cand["availability"]["number"] = True
        receipt = acp.compile_active_claim_portfolio([cand], [], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("AVAILABILITY_IDENTITY_MISMATCH", row(receipt)["reason_codes"])

    def test_bool_is_not_integer(self):
        cand = candidate()
        cand["number"] = True
        with self.assertRaises(acp.ActiveClaimPortfolioError):
            acp.compile_active_claim_portfolio([cand], [], policy(), as_of=AS_OF)

    def test_unsafe_integer_is_rejected(self):
        cand = candidate(reward_minor=(1 << 53))
        with self.assertRaises(acp.ActiveClaimPortfolioError):
            acp.compile_active_claim_portfolio([cand], [], policy(), as_of=AS_OF)

    def test_unknown_candidate_key_rejected(self):
        cand = candidate()
        cand["surprise"] = "x"
        with self.assertRaises(acp.ActiveClaimPortfolioError):
            acp.compile_active_claim_portfolio([cand], [], policy(), as_of=AS_OF)

    def test_input_order_does_not_change_receipt(self):
        a = candidate(repo="o/a", number=1, worker="w1", sponsor="s1")
        b = candidate(repo="o/b", number=2, worker="w2", sponsor="s2")
        ea = claim_event(a, event_id="evt-a")
        eb = claim_event(b, event_id="evt-b")
        one = acp.compile_active_claim_portfolio([a, b], [ea, eb], policy(), as_of=AS_OF)
        two = acp.compile_active_claim_portfolio([b, a], [eb, ea], policy(), as_of=AS_OF)
        self.assertEqual(one, two)

    def test_receipt_verifier_detects_tamper(self):
        cand = candidate()
        receipt = acp.compile_active_claim_portfolio([cand], [], policy(), as_of=AS_OF)
        self.assertTrue(
            acp.verify_active_claim_portfolio_receipt(
                receipt, [cand], [], policy(), as_of=AS_OF
            )
        )
        tampered = json.loads(json.dumps(receipt))
        tampered["results"][0]["disposition"] = "ACTIVE_OWNED"
        self.assertFalse(
            acp.verify_active_claim_portfolio_receipt(
                tampered, [cand], [], policy(), as_of=AS_OF
            )
        )

    def test_policy_drift_breaks_verification(self):
        cand = candidate()
        p = policy()
        receipt = acp.compile_active_claim_portfolio([cand], [], p, as_of=AS_OF)
        changed = policy(max_active_claims_total=3)
        self.assertFalse(
            acp.verify_active_claim_portfolio_receipt(
                receipt, [cand], [], changed, as_of=AS_OF
            )
        )

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{"a":1,"a":2}', encoding="utf-8")
            with self.assertRaises(acp.ActiveClaimPortfolioError):
                acp._load_json_file(str(path))

    def test_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            path.write_text("existing", encoding="utf-8")
            with self.assertRaises(acp.ActiveClaimPortfolioError):
                acp._exclusive_write_text(str(path), "replacement")
            self.assertEqual(path.read_text(encoding="utf-8"), "existing")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_symlink_output_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.json"
            real.write_text("safe", encoding="utf-8")
            link = Path(tmp) / "link.json"
            os.symlink(real, link)
            with self.assertRaises(acp.ActiveClaimPortfolioError):
                acp._exclusive_write_text(str(link), "evil")
            self.assertEqual(real.read_text(encoding="utf-8"), "safe")

    def test_cross_opportunity_event_id_reuse_holds_both(self):
        a = candidate(repo="o/a", number=1, worker="w1", sponsor="s1")
        b = candidate(repo="o/b", number=2, worker="w2", sponsor="s2")
        ea = claim_event(a, event_id="evt-same")
        eb = claim_event(b, event_id="evt-same")
        receipt = acp.compile_active_claim_portfolio([a, b], [ea, eb], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt, "o/a#1")["disposition"], "CONFLICT_HOLD")
        self.assertEqual(row(receipt, "o/b#2")["disposition"], "CONFLICT_HOLD")

    def test_ledger_conflict_blocks_unrelated_new_capacity(self):
        bad = candidate(repo="o/bad", number=1, worker="w1", sponsor="s1")
        one = claim_event(bad, event_id="evt-one", worker="w1")
        two = claim_event(bad, event_id="evt-two", worker="w2")
        target = candidate(repo="o/good", number=2, worker="w3", sponsor="s2")
        receipt = acp.compile_active_claim_portfolio([bad, target], [one, two], policy(), as_of=AS_OF)
        self.assertTrue(receipt["capacity"]["ledger_capacity_uncertain"])
        self.assertEqual(row(receipt, "o/good#2")["disposition"], "CONFLICT_HOLD")
        self.assertIn("LEDGER_CAPACITY_UNCERTAIN", row(receipt, "o/good#2")["reason_codes"])

    def test_cli_compile_and_verify_roundtrip(self):
        cand = candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates_path = root / "candidates.json"
            events_path = root / "events.json"
            policy_path = root / "policy.json"
            receipt_path = root / "receipt.json"
            candidates_path.write_text(json.dumps([cand]), encoding="utf-8")
            events_path.write_text("[]", encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            rc = acp.main([
                "compile",
                "--candidates", str(candidates_path),
                "--events", str(events_path),
                "--policy", str(policy_path),
                "--as-of", AS_OF,
                "--output", str(receipt_path),
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(receipt_path.is_file())
            rc = acp.main([
                "verify",
                "--candidates", str(candidates_path),
                "--events", str(events_path),
                "--policy", str(policy_path),
                "--as-of", AS_OF,
                "--receipt", str(receipt_path),
            ])
            self.assertEqual(rc, 0)

    def test_currency_is_metadata_not_summed(self):
        a = candidate(repo="o/a", number=1, reward_currency="RTC", reward_minor=25)
        b = candidate(repo="o/b", number=2, reward_currency="USD", reward_minor=250000)
        receipt = acp.compile_active_claim_portfolio([a, b], [], policy(), as_of=AS_OF)
        self.assertEqual(row(receipt, "o/a#1")["reward_currency"], "RTC")
        self.assertEqual(row(receipt, "o/b#2")["reward_currency"], "USD")
        self.assertNotIn("reward_total", receipt["capacity"])


if __name__ == "__main__":
    unittest.main()
