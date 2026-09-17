from __future__ import annotations

from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import patch, sentinel

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


def receipt(
    *,
    decision="GO",
    work_id="work-17",
    source="https://github.com/acme/widgets/issues/17",
    at="2026-09-16T20:00:00Z",
):
    return {
        "schema": "paid-work-effort-value-gate-receipt/v1",
        "work_id": work_id,
        "canonical_source_url": source,
        "canonical_source_identity": "github:acme/widgets/issues/17",
        "as_of": at,
        "decision": decision,
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


NOW = datetime(2026, 9, 16, 20, 30, 0, tzinfo=timezone.utc)


class RevenueDispatchTests(unittest.TestCase):
    def run_dispatch(
        self,
        *,
        gate=None,
        work_id=None,
        avail=None,
        incoming=None,
        max_age=3600,
    ):
        with patch(
            "concierge.revenue_dispatch.qualify_live_revenue_intake",
            return_value=incoming or intake(),
        ), patch(
            "concierge.revenue_dispatch.inspect_bounty_availability",
            return_value=avail or availability(),
        ), patch(
            "concierge.revenue_dispatch._trusted_now", return_value=NOW
        ), patch(
            "concierge.revenue_dispatch.verify_paid_work_receipt",
            return_value=True,
        ):
            return qualify_available_live_revenue_intake(
                "acme/widgets",
                17,
                listing_url="https://mirror.example/item",
                token="TOKEN",
                session=sentinel.session,
                max_pages=3,
                saturation_threshold=2,
                gate_receipt=gate,
                work_id=work_id,
                gate_max_age_seconds=max_age,
            )

    def test_clear_live_gates_without_economics_cannot_dispatch(self):
        result = self.run_dispatch()
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("ECONOMICS:GATE_RECEIPT_REQUIRED", result["reason_codes"])
        self.assertFalse(result["dispatch_authority"]["new_work_dispatch"])

    def test_verified_fresh_go_allows_internal_implementation_only(self):
        result = self.run_dispatch(gate=receipt(), work_id="work-17")
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["economic_admission"]["decision"], "GO")
        authority = result["dispatch_authority"]
        self.assertTrue(authority["new_work_dispatch"])
        self.assertTrue(authority["internal_implementation_only"])
        self.assertFalse(authority["external_claim_authority"])
        self.assertFalse(authority["external_submission_authority"])
        self.assertFalse(authority["payment_cash_or_revenue_authority"])

    def test_every_non_go_decision_fails_closed(self):
        for decision in (
            "HOLD_VALUE_UNKNOWN",
            "HOLD_ACCOUNT_GATE",
            "SKIP_ECONOMICS",
        ):
            with self.subTest(decision=decision):
                result = self.run_dispatch(
                    gate=receipt(decision=decision), work_id="work-17"
                )
                self.assertFalse(result["dispatch"])
                self.assertEqual(result["disposition"], "HOLD")
                self.assertIn(f"ECONOMICS:{decision}", result["reason_codes"])

    def test_tampered_receipt_fails(self):
        with patch(
            "concierge.revenue_dispatch.qualify_live_revenue_intake",
            return_value=intake(),
        ), patch(
            "concierge.revenue_dispatch.inspect_bounty_availability",
            return_value=availability(),
        ), patch(
            "concierge.revenue_dispatch._trusted_now", return_value=NOW
        ), patch(
            "concierge.revenue_dispatch.verify_paid_work_receipt",
            return_value=False,
        ):
            with self.assertRaisesRegex(RevenueDispatchError, "integrity"):
                qualify_available_live_revenue_intake(
                    "acme/widgets",
                    17,
                    gate_receipt=receipt(),
                    work_id="work-17",
                )

    def test_source_mismatch_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "source mismatch"):
            self.run_dispatch(
                gate=receipt(source="https://github.com/acme/other/issues/17"),
                work_id="work-17",
            )

    def test_work_id_mismatch_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "work_id mismatch"):
            self.run_dispatch(gate=receipt(work_id="other"), work_id="work-17")

    def test_stale_receipt_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "stale"):
            self.run_dispatch(
                gate=receipt(at="2026-09-16T18:00:00Z"), work_id="work-17"
            )

    def test_future_receipt_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "future"):
            self.run_dispatch(
                gate=receipt(at="2026-09-16T21:00:00Z"), work_id="work-17"
            )

    def test_authority_amplification_fails(self):
        bad = receipt()
        bad["authority"]["external_claim_authority"] = True
        with self.assertRaisesRegex(RevenueDispatchError, "authority"):
            self.run_dispatch(gate=bad, work_id="work-17")

    def test_invalid_max_age_bool_fails(self):
        with self.assertRaisesRegex(RevenueDispatchError, "gate_max_age_seconds"):
            self.run_dispatch(gate=receipt(), work_id="work-17", max_age=True)

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    @patch("concierge.revenue_dispatch.verify_paid_work_receipt")
    def test_upstream_hold_skips_availability_and_economics(
        self, verify_mock, intake_mock, avail_mock
    ):
        incoming = intake(dispatch=False, disposition="HOLD")
        incoming["reason_codes"] = ["QUALIFICATION:SATURATED_COMPETITION"]
        intake_mock.return_value = incoming
        result = qualify_available_live_revenue_intake(
            "acme/widgets", 17, gate_receipt=receipt(), work_id="work-17"
        )
        avail_mock.assert_not_called()
        verify_mock.assert_not_called()
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["economic_admission"]["status"], "NOT_CHECKED")

    def test_availability_hold_skips_economics(self):
        with patch(
            "concierge.revenue_dispatch.qualify_live_revenue_intake",
            return_value=intake(),
        ), patch(
            "concierge.revenue_dispatch.inspect_bounty_availability",
            return_value=availability(
                dispatch=False, reason="MAINTAINER_TERMINAL_OUTCOME"
            ),
        ), patch(
            "concierge.revenue_dispatch.verify_paid_work_receipt"
        ) as verify_mock:
            result = qualify_available_live_revenue_intake(
                "acme/widgets", 17, gate_receipt=receipt(), work_id="work-17"
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
            handle.write(
                b'{"decision":"GO","decision":"SKIP_ECONOMICS"}'
            )
            path = handle.name
        with self.assertRaisesRegex(RevenueDispatchError, "duplicate JSON key"):
            _load_gate_receipt(path)


if __name__ == "__main__":
    unittest.main()
