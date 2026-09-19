from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.paid_work_effort_value_gate import (
    PaidWorkGateInputError,
    compile_paid_work_effort_value_gate,
    verify_receipt,
)


POLICY = {
    "schema": "paid-work-effort-value-policy/v1",
    "evidence_max_age_seconds": 86400,
    "deadline_safety_seconds": 3600,
    "max_active_claims": 1,
    "fleet_economic_policy": {
        "schema": "fleet-economic-policy/v1",
        "min_batch_items": 2,
        "max_batch_items": 200,
        "currencies": {
            "USD": {
                "min_single_reward": "50",
                "min_batch_reward": "500",
                "min_reward_per_agent_hour": "100",
            },
            "RTC": {
                "min_single_reward": "200",
                "min_batch_reward": "500",
                "min_reward_per_agent_hour": "300",
            },
        },
    },
}


def evidence(
    state,
    *,
    authority=None,
    at="2026-09-16T19:30:00Z",
    tag="evidence",
):
    value = {
        "state": state,
        "evidence_url": f"https://evidence.example/{tag}",
        "observed_at": at,
    }
    if authority is not None:
        value["authority"] = authority
    return value


def candidate(
    *,
    amount="300",
    amount_semantics="FIXED",
    min_amount=None,
    max_amount=None,
    currency="USD",
    unit_type="CASH",
    payout_authority="FIRST_PARTY",
    payout_state="VERIFIED",
    hours="1",
    cost="10",
    cost_currency="USD",
    deadline="2026-09-17T20:00:00Z",
    claims=0,
):
    model_tool_cost = (
        {"state": "UNKNOWN"}
        if cost is None
        else {"state": "KNOWN", "amount": cost, "currency": cost_currency}
    )
    payout = {
        "state": payout_state,
        "evidence_url": "https://github.com/example/repo/issues/1",
        "observed_at": "2026-09-16T19:30:00Z",
    }
    if payout_state == "VERIFIED":
        payout.update(
            {
                "amount_semantics": amount_semantics,
                "currency": currency,
                "unit_type": unit_type,
                "authority": payout_authority,
            }
        )
        if amount_semantics == "FIXED":
            payout["amount"] = amount
        elif amount_semantics == "RANGE":
            payout["min_amount"] = min_amount
            payout["max_amount"] = max_amount
        elif amount_semantics == "UP_TO":
            payout["max_amount"] = max_amount
    return {
        "work_id": "work-1",
        "canonical_source_url": "https://github.com/example/repo/issues/1",
        "advertised_payout": payout,
        "estimated_engineering_hours": hours,
        "model_tool_cost": model_tool_cost,
        "deadline_at": deadline,
        "congestion": {
            "active_claims": claims,
            "observed_at": "2026-09-16T19:30:00Z",
        },
        "acceptance": evidence(
            "CONFIRMED",
            authority="FIRST_PARTY",
            tag="acceptance",
        ),
        "payout_route": evidence("CONFIRMED", tag="payout-route"),
        "account_kyc": evidence("READY", tag="account-kyc"),
    }


def request(item):
    return {
        "schema": "paid-work-effort-value-gate/v1",
        "as_of": "2026-09-16T20:00:00Z",
        "policy": deepcopy(POLICY),
        "candidate": item,
    }


class PaidWorkEffortValueGateTests(unittest.TestCase):
    def test_high_value_cash_goes_only_after_all_gates_clear(self):
        receipt = compile_paid_work_effort_value_gate(request(candidate()))
        self.assertEqual(receipt["decision"], "GO")
        self.assertEqual(receipt["reason_codes"], ["ALL_GATES_CLEAR"])
        self.assertEqual(
            receipt["gates"]["dollar_floor"]["decision"], "ACTIVE_REVIEW"
        )
        self.assertEqual(
            receipt["economics"]["net_reward_after_model_tool_cost"], "290"
        )
        self.assertTrue(receipt["economics"]["gross_economically_eligible"])
        self.assertTrue(verify_receipt(receipt))

    def test_75_dollar_fast_job_can_reach_downstream_go(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(amount="75", hours="0.5", cost="0"))
        )
        self.assertEqual(receipt["decision"], "GO")
        self.assertEqual(
            receipt["gates"]["dollar_floor"]["decision"], "ACTIVE_REVIEW"
        )
        self.assertEqual(
            receipt["economics"]["net_reward_after_model_tool_cost"], "75"
        )

    def test_range_above_floor_uses_guaranteed_minimum_for_economics(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(
                amount_semantics="RANGE",
                min_amount="60",
                max_amount="500",
                hours="0.5",
                cost="0",
            ))
        )
        self.assertEqual(receipt["decision"], "GO")
        self.assertEqual(receipt["economics"]["advertised_payout"], "60")
        self.assertEqual(receipt["economics"]["payout_range_min"], "60")
        self.assertEqual(receipt["economics"]["payout_range_max"], "500")
        nested = receipt["economics"]["fleet_economic_receipt"]
        self.assertEqual(nested["candidates"][0]["advertised_reward"], "60")

    def test_cross_boundary_range_and_up_to_never_reach_go(self):
        crossing = compile_paid_work_effort_value_gate(
            request(candidate(
                amount_semantics="RANGE",
                min_amount="49.99",
                max_amount="50",
                cost="0",
            ))
        )
        self.assertEqual(crossing["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertEqual(
            crossing["gates"]["dollar_floor"]["decision"],
            "HOLD_VERIFY_AMOUNT",
        )
        ceiling = compile_paid_work_effort_value_gate(
            request(candidate(
                amount_semantics="UP_TO",
                max_amount="500",
                cost="0",
            ))
        )
        self.assertEqual(ceiling["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertIsNone(ceiling["economics"]["fleet_economic_receipt"])

    def test_one_dollar_is_pruned_before_generic_fleet_economics(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(amount="1", cost="0"))
        )
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertIn("DOLLAR_FLOOR_NOT_ACTIVE", receipt["reason_codes"])
        self.assertEqual(
            receipt["gates"]["dollar_floor"]["decision"],
            "PRUNE_BELOW_FLOOR",
        )
        self.assertIsNone(receipt["economics"]["fleet_economic_receipt"])

    def test_20_dollar_job_is_pile_before_generic_fleet_economics(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(amount="20", cost="0"))
        )
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertEqual(
            receipt["gates"]["dollar_floor"]["decision"], "PILE_SAVE_UP"
        )
        self.assertIsNone(receipt["economics"]["fleet_economic_receipt"])

    def test_rtc_noncash_is_blocked_before_generic_fleet_can_admit_it(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(
                amount="200",
                currency="RTC",
                unit_type="NONCASH",
                cost="0",
                cost_currency="RTC",
            ))
        )
        self.assertEqual(receipt["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertIn(
            "DOLLAR_FLOOR_VALUE_NOT_ACTIVE", receipt["reason_codes"]
        )
        self.assertIn("NONCASH_VALUE_UNAUTHORIZED", receipt["reason_codes"])
        self.assertIsNone(receipt["economics"]["fleet_economic_receipt"])

    def test_unverified_payout_holds_before_economics(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(payout_state="UNVERIFIED", cost="0"))
        )
        self.assertEqual(receipt["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertEqual(
            receipt["gates"]["dollar_floor"]["decision"],
            "HOLD_VERIFY_AMOUNT",
        )

    def test_unknown_tool_cost_holds_value(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(cost=None))
        )
        self.assertEqual(receipt["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertIn("MODEL_TOOL_COST_UNKNOWN", receipt["reason_codes"])

    def test_tool_cost_currency_mismatch_holds_without_fx(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(cost_currency="RTC"))
        )
        self.assertEqual(receipt["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertIn(
            "MODEL_TOOL_COST_CURRENCY_MISMATCH_NO_FX",
            receipt["reason_codes"],
        )

    def test_post_cost_reward_and_rate_floors_can_skip(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(amount="70", cost="30"))
        )
        self.assertTrue(receipt["economics"]["gross_economically_eligible"])
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertIn(
            "POST_COST_REWARD_FLOOR_NOT_MET", receipt["reason_codes"]
        )
        self.assertIn(
            "POST_COST_REWARD_RATE_FLOOR_NOT_MET", receipt["reason_codes"]
        )

    def test_account_and_acceptance_gates_still_apply_after_active_floor(self):
        item = candidate()
        item["payout_route"] = evidence("UNKNOWN", tag="payout-route")
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn("PAYOUT_ROUTE_NOT_CONFIRMED", receipt["reason_codes"])

        item = candidate()
        item["account_kyc"] = evidence("BLOCKED", tag="account-kyc")
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")

        item = candidate()
        item["acceptance"] = evidence(
            "CONFIRMED", authority="OTHER", tag="acceptance"
        )
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn("ACCEPTANCE_NOT_FIRST_PARTY", receipt["reason_codes"])

    def test_deadline_and_congestion_terminal_reasons_still_win(self):
        expired = compile_paid_work_effort_value_gate(
            request(candidate(deadline="2026-09-16T19:59:59Z"))
        )
        self.assertEqual(expired["decision"], "SKIP_ECONOMICS")
        self.assertEqual(expired["reason_codes"], ["DEADLINE_EXPIRED"])

        safety = compile_paid_work_effort_value_gate(
            request(candidate(deadline="2026-09-16T20:30:00Z"))
        )
        self.assertEqual(safety["decision"], "SKIP_ECONOMICS")
        self.assertIn("DEADLINE_SAFETY_WINDOW_NOT_MET", safety["reason_codes"])

        congested = compile_paid_work_effort_value_gate(
            request(candidate(claims=1))
        )
        self.assertEqual(congested["decision"], "SKIP_ECONOMICS")
        self.assertIn("CLAIM_CONGESTION_SATURATED", congested["reason_codes"])

    def test_stale_payout_is_a_dollar_floor_hold(self):
        item = candidate()
        item["advertised_payout"]["observed_at"] = "2026-09-15T00:00:00Z"
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertEqual(
            receipt["gates"]["dollar_floor"]["decision"],
            "HOLD_VERIFY_AMOUNT",
        )

    def test_stale_coordination_and_account_evidence_hold_account(self):
        item = candidate()
        item["congestion"]["observed_at"] = "2026-09-15T00:00:00Z"
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn("CONGESTION_EVIDENCE_STALE", receipt["reason_codes"])

        item = candidate()
        item["account_kyc"] = evidence(
            "READY", at="2026-09-15T00:00:00Z", tag="account-kyc"
        )
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn("ACCOUNT_KYC_EVIDENCE_STALE", receipt["reason_codes"])

    def test_future_and_unknown_input_are_rejected(self):
        item = candidate()
        item["payout_route"]["observed_at"] = "2026-09-16T20:00:01Z"
        with self.assertRaisesRegex(PaidWorkGateInputError, "future"):
            compile_paid_work_effort_value_gate(request(item))
        item = candidate()
        item["mystery"] = "silent-input-expansion"
        with self.assertRaisesRegex(PaidWorkGateInputError, "unsupported field"):
            compile_paid_work_effort_value_gate(request(item))

    def test_float_and_nan_money_are_rejected_by_dollar_floor_ancestor(self):
        for bad in (1.5, True, "NaN", "Infinity", "-1"):
            with self.subTest(value=bad):
                item = candidate()
                item["advertised_payout"]["amount"] = bad
                with self.assertRaises(PaidWorkGateInputError):
                    compile_paid_work_effort_value_gate(request(item))

    def test_nested_dollar_and_fleet_receipt_tamper_are_rejected(self):
        receipt = compile_paid_work_effort_value_gate(request(candidate()))
        changed = deepcopy(receipt)
        changed["gates"]["dollar_floor"]["receipt"]["decision"] = "PILE_SAVE_UP"
        body = dict(changed)
        body.pop("receipt_sha256")
        changed["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                body, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        self.assertFalse(verify_receipt(changed))

        changed = deepcopy(receipt)
        changed["economics"]["fleet_economic_receipt"]["candidates"][0][
            "advertised_reward"
        ] = "999"
        body = dict(changed)
        body.pop("receipt_sha256")
        changed["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                body, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        self.assertFalse(verify_receipt(changed))

    def test_checked_in_policy_matches_guarded_thresholds(self):
        checked_in = json.loads(
            Path("policies/paid_work_effort_value_v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(checked_in, POLICY)

    def test_seed_fixtures_keep_expected_decisions(self):
        fixture_dir = Path("data/paid_work_effort_value_gate")
        expectations = {
            "frantic_one_dollar.json": "SKIP_ECONOMICS",
            "rustchain_25_rtc_unknown_value.json": "HOLD_VALUE_UNKNOWN",
        }
        for name, expected in expectations.items():
            with self.subTest(fixture=name):
                payload = json.loads(
                    (fixture_dir / name).read_text(encoding="utf-8")
                )
                self.assertEqual(payload["policy"], POLICY)
                receipt = compile_paid_work_effort_value_gate(payload)
                self.assertEqual(receipt["decision"], expected)
                self.assertTrue(verify_receipt(receipt))

    def test_cli_round_trip(self):
        payload = request(candidate())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable, "-m",
                    "concierge.paid_work_effort_value_gate",
                    str(path), "--json",
                ],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertEqual(receipt["decision"], "GO")
        self.assertTrue(verify_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
