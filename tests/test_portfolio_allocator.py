from __future__ import annotations

import unittest
from typing import Optional
from unittest.mock import patch

from concierge.portfolio_allocator import (
    PortfolioInputError,
    allocate_portfolio,
    format_summary,
)


def row(
    index: int,
    *,
    reward: str,
    effort: str,
    probability: str = "1",
    skill: str = "0.5",
    rank: Optional[int] = None,
):
    if rank is None:
        rank = index + 1
    return {
        "rank": rank,
        "input_index": index,
        "canonical_source_url": f"https://github.com/acme/widgets/issues/{index + 1}",
        "advertised_reward_usd": reward,
        "estimated_win_probability": probability,
        "estimated_effort_hours": effort,
        "estimated_expected_value_usd": reward,
        "estimated_ev_per_hour_usd": "1",
        "skill_match": skill,
        "authority": {
            "eligibility": "canonical_intake_gate",
            "reward": "advertised_only",
            "probability_and_effort": "operator_estimates",
            "revenue": "not_earned_or_settled_by_this_receipt",
        },
    }


def candidate(deadline: str, *, group: Optional[str] = None, secret: str = ""):
    value = {
        "hours_until_deadline": deadline,
        "snapshot": {"body": secret},
    }
    if group is not None:
        value["collision_group"] = group
    return value


def ranked_result(rows, excluded=None):
    return {
        "schema": "qualified-opportunity-ranking/v1",
        "ranked_count": len(rows),
        "excluded_count": len(excluded or []),
        "ranked": rows,
        "excluded": excluded or [],
        "authority": {"cash_claim": False},
    }


class PortfolioAllocatorTests(unittest.TestCase):
    def test_exact_optimizer_beats_greedy_ev_per_hour_choice(self):
        rows = [
            row(0, reward="12", effort="6", rank=1),
            row(1, reward="9.5", effort="5", rank=2),
            row(2, reward="9.5", effort="5", rank=3),
        ]
        values = [candidate("10"), candidate("10"), candidate("10")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "10")

        self.assertEqual(result["selected_count"], 2)
        self.assertEqual(
            [item["input_index"] for item in result["selected"]], [1, 2]
        )
        self.assertEqual(result["estimated_portfolio_expected_value_usd"], "19")
        self.assertEqual(result["selected_effort_hours"], "10")

    def test_cumulative_earliest_deadline_feasibility_is_enforced(self):
        rows = [
            row(0, reward="10", effort="4"),
            row(1, reward="11", effort="4"),
        ]
        values = [candidate("4"), candidate("5")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "8")

        self.assertEqual([item["input_index"] for item in result["selected"]], [1])
        self.assertEqual(result["selected_effort_hours"], "4")
        self.assertEqual(result["estimated_portfolio_expected_value_usd"], "11")

    def test_collision_group_is_a_hard_mutual_exclusion(self):
        rows = [
            row(0, reward="20", effort="5"),
            row(1, reward="18", effort="4"),
            row(2, reward="7", effort="3"),
        ]
        values = [
            candidate("12", group="same-repo"),
            candidate("12", group="same-repo"),
            candidate("12", group="other"),
        ]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "7")

        self.assertEqual(
            [item["input_index"] for item in result["selected"]], [1, 2]
        )
        self.assertEqual(result["estimated_portfolio_expected_value_usd"], "25")

    def test_missing_collision_group_defaults_to_unique_source(self):
        rows = [row(0, reward="5", effort="1"), row(1, reward="6", effort="1")]
        values = [candidate("5"), candidate("5")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "2")

        self.assertEqual(result["selected_count"], 2)
        self.assertNotEqual(
            result["selected"][0]["collision_group"],
            result["selected"][1]["collision_group"],
        )

    def test_invalid_deadline_fails_closed_per_candidate(self):
        rows = [row(0, reward="5", effort="1")]
        values = [candidate("NaN")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "2")

        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(
            result["portfolio_excluded"][0]["reason_code"],
            "PORTFOLIO_CONSTRAINT_INVALID",
        )

    def test_pathological_capacity_uses_canonical_decimal_bounds(self):
        with patch("concierge.portfolio_allocator.rank_opportunities") as mocked_ranker:
            for capacity in ("1e-999999999", "1e999999999", "0e-999999999"):
                with self.subTest(capacity=capacity):
                    with self.assertRaises(PortfolioInputError):
                        allocate_portfolio([], ["python"], capacity)
        mocked_ranker.assert_not_called()

    def test_pathological_deadline_is_candidate_local_failure(self):
        rows = [
            row(0, reward="5", effort="1"),
            row(1, reward="6", effort="1"),
        ]
        values = [candidate("1e999999999"), candidate("4")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "4")

        self.assertEqual([item["input_index"] for item in result["selected"]], [1])
        self.assertEqual(result["portfolio_excluded"][0]["input_index"], 0)
        self.assertEqual(
            result["portfolio_excluded"][0]["reason_code"],
            "PORTFOLIO_CONSTRAINT_INVALID",
        )

    def test_ranker_boundary_receipts_remain_exact_downstream(self):
        reward = "1" + ("0" * 64)
        effort = "0." + ("0" * 63) + "1"
        rows = [row(0, reward=reward, effort=effort, probability="1")]
        values = [candidate("1")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "1")

        self.assertEqual(result["selected_count"], 1)
        selected = result["selected"][0]
        self.assertEqual(selected["advertised_reward_usd"], reward)
        self.assertEqual(selected["estimated_effort_hours"], effort)
        self.assertEqual(selected["estimated_expected_value_usd"], reward)
        self.assertEqual(selected["estimated_ev_per_hour_usd"], "1" + ("0" * 128))

    def test_candidate_that_cannot_finish_by_own_deadline_is_excluded(self):
        rows = [row(0, reward="50", effort="5")]
        values = [candidate("4")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "10")

        self.assertEqual(result["selected"], [])
        self.assertEqual(
            result["portfolio_excluded"][0]["reason_code"],
            "DEADLINE_INFEASIBLE_ALONE",
        )

    def test_float_capacity_is_rejected(self):
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result([]),
        ):
            with self.assertRaises(PortfolioInputError):
                allocate_portfolio([], ["python"], 4.5)  # type: ignore[arg-type]

    def test_exact_search_fails_closed_above_bound(self):
        rows = [row(i, reward="1", effort="1") for i in range(21)]
        values = [candidate("100") for _ in rows]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            with self.assertRaises(PortfolioInputError):
                allocate_portfolio(values, ["python"], "100")

    def test_ranker_exclusions_are_preserved(self):
        exclusion = {
            "input_index": 9,
            "canonical_source_url": None,
            "reason_code": "INTAKE_NOT_ACTIONABLE",
        }
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result([], [exclusion]),
        ):
            result = allocate_portfolio([], ["python"], "8")

        self.assertEqual(result["ranking_excluded"], [exclusion])
        self.assertFalse(result["authority"]["cash_claim"])

    def test_output_never_echoes_raw_snapshot_text(self):
        rows = [row(0, reward="100", effort="2")]
        values = [candidate("4", secret="SUPER SECRET BOUNTY BODY")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "4")

        self.assertNotIn("SUPER SECRET BOUNTY BODY", repr(result))
        self.assertEqual(
            result["selected"][0]["authority"]["revenue"],
            "not_earned_or_settled_by_this_receipt",
        )

    def test_tie_break_prefers_less_effort(self):
        rows = [
            row(0, reward="10", effort="5", skill="0.5"),
            row(1, reward="10", effort="4", skill="0.5"),
        ]
        values = [
            candidate("10", group="exclusive"),
            candidate("10", group="exclusive"),
        ]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "10")

        self.assertEqual([item["input_index"] for item in result["selected"]], [1])

    def test_summary_disclaims_cash_claim(self):
        rows = [row(0, reward="10", effort="2")]
        values = [candidate("3")]
        with patch(
            "concierge.portfolio_allocator.rank_opportunities",
            return_value=ranked_result(rows),
        ):
            result = allocate_portfolio(values, ["python"], "2")
        self.assertIn("cash_claim=false", format_summary(result))


class PortfolioAllocatorIntegrationTests(unittest.TestCase):
    def test_real_ranker_intake_path_selects_actionable_bounty(self):
        issue_url = "https://github.com/acme/widgets/issues/17"
        value = {
            "snapshot": {
                "listing_url": issue_url,
                "reward_evidence_urls": [issue_url],
                "title": "Python parser repair",
                "body": "/bounty $500\nSECRET ACCEPTANCE TEXT",
                "labels": ["$500"],
                "attempt_count": 0,
                "canonical_audit": {
                    "repo": "acme/widgets",
                    "number": 17,
                    "issue_url": issue_url,
                    "issue_state": "open",
                    "open_pr_count": 0,
                    "stale_listing_signal": False,
                    "search_truncated": False,
                },
            },
            "estimated_effort_hours": "8",
            "estimated_win_probability": "0.4",
            "hours_until_deadline": "24",
        }
        result = allocate_portfolio([value], ["python"], "8")
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["selected"][0]["advertised_reward_usd"], "500")
        self.assertEqual(
            result["estimated_portfolio_expected_value_usd"], "200"
        )
        self.assertNotIn("SECRET ACCEPTANCE TEXT", repr(result))


if __name__ == "__main__":
    unittest.main()
