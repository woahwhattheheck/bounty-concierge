from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, sentinel

from concierge.revenue_dispatch import (
    RevenueDispatchError,
    _load_json_object,
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


def gate_pair(
    *,
    decision="GO",
    reason_codes=None,
    observed_holds=None,
    work_id="acme/widgets#17",
    source_url="https://github.com/acme/widgets/issues/17",
):
    if reason_codes is None:
        reason_codes = ["ALL_GATES_CLEAR"] if decision == "GO" else ["TEST_REASON"]
    request = {
        "schema": "paid-work-effort-value-gate/v1",
        "candidate": {
            "work_id": work_id,
            "canonical_source_url": source_url,
        },
    }
    receipt = {
        "schema": "paid-work-effort-value-receipt/v1",
        "work_id": work_id,
        "canonical_source_url": source_url,
        "canonical_source_identity": "sha256:source-id",
        "decision": decision,
        "reason_codes": list(reason_codes),
        "observed_hold_reasons": list(observed_holds or []),
        "request_sha256": "a" * 64,
        "policy_sha256": "b" * 64,
        "receipt_sha256": "c" * 64,
    }
    return request, receipt


class RevenueDispatchTests(unittest.TestCase):
    def setUp(self):
        self.verify = patch(
            "concierge.revenue_dispatch.verify_paid_work_gate_receipt",
            return_value=True,
        ).start()
        self.compile = patch(
            "concierge.revenue_dispatch.compile_paid_work_effort_value_gate"
        ).start()
        self.addCleanup(patch.stopall)

    def _pair(self, **kwargs):
        request, receipt = gate_pair(**kwargs)
        self.compile.return_value = receipt.copy()
        return request, receipt

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_all_three_gates_clear_authorizes_new_work(self, intake_mock, avail_mock):
        request, receipt = self._pair()
        intake_mock.return_value = intake()
        avail_mock.return_value = availability()
        result = qualify_available_live_revenue_intake(
            "acme/widgets",
            17,
            listing_url="https://mirror.example/item",
            token="TOKEN",
            session=sentinel.session,
            max_pages=3,
            saturation_threshold=2,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["paid_work_gate"]["decision"], "GO")
        self.assertTrue(result["paid_work_gate"]["verified"])
        self.assertEqual(
            result["dispatch_authority"]["paid_work_gate"],
            "verified_exact_receipt_binding",
        )
        self.verify.assert_called_once_with(receipt)
        self.compile.assert_called_once_with(request)
        avail_mock.assert_called_once_with(
            "acme/widgets", 17, "TOKEN", session=sentinel.session, max_pages=3
        )

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_upstream_hold_skips_gate_and_availability(self, intake_mock, avail_mock):
        value = intake(dispatch=False, disposition="HOLD")
        value["reason_codes"] = ["QUALIFICATION:SATURATED_COMPETITION"]
        intake_mock.return_value = value
        result = qualify_available_live_revenue_intake("acme/widgets", 17)
        self.verify.assert_not_called()
        self.compile.assert_not_called()
        avail_mock.assert_not_called()
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["paid_work_gate"]["decision"], "NOT_CHECKED")
        self.assertEqual(
            result["availability"]["reason_code"],
            "UPSTREAM_INTAKE_NOT_DISPATCHABLE",
        )

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_missing_gate_evidence_holds_before_availability(self, intake_mock, avail_mock):
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake("acme/widgets", 17)
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("PAID_WORK_GATE:GATE_EVIDENCE_REQUIRED", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_receipt_verification_failure_holds(self, intake_mock, avail_mock):
        request, receipt = self._pair()
        self.verify.return_value = False
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertFalse(result["dispatch"])
        self.assertIn("PAID_WORK_GATE:RECEIPT_VERIFICATION_FAILED", result["reason_codes"])
        self.compile.assert_not_called()
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_receipt_must_exactly_recompile_from_retained_request(self, intake_mock, avail_mock):
        request, receipt = self._pair()
        recomputed = receipt.copy()
        recomputed["receipt_sha256"] = "d" * 64
        self.compile.return_value = recomputed
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertFalse(result["dispatch"])
        self.assertIn("PAID_WORK_GATE:RECEIPT_REQUEST_MISMATCH", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_go_cannot_replay_across_candidate_identity(self, intake_mock, avail_mock):
        request, receipt = self._pair(work_id="acme/widgets#99")
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertFalse(result["dispatch"])
        self.assertIn("PAID_WORK_GATE:CANDIDATE_ID_MISMATCH", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_go_cannot_replay_across_canonical_source(self, intake_mock, avail_mock):
        request, receipt = self._pair(source_url="https://github.com/acme/widgets/issues/99")
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertFalse(result["dispatch"])
        self.assertIn("PAID_WORK_GATE:CANONICAL_SOURCE_MISMATCH", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_skip_economics_rejects_and_propagates_reason(self, intake_mock, avail_mock):
        request, receipt = self._pair(
            decision="SKIP_ECONOMICS",
            reason_codes=["GROSS_ECONOMICS_INELIGIBLE"],
        )
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "REJECT")
        self.assertIn("PAID_WORK_GATE:SKIP_ECONOMICS", result["reason_codes"])
        self.assertIn("PAID_WORK_GATE:GROSS_ECONOMICS_INELIGIBLE", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_hold_value_unknown_stops_premium_dispatch(self, intake_mock, avail_mock):
        request, receipt = self._pair(
            decision="HOLD_VALUE_UNKNOWN",
            reason_codes=["NONCASH_VALUE_UNAUTHORIZED"],
        )
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn("PAID_WORK_GATE:NONCASH_VALUE_UNAUTHORIZED", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_hold_account_gate_stops_premium_dispatch(self, intake_mock, avail_mock):
        request, receipt = self._pair(
            decision="HOLD_ACCOUNT_GATE",
            reason_codes=["ACCOUNT_KYC_NOT_READY"],
        )
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn("PAID_WORK_GATE:ACCOUNT_KYC_NOT_READY", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_unsupported_decision_fails_closed(self, intake_mock, avail_mock):
        request, receipt = self._pair(decision="DO_IT_ANYWAY")
        intake_mock.return_value = intake()
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertFalse(result["dispatch"])
        self.assertIn("PAID_WORK_GATE:UNSUPPORTED_GATE_DECISION", result["reason_codes"])
        avail_mock.assert_not_called()

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_terminal_availability_overrides_verified_go(self, intake_mock, avail_mock):
        request, receipt = self._pair()
        intake_mock.return_value = intake()
        avail_mock.return_value = availability(dispatch=False, reason="MAINTAINER_TERMINAL_OUTCOME")
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("AVAILABILITY:MAINTAINER_TERMINAL_OUTCOME", result["reason_codes"])

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_reject_is_not_downgraded_by_availability_after_go(self, intake_mock, avail_mock):
        request, receipt = self._pair()
        intake_mock.return_value = intake(dispatch=True, disposition="REJECT")
        avail_mock.return_value = availability(dispatch=False, reason="MAINTAINER_TERMINAL_OUTCOME")
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17,
            paid_work_gate_request=request,
            paid_work_gate_receipt=receipt,
        )
        self.assertEqual(result["disposition"], "REJECT")
        self.assertFalse(result["dispatch"])

    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_malformed_intake_dispatch_fails_closed(self, intake_mock):
        value = intake()
        value["dispatch"] = 1
        intake_mock.return_value = value
        with self.assertRaises(RevenueDispatchError):
            qualify_available_live_revenue_intake("acme/widgets", 17)

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_non_dispatch_availability_requires_reason(self, intake_mock, avail_mock):
        request, receipt = self._pair()
        intake_mock.return_value = intake()
        avail_mock.return_value = availability(dispatch=False, reason=None)
        with self.assertRaises(RevenueDispatchError):
            qualify_available_live_revenue_intake(
                "acme/widgets", 17,
                paid_work_gate_request=request,
                paid_work_gate_receipt=receipt,
            )

    def test_cli_json_loader_rejects_duplicate_keys_and_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"candidate":{},"candidate":{}}', encoding="utf-8")
            with self.assertRaises(RevenueDispatchError):
                _load_json_object(str(duplicate), label="gate request")
            nonfinite = root / "nan.json"
            nonfinite.write_text('{"x":NaN}', encoding="utf-8")
            with self.assertRaises(RevenueDispatchError):
                _load_json_object(str(nonfinite), label="gate request")


if __name__ == "__main__":
    unittest.main()
