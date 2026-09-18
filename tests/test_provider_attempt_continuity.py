from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "concierge" / "provider_attempt_continuity.py"
GATE_PATH = ROOT / "concierge" / "_payout_delivery_gate_core.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


m = load("provider_attempt_continuity_test_target", MODULE_PATH)
gate = load("payout_delivery_gate_core_test_target", GATE_PATH)


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def delivery():
    body = {
        "schema": m.DELIVERY_SCHEMA,
        "as_of": "2026-09-18T07:47:00Z",
        "request_sha256": h("request"),
        "arbitration_sha256": h("arb"),
        "history_sha256": h("history"),
        "request_id": "rustchain-100-eight-prs",
        "opportunity_ref": "Scottcjn/rustchain-bounties#100",
        "target_kind": "EMAIL",
        "target_route_sha256": h("sophia-route"),
        "message_sha256": h("consolidated-message"),
        "work_url": "https://github.com/woahwhattheheck/bounty-concierge/pull/58",
        "work_sha": "1" * 40,
        "reward_reference": "10 RTC per accepted improvement PR, scope-dependent",
        "claimant_label": "woahwhattheheck",
        "owner_seat": "Z-ProviderContinuity",
        "arbiter": "single-writer-control",
        "arbitration_ref": "https://example.invalid/lease/1",
        "lease_issued_at": "2026-09-18T07:46:00Z",
        "lease_expires_at": "2026-09-18T07:56:00Z",
        "history_captured_at": "2026-09-18T07:46:30Z",
        "prior_relevant_mutation_count": 0,
        "blocking_prior_mutation_count": 0,
        "state": "READY_FOR_ONE_PROVIDER_MUTATION",
        "reason": "selected_owner_with_fresh_complete_history_and_no_equivalent_prior_send",
        "one_provider_mutation_authorized": True,
        "external_action_performed": False,
        "sponsor_receipt_inferred": False,
        "sponsor_acceptance_inferred": False,
        "merge_inferred": False,
        "settlement_inferred": False,
        "payment_inferred": False,
        "cash_inferred": False,
        "revenue_inferred": False,
        "normalized_request": {"opaque": "request"},
        "normalized_arbitration": {"opaque": "arb"},
        "normalized_history": {"opaque": "history"},
    }
    body["receipt_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    return body


def report(
    outcome="AMBIGUOUS",
    evidence_class="CONNECTOR_ERROR",
    provider_receipt_key=None,
):
    return {
        "schema": m.REPORT_SCHEMA,
        "attempt_id": "gmail-attempt-001",
        "attempted_at": "2026-09-18T07:48:00Z",
        "outcome": outcome,
        "evidence_class": evidence_class,
        "evidence_sha256": h("provider evidence"),
        "provider_receipt_key": provider_receipt_key,
    }


def gate_bundle():
    route = h("route")
    request = {
        "schema": gate.REQUEST_SCHEMA,
        "request_id": "req-001",
        "opportunity_ref": "Scottcjn/rustchain-bounties#100",
        "target_kind": "EMAIL",
        "target_route_sha256": route,
        "work_url": "https://github.com/woahwhattheheck/bounty-concierge/pull/58",
        "work_sha": "1" * 40,
        "reward_reference": "scope-dependent reward",
        "claimant_label": "woahwhattheheck",
        "source_authority_ref": "https://github.com/Scottcjn/rustchain-bounties/issues/100",
        "source_authority_sha256": h("source"),
        "prepared_at": "2026-09-18T07:45:00Z",
        "message_sha256": h("message"),
    }
    digest = gate.request_sha256(request)
    arbitration = {
        "schema": gate.ARBITRATION_SCHEMA,
        "request_sha256": digest,
        "target_route_sha256": route,
        "decision": "SELECTED",
        "owner_seat": "Z-ProviderContinuity",
        "arbiter": "single-writer-control",
        "arbitration_ref": "https://example.invalid/lease/1",
        "issued_at": "2026-09-18T07:46:00Z",
        "expires_at": "2026-09-18T08:00:00Z",
    }
    history = {
        "schema": gate.HISTORY_SCHEMA,
        "request_sha256": digest,
        "target_route_sha256": route,
        "complete": True,
        "captured_at": "2026-09-18T07:46:30Z",
        "items": [],
        "terminal": None,
    }
    return request, arbitration, history


class ProviderAttemptContinuityTests(unittest.TestCase):
    NOW = datetime(2026, 9, 18, 7, 49, 0, tzinfo=timezone.utc)

    def compile(self, d=None, r=None):
        return m.compile_provider_attempt(
            d or delivery(),
            r or report(),
            as_of=self.NOW,
        )

    def test_connector_error_is_ambiguous_and_blocks_retry_without_census(self):
        out = self.compile()
        self.assertEqual(out["continuity_state"], "HOLD_FOR_PROVIDER_CENSUS")
        self.assertEqual(out["history_mutation"]["outcome"], "AMBIGUOUS")
        self.assertFalse(out["custody_dispatch_allowed"])
        self.assertTrue(out["retry_requires_fresh_gate"])
        self.assertFalse(out["authority"]["connector_error_proves_not_sent"])

    def test_confirmed_not_sent_requires_specific_evidence(self):
        for evidence_class in (
            "POST_ATTEMPT_PROVIDER_CENSUS",
            "PRE_PROVIDER_BLOCK",
        ):
            with self.subTest(evidence_class=evidence_class):
                out = self.compile(
                    r=report("CONFIRMED_NOT_SENT", evidence_class)
                )
                self.assertEqual(
                    out["continuity_state"],
                    "RECAPTURE_AND_REAUTHORIZE_BEFORE_RETRY",
                )
                self.assertFalse(out["custody_dispatch_allowed"])
                self.assertTrue(out["retry_requires_fresh_gate"])
        with self.assertRaises(m.ProviderAttemptError):
            self.compile(
                r=report("CONFIRMED_NOT_SENT", "CONNECTOR_ERROR")
            )

    def test_confirmed_sent_requires_provider_receipt(self):
        out = self.compile(
            r=report(
                "CONFIRMED_SENT",
                "PROVIDER_SENT_RECEIPT",
                "gmail-message-abc",
            )
        )
        self.assertEqual(out["continuity_state"], "RECORD_DISPATCH")
        self.assertTrue(out["custody_dispatch_allowed"])
        self.assertFalse(out["retry_requires_fresh_gate"])
        with self.assertRaises(m.ProviderAttemptError):
            self.compile(
                r=report(
                    "CONFIRMED_SENT",
                    "PROVIDER_SENT_RECEIPT",
                    None,
                )
            )

    def test_non_sent_attempt_cannot_smuggle_provider_receipt_key(self):
        with self.assertRaises(m.ProviderAttemptError):
            self.compile(
                r=report(
                    "AMBIGUOUS",
                    "CONNECTOR_ERROR",
                    "fake-sent-id",
                )
            )

    def test_history_mutation_is_exact_payout_delivery_shape(self):
        d = delivery()
        r = report(
            "CONFIRMED_NOT_SENT",
            "POST_ATTEMPT_PROVIDER_CENSUS",
        )
        row = self.compile(d, r)["history_mutation"]
        self.assertEqual(
            set(row),
            {
                "mutation_id",
                "request_sha256",
                "target_route_sha256",
                "opportunity_ref",
                "message_sha256",
                "owner_seat",
                "attempted_at",
                "outcome",
                "provider_receipt_sha256",
            },
        )
        self.assertEqual(row["request_sha256"], d["request_sha256"])
        self.assertEqual(
            row["provider_receipt_sha256"],
            r["evidence_sha256"],
        )

    def test_ambiguous_row_blocks_the_existing_delivery_gate(self):
        request, arbitration, history = gate_bundle()
        first = gate.compile_payout_delivery(
            request,
            arbitration,
            history,
            as_of=datetime(
                2026, 9, 18, 7, 47, 0,
                tzinfo=timezone.utc,
            ),
        )
        r = report()
        r["attempted_at"] = "2026-09-18T07:48:00Z"
        out = m.compile_provider_attempt(
            first,
            r,
            as_of=self.NOW,
        )
        history["items"] = [out["history_mutation"]]
        history["captured_at"] = "2026-09-18T07:48:30Z"
        second = gate.compile_payout_delivery(
            request,
            arbitration,
            history,
            as_of=self.NOW,
        )
        self.assertEqual(
            second["state"],
            "DUPLICATE_SEND_BLOCKED",
        )

    def test_confirmed_not_sent_row_is_accepted_by_existing_history_schema(self):
        request, arbitration, history = gate_bundle()
        first = gate.compile_payout_delivery(
            request,
            arbitration,
            history,
            as_of=datetime(
                2026, 9, 18, 7, 47, 0,
                tzinfo=timezone.utc,
            ),
        )
        r = report(
            "CONFIRMED_NOT_SENT",
            "POST_ATTEMPT_PROVIDER_CENSUS",
        )
        out = m.compile_provider_attempt(
            first,
            r,
            as_of=self.NOW,
        )
        history["items"] = [out["history_mutation"]]
        history["captured_at"] = "2026-09-18T07:48:30Z"
        second = gate.compile_payout_delivery(
            request,
            arbitration,
            history,
            as_of=self.NOW,
        )
        self.assertEqual(
            second["state"],
            gate.AUTHORITY_CEILING,
        )
        self.assertEqual(
            second["normalized_history"]["items"][0]["outcome"],
            "CONFIRMED_NOT_SENT",
        )
        self.assertTrue(out["retry_requires_fresh_gate"])

    def test_delivery_tamper_is_rejected(self):
        d = delivery()
        d["opportunity_ref"] = "Scottcjn/rustchain-bounties#999"
        with self.assertRaisesRegex(
            m.ProviderAttemptError,
            "digest",
        ):
            self.compile(d)

    def test_non_authorizing_gate_receipt_is_rejected(self):
        for field, value in (
            ("one_provider_mutation_authorized", False),
            ("state", "DUPLICATE_SEND_BLOCKED"),
        ):
            d = delivery()
            d[field] = value
            body = dict(d)
            body.pop("receipt_sha256")
            d["receipt_sha256"] = hashlib.sha256(
                canonical(body)
            ).hexdigest()
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    m.ProviderAttemptError,
                    "does not authorize",
                ):
                    self.compile(d)

    def test_authority_expansion_in_delivery_receipt_is_rejected(self):
        for field in (
            "external_action_performed",
            "payment_inferred",
            "cash_inferred",
            "revenue_inferred",
        ):
            d = delivery()
            d[field] = True
            body = dict(d)
            body.pop("receipt_sha256")
            d["receipt_sha256"] = hashlib.sha256(
                canonical(body)
            ).hexdigest()
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    m.ProviderAttemptError,
                    "expands authority",
                ):
                    self.compile(d)

    def test_attempt_timing_fails_closed(self):
        for ts, expected in (
            ("2026-09-18T07:46:59Z", "predates"),
            ("2026-09-18T07:56:00Z", "outside"),
            ("2026-09-18T07:57:00Z", "outside"),
        ):
            r = report()
            r["attempted_at"] = ts
            with self.subTest(ts=ts):
                with self.assertRaisesRegex(
                    m.ProviderAttemptError,
                    expected,
                ):
                    self.compile(r=r)
        future = report()
        future["attempted_at"] = "2026-09-18T07:50:00Z"
        with self.assertRaisesRegex(
            m.ProviderAttemptError,
            "future",
        ):
            self.compile(r=future)

    def test_bad_evidence_class_matrix_fails_closed(self):
        cases = (
            ("CONFIRMED_SENT", "CONNECTOR_ERROR", "provider-id"),
            ("CONFIRMED_NOT_SENT", "PROVIDER_SENT_RECEIPT", None),
            ("AMBIGUOUS", "POST_ATTEMPT_PROVIDER_CENSUS", None),
        )
        for outcome, evidence_class, key in cases:
            with self.subTest(
                outcome=outcome,
                evidence_class=evidence_class,
            ):
                with self.assertRaisesRegex(
                    m.ProviderAttemptError,
                    "not supported",
                ):
                    self.compile(
                        r=report(
                            outcome,
                            evidence_class,
                            key,
                        )
                    )

    def test_receipt_verifies_and_tamper_fails(self):
        d = delivery()
        r = report()
        receipt = self.compile(d, r)
        self.assertEqual(
            m.verify_provider_attempt(d, r, receipt),
            receipt,
        )
        bad = copy.deepcopy(receipt)
        bad["custody_dispatch_allowed"] = True
        with self.assertRaisesRegex(
            m.ProviderAttemptError,
            "differs",
        ):
            m.verify_provider_attempt(d, r, bad)

    def test_receipt_never_claims_external_or_money_truth(self):
        out = self.compile()
        self.assertEqual(
            out["authority"],
            {
                "external_action_performed_by_module": False,
                "provider_receipt_authenticated_by_module": False,
                "connector_error_proves_not_sent": False,
                "advertised_reward_is_debt": False,
                "payment_inferred": False,
                "cash_inferred": False,
                "revenue_inferred": False,
            },
        )

    def test_strict_json_rejects_duplicate_keys_and_nan(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            dup = td / "dup.json"
            dup.write_text(
                '{"a":1,"a":2}',
                encoding="utf-8",
            )
            nan = td / "nan.json"
            nan.write_text(
                '{"a":NaN}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                m.ProviderAttemptError,
                "duplicate JSON key",
            ):
                m._read_json(dup)
            with self.assertRaisesRegex(
                m.ProviderAttemptError,
                "non-finite",
            ):
                m._read_json(nan)

    def test_cli_compile_verify_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            d = delivery()
            r = report()
            now = datetime.now(
                timezone.utc
            ).replace(microsecond=0)
            d["as_of"] = now.isoformat().replace(
                "+00:00",
                "Z",
            )
            d["lease_issued_at"] = d["as_of"]
            d["lease_expires_at"] = (
                now + timedelta(minutes=10)
            ).isoformat().replace("+00:00", "Z")
            body = dict(d)
            body.pop("receipt_sha256")
            d["receipt_sha256"] = hashlib.sha256(
                canonical(body)
            ).hexdigest()
            r["attempted_at"] = d["as_of"]
            dp = td / "delivery.json"
            rp = td / "report.json"
            dp.write_text(
                json.dumps(d),
                encoding="utf-8",
            )
            rp.write_text(
                json.dumps(r),
                encoding="utf-8",
            )
            output = td / "receipt.json"
            command = [
                sys.executable,
                str(MODULE_PATH),
                "compile",
                str(dp),
                str(rp),
                "--output",
                str(output),
            ]
            first = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                first.returncode,
                0,
                first.stderr,
            )
            second = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(
                second.returncode,
                0,
            )
            self.assertIn(
                "refusing to overwrite",
                second.stderr,
            )
            verified = td / "verified.json"
            check = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "verify",
                    str(dp),
                    str(rp),
                    str(output),
                    "--output",
                    str(verified),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                check.returncode,
                0,
                check.stderr,
            )
            self.assertEqual(
                json.loads(output.read_text()),
                json.loads(verified.read_text()),
            )


if __name__ == "__main__":
    unittest.main()
