from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.portfolio_allocator import (
    PortfolioAllocationError,
    allocate_portfolio,
    allocate_ranked_portfolio,
    format_summary,
)


def ranked_row(
    rank: int,
    *,
    source: str | None = None,
    input_index: int | None = None,
    reward: str = "500",
    effort: str = "10",
    probability: str = "0.5",
    skill: str = "0.5",
):
    from decimal import Decimal, ROUND_HALF_UP

    if source is None:
        source = f"https://github.com/acme/widgets/issues/{rank}"
    if input_index is None:
        input_index = rank - 1
    reward_d = Decimal(reward)
    effort_d = Decimal(effort)
    probability_d = Decimal(probability)
    quantum = Decimal("0.000001")
    ev = (reward_d * probability_d).quantize(quantum, rounding=ROUND_HALF_UP)
    evh = (reward_d * probability_d / effort_d).quantize(
        quantum, rounding=ROUND_HALF_UP
    )

    def fmt(value):
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    return {
        "rank": rank,
        "input_index": input_index,
        "canonical_source_url": source,
        "advertised_reward_usd": reward,
        "estimated_win_probability": probability,
        "estimated_effort_hours": effort,
        "estimated_expected_value_usd": fmt(ev),
        "estimated_ev_per_hour_usd": fmt(evh),
        "skill_match": skill,
        "authority": {
            "eligibility": "canonical_intake_gate",
            "reward": "advertised_only",
            "probability_and_effort": "operator_estimates",
            "revenue": "not_earned_or_settled_by_this_receipt",
        },
    }


def ranking(*rows, excluded=None):
    excluded = excluded or []
    return {
        "schema": "qualified-opportunity-ranking/v1",
        "candidate_count": len(rows) + len(excluded),
        "ranked_count": len(rows),
        "excluded_count": len(excluded),
        "ranked": list(rows),
        "excluded": excluded,
        "authority": {
            "eligibility": "canonical_intake_gate",
            "reward": "advertised_only",
            "estimates": "operator_supplied",
            "cash_claim": False,
        },
    }


class PortfolioAllocatorTests(unittest.TestCase):
    def test_exact_portfolio_beats_greedy_rank(self):
        # Rank 1 has better EV/hour, but rows 2+3 jointly maximize EV in 8h.
        value = ranking(
            ranked_row(1, reward="600", probability="0.5", effort="6"),
            ranked_row(2, reward="400", probability="0.45", effort="4"),
            ranked_row(3, reward="400", probability="0.45", effort="4"),
        )
        result = allocate_ranked_portfolio(
            value, available_hours="8", max_active=2
        )
        self.assertEqual(
            [row["canonical_source_url"].rsplit("/", 1)[-1] for row in result["selected"]],
            ["2", "3"],
        )
        self.assertEqual(result["metrics"]["estimated_expected_value_usd_total"], "360")
        self.assertEqual(result["metrics"]["used_hours"], "8")

    def test_reserve_hours_reduces_allocatable_capacity(self):
        value = ranking(
            ranked_row(1, reward="800", effort="6", probability="0.5"),
            ranked_row(2, reward="500", effort="4", probability="0.5"),
        )
        result = allocate_ranked_portfolio(
            value,
            available_hours="10",
            reserve_hours="4",
            max_active=2,
        )
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["selected"][0]["rank"], 1)
        self.assertEqual(result["capacity"]["allocatable_hours"], "6")

    def test_max_active_is_hard_constraint(self):
        value = ranking(
            ranked_row(1, reward="600", effort="2"),
            ranked_row(2, reward="500", effort="2"),
            ranked_row(3, reward="400", effort="2"),
        )
        result = allocate_ranked_portfolio(
            value, available_hours="20", max_active=2
        )
        self.assertEqual([row["rank"] for row in result["selected"]], [1, 2])

    def test_minimum_skill_is_policy_exclusion(self):
        value = ranking(
            ranked_row(1, skill="0.2", reward="1000"),
            ranked_row(2, skill="0.9", reward="500"),
        )
        result = allocate_ranked_portfolio(
            value,
            available_hours="10",
            max_active=1,
            minimum_skill_match="0.8",
        )
        self.assertEqual(result["selected"][0]["rank"], 2)
        self.assertIn(
            {
                "input_index": 0,
                "canonical_source_url": "https://github.com/acme/widgets/issues/1",
                "reason_code": "BELOW_MINIMUM_SKILL",
            },
            result["not_selected"],
        )

    def test_zero_estimated_value_selects_nothing(self):
        value = ranking(ranked_row(1, probability="0", effort="1"))
        result = allocate_ranked_portfolio(
            value, available_hours="8", max_active=1
        )
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["metrics"]["used_hours"], "0")

    def test_equal_ev_prefers_lower_effort(self):
        value = ranking(
            ranked_row(1, reward="600", probability="0.5", effort="6"),
            ranked_row(2, reward="600", probability="0.5", effort="3"),
        )
        result = allocate_ranked_portfolio(
            value, available_hours="10", max_active=1
        )
        self.assertEqual(result["selected"][0]["rank"], 2)

    def test_full_tie_prefers_lexicographically_lower_source(self):
        value = ranking(
            ranked_row(1, source="https://github.com/acme/z/issues/1"),
            ranked_row(2, source="https://github.com/acme/a/issues/2"),
        )
        result = allocate_ranked_portfolio(
            value, available_hours="10", max_active=1
        )
        self.assertEqual(
            result["selected"][0]["canonical_source_url"],
            "https://github.com/acme/a/issues/2",
        )

    def test_ranker_exclusions_are_preserved_without_raw_fields(self):
        excluded = [
            {
                "input_index": 1,
                "canonical_source_url": "https://github.com/acme/x/issues/8",
                "reason_code": "INTAKE_NOT_ACTIONABLE",
                "private_extra": "DO NOT ECHO",
            }
        ]
        result = allocate_ranked_portfolio(
            ranking(ranked_row(1), excluded=excluded),
            available_hours="10",
            max_active=1,
        )
        self.assertEqual(
            result["intake_excluded"],
            [
                {
                    "input_index": 1,
                    "canonical_source_url": "https://github.com/acme/x/issues/8",
                    "reason_code": "INTAKE_NOT_ACTIONABLE",
                }
            ],
        )
        self.assertNotIn("DO NOT ECHO", repr(result))

    def test_tampered_expected_value_is_rejected(self):
        row = ranked_row(1)
        row["estimated_expected_value_usd"] = "999999"
        with self.assertRaisesRegex(
            PortfolioAllocationError, "does not match reward"
        ):
            allocate_ranked_portfolio(
                ranking(row), available_hours="10", max_active=1
            )

    def test_tampered_ev_per_hour_is_rejected(self):
        row = ranked_row(1)
        row["estimated_ev_per_hour_usd"] = "999"
        with self.assertRaisesRegex(
            PortfolioAllocationError, "does not match expected value"
        ):
            allocate_ranked_portfolio(
                ranking(row), available_hours="10", max_active=1
            )

    def test_authority_must_disclaim_cash(self):
        value = ranking(ranked_row(1))
        value["authority"]["cash_claim"] = True
        with self.assertRaisesRegex(PortfolioAllocationError, "disclaim cash"):
            allocate_ranked_portfolio(
                value, available_hours="10", max_active=1
            )

    def test_duplicate_source_and_index_fail_closed(self):
        first = ranked_row(1, source="https://github.com/acme/x/issues/1")
        second = ranked_row(
            2,
            source="https://github.com/acme/x/issues/1",
            input_index=5,
        )
        with self.assertRaisesRegex(PortfolioAllocationError, "duplicate canonical"):
            allocate_ranked_portfolio(
                ranking(first, second), available_hours="10", max_active=1
            )

        second = ranked_row(
            2,
            source="https://github.com/acme/x/issues/2",
            input_index=first["input_index"],
        )
        with self.assertRaisesRegex(PortfolioAllocationError, "duplicate input"):
            allocate_ranked_portfolio(
                ranking(first, second), available_hours="10", max_active=1
            )

    def test_resource_bound_fails_closed_instead_of_approximating(self):
        rows = [
            ranked_row(i + 1, source=f"https://github.com/acme/x/issues/{i+1}")
            for i in range(35)
        ]
        with self.assertRaisesRegex(PortfolioAllocationError, "resource bound"):
            allocate_ranked_portfolio(
                ranking(*rows), available_hours="1000", max_active=8
            )

    def test_extreme_decimal_precision_is_rejected(self):
        value = ranking(ranked_row(1))
        with self.assertRaisesRegex(PortfolioAllocationError, "bounded decimal precision"):
            allocate_ranked_portfolio(
                value, available_hours="1e-1000000", max_active=1
            )

        row = ranked_row(1)
        row["advertised_reward_usd"] = "1e1000000"
        with self.assertRaisesRegex(PortfolioAllocationError, "bounded decimal precision"):
            allocate_ranked_portfolio(
                ranking(row), available_hours="10", max_active=1
            )

    def test_receipt_indices_must_cover_candidate_batch_exactly(self):
        row = ranked_row(1, input_index=9)
        with self.assertRaisesRegex(PortfolioAllocationError, "cover the candidate batch"):
            allocate_ranked_portfolio(
                ranking(row), available_hours="10", max_active=1
            )

    def test_capacity_input_validation(self):
        value = ranking(ranked_row(1))
        bad = [
            {"available_hours": "0", "max_active": 1},
            {"available_hours": "10", "reserve_hours": "11", "max_active": 1},
            {"available_hours": "10", "max_active": 0},
            {"available_hours": "10", "max_active": True},
            {
                "available_hours": "10",
                "max_active": 1,
                "minimum_skill_match": "1.1",
            },
        ]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(PortfolioAllocationError):
                    allocate_ranked_portfolio(value, **kwargs)

    def test_all_capacity_may_be_reserved_explicitly(self):
        result = allocate_ranked_portfolio(
            ranking(ranked_row(1)),
            available_hours="10",
            reserve_hours="10",
            max_active=1,
        )
        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["capacity"]["allocatable_hours"], "0")

    def test_summary_disclaims_earned_and_settled_money(self):
        result = allocate_ranked_portfolio(
            ranking(ranked_row(1)),
            available_hours="10",
            max_active=1,
        )
        summary = format_summary(result)
        self.assertIn("earned_revenue_claim=false", summary)
        self.assertIn("settled_cash_claim=false", summary)
        self.assertFalse(result["authority"]["earned_revenue_claim"])
        self.assertFalse(result["authority"]["settled_cash_claim"])

    @patch("concierge.opportunity_ranker.rank_opportunities")
    def test_high_level_wrapper_uses_canonical_ranker(self, mock_rank):
        mock_rank.return_value = ranking(ranked_row(1))
        result = allocate_portfolio(
            [{"snapshot": {"x": 1}}],
            ["python"],
            available_hours="10",
            max_active=1,
            saturation_threshold=7,
        )
        mock_rank.assert_called_once_with(
            [{"snapshot": {"x": 1}}],
            ["python"],
            saturation_threshold=7,
        )
        self.assertEqual(result["selected_count"], 1)


if __name__ == "__main__":
    unittest.main()
