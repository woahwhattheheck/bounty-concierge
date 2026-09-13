from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.opportunity_ranker import (
    OpportunityRankInputError,
    format_summary,
    rank_opportunities,
)


def intake(
    source: str,
    *,
    reward: str = "500",
    disposition: str = "ACTIONABLE",
    dispatch: bool | None = None,
    reasons: list[str] | None = None,
):
    if dispatch is None:
        dispatch = disposition == "ACTIONABLE"
    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "canonical_source_url": source,
        "reason_codes": reasons or [],
        "qualification": {
            "disposition": disposition,
            "dispatch": dispatch,
            "signals": {
                "advertised_reward_usd": [reward] if reward else [],
                "live_label_reward_usd": [reward] if reward else [],
            },
        },
        "provenance": {
            "disposition": disposition,
            "dispatch": dispatch,
            "signals": {},
        },
    }


def candidate(
    number: int,
    *,
    reward: str = "500",
    effort: str = "10",
    probability: str = "0.5",
    skill: float = 0.5,
):
    return {
        "snapshot": {
            "marker": number,
            "title": "python parser bounty",
            "_skill": skill,
            "_reward": reward,
        },
        "estimated_effort_hours": effort,
        "estimated_win_probability": probability,
    }


class OpportunityRankerTests(unittest.TestCase):
    def setUp(self):
        self.intake_patch = patch(
            "concierge.opportunity_ranker.qualify_revenue_intake"
        )
        self.skill_patch = patch("concierge.opportunity_ranker.match_skills")
        self.mock_intake = self.intake_patch.start()
        self.mock_skill = self.skill_patch.start()
        self.addCleanup(self.intake_patch.stop)
        self.addCleanup(self.skill_patch.stop)

        self.mock_intake.side_effect = lambda snapshot, **_: intake(
            f"https://github.com/acme/widgets/issues/{snapshot['marker']}",
            reward=snapshot.get("_reward", "500"),
        )
        self.mock_skill.side_effect = lambda snapshot, _skills: snapshot.get(
            "_skill", 0
        )

    def test_ev_per_hour_beats_larger_raw_reward(self):
        first = candidate(1, reward="1000", effort="40", probability="0.5")
        second = candidate(2, reward="600", effort="10", probability="0.5")
        result = rank_opportunities([first, second], ["python"])
        self.assertEqual(
            [row["canonical_source_url"] for row in result["ranked"]],
            [
                "https://github.com/acme/widgets/issues/2",
                "https://github.com/acme/widgets/issues/1",
            ],
        )
        self.assertEqual(result["ranked"][0]["estimated_ev_per_hour_usd"], "30")
        self.assertEqual(result["ranked"][1]["estimated_ev_per_hour_usd"], "12.5")

    def test_hold_candidate_is_excluded_not_scored(self):
        self.mock_intake.side_effect = lambda snapshot, **_: intake(
            "https://github.com/acme/widgets/issues/1",
            disposition="HOLD",
            dispatch=False,
            reasons=["SATURATED_COMPETITION"],
        )
        result = rank_opportunities([candidate(1)], ["python"])
        self.assertEqual(result["ranked"], [])
        self.assertEqual(result["excluded"][0]["reason_code"], "INTAKE_NOT_ACTIONABLE")
        self.assertEqual(
            result["excluded"][0]["intake_reason_codes"],
            ["SATURATED_COMPETITION"],
        )

    def test_duplicate_canonical_issue_fails_closed(self):
        self.mock_intake.side_effect = lambda snapshot, **_: intake(
            "https://github.com/acme/widgets/issues/17",
            reward=snapshot.get("_reward", "500"),
        )
        result = rank_opportunities([candidate(1), candidate(2)], ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            [row["reason_code"] for row in result["excluded"]],
            ["DUPLICATE_CANONICAL_SOURCE", "DUPLICATE_CANONICAL_SOURCE"],
        )

    def test_skill_match_is_tiebreak_not_cash_authority(self):
        result = rank_opportunities(
            [candidate(1, skill=0.25), candidate(2, skill=1.0)], ["python"]
        )
        self.assertEqual(
            result["ranked"][0]["canonical_source_url"].rsplit("/", 1)[-1], "2"
        )
        self.assertEqual(
            result["ranked"][0]["authority"]["probability_and_effort"],
            "operator_estimates",
        )
        self.assertEqual(
            result["ranked"][0]["authority"]["revenue"],
            "not_earned_or_settled_by_this_receipt",
        )

    def test_invalid_estimates_fail_closed_per_candidate(self):
        values = [
            candidate(1, effort="0"),
            candidate(2, effort="-1"),
            candidate(3, probability="-0.01"),
            candidate(4, probability="1.01"),
            candidate(5, probability="NaN"),
        ]
        result = rank_opportunities(values, ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(result["excluded_count"], 5)
        self.assertTrue(
            all(
                row["reason_code"] == "ESTIMATE_OR_REWARD_INVALID"
                for row in result["excluded"]
            )
        )

    def test_float_estimates_are_rejected_to_avoid_binary_money_math(self):
        value = candidate(1)
        value["estimated_win_probability"] = 0.5
        result = rank_opportunities([value], ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            result["excluded"][0]["reason_code"], "ESTIMATE_OR_REWARD_INVALID"
        )

    def test_zero_reward_is_not_ranked(self):
        result = rank_opportunities([candidate(1, reward="0")], ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            result["excluded"][0]["reason_code"], "ESTIMATE_OR_REWARD_INVALID"
        )

    def test_conflicting_authorized_reward_signals_are_not_ranked(self):
        self.mock_intake.side_effect = lambda snapshot, **_: {
            **intake("https://github.com/acme/widgets/issues/1", reward="500"),
            "qualification": {
                "disposition": "ACTIONABLE",
                "dispatch": True,
                "signals": {
                    "advertised_reward_usd": ["500"],
                    "live_label_reward_usd": ["600"],
                },
            },
        }
        result = rank_opportunities([candidate(1)], ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            result["excluded"][0]["reason_code"], "ESTIMATE_OR_REWARD_INVALID"
        )

    def test_result_does_not_echo_raw_snapshot_text(self):
        value = candidate(1)
        value["snapshot"]["body"] = "SUPER SECRET ISSUE BODY"
        value["snapshot"]["comments"] = ["SUPER SECRET COMMENT"]
        result = rank_opportunities([value], ["python"])
        rendered = repr(result)
        self.assertNotIn("SUPER SECRET ISSUE BODY", rendered)
        self.assertNotIn("SUPER SECRET COMMENT", rendered)

    def test_canonical_url_breaks_full_tie_stably(self):
        result = rank_opportunities([candidate(20), candidate(3)], ["python"])
        self.assertEqual(
            [row["canonical_source_url"].rsplit("/", 1)[-1] for row in result["ranked"]],
            ["20", "3"],
        )

    def test_summary_explicitly_disclaims_cash_claim(self):
        result = rank_opportunities([candidate(1)], ["python"])
        self.assertIn("cash_claim=false", format_summary(result))
        self.assertFalse(result["authority"]["cash_claim"])

    def test_skills_and_batch_shape_fail_closed(self):
        with self.assertRaises(OpportunityRankInputError):
            rank_opportunities("not-a-list", ["python"])  # type: ignore[arg-type]
        with self.assertRaises(OpportunityRankInputError):
            rank_opportunities([], [""])


class OpportunityRankerIntegrationTests(unittest.TestCase):
    def test_real_intake_gate_authorizes_one_canonical_bounty(self):
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
        }
        result = rank_opportunities([value], ["python"])
        self.assertEqual(result["ranked_count"], 1)
        self.assertEqual(result["ranked"][0]["advertised_reward_usd"], "500")
        self.assertEqual(result["ranked"][0]["estimated_expected_value_usd"], "200")
        self.assertEqual(result["ranked"][0]["estimated_ev_per_hour_usd"], "25")
        self.assertNotIn("SECRET ACCEPTANCE TEXT", repr(result))


if __name__ == "__main__":
    unittest.main()
