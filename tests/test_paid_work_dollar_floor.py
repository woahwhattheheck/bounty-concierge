from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import concierge.paid_work_dollar_floor as gate
from concierge.paid_work_dollar_floor import (
    DollarFloorInputError,
    compile_paid_work_dollar_floor,
    verify_receipt,
)


POLICY = {
    "schema": "paid-work-dollar-floor-policy/v1",
    "currency": "USD",
    "pile_floor": "10",
    "active_floor": "50",
    "max_evidence_age_seconds": 86400,
    "pile_route": "bounty-pile-10-49",
    "active_route": "main-bounty-queue",
}


def evidence(
    *,
    state="VERIFIED",
    amount="50",
    min_amount=None,
    max_amount=None,
    amount_semantics="FIXED",
    currency="USD",
    unit_type="CASH",
    authority="FIRST_PARTY",
    at="2026-09-19T22:00:00Z",
):
    out = {
        "state": state,
        "evidence_url": "https://sponsor.example/bounty/1",
        "observed_at": at,
    }
    if state == "VERIFIED":
        out.update(
            {
                "amount_semantics": amount_semantics,
                "currency": currency,
                "unit_type": unit_type,
                "authority": authority,
            }
        )
        if amount_semantics == "FIXED":
            out["amount"] = amount
        elif amount_semantics == "RANGE":
            out["min_amount"] = min_amount
            out["max_amount"] = max_amount
        elif amount_semantics == "UP_TO":
            out["max_amount"] = max_amount
    return out


def request(payout=None, *, include_policy=True, work_id="repo-1"):
    out = {
        "schema": "paid-work-dollar-floor/v2",
        "evaluated_at": "2026-09-19T23:00:00Z",
        "candidate": {
            "work_id": work_id,
            "canonical_source_url": "https://github.com/example/repo/issues/1",
            "payout_evidence": payout or evidence(),
        },
    }
    if include_policy:
        out["policy"] = deepcopy(POLICY)
    return out


class PaidWorkDollarFloorTests(unittest.TestCase):
    def compile_amount(self, amount):
        return compile_paid_work_dollar_floor(
            request(evidence(amount=amount))
        )

    def test_50_is_active_boundary(self):
        result = self.compile_amount("50")
        self.assertEqual(result["decision"], "ACTIVE_REVIEW")
        self.assertEqual(result["route"], "main-bounty-queue")
        self.assertEqual(result["payout_evidence"]["guaranteed_amount"], "50")
        self.assertTrue(verify_receipt(result))

    def test_10_through_49_99_are_pile_only(self):
        for amount in ("10", "10.01", "25", "49", "49.99"):
            with self.subTest(amount=amount):
                result = self.compile_amount(amount)
                self.assertEqual(result["decision"], "PILE_SAVE_UP")
                self.assertEqual(result["route"], "bounty-pile-10-49")

    def test_below_10_is_pruned(self):
        for amount in ("0.01", "1", "5", "9.99"):
            with self.subTest(amount=amount):
                result = self.compile_amount(amount)
                self.assertEqual(result["decision"], "PRUNE_BELOW_FLOOR")
                self.assertEqual(result["route"], "discard")

    def test_source_policy_is_optional_but_caller_cannot_weaken_it(self):
        self.assertEqual(
            compile_paid_work_dollar_floor(
                request(include_policy=False)
            )["decision"],
            "ACTIVE_REVIEW",
        )
        payload = request()
        payload["policy"]["active_floor"] = "49"
        with self.assertRaisesRegex(DollarFloorInputError, "source-owned"):
            compile_paid_work_dollar_floor(payload)

    def test_public_policy_authority_rebinding_does_not_weaken_generation(self):
        original_policy = gate._SOURCE_POLICY
        original_allowed = gate._ALLOWED_AUTHORITIES
        original_authority = gate._AUTHORITY
        try:
            gate._SOURCE_POLICY = {
                **deepcopy(POLICY),
                "active_floor": "1",
                "pile_floor": "0.01",
            }
            gate._ALLOWED_AUTHORITIES = frozenset({"OTHER"})
            gate._AUTHORITY = {
                "advisory_only": False,
                "external_claim_authority": True,
            }
            payload = request()
            payload["policy"]["active_floor"] = "1"
            payload["policy"]["pile_floor"] = "0.01"
            with self.assertRaises(DollarFloorInputError):
                compile_paid_work_dollar_floor(payload)
            result = compile_paid_work_dollar_floor(
                request(evidence(amount="50", authority="FIRST_PARTY"))
            )
            self.assertEqual(result["decision"], "ACTIVE_REVIEW")
            self.assertFalse(result["authority"]["external_claim_authority"])
            self.assertTrue(verify_receipt(result))
        finally:
            gate._SOURCE_POLICY = original_policy
            gate._ALLOWED_AUTHORITIES = original_allowed
            gate._AUTHORITY = original_authority

    def test_range_wholly_above_active_uses_guaranteed_minimum(self):
        result = compile_paid_work_dollar_floor(
            request(evidence(
                amount_semantics="RANGE",
                min_amount="50",
                max_amount="500",
            ))
        )
        self.assertEqual(result["decision"], "ACTIVE_REVIEW")
        self.assertEqual(result["payout_evidence"]["guaranteed_amount"], "50")
        self.assertEqual(
            result["reason_codes"],
            ["GUARANTEED_ACTIVE_DOLLAR_FLOOR_MET"],
        )

    def test_range_wholly_inside_pile_stays_pile(self):
        result = compile_paid_work_dollar_floor(
            request(evidence(
                amount_semantics="RANGE",
                min_amount="10",
                max_amount="49.99",
            ))
        )
        self.assertEqual(result["decision"], "PILE_SAVE_UP")
        self.assertEqual(result["payout_evidence"]["guaranteed_amount"], "10")

    def test_ranges_crossing_10_or_50_hold(self):
        for low, high in (
            ("9.99", "10"),
            ("49.99", "50"),
            ("10", "500"),
            ("0", "500"),
        ):
            with self.subTest(low=low, high=high):
                result = compile_paid_work_dollar_floor(
                    request(evidence(
                        amount_semantics="RANGE",
                        min_amount=low,
                        max_amount=high,
                    ))
                )
                self.assertEqual(result["decision"], "HOLD_VERIFY_AMOUNT")
                self.assertIn(
                    "PAYOUT_RANGE_CROSSES_ROUTING_BOUNDARY",
                    result["reason_codes"],
                )

    def test_up_to_ceiling_never_promotes(self):
        for maximum in ("20", "50", "500"):
            with self.subTest(maximum=maximum):
                result = compile_paid_work_dollar_floor(
                    request(evidence(
                        amount_semantics="UP_TO",
                        max_amount=maximum,
                    ))
                )
                self.assertEqual(result["decision"], "HOLD_VERIFY_AMOUNT")
                self.assertEqual(
                    result["payout_evidence"]["guaranteed_amount"], "0"
                )
                self.assertIn(
                    "PAYOUT_CEILING_NOT_GUARANTEED",
                    result["reason_codes"],
                )

    def test_invalid_range_and_semantics_fail_closed(self):
        with self.assertRaisesRegex(DollarFloorInputError, "must not exceed"):
            compile_paid_work_dollar_floor(
                request(evidence(
                    amount_semantics="RANGE",
                    min_amount="60",
                    max_amount="50",
                ))
            )
        bad = evidence()
        bad["amount_semantics"] = "MAXIMUM"
        with self.assertRaises(DollarFloorInputError):
            compile_paid_work_dollar_floor(request(bad))

    def test_unverified_amount_never_routes_or_smuggles_values(self):
        result = compile_paid_work_dollar_floor(
            request(evidence(state="UNVERIFIED"))
        )
        self.assertEqual(result["decision"], "HOLD_VERIFY_AMOUNT")
        bad = evidence(state="UNVERIFIED")
        bad["amount"] = "1000"
        with self.assertRaisesRegex(DollarFloorInputError, "must not assert"):
            compile_paid_work_dollar_floor(request(bad))

    def test_stale_future_noncash_nonusd_and_other_authority_hold(self):
        stale = compile_paid_work_dollar_floor(
            request(evidence(amount="100", at="2026-09-17T22:59:59Z"))
        )
        self.assertEqual(stale["decision"], "HOLD_VERIFY_AMOUNT")
        with self.assertRaisesRegex(DollarFloorInputError, "future"):
            compile_paid_work_dollar_floor(
                request(evidence(at="2026-09-19T23:00:01Z"))
            )
        noncash = compile_paid_work_dollar_floor(
            request(evidence(amount="1000", unit_type="NONCASH"))
        )
        self.assertEqual(noncash["decision"], "HOLD_VALUE_NONCOMPARABLE")
        eur = compile_paid_work_dollar_floor(
            request(evidence(amount="1000", currency="EUR"))
        )
        self.assertEqual(eur["decision"], "HOLD_VALUE_NONCOMPARABLE")
        other = compile_paid_work_dollar_floor(
            request(evidence(amount="500", authority="OTHER"))
        )
        self.assertEqual(other["decision"], "HOLD_VERIFY_AMOUNT")

    def test_float_bool_nan_zero_and_unknown_fields_fail_closed(self):
        for bad in (50.0, True, "NaN", "Infinity", "0", "-1"):
            with self.subTest(value=bad):
                with self.assertRaises(DollarFloorInputError):
                    compile_paid_work_dollar_floor(
                        request(evidence(amount=bad))
                    )
        payload = request()
        payload["candidate"]["mystery"] = "x"
        with self.assertRaisesRegex(DollarFloorInputError, "unsupported field"):
            compile_paid_work_dollar_floor(payload)

    def test_lone_surrogate_is_rejected_not_unicode_crash(self):
        payload = request()
        payload["candidate"]["work_id"] = "bad\ud800id"
        with self.assertRaises(DollarFloorInputError):
            compile_paid_work_dollar_floor(payload)

    def test_receipt_is_semantically_replayed_and_tamper_evident(self):
        first = compile_paid_work_dollar_floor(request())
        self.assertTrue(verify_receipt(first))
        changed = deepcopy(first)
        changed["decision"] = "PILE_SAVE_UP"
        self.assertFalse(verify_receipt(changed))
        changed = deepcopy(first)
        changed["input"]["policy"]["active_floor"] = "1"
        body = dict(changed)
        body.pop("receipt_sha256", None)
        changed["receipt_sha256"] = gate._sha256_json(body)
        self.assertFalse(verify_receipt(changed))

    def test_checked_in_policy_matches_owner_floor(self):
        checked = json.loads(
            Path("policies/paid_work_dollar_floor_v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(checked, POLICY)

    def test_cli_round_trip_and_float_rejection(self):
        payload = request()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable, "-m",
                    "concierge.paid_work_dollar_floor",
                    str(path), "--json",
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(verify_receipt(json.loads(proc.stdout)))

            payload["candidate"]["payout_evidence"]["amount"] = 50.0
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable, "-m",
                    "concierge.paid_work_dollar_floor",
                    str(path), "--json",
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("floating-point", proc.stderr)


if __name__ == "__main__":
    unittest.main()
