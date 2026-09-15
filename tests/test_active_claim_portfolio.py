# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from concierge import active_claim_portfolio as acp

NOW = datetime(2026, 9, 14, 23, 45, 0, tzinfo=timezone.utc)
ONE = "1" * 64


def live_candidate(repo="Acme/Widget", number=17, worker="worker-a", sponsor="sponsor-a"):
    return {
        "repo": repo,
        "number": number,
        "sponsor_key": sponsor,
        "worker_id": worker,
        "reward_currency": "USD",
        "reward_minor": 9000,
    }


def qualification(dispatch=True, *, currency="USD", amount="90"):
    disposition = "ACTIONABLE" if dispatch else "HOLD"
    signals = {
        "advertised_reward_usd": [amount] if dispatch and currency == "USD" else [],
        "live_label_reward_usd": [],
        "advertised_reward_rtc": [amount] if dispatch and currency == "RTC" else [],
        "live_label_reward_rtc": [],
        "canonical_audit_complete": True,
    }
    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "reason_codes": [],
        "qualification": {
            "disposition": disposition,
            "dispatch": dispatch,
            "signals": signals,
        },
        "provenance": {"disposition": disposition, "dispatch": dispatch, "signals": {}},
    }


def availability(repo="acme/widget", number=17, dispatch=True):
    return {
        "schema": "bounty-availability/v1",
        "repo": repo,
        "number": number,
        "disposition": "CLEAR" if dispatch else "HOLD",
        "dispatch": dispatch,
        "reason_code": None if dispatch else "BUSY",
        "issue_state": "open",
        "signal_codes": [],
        "evidence": [],
        "authority": {"effect": "new_work_dispatch_only"},
    }


def replay_candidate(repo="Acme/Widget", number=17):
    return {
        "repo": repo,
        "number": number,
        "sponsor_key": "sponsor-a",
        "worker_id": "worker-a",
        "observed_at": "2026-09-14T23:40:00Z",
        "reward_currency": "USD",
        "reward_minor": 9000,
        "qualification": qualification(),
        "availability": availability(repo=repo.casefold(), number=number),
    }


def policy(**changes):
    value = {
        "version": "policy-v2",
        "max_active_claims_total": 4,
        "max_active_claims_per_worker": 2,
        "max_active_claims_per_sponsor": 2,
        "sponsor_overrides": {},
        "max_claim_age_seconds": 3600,
    }
    value.update(changes)
    return value


def claim_event(generation, repo="acme/widget", event_at="2026-09-14T23:40:00Z"):
    return {
        "event_id": "evt-1",
        "repo": repo,
        "number": 17,
        "sponsor_key": "sponsor-a",
        "worker_id": "worker-a",
        "opportunity_digest": generation,
        "state": "CLAIMED",
        "event_at": event_at,
        "predecessor_event_id": None,
        "predecessor_event_digest": None,
        "evidence_ref": "internal:evt-1",
        "evidence_sha256": ONE,
    }


def result(receipt, opportunity="acme/widget#17"):
    return next(row for row in receipt["results"] if row["opportunity_id"] == opportunity)


class ActiveClaimPortfolioV2Tests(unittest.TestCase):
    def setUp(self):
        self.clock = mock.patch.object(acp, "_utc_now", return_value=NOW)
        self.qual = mock.patch.object(
            acp, "_qualify_live_authority", return_value=qualification()
        )
        self.avail = mock.patch.object(
            acp, "_inspect_live_availability", return_value=availability()
        )
        self.clock.start()
        self.q = self.qual.start()
        self.a = self.avail.start()

    def tearDown(self):
        mock.patch.stopall()

    def test_clean_live_candidate_is_ready(self):
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        self.assertEqual(receipt["schema"], acp.SCHEMA)
        self.assertEqual(receipt["mode"], "live")
        self.assertEqual(result(receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM")
        self.assertTrue(receipt["authority"]["live_authority_refreshed"])
        self.assertFalse(receipt["authority"]["external_github_claim"])
        self.assertFalse(receipt["authority"]["revenue_recognition"])
        self.assertFalse(receipt["authority"]["caller_reward_metadata_authoritative"])

    def test_live_candidate_cannot_supply_green_receipts(self):
        cand = live_candidate()
        cand["qualification"] = qualification()
        cand["availability"] = availability()
        with self.assertRaises(acp.ActiveClaimPortfolioError):
            acp.compile_live_active_claim_portfolio([cand], [], policy())
        self.q.assert_not_called()
        self.a.assert_not_called()

    def test_live_candidate_cannot_supply_observed_at(self):
        cand = live_candidate()
        cand["observed_at"] = "2000-01-01T00:00:00Z"
        with self.assertRaises(acp.ActiveClaimPortfolioError):
            acp.compile_live_active_claim_portfolio([cand], [], policy())

    def test_live_api_has_no_caller_clock(self):
        with self.assertRaises(TypeError):
            acp.compile_live_active_claim_portfolio(
                [live_candidate()], [], policy(), as_of="2000-01-01T00:00:00Z"
            )

    def test_live_cli_rejects_as_of(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (
                ("candidates.json", [live_candidate()]),
                ("events.json", []),
                ("policy.json", policy()),
            ):
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(SystemExit):
                acp.main([
                    "live", "--candidates", str(root / "candidates.json"),
                    "--events", str(root / "events.json"),
                    "--policy", str(root / "policy.json"),
                    "--as-of", "2000-01-01T00:00:00Z",
                    "--output", str(root / "out.json"),
                ])

    def test_live_reads_both_authorities_with_canonical_repo(self):
        acp.compile_live_active_claim_portfolio([live_candidate()], [], policy())
        self.q.assert_called_once_with("acme/widget", 17, None, 10)
        self.a.assert_called_once_with("acme/widget", 17, 10)

    def test_qualification_read_failure_holds_without_exception_text(self):
        self.q.side_effect = RuntimeError("SECRET_PROVIDER_TEXT")
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_QUALIFICATION_READ_FAILED", row["reason_codes"])
        self.assertNotIn("SECRET_PROVIDER_TEXT", json.dumps(receipt))

    def test_availability_read_failure_holds_without_exception_text(self):
        self.a.side_effect = RuntimeError("PRIVATE_COMMENT_TEXT")
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_AVAILABILITY_READ_FAILED", row["reason_codes"])
        self.assertNotIn("PRIVATE_COMMENT_TEXT", json.dumps(receipt))

    def test_non_dict_authority_result_fails_closed(self):
        self.q.return_value = "green"
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        self.assertEqual(result(receipt)["disposition"], "CONFLICT_HOLD")

    def test_availability_identity_mismatch_holds(self):
        self.a.return_value = availability(repo="other/repo")
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        self.assertEqual(result(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("AVAILABILITY_IDENTITY_MISMATCH", result(receipt)["reason_codes"])

    def test_live_usd_reward_amount_mismatch_holds(self):
        cand = live_candidate()
        cand["reward_minor"] = 2500000
        receipt = acp.compile_live_active_claim_portfolio([cand], [], policy())
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_AMOUNT_MISMATCH", row["reason_codes"])

    def test_live_reward_currency_mismatch_holds(self):
        cand = live_candidate()
        cand["reward_currency"] = "RTC"
        cand["reward_minor"] = 90
        receipt = acp.compile_live_active_claim_portfolio([cand], [], policy())
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_CURRENCY_MISMATCH", row["reason_codes"])

    def test_live_rtc_reward_exact_match_is_ready(self):
        cand = live_candidate()
        cand["reward_currency"] = "RTC"
        cand["reward_minor"] = 25
        self.q.return_value = qualification(currency="RTC", amount="25")
        receipt = acp.compile_live_active_claim_portfolio([cand], [], policy())
        self.assertEqual(result(receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM")

    def test_fractional_rtc_reward_holds_in_integer_custody_schema(self):
        cand = live_candidate()
        cand["reward_currency"] = "RTC"
        cand["reward_minor"] = 25
        self.q.return_value = qualification(currency="RTC", amount="25.5")
        receipt = acp.compile_live_active_claim_portfolio([cand], [], policy())
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_AMOUNT_UNREPRESENTABLE", row["reason_codes"])

    def test_dual_currency_live_reward_holds_ambiguous(self):
        q = qualification()
        q["qualification"]["signals"]["advertised_reward_rtc"] = ["90"]
        self.q.return_value = q
        receipt = acp.compile_live_active_claim_portfolio([live_candidate()], [], policy())
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_CURRENCY_AMBIGUOUS", row["reason_codes"])

    def test_actionable_missing_reward_signals_holds(self):
        q = qualification()
        q["qualification"]["signals"] = {"canonical_audit_complete": True}
        self.q.return_value = q
        receipt = acp.compile_live_active_claim_portfolio([live_candidate()], [], policy())
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_BINDING_UNAVAILABLE", row["reason_codes"])

    def test_non_actionable_qualification_does_not_invent_reward_failure(self):
        self.q.return_value = qualification(dispatch=False)
        receipt = acp.compile_live_active_claim_portfolio([live_candidate()], [], policy())
        row = result(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertNotIn("LIVE_REWARD_BINDING_UNAVAILABLE", row["reason_codes"])

    def test_case_alias_candidates_collapse_to_one_identity(self):
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate(repo="Acme/Widget"), live_candidate(repo="acme/widget")],
            [], policy(),
        )
        self.assertEqual(len(receipt["results"]), 1)
        self.assertEqual(receipt["results"][0]["opportunity_id"], "acme/widget#17")
        self.assertEqual(self.q.call_count, 1)
        self.assertEqual(self.a.call_count, 1)

    def test_case_alias_workers_still_conflict_for_same_issue(self):
        receipt = acp.compile_live_active_claim_portfolio(
            [
                live_candidate(repo="Acme/Widget", worker="worker-a"),
                live_candidate(repo="acme/widget", worker="worker-b"),
            ],
            [], policy(),
        )
        self.assertEqual(result(receipt)["disposition"], "CONFLICT_HOLD")
        self.assertIn("CANDIDATE_VARIANT_CONFLICT", result(receipt)["reason_codes"])

    def test_generation_is_stable_across_verifier_clock_ticks(self):
        first = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        with mock.patch.object(
            acp, "_utc_now", return_value=datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
        ):
            second = acp.compile_live_active_claim_portfolio(
                [live_candidate()], [], policy()
            )
        self.assertEqual(
            result(first)["generation_sha256"], result(second)["generation_sha256"]
        )

    def test_mixed_case_event_binds_canonical_generation(self):
        first = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        event = claim_event(result(first)["generation_sha256"], repo="ACME/WIDGET")
        second = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [event], policy()
        )
        self.assertEqual(result(second)["disposition"], "ACTIVE_OWNED")

    def test_staleness_uses_verifier_owned_now(self):
        first = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        event = claim_event(
            result(first)["generation_sha256"], event_at="2026-09-14T22:44:59Z"
        )
        second = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [event], policy(max_claim_age_seconds=3600)
        )
        self.assertEqual(result(second)["disposition"], "STALE_CLAIM_REVIEW")

    def test_replay_clean_candidate_is_never_ready(self):
        receipt = acp.compile_replay_active_claim_portfolio(
            [replay_candidate()], [], policy(), as_of="2026-09-14T23:45:00Z"
        )
        row = result(receipt)
        self.assertEqual(receipt["mode"], "replay")
        self.assertEqual(row["disposition"], "REPLAY_ONLY_HOLD")
        self.assertIn("HISTORICAL_REPLAY_NON_DISPATCH", row["reason_codes"])

    def test_legacy_compile_alias_is_replay_only(self):
        receipt = acp.compile_active_claim_portfolio(
            [replay_candidate()], [], policy(), as_of="2026-09-14T23:45:00Z"
        )
        self.assertEqual(result(receipt)["disposition"], "REPLAY_ONLY_HOLD")

    def test_replay_casefolds_candidate_and_availability_identity(self):
        cand = replay_candidate(repo="Acme/Widget")
        receipt = acp.compile_replay_active_claim_portfolio(
            [cand], [], policy(), as_of="2026-09-14T23:45:00Z"
        )
        self.assertEqual(result(receipt)["opportunity_id"], "acme/widget#17")

    def test_receipt_integrity_detects_tamper(self):
        receipt = acp.compile_replay_active_claim_portfolio(
            [replay_candidate()], [], policy(), as_of="2026-09-14T23:45:00Z"
        )
        self.assertTrue(acp.verify_receipt_integrity(receipt))
        changed = json.loads(json.dumps(receipt))
        changed["results"][0]["disposition"] = "ACTIVE_OWNED"
        self.assertFalse(acp.verify_receipt_integrity(changed))

    def test_replay_verifier_detects_policy_drift(self):
        cand = replay_candidate()
        p = policy()
        receipt = acp.compile_replay_active_claim_portfolio(
            [cand], [], p, as_of="2026-09-14T23:45:00Z"
        )
        self.assertTrue(
            acp.verify_active_claim_portfolio_receipt(
                receipt, [cand], [], p, as_of="2026-09-14T23:45:00Z"
            )
        )
        self.assertFalse(
            acp.verify_active_claim_portfolio_receipt(
                receipt, [cand], [], policy(max_active_claims_total=3),
                as_of="2026-09-14T23:45:00Z",
            )
        )

    def test_currency_is_metadata_not_summed(self):
        self.a.side_effect = lambda repo, number, max_pages: availability(repo, number)
        one = live_candidate(repo="o/a", number=1)
        one["reward_currency"] = "RTC"
        one["reward_minor"] = 25
        two = live_candidate(repo="o/b", number=2, worker="worker-b", sponsor="sponsor-b")
        two["reward_currency"] = "USD"
        two["reward_minor"] = 250000
        self.q.side_effect = [
            qualification(currency="RTC", amount="25"),
            qualification(currency="USD", amount="2500"),
        ]
        receipt = acp.compile_live_active_claim_portfolio([one, two], [], policy())
        self.assertNotIn("reward_total", receipt["capacity"])
        self.assertEqual(result(receipt, "o/a#1")["reward_currency"], "RTC")
        self.assertEqual(result(receipt, "o/b#2")["reward_currency"], "USD")

    def test_live_receipt_authority_flags_deny_external_side_effects(self):
        receipt = acp.compile_live_active_claim_portfolio(
            [live_candidate()], [], policy()
        )
        for key in (
            "external_github_claim", "sponsor_or_maintainer_contact",
            "upstream_submission", "wallet_or_payment_mutation", "provider_mutation",
            "acceptance_or_payout_assertion", "cash_assertion", "revenue_recognition",
        ):
            self.assertFalse(receipt["authority"][key])

    def test_bad_event_shape_fails_before_authority_reads(self):
        with self.assertRaises(acp.ActiveClaimPortfolioError):
            acp.compile_live_active_claim_portfolio(
                [live_candidate()], [{"repo": "acme/widget"}], policy()
            )
        self.q.assert_not_called()
        self.a.assert_not_called()

    def test_bad_max_pages_fails_before_authority_reads(self):
        with self.assertRaises(acp.ActiveClaimPortfolioError):
            acp.compile_live_active_claim_portfolio(
                [live_candidate()], [], policy(), max_pages=True
            )
        self.q.assert_not_called()
        self.a.assert_not_called()


if __name__ == "__main__":
    unittest.main()
