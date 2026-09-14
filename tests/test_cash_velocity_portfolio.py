# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from concierge import cash_velocity_portfolio as velocity


NOW = datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc)


def candidate(source: str, reward: str, effort: str, probability: str = "1", *, deadline: str = "100", group=None):
    row = {
        "snapshot": {},
        "estimated_effort_hours": effort,
        "estimated_win_probability": probability,
        "hours_until_deadline": deadline,
        "test_reward": reward,
        "test_source": source,
    }
    if group is not None:
        row["collision_group"] = group
    return row


def fake_rank(candidates, skills, *, saturation_threshold=4):
    ranked = []
    for index, row in enumerate(candidates):
        ranked.append(
            {
                "input_index": index,
                "canonical_source_url": row["test_source"],
                "rank": index + 1,
                "advertised_reward_usd": row["test_reward"],
                "estimated_win_probability": str(row["estimated_win_probability"]),
                "estimated_effort_hours": str(row["estimated_effort_hours"]),
                "skill_match": "1",
            }
        )
    return {"ranked": ranked, "excluded": []}


def cycle_receipt(*, evaluated_at="2026-09-14T23:00:00Z", slow_repo="slow/repo", state="READY_FOR_OWNER_CASH_CYCLE_REVIEW"):
    authority = {
        "owner_review_only": True,
        "payer_or_sponsor_identity_inference": False,
        "sponsor_or_maintainer_contact": False,
        "opportunity_rank_mutation": False,
        "wallet_or_provider_mutation": False,
        "payout_or_transfer_initiation": False,
        "debt_or_receivable_assertion": False,
        "cash_recognition": False,
        "revenue_recognition": False,
    }
    return {
        "schema_version": 1,
        "product": "bounty-cash-cycle-review/v1",
        "evaluated_at": evaluated_at,
        "receipt_sha256": "a" * 64,
        "authority": authority,
        "repositories": [
            {
                "repo": slow_repo,
                "state": state,
                "confirmed_sample_count": 3,
                "median_last_confirmed_transfer_lag_seconds": "172800",
                "review_lag_hours": 24,
            }
        ],
    }


def calibration(candidates, *, overrides=None):
    overrides = overrides or {}
    rows = []
    for index, row in enumerate(candidates):
        rows.append(
            {
                "input_index": index,
                "calibrated_estimated_win_probability": overrides.get(
                    index, str(row["estimated_win_probability"])
                ),
            }
        )
    return {
        "calibration_receipt_sha256": "b" * 64,
        "calibration": rows,
        "portfolio": {
            "schema": "qualified-opportunity-portfolio/v1",
            "selected_count": 2,
            "selected": [],
        },
        "authority": {
            "probability_can_only_decrease": True,
            "currency_conversion": False,
            "rtc_amount_used_in_usd_math": False,
            "claim_or_submission_authority": False,
            "cash_or_settlement_claim": False,
            "accounting_or_tax_claim": False,
        },
    }


def request_candidates():
    return [
        candidate("https://github.com/slow/repo/issues/1", "1000", "4"),
        candidate("https://github.com/slow/repo/issues/2", "900", "4"),
        candidate("https://github.com/fast/repo/issues/1", "800", "4"),
    ]


def policy(fraction="0.5", age=24):
    return {
        "schema_version": 1,
        "max_receipt_age_hours": age,
        "slow_repo_max_capacity_fraction": fraction,
    }


class CashVelocityPortfolioTests(unittest.TestCase):
    def run_allocate(self, candidates=None, receipt=None, velocity_policy=None, calibration_result=None):
        candidates = request_candidates() if candidates is None else candidates
        receipt = cycle_receipt() if receipt is None else receipt
        velocity_policy = policy() if velocity_policy is None else velocity_policy
        calibration_result = calibration(candidates) if calibration_result is None else calibration_result
        with patch.object(velocity._cycle, "verify_cash_cycle_review", return_value=True), patch.object(
            velocity._cash, "allocate_cash_calibrated_portfolio", return_value=calibration_result
        ), patch.object(velocity, "rank_opportunities", side_effect=fake_rank):
            return velocity.allocate_cash_velocity_portfolio(
                candidates,
                ["python"],
                "8",
                [],
                [],
                {},
                receipt,
                {},
                {},
                {},
                {},
                velocity_policy,
                wallet="wallet",
                evaluated_at=NOW,
            )

    def test_slow_repo_cap_forces_faster_cash_diversification(self):
        result = self.run_allocate()
        sources = [row["canonical_source_url"] for row in result["portfolio"]["selected"]]
        self.assertEqual(
            sources,
            [
                "https://github.com/fast/repo/issues/1",
                "https://github.com/slow/repo/issues/1",
            ],
        )
        self.assertEqual(result["portfolio"]["estimated_portfolio_expected_value_usd"], "1800")
        constraint = result["portfolio"]["repo_velocity_constraints"][0]
        self.assertEqual(constraint["repo"], "slow/repo")
        self.assertEqual(constraint["capacity_cap_hours"], "4")
        self.assertEqual(constraint["selected_effort_hours"], "4")

    def test_fraction_one_preserves_unconstrained_best_pair(self):
        result = self.run_allocate(velocity_policy=policy("1"))
        sources = {row["canonical_source_url"] for row in result["portfolio"]["selected"]}
        self.assertEqual(
            sources,
            {
                "https://github.com/slow/repo/issues/1",
                "https://github.com/slow/repo/issues/2",
            },
        )
        self.assertEqual(result["portfolio"]["estimated_portfolio_expected_value_usd"], "1900")

    def test_zero_fraction_excludes_slow_repo_work(self):
        result = self.run_allocate(velocity_policy=policy("0"))
        sources = [row["canonical_source_url"] for row in result["portfolio"]["selected"]]
        self.assertEqual(sources, ["https://github.com/fast/repo/issues/1"])
        reasons = [row["reason_code"] for row in result["portfolio"]["excluded"]]
        self.assertEqual(reasons.count("EXCEEDS_REPO_VELOCITY_CAP"), 2)

    def test_insufficient_cycle_history_does_not_create_cap(self):
        receipt = cycle_receipt(state="INSUFFICIENT_CONFIRMED_HISTORY")
        result = self.run_allocate(receipt=receipt)
        self.assertEqual(result["portfolio"]["repo_velocity_constraints"], [])
        sources = {row["canonical_source_url"] for row in result["portfolio"]["selected"]}
        self.assertEqual(
            sources,
            {
                "https://github.com/slow/repo/issues/1",
                "https://github.com/slow/repo/issues/2",
            },
        )

    def test_unmatched_slow_repo_does_not_penalize_other_repositories(self):
        receipt = cycle_receipt(slow_repo="other/repo")
        result = self.run_allocate(receipt=receipt)
        sources = {row["canonical_source_url"] for row in result["portfolio"]["selected"]}
        self.assertEqual(
            sources,
            {
                "https://github.com/slow/repo/issues/1",
                "https://github.com/slow/repo/issues/2",
            },
        )

    def test_stale_cycle_receipt_fails_closed_before_allocation(self):
        receipt = cycle_receipt(evaluated_at="2026-09-13T23:00:00Z")
        with self.assertRaisesRegex(velocity.CashVelocityInputError, "max_receipt_age_hours"):
            self.run_allocate(receipt=receipt, velocity_policy=policy("0.5", age=1))

    def test_future_cycle_receipt_fails_closed(self):
        receipt = cycle_receipt(evaluated_at="2026-09-15T00:00:00Z")
        with self.assertRaisesRegex(velocity.CashVelocityInputError, "future"):
            self.run_allocate(receipt=receipt)

    def test_failed_cycle_verification_fails_closed(self):
        candidates = request_candidates()
        with patch.object(velocity._cycle, "verify_cash_cycle_review", return_value=False):
            with self.assertRaisesRegex(velocity.CashVelocityInputError, "verification failed"):
                velocity.allocate_cash_velocity_portfolio(
                    candidates, ["python"], "8", [], [], {}, cycle_receipt(), {}, {}, {}, {},
                    policy(), wallet="wallet", evaluated_at=NOW
                )

    def test_cycle_authority_drift_fails_closed(self):
        receipt = cycle_receipt()
        receipt["authority"]["cash_recognition"] = True
        with self.assertRaisesRegex(velocity.CashVelocityInputError, "authority ceiling"):
            self.run_allocate(receipt=receipt)

    def test_duplicate_cycle_repository_identity_fails_closed(self):
        receipt = cycle_receipt()
        receipt["repositories"].append(copy.deepcopy(receipt["repositories"][0]))
        with self.assertRaisesRegex(velocity.CashVelocityInputError, "repeats repository"):
            self.run_allocate(receipt=receipt)

    def test_cash_calibration_cannot_raise_probability(self):
        candidates = request_candidates()
        candidates[0]["estimated_win_probability"] = "0.5"
        bad = calibration(candidates, overrides={0: "0.6"})
        with self.assertRaisesRegex(velocity.CashVelocityInputError, "may not increase"):
            self.run_allocate(candidates=candidates, calibration_result=bad)

    def test_duplicate_calibration_index_fails_closed(self):
        candidates = request_candidates()
        bad = calibration(candidates)
        bad["calibration"].append(copy.deepcopy(bad["calibration"][0]))
        with self.assertRaisesRegex(velocity.CashVelocityInputError, "input_index is invalid"):
            self.run_allocate(candidates=candidates, calibration_result=bad)

    def test_individual_candidate_over_repo_cap_is_explicitly_excluded(self):
        candidates = [
            candidate("https://github.com/slow/repo/issues/1", "2000", "5"),
            candidate("https://github.com/fast/repo/issues/1", "800", "4"),
        ]
        result = self.run_allocate(candidates=candidates, calibration_result=calibration(candidates))
        excluded = {row["canonical_source_url"]: row["reason_code"] for row in result["portfolio"]["excluded"]}
        self.assertEqual(
            excluded["https://github.com/slow/repo/issues/1"],
            "EXCEEDS_REPO_VELOCITY_CAP",
        )

    def test_deadline_and_collision_constraints_remain_authoritative(self):
        candidates = [
            candidate("https://github.com/slow/repo/issues/1", "1000", "4", deadline="3"),
            candidate("https://github.com/slow/repo/issues/2", "900", "4", group="same"),
            candidate("https://github.com/fast/repo/issues/1", "800", "4", group="same"),
        ]
        result = self.run_allocate(candidates=candidates, calibration_result=calibration(candidates))
        self.assertEqual(result["portfolio"]["selected_count"], 1)
        self.assertEqual(
            result["portfolio"]["selected"][0]["canonical_source_url"],
            "https://github.com/slow/repo/issues/2",
        )

    def test_output_retains_strict_no_cash_no_fx_authority(self):
        result = self.run_allocate()
        authority = result["authority"]
        self.assertFalse(authority["currency_conversion"])
        self.assertFalse(authority["rtc_amount_used_in_usd_math"])
        self.assertFalse(authority["cash_recognition"])
        self.assertFalse(authority["revenue_recognition"])
        self.assertFalse(authority["claim_or_submission_authority"])
        self.assertFalse(authority["sponsor_or_maintainer_contact"])
        self.assertRegex(result["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_velocity_policy_is_exact_and_bounded(self):
        for raw in (
            {"schema_version": 1, "max_receipt_age_hours": 24},
            {"schema_version": 1, "max_receipt_age_hours": True, "slow_repo_max_capacity_fraction": "0.5"},
            {"schema_version": 1, "max_receipt_age_hours": 24, "slow_repo_max_capacity_fraction": "1.1"},
            {"schema_version": 1, "max_receipt_age_hours": 24, "slow_repo_max_capacity_fraction": 0.5},
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(velocity.CashVelocityInputError):
                    velocity._velocity_policy(raw)


if __name__ == "__main__":
    unittest.main()
