# SPDX-License-Identifier: MIT
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import unittest
from unittest import mock

from concierge import reinvestment_allocator as ra

NOW = datetime(2026, 9, 14, 23, 58, 0, tzinfo=timezone.utc)
KEY_HEX = "11" * 32
PROVIDER = "owner-review-host"
PRINCIPAL = "22" * 32


def manifest():
    return [{"repo": "acme/widgets", "pr": 17, "expected_head_sha": "3" * 40}]


def bindings():
    return [
        {
            "repo": "acme/widgets",
            "pr": 17,
            "transfer_id": "real-transfer-but-attribution-needs-auth",
        }
    ]


def effort(minutes=1):
    return {"schema_version": 1, "items": [{"repo": "acme/widgets", "pr": 17, "active_minutes": minutes}]}


def taxonomy():
    return {
        "schema": "realized-reinvestment-taxonomy/v1",
        "version": 1,
        "mappings": [{"repo": "acme/widgets", "pr": 17, "family": "bounty-fix"}],
        "taxonomy_sha256": "4" * 64,
    }


def policy():
    return {
        "schema": "realized-reinvestment-policy/v1",
        "version": 1,
        "minimum_samples": 1,
        "minimum_nonzero_cash_samples": 1,
        "minimum_median_rtc_per_hour": "1",
        "maximum_family_capacity_bps": 10000,
        "policy_sha256": "5" * 64,
    }


def core_receipt(scale=True):
    payload = {
        "schema": "realized-reinvestment-review/v2",
        "source_economics_receipt_sha256": "6" * 64,
        "source_economics_scope_sha256": "7" * 64,
        "taxonomy_sha256": "4" * 64,
        "policy_sha256": "5" * 64,
        "capacity_minutes": 60,
        "summary": {
            "family_count": 1,
            "scale_review_eligible_families": 1 if scale else 0,
            "recommended_review_capacity_minutes": 60 if scale else 0,
            "unallocated_capacity_minutes": 0 if scale else 60,
            "review_state": "READY_FOR_OWNER_REINVESTMENT_REVIEW" if scale else "NO_SCALE_REVIEW_ELIGIBLE",
        },
        "authority": {
            "closeout_state": "live_github_reacquired_in_process",
            "wallet_history": "canonical_provider_reacquired_in_process",
            "economics": "compiled_in_process_from_live_provider_observations",
            "cash_evidence_authority": "reacquired_not_inherited",
        },
        "families": [
            {
                "family": "bounty-fix",
                "state": "SCALE_REVIEW_ELIGIBLE" if scale else "HOLD",
                "recommended_review_capacity_minutes": 60 if scale else 0,
            }
        ],
    }
    payload["receipt_sha256"] = ra._core._sha256(payload)
    return payload


def authority_for(
    *,
    manifest_value=None,
    bindings_value=None,
    effort_value=None,
    wallet="wallet-1",
    captured_at="2026-09-14T23:58:00Z",
    provider=PROVIDER,
    principal=PRINCIPAL,
):
    m = manifest() if manifest_value is None else manifest_value
    b = bindings() if bindings_value is None else bindings_value
    e = effort() if effort_value is None else effort_value
    auth = {
        "schema_version": 1,
        "purpose": "realized-reinvestment-commercial-evidence-authority/v1",
        "provider": provider,
        "principal_sha256": principal,
        "captured_at": captured_at,
        "scope_sha256": ra.commercial_evidence_scope_sha256(m, b, e, wallet=wallet),
    }
    auth["signature_sha256"] = hmac.new(
        bytes.fromhex(KEY_HEX),
        ra._canonical_json(auth),
        hashlib.sha256,
    ).hexdigest()
    return auth


class ReinvestmentAuthorityAdapterTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                "REALIZED_REINVESTMENT_HMAC_KEY_HEX": KEY_HEX,
                "REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER": PROVIDER,
                "REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256": PRINCIPAL,
            },
            clear=False,
        )
        self.env.start()
        self.clock = mock.patch.object(ra, "_utc_now", return_value=NOW)
        self.clock.start()
        self.core = mock.patch.object(
            ra._core, "compile_reinvestment_review", return_value=core_receipt()
        )
        self.core_call = self.core.start()

    def tearDown(self):
        mock.patch.stopall()

    def compile(self, authority=None, **changes):
        return ra.compile_reinvestment_review(
            changes.get("manifest_value", manifest()),
            changes.get("bindings_value", bindings()),
            changes.get("effort_value", effort()),
            authority_for() if authority is None else authority,
            changes.get("taxonomy_value", taxonomy()),
            changes.get("policy_value", policy()),
            changes.get("capacity_minutes", 60),
            wallet=changes.get("wallet", "wallet-1"),
        )

    def test_valid_authority_preserves_scale_only_as_owner_review(self):
        receipt = self.compile()
        self.assertEqual(receipt["schema"], "realized-reinvestment-review/v3")
        self.assertEqual(receipt["families"][0]["state"], "SCALE_REVIEW_ELIGIBLE")
        self.assertEqual(
            receipt["authority"]["commercial_attribution_effort_authority"],
            "fresh_host_hmac_authenticated",
        )
        self.assertFalse(receipt["authority"]["unsigned_payment_attribution_accepted"])
        self.assertFalse(receipt["authority"]["unsigned_effort_evidence_accepted"])
        self.assertFalse(receipt["authority"]["spend_authorized"])
        self.assertFalse(receipt["authority"]["external_contact_authorized"])
        self.assertFalse(receipt["authority"]["payment_or_wallet_mutation_authorized"])
        self.assertTrue(ra.verify_receipt_integrity_only(receipt))

    def test_missing_authority_blocks_one_minute_unrelated_transfer_before_core(self):
        with self.assertRaises(ra.ReinvestmentInputError):
            ra.compile_reinvestment_review(
                manifest(), bindings(), effort(1), None, taxonomy(), policy(), 60, wallet="wallet-1"
            )
        self.core_call.assert_not_called()

    def test_forged_signature_blocks_before_core(self):
        auth = authority_for()
        auth["signature_sha256"] = "0" * 64
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "HMAC mismatch"):
            self.compile(authority=auth)
        self.core_call.assert_not_called()

    def test_wrong_provider_blocks(self):
        auth = authority_for(provider="other-host")
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "provider"):
            self.compile(authority=auth)

    def test_wrong_principal_blocks(self):
        auth = authority_for(principal="9" * 64)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "principal"):
            self.compile(authority=auth)

    def test_missing_host_configuration_blocks(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "configuration"):
                self.compile()

    def test_short_host_key_blocks(self):
        with mock.patch.dict(os.environ, {"REALIZED_REINVESTMENT_HMAC_KEY_HEX": "11" * 8}, clear=False):
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "32 bytes"):
                self.compile()

    def test_stale_authority_blocks(self):
        auth = authority_for(captured_at="2026-09-14T23:52:59Z")
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "stale"):
            self.compile(authority=auth)

    def test_300_second_boundary_is_accepted(self):
        auth = authority_for(captured_at="2026-09-14T23:53:00Z")
        receipt = self.compile(authority=auth)
        self.assertEqual(receipt["schema"], "realized-reinvestment-review/v3")

    def test_future_authority_blocks(self):
        auth = authority_for(captured_at="2026-09-14T23:58:01Z")
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "future"):
            self.compile(authority=auth)

    def test_fractional_capture_timestamp_blocks(self):
        auth = authority_for()
        auth["captured_at"] = "2026-09-14T23:58:00.1Z"
        auth["signature_sha256"] = hmac.new(
            bytes.fromhex(KEY_HEX),
            ra._canonical_json({k: v for k, v in auth.items() if k != "signature_sha256"}),
            hashlib.sha256,
        ).hexdigest()
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "fractional seconds"):
            self.compile(authority=auth)

    def test_manifest_scope_change_blocks(self):
        changed = manifest() + [{"repo": "acme/other", "pr": 2, "expected_head_sha": "8" * 40}]
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "scope mismatch"):
            self.compile(manifest_value=changed)
        self.core_call.assert_not_called()

    def test_binding_scope_change_blocks(self):
        changed = deepcopy(bindings())
        changed[0]["transfer_id"] = "different-transfer"
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "scope mismatch"):
            self.compile(bindings_value=changed)

    def test_effort_scope_change_blocks(self):
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "scope mismatch"):
            self.compile(effort_value=effort(2))

    def test_wallet_scope_change_blocks(self):
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "scope mismatch"):
            self.compile(wallet="wallet-2")

    def test_extra_authority_key_blocks(self):
        auth = authority_for()
        auth["trusted"] = True
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "exact keys"):
            self.compile(authority=auth)

    def test_bad_purpose_blocks(self):
        auth = authority_for()
        auth["purpose"] = "anything-else"
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "purpose"):
            self.compile(authority=auth)

    def test_bad_schema_blocks(self):
        auth = authority_for()
        auth["schema_version"] = 2
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "schema_version"):
            self.compile(authority=auth)

    def test_core_is_called_only_after_authority_and_with_frozen_inputs(self):
        self.compile()
        args, kwargs = self.core_call.call_args
        self.assertEqual(args[0], manifest())
        self.assertEqual(args[1], bindings())
        self.assertEqual(args[2], effort())
        self.assertEqual(kwargs["wallet"], "wallet-1")

    def test_core_non_object_blocks(self):
        self.core_call.return_value = "not-a-receipt"
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "non-object"):
            self.compile()

    def test_core_integrity_failure_blocks(self):
        broken = core_receipt()
        broken["summary"]["family_count"] = 99
        self.core_call.return_value = broken
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "self-integrity"):
            self.compile()

    def test_outer_receipt_tamper_is_detected(self):
        receipt = self.compile()
        changed = deepcopy(receipt)
        changed["families"][0]["recommended_review_capacity_minutes"] = 999
        self.assertFalse(ra.verify_receipt_integrity_only(changed))

    def test_authority_digest_changes_if_signature_changes(self):
        first = self.compile()
        auth = authority_for()
        auth["captured_at"] = "2026-09-14T23:57:59Z"
        auth["signature_sha256"] = hmac.new(
            bytes.fromhex(KEY_HEX),
            ra._canonical_json({k: v for k, v in auth.items() if k != "signature_sha256"}),
            hashlib.sha256,
        ).hexdigest()
        second = self.compile(authority=auth)
        self.assertNotEqual(
            first["authority"]["commercial_evidence_authority_sha256"],
            second["authority"]["commercial_evidence_authority_sha256"],
        )

    def test_scope_digest_is_key_order_deterministic(self):
        one = {"schema_version": 1, "items": [{"repo": "acme/widgets", "pr": 17, "active_minutes": 1}]}
        two = {"items": [{"active_minutes": 1, "pr": 17, "repo": "acme/widgets"}], "schema_version": 1}
        self.assertEqual(
            ra.commercial_evidence_scope_sha256(manifest(), bindings(), one, wallet="wallet-1"),
            ra.commercial_evidence_scope_sha256(manifest(), bindings(), two, wallet="wallet-1"),
        )

    def test_verify_current_accepts_exact_current_receipt(self):
        receipt = self.compile()
        self.assertTrue(
            ra.verify_reinvestment_receipt_current(
                receipt,
                manifest(),
                bindings(),
                effort(),
                authority_for(),
                taxonomy(),
                policy(),
                60,
                wallet="wallet-1",
            )
        )

    def test_verify_current_rejects_tampered_receipt(self):
        receipt = self.compile()
        receipt["summary"]["review_state"] = "PAY_ME"
        self.assertFalse(
            ra.verify_reinvestment_receipt_current(
                receipt,
                manifest(),
                bindings(),
                effort(),
                authority_for(),
                taxonomy(),
                policy(),
                60,
                wallet="wallet-1",
            )
        )

    def test_verify_current_fails_closed_on_core_input_error(self):
        self.core_call.side_effect = ra.ReinvestmentInputError("no")
        self.assertFalse(
            ra.verify_reinvestment_receipt_current(
                {"receipt_sha256": "0" * 64},
                manifest(),
                bindings(),
                effort(),
                authority_for(),
                taxonomy(),
                policy(),
                60,
                wallet="wallet-1",
            )
        )

    def test_commercial_authority_does_not_authenticate_planning_inputs(self):
        receipt = self.compile()
        self.assertFalse(receipt["authority"]["taxonomy_policy_authenticated_as_owner"])
        self.assertEqual(
            receipt["authority"]["taxonomy_policy_basis"],
            "caller_supplied_planning_inputs",
        )

    def test_wrapper_never_emits_raw_hmac_signature(self):
        receipt = self.compile()
        rendered = json.dumps(receipt, sort_keys=True)
        self.assertNotIn(authority_for()["signature_sha256"], rendered)


if __name__ == "__main__":
    unittest.main()
