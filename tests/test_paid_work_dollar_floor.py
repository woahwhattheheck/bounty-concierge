from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
                "amount": amount,
                "currency": currency,
                "unit_type": unit_type,
                "authority": authority,
            }
        )
    return out


def request(payout=None):
    return {
        "schema": "paid-work-dollar-floor/v1",
        "evaluated_at": "2026-09-19T23:00:00Z",
        "policy": deepcopy(POLICY),
        "candidate": {
            "work_id": "repo-1",
            "canonical_source_url": "https://github.com/example/repo/issues/1",
            "payout_evidence": payout or evidence(),
        },
    }


class PaidWorkDollarFloorTests(unittest.TestCase):
    def compile_amount(self, amount):
        return compile_paid_work_dollar_floor(
            request(evidence(amount=amount))
        )

    def test_50_is_active_boundary(self):
        result = self.compile_amount("50")
        self.assertEqual(result["decision"], "ACTIVE_REVIEW")
        self.assertEqual(result["route"], "main-bounty-queue")
        self.assertEqual(
            result["reason_codes"], ["ACTIVE_DOLLAR_FLOOR_MET"]
        )
        self.assertTrue(verify_receipt(result))

    def test_75_and_100_are_active(self):
        for amount in ("75", "100", "750"):
            with self.subTest(amount=amount):
                self.assertEqual(
                    self.compile_amount(amount)["decision"], "ACTIVE_REVIEW"
                )

    def test_10_through_49_99_are_pile_only(self):
        for amount in ("10", "10.01", "25", "49", "49.99"):
            with self.subTest(amount=amount):
                result = self.compile_amount(amount)
                self.assertEqual(result["decision"], "PILE_SAVE_UP")
                self.assertEqual(
                    result["route"], "bounty-pile-10-49"
                )

    def test_below_10_is_pruned_even_if_batchable_elsewhere(self):
        for amount in ("0.01", "1", "5", "9.99"):
            with self.subTest(amount=amount):
                result = self.compile_amount(amount)
                self.assertEqual(
                    result["decision"], "PRUNE_BELOW_FLOOR"
                )
                self.assertEqual(result["route"], "discard")

    def test_unverified_amount_never_routes(self):
        result = compile_paid_work_dollar_floor(
            request(evidence(state="UNVERIFIED"))
        )
        self.assertEqual(result["decision"], "HOLD_VERIFY_AMOUNT")
        self.assertEqual(result["route"], "none")
        self.assertIsNone(result["payout_evidence"]["amount"])

    def test_unverified_cannot_smuggle_amount(self):
        bad = evidence(state="UNVERIFIED")
        bad["amount"] = "1000"
        with self.assertRaisesRegex(
            DollarFloorInputError, "must not assert"
        ):
            compile_paid_work_dollar_floor(request(bad))

    def test_stale_verified_amount_holds(self):
        result = compile_paid_work_dollar_floor(
            request(
                evidence(
                    amount="100", at="2026-09-17T22:59:59Z"
                )
            )
        )
        self.assertEqual(result["decision"], "HOLD_VERIFY_AMOUNT")
        self.assertIn(
            "PAYOUT_EVIDENCE_STALE", result["reason_codes"]
        )

    def test_future_evidence_rejected(self):
        with self.assertRaisesRegex(
            DollarFloorInputError, "must not be in the future"
        ):
            compile_paid_work_dollar_floor(
                request(evidence(at="2026-09-19T23:00:01Z"))
            )

    def test_noncash_and_nonusd_hold_without_fx(self):
        noncash = compile_paid_work_dollar_floor(
            request(
                evidence(amount="1000", unit_type="NONCASH")
            )
        )
        self.assertEqual(
            noncash["decision"], "HOLD_VALUE_NONCOMPARABLE"
        )
        self.assertIn(
            "NONCASH_VALUE_UNAUTHORIZED", noncash["reason_codes"]
        )
        self.assertFalse(noncash["authority"]["noncash_valuation"])

        eur = compile_paid_work_dollar_floor(
            request(evidence(amount="1000", currency="EUR"))
        )
        self.assertEqual(
            eur["decision"], "HOLD_VALUE_NONCOMPARABLE"
        )
        self.assertIn(
            "CURRENCY_REQUIRES_AUTHORIZED_FX",
            eur["reason_codes"],
        )
        self.assertFalse(eur["authority"]["fx_conversion"])

    def test_first_party_and_settlement_platform_are_accepted(self):
        for authority in ("FIRST_PARTY", "SETTLEMENT_PLATFORM"):
            with self.subTest(authority=authority):
                result = compile_paid_work_dollar_floor(
                    request(
                        evidence(
                            amount="50", authority=authority
                        )
                    )
                )
                self.assertEqual(
                    result["decision"], "ACTIVE_REVIEW"
                )

    def test_other_authority_holds(self):
        result = compile_paid_work_dollar_floor(
            request(
                evidence(amount="500", authority="OTHER")
            )
        )
        self.assertEqual(result["decision"], "HOLD_VERIFY_AMOUNT")
        self.assertIn(
            "PAYOUT_AUTHORITY_NOT_ACCEPTED",
            result["reason_codes"],
        )

    def test_float_bool_nan_and_zero_fail_closed(self):
        for bad in (50.0, True, "NaN", "Infinity", "0", "-1"):
            with self.subTest(value=bad):
                with self.assertRaises(DollarFloorInputError):
                    compile_paid_work_dollar_floor(
                        request(evidence(amount=bad))
                    )

    def test_unknown_fields_fail_closed(self):
        payload = request()
        payload["candidate"]["mystery"] = "x"
        with self.assertRaisesRegex(
            DollarFloorInputError, "unsupported field"
        ):
            compile_paid_work_dollar_floor(payload)

    def test_policy_requires_strict_floor_order(self):
        payload = request()
        payload["policy"]["active_floor"] = "10"
        with self.assertRaisesRegex(
            DollarFloorInputError, "greater than"
        ):
            compile_paid_work_dollar_floor(payload)

    def test_source_owned_policy_cannot_be_weakened(self):
        payload = request()
        payload["policy"]["active_floor"] = "49"
        with self.assertRaisesRegex(
            DollarFloorInputError, "source-owned"
        ):
            compile_paid_work_dollar_floor(payload)

    def test_evidence_permalink_fragment_is_allowed(self):
        payout = evidence(amount="50")
        payout["evidence_url"] = (
            "https://github.com/example/repo/issues/1#issuecomment-123"
        )
        result = compile_paid_work_dollar_floor(request(payout))
        self.assertEqual(result["decision"], "ACTIVE_REVIEW")
        self.assertTrue(verify_receipt(result))

    def test_receipt_is_deterministic_and_tamper_evident(self):
        first = compile_paid_work_dollar_floor(request())
        second = compile_paid_work_dollar_floor(
            deepcopy(request())
        )
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))

        changed = deepcopy(first)
        changed["decision"] = "PILE_SAVE_UP"
        self.assertFalse(verify_receipt(changed))

        changed = deepcopy(first)
        changed["decision"] = "PILE_SAVE_UP"
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

        changed = deepcopy(first)
        changed["policy"]["active_floor"] = "500"
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

    def test_checked_in_policy_matches_owner_floor(self):
        checked = json.loads(
            Path(
                "policies/paid_work_dollar_floor_v1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(checked, POLICY)

    def test_cli_rejects_json_float_amount(self):
        payload = request()
        payload["candidate"]["payout_evidence"]["amount"] = 50.0
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.paid_work_dollar_floor",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("floating-point", proc.stderr)

    def test_cli_round_trip(self):
        payload = request()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.paid_work_dollar_floor",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertEqual(
            receipt["decision"], "ACTIVE_REVIEW"
        )
        self.assertTrue(verify_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
