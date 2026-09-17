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
    currency="USD",
    unit_type="CASH",
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
    return {
        "work_id": "work-1",
        "canonical_source_url": "https://example.test/work/1",
        "advertised_payout": {
            "amount": amount,
            "currency": currency,
            "unit_type": unit_type,
            "observed_at": "2026-09-16T19:30:00Z",
        },
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
            receipt["economics"]["net_reward_after_model_tool_cost"],
            "290",
        )
        self.assertTrue(receipt["economics"]["gross_economically_eligible"])
        self.assertTrue(verify_receipt(receipt))
        self.assertFalse(receipt["authority"]["external_claim_authority"])
        self.assertFalse(receipt["authority"]["external_submission_authority"])
        self.assertFalse(
            receipt["authority"]["payment_cash_or_revenue_authority"]
        )

    def test_frantic_one_dollar_seed_skips_economics(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(amount="1", cost="0"))
        )
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertIn(
            "GROSS_ECONOMICS_INELIGIBLE",
            receipt["reason_codes"],
        )
        nested = receipt["economics"]["fleet_economic_receipt"]
        self.assertIsNotNone(nested)
        self.assertFalse(nested["candidates"][0]["economically_eligible"])

    def test_rustchain_25_rtc_seed_holds_value_without_fx(self):
        receipt = compile_paid_work_effort_value_gate(
            request(
                candidate(
                    amount="25",
                    currency="RTC",
                    unit_type="NONCASH",
                    cost="0",
                    cost_currency="RTC",
                )
            )
        )
        self.assertEqual(receipt["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertIn("NONCASH_VALUE_UNAUTHORIZED", receipt["reason_codes"])
        self.assertIsNone(receipt["economics"]["fleet_economic_receipt"])
        self.assertFalse(receipt["authority"]["fx_conversion"])
        self.assertFalse(receipt["authority"]["noncash_valuation"])

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

    def test_post_cost_reward_floor_can_turn_gross_go_into_skip(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(amount="120", cost="30"))
        )
        self.assertTrue(receipt["economics"]["gross_economically_eligible"])
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertIn(
            "POST_COST_REWARD_FLOOR_NOT_MET",
            receipt["reason_codes"],
        )
        self.assertIn(
            "POST_COST_REWARD_RATE_FLOOR_NOT_MET",
            receipt["reason_codes"],
        )

    def test_missing_payout_route_holds_account_gate(self):
        item = candidate()
        item["payout_route"] = evidence("UNKNOWN", tag="payout-route")
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn(
            "PAYOUT_ROUTE_NOT_CONFIRMED",
            receipt["reason_codes"],
        )

    def test_blocked_kyc_holds_account_gate(self):
        item = candidate()
        item["account_kyc"] = evidence("BLOCKED", tag="account-kyc")
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn("ACCOUNT_KYC_NOT_READY", receipt["reason_codes"])

    def test_acceptance_requires_first_party_authority(self):
        item = candidate()
        item["acceptance"] = evidence(
            "CONFIRMED",
            authority="OTHER",
            tag="acceptance",
        )
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn(
            "ACCEPTANCE_NOT_FIRST_PARTY",
            receipt["reason_codes"],
        )

    def test_expired_deadline_skips_even_when_value_is_unknown(self):
        receipt = compile_paid_work_effort_value_gate(
            request(
                candidate(
                    currency="RTC",
                    unit_type="NONCASH",
                    cost="0",
                    cost_currency="RTC",
                    deadline="2026-09-16T19:59:59Z",
                )
            )
        )
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertEqual(receipt["reason_codes"], ["DEADLINE_EXPIRED"])
        self.assertIn(
            "NONCASH_VALUE_UNAUTHORIZED",
            receipt["observed_hold_reasons"],
        )

    def test_deadline_safety_window_skips(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(deadline="2026-09-16T20:30:00Z"))
        )
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertIn(
            "DEADLINE_SAFETY_WINDOW_NOT_MET",
            receipt["reason_codes"],
        )

    def test_claim_congestion_saturation_skips(self):
        receipt = compile_paid_work_effort_value_gate(
            request(candidate(claims=1))
        )
        self.assertEqual(receipt["decision"], "SKIP_ECONOMICS")
        self.assertIn(
            "CLAIM_CONGESTION_SATURATED",
            receipt["reason_codes"],
        )

    def test_stale_payout_evidence_holds_value(self):
        item = candidate()
        item["advertised_payout"]["observed_at"] = "2026-09-15T00:00:00Z"
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_VALUE_UNKNOWN")
        self.assertIn("PAYOUT_EVIDENCE_STALE", receipt["reason_codes"])

    def test_stale_coordination_evidence_holds_account(self):
        item = candidate()
        item["congestion"]["observed_at"] = "2026-09-15T00:00:00Z"
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn(
            "CONGESTION_EVIDENCE_STALE",
            receipt["reason_codes"],
        )

    def test_stale_account_evidence_holds_account(self):
        item = candidate()
        item["account_kyc"] = evidence(
            "READY",
            at="2026-09-15T00:00:00Z",
            tag="account-kyc",
        )
        receipt = compile_paid_work_effort_value_gate(request(item))
        self.assertEqual(receipt["decision"], "HOLD_ACCOUNT_GATE")
        self.assertIn(
            "ACCOUNT_KYC_EVIDENCE_STALE",
            receipt["reason_codes"],
        )

    def test_future_evidence_is_rejected(self):
        item = candidate()
        item["payout_route"]["observed_at"] = "2026-09-16T20:00:01Z"
        with self.assertRaisesRegex(
            PaidWorkGateInputError,
            "must not be in the future",
        ):
            compile_paid_work_effort_value_gate(request(item))

    def test_unknown_fields_are_rejected(self):
        item = candidate()
        item["mystery"] = "silent-input-expansion"
        with self.assertRaisesRegex(
            PaidWorkGateInputError,
            "unsupported field",
        ):
            compile_paid_work_effort_value_gate(request(item))

    def test_float_and_nan_money_are_rejected(self):
        for bad in (1.5, True, "NaN", "Infinity", "-1"):
            with self.subTest(value=bad):
                item = candidate()
                item["advertised_payout"]["amount"] = bad
                with self.assertRaises(PaidWorkGateInputError):
                    compile_paid_work_effort_value_gate(request(item))

    def test_receipt_is_deterministic_and_tamper_evident(self):
        payload = request(candidate())
        first = compile_paid_work_effort_value_gate(payload)
        second = compile_paid_work_effort_value_gate(deepcopy(payload))
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))

        changed = deepcopy(first)
        changed["decision"] = "SKIP_ECONOMICS"
        self.assertFalse(verify_receipt(changed))

    def test_nested_fleet_receipt_tamper_is_rejected_even_if_outer_rehashed(self):
        receipt = compile_paid_work_effort_value_gate(request(candidate()))
        changed = deepcopy(receipt)
        changed["economics"]["fleet_economic_receipt"]["candidates"][0][
            "advertised_reward"
        ] = "999"
        body = dict(changed)
        body.pop("receipt_sha256")
        changed["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
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

    def test_seed_fixtures_keep_policy_and_expected_decisions(self):
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
                    sys.executable,
                    "-m",
                    "concierge.paid_work_effort_value_gate",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertEqual(receipt["decision"], "GO")
        self.assertTrue(verify_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
