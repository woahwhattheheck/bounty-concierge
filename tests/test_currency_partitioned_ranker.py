from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.currency_partitioned_ranker import (
    CurrencyPartitionedRankInputError,
    _strict_json_bytes,
    format_summary,
    rank_partitioned_opportunities,
)


def intake(
    source: str,
    *,
    currency: str = "USD",
    reward: str = "500",
    disposition: str = "ACTIONABLE",
    dispatch: bool | None = None,
    reasons: list[str] | None = None,
):
    if dispatch is None:
        dispatch = disposition == "ACTIONABLE"
    signals = {
        "advertised_reward_usd": [],
        "live_label_reward_usd": [],
        "advertised_reward_rtc": [],
        "live_label_reward_rtc": [],
    }
    if reward:
        suffix = currency.casefold()
        signals[f"advertised_reward_{suffix}"] = [reward]
        signals[f"live_label_reward_{suffix}"] = [reward]
    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "canonical_source_url": source,
        "reason_codes": reasons or [],
        "qualification": {
            "disposition": disposition,
            "dispatch": dispatch,
            "signals": signals,
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
    currency: str = "USD",
    reward: str = "500",
    effort: object = "10",
    probability: object = "0.5",
    skill: object = 0.5,
):
    return {
        "snapshot": {
            "marker": number,
            "title": "python parser bounty",
            "_currency": currency,
            "_reward": reward,
            "_skill": skill,
        },
        "estimated_effort_hours": effort,
        "estimated_win_probability": probability,
    }


class CurrencyPartitionedRankerTests(unittest.TestCase):
    def setUp(self):
        self.intake_patch = patch(
            "concierge.currency_partitioned_ranker.qualify_revenue_intake"
        )
        self.skill_patch = patch(
            "concierge.currency_partitioned_ranker.match_skills"
        )
        self.mock_intake = self.intake_patch.start()
        self.mock_skill = self.skill_patch.start()
        self.addCleanup(self.intake_patch.stop)
        self.addCleanup(self.skill_patch.stop)

        self.mock_intake.side_effect = lambda snapshot, **_: intake(
            f"https://github.com/acme/widgets/issues/{snapshot['marker']}",
            currency=snapshot.get("_currency", "USD"),
            reward=snapshot.get("_reward", "500"),
        )
        self.mock_skill.side_effect = lambda snapshot, _skills: snapshot.get(
            "_skill", 0
        )

    def test_native_rtc_happy_path(self):
        result = rank_partitioned_opportunities(
            [
                candidate(
                    1,
                    currency="RTC",
                    reward="30",
                    effort="3",
                    probability="0.5",
                )
            ],
            ["python"],
        )
        self.assertEqual(result["ranked_count"], 1)
        self.assertEqual(result["partitions"]["USD"]["ranked"], [])
        row = result["partitions"]["RTC"]["ranked"][0]
        self.assertEqual(row["currency"], "RTC")
        self.assertEqual(row["advertised_reward"], "30")
        self.assertEqual(row["estimated_expected_value"], "15")
        self.assertEqual(row["estimated_ev_per_hour"], "5")
        self.assertFalse(row["authority"]["fx_conversion"])

    def test_mixed_request_is_partitioned_without_global_winner(self):
        result = rank_partitioned_opportunities(
            [
                candidate(1, currency="USD", reward="1000000", effort="1"),
                candidate(2, currency="RTC", reward="1", effort="100"),
            ],
            ["python"],
        )
        self.assertEqual(result["ranked_count"], 2)
        self.assertNotIn("ranked", result)
        self.assertEqual(result["partitions"]["USD"]["ranked_count"], 1)
        self.assertEqual(result["partitions"]["RTC"]["ranked_count"], 1)
        self.assertFalse(result["authority"]["fx_conversion"])
        self.assertFalse(result["authority"]["cross_currency_ranking"])

    def test_each_currency_partition_has_independent_exact_order(self):
        result = rank_partitioned_opportunities(
            [
                candidate(1, currency="USD", reward="100", effort="10", probability="0.5"),
                candidate(2, currency="USD", reward="80", effort="2", probability="0.5"),
                candidate(3, currency="RTC", reward="30", effort="12", probability="0.5"),
                candidate(4, currency="RTC", reward="20", effort="2", probability="0.5"),
            ],
            ["python"],
        )
        self.assertEqual(
            [row["input_index"] for row in result["partitions"]["USD"]["ranked"]],
            [1, 0],
        )
        self.assertEqual(
            [row["input_index"] for row in result["partitions"]["RTC"]["ranked"]],
            [3, 2],
        )

    def test_duplicate_canonical_source_is_global_across_currencies(self):
        self.mock_intake.side_effect = lambda snapshot, **_: intake(
            "https://github.com/acme/widgets/issues/17",
            currency=snapshot["_currency"],
            reward=snapshot["_reward"],
        )
        result = rank_partitioned_opportunities(
            [
                candidate(1, currency="USD", reward="50"),
                candidate(2, currency="RTC", reward="50"),
            ],
            ["python"],
        )
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            [row["reason_code"] for row in result["excluded"]],
            ["DUPLICATE_CANONICAL_SOURCE", "DUPLICATE_CANONICAL_SOURCE"],
        )

    def test_dual_currency_actionable_evidence_is_rejected(self):
        def dual(snapshot, **_):
            value = intake(
                "https://github.com/acme/widgets/issues/1",
                currency="USD",
                reward="100",
            )
            value["qualification"]["signals"]["advertised_reward_rtc"] = ["20"]
            return value

        self.mock_intake.side_effect = dual
        result = rank_partitioned_opportunities([candidate(1)], ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            result["excluded"][0]["reason_code"], "REWARD_CURRENCY_INVALID"
        )

    def test_conflicting_rtc_reward_authority_is_rejected(self):
        def conflicting(snapshot, **_):
            value = intake(
                "https://github.com/acme/widgets/issues/1",
                currency="RTC",
                reward="30",
            )
            value["qualification"]["signals"]["live_label_reward_rtc"] = ["40"]
            return value

        self.mock_intake.side_effect = conflicting
        result = rank_partitioned_opportunities(
            [candidate(1, currency="RTC")],
            ["python"],
        )
        self.assertEqual(
            result["excluded"][0]["reason_code"], "REWARD_CURRENCY_INVALID"
        )

    def test_intake_hold_is_propagated_not_scored(self):
        self.mock_intake.side_effect = lambda snapshot, **_: intake(
            "https://github.com/acme/widgets/issues/1",
            currency="RTC",
            reward="",
            disposition="HOLD",
            dispatch=False,
            reasons=["AMBIGUOUS_ADVERTISED_REWARD"],
        )
        result = rank_partitioned_opportunities(
            [candidate(1, currency="RTC")],
            ["python"],
        )
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(
            result["excluded"][0]["reason_code"], "INTAKE_NOT_ACTIONABLE"
        )
        self.assertEqual(
            result["excluded"][0]["intake_reason_codes"],
            ["AMBIGUOUS_ADVERTISED_REWARD"],
        )

    def test_invalid_operator_estimates_fail_candidate_locally(self):
        values = [
            candidate(1, currency="RTC", effort="0"),
            candidate(2, currency="RTC", effort="-1"),
            candidate(3, currency="RTC", probability="-0.01"),
            candidate(4, currency="RTC", probability="1.01"),
            candidate(5, currency="RTC", probability="NaN"),
            candidate(6, currency="RTC", probability=0.5),
            candidate(7, currency="RTC", effort=True),
        ]
        result = rank_partitioned_opportunities(values, ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(result["excluded_count"], len(values))
        self.assertTrue(
            all(row["reason_code"] == "ESTIMATE_INVALID" for row in result["excluded"])
        )

    def test_pathological_and_huge_exact_decimal_behavior_matches_usd_ranker(self):
        result = rank_partitioned_opportunities(
            [
                candidate(1, currency="RTC", effort="1e-999999999"),
                candidate(
                    2,
                    currency="RTC",
                    reward="1e64",
                    effort="1e-64",
                    probability="1",
                ),
            ],
            ["python"],
        )
        self.assertEqual(result["excluded"][0]["reason_code"], "ESTIMATE_INVALID")
        row = result["partitions"]["RTC"]["ranked"][0]
        self.assertEqual(row["advertised_reward"], "1" + ("0" * 64))
        self.assertEqual(
            row["estimated_effort_hours"], "0." + ("0" * 63) + "1"
        )
        self.assertEqual(
            row["estimated_ev_per_hour"], "1" + ("0" * 128)
        )

    def test_skill_score_is_tiebreak_only_and_invalid_score_fails_closed(self):
        result = rank_partitioned_opportunities(
            [
                candidate(1, currency="RTC", skill="0.25"),
                candidate(2, currency="RTC", skill="1"),
            ],
            ["python"],
        )
        self.assertEqual(
            [row["input_index"] for row in result["partitions"]["RTC"]["ranked"]],
            [1, 0],
        )
        self.mock_skill.side_effect = lambda *_: "NaN"
        denied = rank_partitioned_opportunities(
            [candidate(3, currency="RTC")], ["python"]
        )
        self.assertEqual(
            denied["excluded"][0]["reason_code"], "SKILL_SCORE_INVALID"
        )

    def test_full_tie_uses_canonical_url_stably(self):
        result = rank_partitioned_opportunities(
            [
                candidate(20, currency="RTC"),
                candidate(3, currency="RTC"),
            ],
            ["python"],
        )
        self.assertEqual(
            [
                row["canonical_source_url"].rsplit("/", 1)[-1]
                for row in result["partitions"]["RTC"]["ranked"]
            ],
            ["20", "3"],
        )

    def test_raw_snapshot_text_is_not_echoed(self):
        value = candidate(1, currency="RTC")
        value["snapshot"]["body"] = "SUPER SECRET ACCEPTANCE TEXT"
        value["snapshot"]["comments"] = ["SUPER SECRET COMMENT"]
        result = rank_partitioned_opportunities([value], ["python"])
        rendered = repr(result)
        self.assertNotIn("SUPER SECRET ACCEPTANCE TEXT", rendered)
        self.assertNotIn("SUPER SECRET COMMENT", rendered)

    def test_empty_request_is_valid_and_structural_malformed_inputs_raise(self):
        result = rank_partitioned_opportunities([], ["python"])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(result["excluded_count"], 0)
        with self.assertRaises(CurrencyPartitionedRankInputError):
            rank_partitioned_opportunities("not-a-list", ["python"])  # type: ignore[arg-type]
        with self.assertRaises(CurrencyPartitionedRankInputError):
            rank_partitioned_opportunities([], [""])
        with self.assertRaises(CurrencyPartitionedRankInputError):
            rank_partitioned_opportunities([], ["python"], saturation_threshold=True)  # type: ignore[arg-type]

    def test_strict_json_rejects_duplicate_keys_and_nonstandard_numbers(self):
        with self.assertRaises(CurrencyPartitionedRankInputError):
            _strict_json_bytes(
                b'{"candidates":[],"candidates":[],"skills":[]}',
                source="test",
            )
        with self.assertRaises(CurrencyPartitionedRankInputError):
            _strict_json_bytes(
                b'{"candidates":[],"skills":[],"x":NaN}',
                source="test",
            )

    def test_summary_makes_no_fx_cash_or_cross_currency_claim(self):
        result = rank_partitioned_opportunities(
            [candidate(1, currency="RTC")],
            ["python"],
        )
        summary = format_summary(result)
        self.assertIn("rtc_ranked=1", summary)
        self.assertIn("fx_conversion=false", summary)
        self.assertIn("cross_currency_ranking=false", summary)
        self.assertIn("cash_claim=false", summary)


class CurrencyPartitionedRankerIntegrationTests(unittest.TestCase):
    def _snapshot(self, number: int, title: str, body: str = ""):
        issue_url = f"https://github.com/acme/widgets/issues/{number}"
        return {
            "listing_url": issue_url,
            "reward_evidence_urls": [issue_url],
            "title": title,
            "body": body,
            "labels": ["bounty"],
            "attempt_count": 0,
            "canonical_audit": {
                "repo": "acme/widgets",
                "number": number,
                "issue_url": issue_url,
                "issue_state": "open",
                "open_pr_count": 0,
                "stale_listing_signal": False,
                "search_truncated": False,
            },
        }

    def test_real_intake_gate_ranks_native_rtc_without_usd_conversion(self):
        value = {
            "snapshot": self._snapshot(
                17,
                "[BOUNTY: 30 RTC] Python parser repair",
                "Implement the parser.",
            ),
            "estimated_effort_hours": "3",
            "estimated_win_probability": "0.5",
        }
        result = rank_partitioned_opportunities([value], ["python"])
        self.assertEqual(result["ranked_count"], 1)
        self.assertEqual(result["partitions"]["USD"]["ranked_count"], 0)
        row = result["partitions"]["RTC"]["ranked"][0]
        self.assertEqual(row["advertised_reward"], "30")
        self.assertEqual(row["estimated_expected_value"], "15")
        self.assertEqual(row["estimated_ev_per_hour"], "5")

    def test_reference_rate_prose_does_not_create_usd_partition(self):
        value = {
            "snapshot": self._snapshot(
                18,
                "[BOUNTY] Native reward",
                "**Reward: 100 RTC** (~$10 at reference rate)",
            ),
            "estimated_effort_hours": "10",
            "estimated_win_probability": "0.5",
        }
        result = rank_partitioned_opportunities([value], ["python"])
        self.assertEqual(result["partitions"]["USD"]["ranked_count"], 0)
        self.assertEqual(result["partitions"]["RTC"]["ranked_count"], 1)
        self.assertEqual(
            result["partitions"]["RTC"]["ranked"][0]["advertised_reward"],
            "100",
        )


if __name__ == "__main__":
    unittest.main()
