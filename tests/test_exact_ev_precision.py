from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.opportunity_ranker import rank_opportunities
from concierge.portfolio_allocator import allocate_portfolio


_HIGH_PROBABILITY = "0.50000000000000000000000000001"
_HIGH_EFFORT = "0.50000000000000000000000000001"


def _intake(source: str, reward: str = "1") -> dict:
    return {
        "disposition": "ACTIONABLE",
        "dispatch": True,
        "canonical_source_url": source,
        "reason_codes": [],
        "qualification": {
            "disposition": "ACTIONABLE",
            "dispatch": True,
            "signals": {
                "advertised_reward_usd": [reward],
                "live_label_reward_usd": [reward],
            },
        },
        "provenance": {
            "disposition": "ACTIONABLE",
            "dispatch": True,
            "signals": {},
        },
    }


def _rank_candidate(marker: int, probability: str) -> dict:
    return {
        "snapshot": {"marker": marker, "_reward": "1"},
        "estimated_effort_hours": "1",
        "estimated_win_probability": probability,
    }


def _row(
    index: int,
    *,
    reward: str = "1",
    effort: str = "1",
    probability: str = "1",
) -> dict:
    return {
        "rank": index + 1,
        "input_index": index,
        "canonical_source_url": f"https://github.com/acme/widgets/issues/{index + 1}",
        "advertised_reward_usd": reward,
        "estimated_win_probability": probability,
        "estimated_effort_hours": effort,
        "estimated_expected_value_usd": reward,
        "estimated_ev_per_hour_usd": reward,
        "skill_match": "0.5",
        "authority": {
            "eligibility": "canonical_intake_gate",
            "reward": "advertised_only",
            "probability_and_effort": "operator_estimates",
            "revenue": "not_earned_or_settled_by_this_receipt",
        },
    }


def _ranked(rows: list[dict]) -> dict:
    return {
        "schema": "qualified-opportunity-ranking/v1",
        "ranked_count": len(rows),
        "excluded_count": 0,
        "ranked": rows,
        "excluded": [],
        "authority": {"cash_claim": False},
    }


def _portfolio_candidate(deadline: str, group: str) -> dict:
    return {
        "hours_until_deadline": deadline,
        "collision_group": group,
        "snapshot": {},
    }


class ExactEVPrecisionTests(unittest.TestCase):
    def test_ranker_uses_exact_values_beyond_decimal_context_precision(self):
        lower = _rank_candidate(1, "0.5")
        higher = _rank_candidate(9, _HIGH_PROBABILITY)

        with patch(
            "concierge.opportunity_ranker.qualify_revenue_intake",
            side_effect=lambda snapshot, **_: _intake(
                f"https://github.com/acme/widgets/issues/{snapshot['marker']}"
            ),
        ), patch(
            "concierge.opportunity_ranker.match_skills",
            return_value=0.5,
        ):
            result = rank_opportunities([lower, higher], ["python"])

        self.assertEqual(
            [row["canonical_source_url"].rsplit("/", 1)[-1] for row in result["ranked"]],
            ["9", "1"],
        )
        # Public metrics intentionally remain six-decimal display values. The
        # exact hidden ordering authority must not be inferred from display ties.
        self.assertEqual(
            [row["estimated_expected_value_usd"] for row in result["ranked"]],
            ["0.5", "0.5"],
        )
        self.assertEqual(
            [row["estimated_ev_per_hour_usd"] for row in result["ranked"]],
            ["0.5", "0.5"],
        )

    def test_allocator_uses_exact_ev_inside_collision_group(self):
        rows = [
            _row(0, probability="0.5"),
            _row(1, probability=_HIGH_PROBABILITY),
        ]
        candidates = [
            _portfolio_candidate("2", "exclusive"),
            _portfolio_candidate("2", "exclusive"),
        ]

        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=_ranked(rows),
        ):
            result = allocate_portfolio(candidates, ["python"], "1")

        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["selected"][0]["input_index"], 1)
        self.assertEqual(
            result["selected"][0]["estimated_expected_value_usd"], "0.5"
        )

    def test_allocator_never_rounds_over_capacity_sum_into_feasibility(self):
        rows = [
            _row(0, effort=_HIGH_EFFORT),
            _row(1, effort="0.5"),
        ]
        candidates = [
            _portfolio_candidate("2", "first"),
            _portfolio_candidate("2", "second"),
        ]

        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=_ranked(rows),
        ):
            result = allocate_portfolio(candidates, ["python"], "1")

        self.assertEqual(result["selected_count"], 1)
        self.assertEqual([item["input_index"] for item in result["selected"]], [1])
        self.assertEqual(result["selected_effort_hours"], "0.5")
        self.assertEqual(result["remaining_capacity_hours"], "0.5")
        self.assertEqual(result["estimated_portfolio_expected_value_usd"], "1")


if __name__ == "__main__":
    unittest.main()
