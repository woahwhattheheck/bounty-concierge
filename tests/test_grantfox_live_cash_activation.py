# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from concierge.grantfox_live_cash_activation import (
    GrantFoxLiveCashActivationInputError,
    compile_live_cash_activation,
    verify_live_cash_activation_receipt,
)


def _activation(
    disposition: str = "APPLY_ELIGIBLE",
    *,
    owner: str = "ExampleOrg",
    repo: str = "demo",
    issue: int = 42,
) -> dict:
    return {
        "schema": "grantfox-activation-gate/v1",
        "disposition": disposition,
        "advisory_next_action": {
            "APPLY_ELIGIBLE": "APPLY_THROUGH_VERIFIED_PROVIDER_ROUTE",
            "WAIT_ASSIGNMENT": "WAIT_FOR_DURABLE_PROVIDER_ASSIGNMENT",
            "WAIT_DEPENDENCIES": "WAIT_FOR_PREREQUISITES_BEFORE_IMPLEMENTATION",
            "IMPLEMENT_ASSIGNED_SCOPE": "IMPLEMENT_ONLY_THE_ASSIGNED_PINNED_SCOPE",
            "REPLAN_BEFORE_APPLY": "REPLAN_FROM_PINNED_SOURCE_THEN_REFRESH_GATE",
            "HOLD_SOURCE": "REFRESH_OR_RECONCILE_PINNED_SOURCE",
            "HOLD_DEPENDENCIES": "REFRESH_OR_RECONCILE_DEPENDENCY_EVIDENCE",
            "HOLD_RECONCILE": "STOP_AND_RECONCILE_RECEIPTS",
        }[disposition],
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue,
            "actor_login": "woahwhattheheck",
        },
        "activation_receipt_sha256": "a" * 64,
    }


def _cash(
    disposition: str = "ACTIVE_REVIEW",
    *,
    route: str | None = "main_bounty_queue",
    amount: str | None = "100",
    currency: str | None = "USD",
    fixed_semantics: bool = True,
    owner: str = "ExampleOrg",
    repo: str = "demo",
    issue: int = 42,
) -> dict:
    return {
        "schema": "bounty-live-cash-admission-receipt/v1",
        "identity": {
            "repo": f"{owner}/{repo}",
            "issue_number": issue,
            "canonical_issue_url": (
                f"https://github.com/{owner}/{repo}/issues/{issue}"
            ),
        },
        "economics": {
            "currency": currency,
            "fixed_amount": amount,
            "fixed_semantics": fixed_semantics,
        },
        "disposition": disposition,
        "route": route,
        "receipt_sha256": "b" * 64,
    }


def _request(activation: dict, cash: dict) -> dict:
    return {
        "schema": "grantfox-live-cash-activation/v1",
        "activation_receipt": activation,
        "live_cash_receipt": cash,
    }


class GrantFoxLiveCashActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.verifiers = patch.multiple(
            "concierge.grantfox_live_cash_activation",
            verify_activation_receipt=lambda _: True,
            verify_live_cash_receipt=lambda *args, **kwargs: True,
        )
        self.verifiers.start()
        self.addCleanup(self.verifiers.stop)

    def test_apply_requires_current_active_main_queue_cash(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation(), _cash())
        )
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertEqual(receipt["economics"]["fixed_amount"], "100")
        self.assertEqual(
            receipt["anchors"]["activation_receipt_sha256"], "a" * 64
        )
        self.assertEqual(
            receipt["anchors"]["live_cash_receipt_sha256"], "b" * 64
        )

    def test_assigned_implementation_passes_only_with_active_cash(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation("IMPLEMENT_ASSIGNED_SCOPE"), _cash(amount="250"))
        )
        self.assertEqual(receipt["disposition"], "IMPLEMENT_ASSIGNED_SCOPE")

    def test_wait_state_remains_nonimplementation_when_cash_is_active(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation("WAIT_ASSIGNMENT"), _cash(amount="75"))
        )
        self.assertEqual(receipt["disposition"], "WAIT_ASSIGNMENT")
        self.assertEqual(
            receipt["advisory_next_action"],
            "WAIT_FOR_DURABLE_PROVIDER_ASSIGNMENT",
        )

    def test_10_to_49_pile_cannot_enter_active_grantfox_queue(self) -> None:
        receipt = compile_live_cash_activation(
            _request(
                _activation(),
                _cash(
                    disposition="PILE_SAVE_UP",
                    route="bounty_pile_10_49",
                    amount="49",
                ),
            )
        )
        self.assertEqual(receipt["disposition"], "HOLD_ECONOMICS")
        self.assertIn("LIVE_CASH_NOT_ACTIVE_REVIEW", receipt["reason_codes"])
        self.assertIn("LIVE_CASH_NOT_MAIN_BOUNTY_QUEUE", receipt["reason_codes"])
        self.assertIn("LIVE_CASH_BELOW_ACTIVE_FLOOR", receipt["reason_codes"])

    def test_nonfixed_semantics_fail_closed_even_if_shape_claims_active(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation(), _cash(amount="500", fixed_semantics=False))
        )
        self.assertEqual(receipt["disposition"], "HOLD_ECONOMICS")
        self.assertIn("LIVE_CASH_NOT_FIXED_SEMANTICS", receipt["reason_codes"])

    def test_non_usd_semantics_fail_closed(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation(), _cash(amount="100", currency="USDC"))
        )
        self.assertEqual(receipt["disposition"], "HOLD_ECONOMICS")
        self.assertIn("LIVE_CASH_NOT_FIXED_USD", receipt["reason_codes"])

    def test_activation_hold_dominates_even_when_cash_is_green(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation("HOLD_SOURCE"), _cash(amount="1000"))
        )
        self.assertEqual(receipt["disposition"], "HOLD_GRANTFOX_ACTIVATION")
        self.assertEqual(
            receipt["reason_codes"][0], "GRANTFOX_ACTIVATION_NOT_ACTIONABLE"
        )

    def test_identity_mismatch_is_rejected(self) -> None:
        with self.assertRaises(GrantFoxLiveCashActivationInputError):
            compile_live_cash_activation(
                _request(_activation(issue=42), _cash(issue=43))
            )

    def test_tampered_activation_is_rejected_before_dispatch(self) -> None:
        with patch(
            "concierge.grantfox_live_cash_activation.verify_activation_receipt",
            return_value=False,
        ):
            with self.assertRaises(GrantFoxLiveCashActivationInputError):
                compile_live_cash_activation(
                    _request(_activation(), _cash())
                )

    def test_stale_live_cash_receipt_is_rejected_before_dispatch(self) -> None:
        with patch(
            "concierge.grantfox_live_cash_activation.verify_live_cash_receipt",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                GrantFoxLiveCashActivationInputError,
                "current canonical GitHub state",
            ):
                compile_live_cash_activation(
                    _request(_activation(), _cash())
                )

    def test_receipt_tamper_fails_verification(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation(), _cash())
        )
        self.assertTrue(verify_live_cash_activation_receipt(receipt))
        tampered = copy.deepcopy(receipt)
        tampered["economics"]["fixed_amount"] = "1000000"
        self.assertFalse(verify_live_cash_activation_receipt(tampered))

    def test_nested_evidence_shape_is_closed(self) -> None:
        receipt = compile_live_cash_activation(
            _request(_activation(), _cash())
        )
        tampered = copy.deepcopy(receipt)
        tampered["evidence"]["extra"] = {}
        self.assertFalse(verify_live_cash_activation_receipt(tampered))


if __name__ == "__main__":
    unittest.main()
