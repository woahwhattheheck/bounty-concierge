from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.fleet_economic_admission import (
    EconomicAdmissionInputError,
    compile_fleet_economic_admission,
    verify_receipt,
)


POLICY = {
    "schema": "fleet-economic-policy/v1",
    "min_batch_items": 2,
    "max_batch_items": 200,
    "currencies": {
        "USD": {
            "min_single_reward": "100",
            "min_batch_reward": "500",
            "min_reward_per_agent_hour": "100",
        },
        "RTC": {
            "min_single_reward": "200",
            "min_batch_reward": "500",
            "min_reward_per_agent_hour": "300",
        },
    },
}


def candidate(
    work_id,
    reward,
    effort,
    *,
    currency="RTC",
    batch_key=None,
    source=None,
):
    return {
        "work_id": work_id,
        "canonical_source_url": source or f"https://example.test/work/{work_id}",
        "currency": currency,
        "advertised_reward": reward,
        "estimated_agent_hours": effort,
        "batch_key": batch_key,
    }


def request(*items, policy=None):
    return {
        "schema": "fleet-economic-admission/v1",
        "policy": deepcopy(policy or POLICY),
        "candidates": list(items),
    }


class FleetEconomicAdmissionTests(unittest.TestCase):
    def test_one_rtc_hour_is_held(self):
        receipt = compile_fleet_economic_admission(
            request(candidate("tiny", "1", "1"))
        )
        row = receipt["candidates"][0]
        self.assertEqual(row["disposition"], "ECONOMIC_HOLD")
        self.assertFalse(row["economically_eligible"])
        self.assertIn("BELOW_SINGLE_REWARD_FLOOR", row["reason_codes"])
        self.assertIn("BELOW_REWARD_RATE_FLOOR", row["reason_codes"])
        self.assertIn("NO_COMPATIBLE_BATCH", row["reason_codes"])

    def test_high_value_single_must_clear_reward_and_rate(self):
        receipt = compile_fleet_economic_admission(
            request(candidate("fast", "200", "0.5"))
        )
        row = receipt["candidates"][0]
        self.assertEqual(row["disposition"], "SINGLE_ELIGIBLE")
        self.assertEqual(row["reward_per_agent_hour"], "400")
        self.assertTrue(row["economically_eligible"])

    def test_high_reward_slow_single_still_holds(self):
        receipt = compile_fleet_economic_admission(
            request(candidate("slow", "200", "2"))
        )
        row = receipt["candidates"][0]
        self.assertFalse(row["economically_eligible"])
        self.assertEqual(row["reason_codes"], [
            "BELOW_REWARD_RATE_FLOOR",
            "NO_COMPATIBLE_BATCH",
        ])

    def test_microtasks_only_survive_as_compatible_batch(self):
        items = [
            candidate(f"m{i}", "5", "0.01", batch_key="repo-a:mechanical")
            for i in range(100)
        ]
        receipt = compile_fleet_economic_admission(request(*items))
        self.assertEqual(receipt["economically_eligible_count"], 100)
        self.assertEqual(receipt["economic_hold_count"], 0)
        self.assertTrue(all(
            row["disposition"] == "BATCH_ELIGIBLE"
            for row in receipt["candidates"]
        ))
        batch = receipt["batches"][0]
        self.assertEqual(batch["aggregate_advertised_reward"], "500")
        self.assertEqual(batch["aggregate_estimated_agent_hours"], "1")
        self.assertEqual(batch["aggregate_reward_per_agent_hour"], "500")
        self.assertTrue(batch["economically_eligible"])

    def test_batch_below_total_floor_holds(self):
        items = [
            candidate(f"m{i}", "5", "0.01", batch_key="repo-a:mechanical")
            for i in range(20)
        ]
        receipt = compile_fleet_economic_admission(request(*items))
        self.assertEqual(receipt["economically_eligible_count"], 0)
        self.assertIn(
            "BATCH_REWARD_FLOOR_NOT_MET",
            receipt["batches"][0]["reason_codes"],
        )

    def test_batch_can_clear_total_but_fail_reward_rate(self):
        items = [
            candidate(f"m{i}", "50", "1", batch_key="repo-a:slow")
            for i in range(10)
        ]
        receipt = compile_fleet_economic_admission(request(*items))
        batch = receipt["batches"][0]
        self.assertEqual(batch["aggregate_advertised_reward"], "500")
        self.assertFalse(batch["economically_eligible"])
        self.assertIn("BATCH_REWARD_RATE_FLOOR_NOT_MET", batch["reason_codes"])

    def test_single_eligible_item_does_not_subsidize_micro_batch(self):
        receipt = compile_fleet_economic_admission(
            request(
                candidate("anchor", "200", "0.5", batch_key="same"),
                candidate("tiny", "1", "0.001", batch_key="same"),
            )
        )
        by_id = {row["work_id"]: row for row in receipt["candidates"]}
        self.assertEqual(by_id["anchor"]["disposition"], "SINGLE_ELIGIBLE")
        self.assertEqual(by_id["tiny"]["disposition"], "ECONOMIC_HOLD")
        self.assertEqual(receipt["batches"][0]["item_count"], 1)
        self.assertIn(
            "BATCH_ITEM_COUNT_BELOW_FLOOR",
            receipt["batches"][0]["reason_codes"],
        )

    def test_duplicate_source_and_work_id_fail_closed(self):
        duplicate_source = request(
            candidate("a", "200", "0.5", source="https://same.test/1"),
            candidate("b", "200", "0.5", source="https://same.test/1"),
        )
        with self.assertRaisesRegex(
            EconomicAdmissionInputError, "duplicate canonical_source_url"
        ):
            compile_fleet_economic_admission(duplicate_source)

        duplicate_id = request(
            candidate("a", "200", "0.5", source="https://one.test/1"),
            candidate("a", "200", "0.5", source="https://two.test/2"),
        )
        with self.assertRaisesRegex(EconomicAdmissionInputError, "duplicate work_id"):
            compile_fleet_economic_admission(duplicate_id)

    def test_mixed_currency_batch_key_fails_closed_without_fx(self):
        payload = request(
            candidate("rtc", "200", "0.5", batch_key="mixed"),
            candidate(
                "usd",
                "100",
                "0.5",
                currency="USD",
                batch_key="mixed",
            ),
        )
        with self.assertRaisesRegex(
            EconomicAdmissionInputError, "mixes native currencies"
        ):
            compile_fleet_economic_admission(payload)

    def test_float_bool_nan_and_unknown_currency_fail_closed(self):
        bad_values = [1.0, True, "NaN", "Infinity", "-1"]
        for bad in bad_values:
            with self.subTest(value=bad):
                payload = request(candidate("bad", bad, "1"))
                with self.assertRaises(EconomicAdmissionInputError):
                    compile_fleet_economic_admission(payload)

        payload = request(candidate("eur", "100", "1", currency="EUR"))
        with self.assertRaisesRegex(
            EconomicAdmissionInputError, "has no economics policy"
        ):
            compile_fleet_economic_admission(payload)

    def test_policy_has_no_implicit_default_or_cross_currency_winner(self):
        payload = request(candidate("a", "200", "0.5"))
        del payload["policy"]
        with self.assertRaisesRegex(EconomicAdmissionInputError, "policy must be"):
            compile_fleet_economic_admission(payload)

        receipt = compile_fleet_economic_admission(
            request(
                candidate("rtc", "200", "0.5"),
                candidate("usd", "100", "0.5", currency="USD"),
            )
        )
        self.assertEqual(receipt["economically_eligible_count"], 2)
        self.assertNotIn("winner", receipt)
        self.assertFalse(receipt["authority"]["fx_conversion"])
        self.assertFalse(receipt["authority"]["dispatch_authority"])

    def test_receipt_digest_is_deterministic_and_tamper_evident(self):
        payload = request(candidate("a", "200", "0.5"))
        first = compile_fleet_economic_admission(payload)
        second = compile_fleet_economic_admission(deepcopy(payload))
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))

        changed = deepcopy(first)
        changed["candidates"][0]["advertised_reward"] = "9999"
        self.assertFalse(verify_receipt(changed))


    def test_checked_in_swarm_policy_matches_guarded_thresholds(self):
        policy_path = Path("policies/swarm_fleet_economics_v1.json")
        checked_in = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(checked_in, POLICY)

    def test_cli_round_trip_and_authority_ceiling(self):
        payload = request(candidate("a", "200", "0.5"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.fleet_economic_admission",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(verify_receipt(result))
        self.assertFalse(result["authority"]["dispatch_authority"])
        self.assertFalse(result["authority"]["claim_or_submission_authority"])
        self.assertFalse(result["authority"]["payment_cash_or_revenue_authority"])


if __name__ == "__main__":
    unittest.main()
