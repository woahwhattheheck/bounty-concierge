from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, sentinel

from concierge.paid_work_effort_value_gate import compile_paid_work_effort_value_gate
from concierge.revenue_dispatch import (
    RevenueDispatchError,
    _load_gate_receipt,
    qualify_available_live_revenue_intake,
)


def intake(*, dispatch=True, disposition="ACTIONABLE"):
    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "canonical_source_url": "https://github.com/acme/widgets/issues/17",
        "reason_codes": [],
        "reasons": [],
        "qualification": {"disposition": disposition, "dispatch": dispatch, "signals": {}},
        "provenance": {"disposition": "ACTIONABLE", "dispatch": True, "signals": {}},
    }


def availability(*, dispatch=True, reason=None):
    return {
        "schema": "bounty-availability/v1",
        "repo": "acme/widgets",
        "number": 17,
        "disposition": "CLEAR" if dispatch else "HOLD",
        "dispatch": dispatch,
        "reason_code": reason,
        "issue_state": "open",
        "signal_codes": [] if dispatch else ["MAINTAINER_ACCEPTANCE_SIGNAL"],
        "evidence": [],
        "authority": {
            "effect": "new_work_dispatch_only",
            "terminal_signal_is_payout_proof": False,
            "terminal_signal_is_revenue_proof": False,
            "raw_comment_text_retained": False,
            "user_identity_retained": False,
        },
    }


LIVE_CASH_AUTHORITY = {
    "advisory_only": True,
    "claim_authority": False,
    "implementation_authority": False,
    "submission_authority": False,
    "outbound_contact_authority": False,
    "payment_or_wallet_authority": False,
}


def seal_live_cash(value):
    body = deepcopy(value)
    body.pop("receipt_sha256", None)
    value["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return value


def live_cash(
    *,
    disposition="ACTIVE_REVIEW",
    route="main_bounty_queue",
    amount="50",
    currency="USD",
    fixed_semantics=True,
    reasons=(),
    repo="acme/widgets",
    number=17,
):
    body = {
        "schema": "bounty-live-cash-admission-receipt/v1",
        "identity": {
            "repo": repo,
            "issue_number": number,
            "canonical_issue_url": f"https://github.com/{repo}/issues/{number}",
        },
        "source": {
            "kind": "LIVE_GITHUB_PREFLIGHT",
            "issue_generation_sha256": "1" * 64,
            "preflight_sha256": "2" * 64,
            "preflight": {},
        },
        "economics": {
            "currency": currency,
            "fixed_amount": amount,
            "fixed_semantics": fixed_semantics,
            "active_floor": "50",
            "pile_floor": "10",
        },
        "disposition": disposition,
        "route": route,
        "reason_codes": list(reasons),
        "authority": deepcopy(LIVE_CASH_AUTHORITY),
    }
    return seal_live_cash(body)


POLICY_SHA = "b" * 64
REQUEST_SHA = "c" * 64


REAL_POLICY = {
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
            }
        },
    },
}


def _evidence(state, tag, authority=None):
    value = {
        "state": state,
        "evidence_url": f"https://evidence.example/{tag}",
        "observed_at": "2026-09-16T19:30:00Z",
    }
    if authority is not None:
        value["authority"] = authority
    return value


def real_gate_request():
    return {
        "schema": "paid-work-effort-value-gate/v1",
        "as_of": "2026-09-16T20:00:00Z",
        "policy": deepcopy(REAL_POLICY),
        "candidate": {
            "work_id": "work-17",
            "canonical_source_url": "https://github.com/acme/widgets/issues/17",
            "advertised_payout": {
                "amount": "300",
                "currency": "USD",
                "unit_type": "CASH",
                "observed_at": "2026-09-16T19:30:00Z",
            },
            "estimated_engineering_hours": "1",
            "model_tool_cost": {"state": "KNOWN", "amount": "10", "currency": "USD"},
            "deadline_at": "2026-09-17T20:00:00Z",
            "congestion": {"active_claims": 0, "observed_at": "2026-09-16T19:30:00Z"},
            "acceptance": _evidence("CONFIRMED", "acceptance", "FIRST_PARTY"),
            "payout_route": _evidence("CONFIRMED", "payout-route"),
            "account_kyc": _evidence("READY", "account-kyc"),
        },
    }


def receipt(
    *,
    decision="GO",
    work_id="work-17",
    source="https://github.com/acme/widgets/issues/17",
    at="2026-09-16T20:00:00Z",
    policy_sha=POLICY_SHA,
):
    return {
        "schema": "paid-work-effort-value-gate-receipt/v1",
        "request_sha256": REQUEST_SHA,
        "policy_sha256": policy_sha,
        "work_id": work_id,
        "canonical_source_url": source,
        "canonical_source_identity": "github:acme/widgets/issues/17",
        "as_of": at,
        "decision": decision,
        "reason_codes": ["ALL_GATES_CLEAR"] if decision == "GO" else [decision],
        "economics": {"fleet_economic_receipt": None},
        "gates": {},
        "receipt_sha256": "a" * 64,
        "authority": {
            "go_is_internal_admission_signal": True,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "payment_cash_or_revenue_authority": False,
            "fx_conversion": False,
            "noncash_valuation": False,
        },
    }


def raw(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


class RevenueDispatchTests(unittest.TestCase):
    def run_dispatch(
        self,
        *,
        gate=None,
        gate_bytes=None,
        work_id=None,
        avail=None,
        incoming=None,
        max_age=3600,
        expected_bytes_sha=None,
        expected_policy_sha=POLICY_SHA,
        dispatch_as_of="2026-09-16T20:30:00Z",
        verify=True,
        cash=None,
        cash_error=None,
    ):
        if gate_bytes is None and gate is not None:
            gate_bytes = raw(gate)
        if gate_bytes is not None and expected_bytes_sha is None:
            expected_bytes_sha = hashlib.sha256(gate_bytes).hexdigest()
        cash_patch = patch(
            "concierge.revenue_dispatch._LIVE_CASH_EVALUATOR",
            return_value=cash or live_cash(),
        )
        if cash_error is not None:
            cash_patch = patch(
                "concierge.revenue_dispatch._LIVE_CASH_EVALUATOR",
                side_effect=cash_error,
            )
        with patch(
            "concierge.revenue_dispatch.qualify_live_revenue_intake",
            return_value=incoming or intake(),
        ), patch(
            "concierge.revenue_dispatch.inspect_bounty_availability",
            return_value=avail or availability(),
        ), patch(
            "concierge.revenue_dispatch.verify_paid_work_receipt",
            return_value=verify,
        ), cash_patch:
            return qualify_available_live_revenue_intake(
                "acme/widgets",
                17,
                listing_url="https://mirror.example/item",
                token="TOKEN",
                session=sentinel.session,
                max_pages=3,
                saturation_threshold=2,
                gate_receipt_bytes=gate_bytes,
                work_id=work_id,
                expected_gate_receipt_bytes_sha256=expected_bytes_sha,
                expected_policy_sha256=expected_policy_sha,
                dispatch_as_of=dispatch_as_of,
                gate_max_age_seconds=max_age,
            )

    def test_clear_live_gates_without_economics_cannot_dispatch(self):
        result = self.run_dispatch()
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("ECONOMICS:GATE_RECEIPT_REQUIRED", result["reason_codes"])
        self.assertFalse(result["dispatch_authority"]["new_work_dispatch"])

    def test_real_upstream_gate_receipt_composes_to_dispatch(self):
        gate = compile_paid_work_effort_value_gate(real_gate_request())
        payload = raw(gate)
        with patch(
            "concierge.revenue_dispatch.qualify_live_revenue_intake",
            return_value=intake(),
        ), patch(
            "concierge.revenue_dispatch.inspect_bounty_availability",
            return_value=availability(),
        ), patch(
            "concierge.revenue_dispatch._LIVE_CASH_EVALUATOR",
            return_value=live_cash(),
        ):
            result = qualify_available_live_revenue_intake(
                "acme/widgets",
                17,
                gate_receipt_bytes=payload,
                work_id="work-17",
                expected_gate_receipt_bytes_sha256=hashlib.sha256(payload).hexdigest(),
                expected_policy_sha256=gate["policy_sha256"],
                dispatch_as_of="2026-09-16T20:05:00Z",
            )
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["economic_admission"]["receipt_sha256"], gate["receipt_sha256"])
        self.assertEqual(result["economic_admission"]["policy_sha256"], gate["policy_sha256"])

    def test_verified_fresh_go_binds_exact_provenance(self):
        gate = receipt()
        payload = raw(gate)
        result = self.run_dispatch(gate_bytes=payload, work_id="work-17")
        self.assertTrue(result["dispatch"])
        economics = result["economic_admission"]
        self.assertEqual(economics["decision"], "GO")
        self.assertEqual(economics["gate_receipt_bytes_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(economics["policy_sha256"], POLICY_SHA)
        self.assertEqual(economics["request_sha256"], REQUEST_SHA)
        self.assertEqual(economics["dispatch_as_of"], "2026-09-16T20:30:00Z")
        self.assertRegex(economics["binding_sha256"], r"^[0-9a-f]{64}$")
        authority = result["dispatch_authority"]
        self.assertEqual(authority["evidence_binding_sha256"], economics["binding_sha256"])
        self.assertTrue(authority["internal_implementation_only"])
        self.assertEqual(
            authority["live_cash"], "verified_source_bound_50_10_admission"
        )
        self.assertEqual(
            result["live_cash_admission"]["disposition"], "ACTIVE_REVIEW"
        )
        self.assertFalse(authority["external_claim_authority"])
        self.assertFalse(authority["external_submission_authority"])
        self.assertFalse(authority["payment_cash_or_revenue_authority"])

    def test_every_non_go_decision_fails_closed_with_binding(self):
        for decision in ("HOLD_VALUE_UNKNOWN", "HOLD_ACCOUNT_GATE", "SKIP_ECONOMICS"):
            with self.subTest(decision=decision):
                result = self.run_dispatch(gate=receipt(decision=decision), work_id="work-17")
                self.assertFalse(result["dispatch"])
                self.assertEqual(result["disposition"], "HOLD")
                self.assertIn(f"ECONOMICS:{decision}", result["reason_codes"])
                self.assertRegex(result["economic_admission"]["binding_sha256"], r"^[0-9a-f]{64}$")

    def test_exact_byte_digest_mismatch_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "exact-byte"):
            self.run_dispatch(gate=receipt(), work_id="work-17", expected_bytes_sha="0" * 64)

    def test_policy_digest_mismatch_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "policy sha256 mismatch"):
            self.run_dispatch(gate=receipt(), work_id="work-17", expected_policy_sha="0" * 64)

    def test_source_mismatch_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "source mismatch"):
            self.run_dispatch(gate=receipt(source="https://github.com/acme/other/issues/17"), work_id="work-17")

    def test_work_id_mismatch_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "work_id mismatch"):
            self.run_dispatch(gate=receipt(work_id="other"), work_id="work-17")

    def test_stale_receipt_uses_explicit_dispatch_time(self):
        with self.assertRaisesRegex(RevenueDispatchError, "stale"):
            self.run_dispatch(gate=receipt(at="2026-09-16T18:00:00Z"), work_id="work-17")

    def test_future_receipt_uses_explicit_dispatch_time(self):
        with self.assertRaisesRegex(RevenueDispatchError, "future"):
            self.run_dispatch(gate=receipt(at="2026-09-16T21:00:00Z"), work_id="work-17")

    def test_invalid_dispatch_as_of_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "dispatch_as_of"):
            self.run_dispatch(gate=receipt(), work_id="work-17", dispatch_as_of="now")

    def test_authority_amplification_fails(self):
        bad = receipt()
        bad["authority"]["external_claim_authority"] = True
        with self.assertRaisesRegex(RevenueDispatchError, "authority"):
            self.run_dispatch(gate=bad, work_id="work-17")

    def test_authority_extension_fails(self):
        bad = receipt()
        bad["authority"]["future_authority"] = False
        with self.assertRaisesRegex(RevenueDispatchError, "authority schema"):
            self.run_dispatch(gate=bad, work_id="work-17")

    def test_invalid_max_age_bool_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "gate_max_age_seconds"):
            self.run_dispatch(gate=receipt(), work_id="work-17", max_age=True)

    def test_tampered_receipt_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "integrity"):
            self.run_dispatch(gate=receipt(), work_id="work-17", verify=False)

    def test_25_dollar_pile_blocks_internal_dispatch(self):
        result = self.run_dispatch(
            gate=receipt(),
            work_id="work-17",
            cash=live_cash(
                disposition="PILE_SAVE_UP",
                route="bounty_pile_10_49",
                amount="25",
            ),
        )
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("LIVE_CASH:PILE_SAVE_UP", result["reason_codes"])
        self.assertEqual(
            result["live_cash_admission"]["fixed_amount_usd"], "25"
        )
        self.assertFalse(result["dispatch_authority"]["internal_implementation_only"])

    def test_below_10_prune_rejects_internal_dispatch(self):
        result = self.run_dispatch(
            gate=receipt(),
            work_id="work-17",
            cash=live_cash(
                disposition="PRUNE_BELOW_DOLLAR_FLOOR",
                route=None,
                amount="5",
                reasons=("FIXED_USD_REWARD_BELOW_10",),
            ),
        )
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "REJECT")
        self.assertIn(
            "LIVE_CASH:PRUNE_BELOW_DOLLAR_FLOOR", result["reason_codes"]
        )

    def test_nonfixed_mixed_and_no_fixed_cash_all_hold(self):
        cases = (
            live_cash(
                disposition="HOLD_NON_FIXED_USD_REWARD",
                route=None,
                amount="75",
                fixed_semantics=False,
                reasons=("USD_REWARD_NOT_FIXED_GUARANTEED_AMOUNT",),
            ),
            live_cash(
                disposition="HOLD_MIXED_REWARD_CURRENCY",
                route=None,
                amount="75",
                reasons=("MIXED_USD_AND_NATIVE_TOKEN_REWARD",),
            ),
            live_cash(
                disposition="HOLD_NO_FIXED_USD_REWARD",
                route=None,
                amount=None,
                currency=None,
                fixed_semantics=False,
                reasons=("FIXED_USD_REWARD_NOT_OBSERVED",),
            ),
        )
        for cash in cases:
            with self.subTest(disposition=cash["disposition"]):
                result = self.run_dispatch(
                    gate=receipt(), work_id="work-17", cash=cash
                )
                self.assertFalse(result["dispatch"])
                self.assertEqual(result["disposition"], "HOLD")
                self.assertIn(
                    f"LIVE_CASH:{cash['disposition']}", result["reason_codes"]
                )

    def test_live_cash_target_binding_is_exact(self):
        with self.assertRaisesRegex(RevenueDispatchError, "target binding"):
            self.run_dispatch(
                gate=receipt(),
                work_id="work-17",
                cash=live_cash(repo="other/widgets"),
            )

    def test_active_live_cash_route_corruption_fails_closed(self):
        bad = live_cash(route="bounty_pile_10_49")
        with self.assertRaisesRegex(RevenueDispatchError, "active live cash"):
            self.run_dispatch(gate=receipt(), work_id="work-17", cash=bad)

    def test_live_cash_authority_amplification_fails_closed(self):
        bad = live_cash()
        bad["authority"]["implementation_authority"] = True
        seal_live_cash(bad)
        with self.assertRaisesRegex(RevenueDispatchError, "authority ceiling"):
            self.run_dispatch(gate=receipt(), work_id="work-17", cash=bad)

    def test_live_cash_digest_tamper_fails_closed(self):
        bad = live_cash()
        bad["economics"]["fixed_amount"] = "500"
        with self.assertRaisesRegex(RevenueDispatchError, "sha256 mismatch"):
            self.run_dispatch(gate=receipt(), work_id="work-17", cash=bad)

    def test_live_cash_provider_failure_fails_closed(self):
        from concierge.bounty_live_cash_admission import LiveCashAdmissionError

        with self.assertRaisesRegex(
            RevenueDispatchError, "live source-bound cash admission failed"
        ):
            self.run_dispatch(
                gate=receipt(),
                work_id="work-17",
                cash_error=LiveCashAdmissionError("provider read failed"),
            )

    def test_economic_hold_skips_live_cash_provider(self):
        gate = receipt(decision="HOLD_VALUE_UNKNOWN")
        payload = raw(gate)
        with patch(
            "concierge.revenue_dispatch.qualify_live_revenue_intake",
            return_value=intake(),
        ), patch(
            "concierge.revenue_dispatch.inspect_bounty_availability",
            return_value=availability(),
        ), patch(
            "concierge.revenue_dispatch.verify_paid_work_receipt",
            return_value=True,
        ), patch(
            "concierge.revenue_dispatch._LIVE_CASH_EVALUATOR"
        ) as cash_mock:
            result = qualify_available_live_revenue_intake(
                "acme/widgets",
                17,
                gate_receipt_bytes=payload,
                work_id="work-17",
                expected_gate_receipt_bytes_sha256=hashlib.sha256(payload).hexdigest(),
                expected_policy_sha256=POLICY_SHA,
                dispatch_as_of="2026-09-16T20:30:00Z",
            )
        cash_mock.assert_not_called()
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["live_cash_admission"]["status"], "NOT_CHECKED")
        self.assertEqual(result["dispatch_authority"]["live_cash"], "not_reached")

    def test_deterministic_binding_for_same_evidence_and_time(self):
        gate = receipt()
        one = self.run_dispatch(gate=gate, work_id="work-17")
        two = self.run_dispatch(gate=gate, work_id="work-17")
        self.assertEqual(one["economic_admission"], two["economic_admission"])

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    @patch("concierge.revenue_dispatch.verify_paid_work_receipt")
    def test_upstream_hold_skips_availability_and_economics(self, verify_mock, intake_mock, avail_mock):
        incoming = intake(dispatch=False, disposition="HOLD")
        incoming["reason_codes"] = ["QUALIFICATION:SATURATED_COMPETITION"]
        intake_mock.return_value = incoming
        result = qualify_available_live_revenue_intake(
            "acme/widgets",
            17,
            gate_receipt_bytes=raw(receipt()),
            work_id="work-17",
        )
        avail_mock.assert_not_called()
        verify_mock.assert_not_called()
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["economic_admission"]["status"], "NOT_CHECKED")

    def test_availability_hold_skips_economics(self):
        with patch("concierge.revenue_dispatch.qualify_live_revenue_intake", return_value=intake()), patch(
            "concierge.revenue_dispatch.inspect_bounty_availability",
            return_value=availability(dispatch=False, reason="MAINTAINER_TERMINAL_OUTCOME"),
        ), patch("concierge.revenue_dispatch.verify_paid_work_receipt") as verify_mock:
            result = qualify_available_live_revenue_intake(
                "acme/widgets", 17, gate_receipt_bytes=raw(receipt()), work_id="work-17"
            )
        verify_mock.assert_not_called()
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["economic_admission"]["status"], "NOT_CHECKED")

    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_malformed_intake_dispatch_fails_closed(self, intake_mock):
        value = intake()
        value["dispatch"] = 1
        intake_mock.return_value = value
        with self.assertRaises(RevenueDispatchError):
            qualify_available_live_revenue_intake("acme/widgets", 17)

    def test_duplicate_receipt_json_key_rejected(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
            handle.write(b'{"decision":"GO","decision":"SKIP_ECONOMICS"}')
            path = handle.name
        with self.assertRaisesRegex(RevenueDispatchError, "duplicate JSON key"):
            _load_gate_receipt(path)

    def test_bom_receipt_rejected(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
            handle.write(b"\xef\xbb\xbf{}")
            path = handle.name
        with self.assertRaisesRegex(RevenueDispatchError, "BOM"):
            _load_gate_receipt(path)

    def test_float_receipt_rejected(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
            handle.write(b'{"x":1.5}')
            path = handle.name
        with self.assertRaisesRegex(RevenueDispatchError, "forbidden float"):
            _load_gate_receipt(path)


if __name__ == "__main__":
    unittest.main()
