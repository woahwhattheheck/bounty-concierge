import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from concierge import sponsor_adjudication as sa
from concierge.sponsor_adjudication.authority import (
    KEY_ENV,
    PRINCIPAL_ENV,
    PROVIDER_ENV,
    TEST_UNSIGNED_ENV,
    sign_for_test_or_host_fixture,
)


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def utc_text(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_sha(value):
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reseal_report(report):
    core = {k: copy.deepcopy(v) for k, v in report.items() if k != "report_sha256"}
    report["report_sha256"] = canonical_sha(core)
    return report


def base_manifest(events=True):
    sponsor_events = []
    if events:
        sponsor_events = [
            {
                "event_id": "evt-1",
                "event_type": "SPONSOR_VERIFIED",
                "event_at": "2026-09-13T21:00:00Z",
                "source_ref": "mail:sponsor-1",
                "source_sha256": h("provider-message-1"),
                "claim_unit_id": "unit-1",
                "finding_ids": ["f-1"],
                "submission_ids": ["s-1"],
                "note": "Sponsor verified concrete finding",
            }
        ]
    return {
        "schema_version": 1,
        "program": {
            "program_id": "synthetic-bounty",
            "sponsor": "Synthetic Sponsor",
            "source_ref": "https://example.invalid/program",
        },
        "findings": [
            {
                "finding_id": "f-1",
                "fingerprint_sha256": h("finding"),
                "title": "Synthetic finding",
                "submitted_by": "tester",
                "evidence_ref": "repo#1",
            }
        ],
        "submissions": [
            {
                "submission_id": "s-1",
                "finding_ids": ["f-1"],
                "submitted_at": "2026-09-13T20:00:00Z",
                "channel": "email",
                "receipt_ref": "mail:submission-1",
                "receipt_sha256": h("submission-1"),
            }
        ],
        "sponsor_events": sponsor_events,
    }


class SponsorAdjudicationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                KEY_ENV: "11" * 32,
                PROVIDER_ENV: "gmail",
                PRINCIPAL_ENV: h("tokenjunkielabs@gmail.com"),
            },
            clear=False,
        )
        self.env.start()
        os.environ.pop(TEST_UNSIGNED_ENV, None)

    def tearDown(self):
        self.env.stop()

    def authorize(self, manifest, captured_at=None):
        when = captured_at or utc_text(datetime.now(timezone.utc))
        manifest["sponsor_authority"] = sign_for_test_or_host_fixture(
            manifest,
            manifest["program"],
            manifest["sponsor_events"],
            captured_at=when,
        )
        return manifest

    def test_unsigned_sponsor_event_cannot_self_mint_verified_posture(self):
        with self.assertRaisesRegex(sa.AdjudicationError, "require host sponsor_authority"):
            sa.compile_manifest(base_manifest())

    def test_valid_host_authority_allows_verified_posture_and_is_retained(self):
        report = sa.compile_manifest(self.authorize(base_manifest()))
        self.assertTrue(report["claim_units"][0]["sponsor_verified"])
        self.assertEqual(report["claim_units"][0]["action"], "WAIT_SPONSOR")
        authority = report["program"]["sponsor_authority"]
        binding = report["sponsor_authority_binding"]
        self.assertEqual(authority["provider"], "gmail")
        self.assertEqual(authority["principal_sha256"], h("tokenjunkielabs@gmail.com"))
        self.assertEqual(binding["manifest_sha256"], authority["manifest_sha256"])
        self.assertEqual(sa.verify_report(report)["report_sha256"], report["report_sha256"])

    def test_event_tamper_after_host_attestation_fails(self):
        manifest = self.authorize(base_manifest())
        manifest["sponsor_events"][0]["event_type"] = "REWARD_OFFERED"
        manifest["sponsor_events"][0]["amount"] = "90.00"
        manifest["sponsor_events"][0]["currency"] = "USD"
        with self.assertRaisesRegex(sa.AdjudicationError, "event scope mismatch"):
            sa.compile_manifest(manifest)

    def test_non_event_manifest_tamper_after_host_attestation_fails(self):
        manifest = self.authorize(base_manifest())
        manifest["findings"][0]["title"] = "Caller rebound title"
        with self.assertRaisesRegex(sa.AdjudicationError, "manifest generation mismatch"):
            sa.compile_manifest(manifest)

    def test_source_ref_or_digest_cannot_be_rebound_after_attestation(self):
        manifest = self.authorize(base_manifest())
        manifest["sponsor_events"][0]["source_sha256"] = h("caller-fake")
        with self.assertRaisesRegex(sa.AdjudicationError, "event scope mismatch"):
            sa.compile_manifest(manifest)

    def test_provider_transplant_fails(self):
        manifest = self.authorize(base_manifest())
        manifest["sponsor_authority"]["provider"] = "slack"
        with self.assertRaises(sa.AdjudicationError):
            sa.compile_manifest(manifest)

    def test_principal_transplant_fails(self):
        manifest = self.authorize(base_manifest())
        manifest["sponsor_authority"]["principal_sha256"] = h("other@example.com")
        with self.assertRaises(sa.AdjudicationError):
            sa.compile_manifest(manifest)

    def test_signature_tamper_fails(self):
        manifest = self.authorize(base_manifest())
        manifest["sponsor_authority"]["signature_sha256"] = "00" * 32
        with self.assertRaisesRegex(sa.AdjudicationError, "HMAC mismatch"):
            sa.compile_manifest(manifest)

    def test_stale_capture_replay_fails(self):
        manifest = self.authorize(base_manifest(), utc_text(datetime.now(timezone.utc) - timedelta(minutes=10)))
        with self.assertRaisesRegex(sa.AdjudicationError, "stale"):
            sa.compile_manifest(manifest)

    def test_future_capture_fails(self):
        manifest = self.authorize(base_manifest(), utc_text(datetime.now(timezone.utc) + timedelta(minutes=2)))
        with self.assertRaisesRegex(sa.AdjudicationError, "future"):
            sa.compile_manifest(manifest)

    def test_report_event_transplant_fails_authority_aware_verifier(self):
        report = sa.compile_manifest(self.authorize(base_manifest()))
        forged = copy.deepcopy(report)
        forged["sponsor_events"][0]["source_ref"] = "mail:transplanted"
        reseal_report(forged)
        with self.assertRaisesRegex(sa.AdjudicationError, "event scope mismatch"):
            sa.verify_report(forged)

    def test_resealed_non_event_report_tamper_fails_generation_binding(self):
        report = sa.compile_manifest(self.authorize(base_manifest()))
        forged = copy.deepcopy(report)
        forged["findings"][0]["title"] = "Forged historical title"
        reseal_report(forged)
        with self.assertRaisesRegex(sa.AdjudicationError, "report generation binding mismatch"):
            sa.verify_report(forged)

    def test_resealed_valid_enum_derived_action_tamper_fails_generation_binding(self):
        report = sa.compile_manifest(self.authorize(base_manifest()))
        forged = copy.deepcopy(report)
        self.assertEqual(forged["claim_units"][0]["action"], "WAIT_SPONSOR")
        forged["claim_units"][0]["action"] = "OWNER_REVIEW"
        reseal_report(forged)
        with self.assertRaisesRegex(sa.AdjudicationError, "report generation binding mismatch"):
            sa.verify_report(forged)

    def test_report_binding_transplant_fails(self):
        first = sa.compile_manifest(self.authorize(base_manifest()))
        second_manifest = base_manifest()
        second_manifest["findings"][0]["title"] = "Different signed generation"
        second = sa.compile_manifest(self.authorize(second_manifest))
        forged = copy.deepcopy(second)
        forged["sponsor_authority_binding"] = copy.deepcopy(first["sponsor_authority_binding"])
        reseal_report(forged)
        with self.assertRaises(sa.AdjudicationError):
            sa.verify_report(forged)

    def test_receipt_reseal_cannot_rebind_authority_manifest_generation(self):
        manifest = self.authorize(base_manifest())
        with tempfile.TemporaryDirectory() as tmp:
            sa.write_artifacts(manifest, tmp)
            receipt_path = Path(tmp) / "receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["authority_manifest_sha256"] = "00" * 32
            receipt["receipt_sha256"] = canonical_sha({k: copy.deepcopy(v) for k, v in receipt.items() if k != "receipt_sha256"})
            receipt_path.write_text(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(sa.AdjudicationError, "receipt/manifest binding mismatch"):
                sa.verify_artifacts(tmp)

    def test_empty_event_generation_needs_no_authority(self):
        report = sa.compile_manifest(base_manifest(events=False))
        self.assertEqual(report["sponsor_events"], [])
        self.assertNotIn("sponsor_authority", report["program"])
        self.assertIsNone(report["sponsor_authority_binding"])
        sa.verify_report(report)

    def test_empty_event_generation_rejects_meaningless_authority(self):
        manifest = base_manifest(events=False)
        manifest["sponsor_authority"] = {
            "schema_version": 1,
            "purpose": "x",
            "provider": "gmail",
            "principal_sha256": h("x"),
            "captured_at": utc_text(datetime.now(timezone.utc)),
            "event_scope_sha256": h("x"),
            "manifest_sha256": h("x"),
            "signature_sha256": h("x"),
        }
        with self.assertRaisesRegex(sa.AdjudicationError, "forbidden"):
            sa.compile_manifest(manifest)

    def test_host_config_is_required_and_not_a_caller_argument(self):
        manifest = base_manifest()
        with patch.dict(os.environ, {KEY_ENV: "", PROVIDER_ENV: "", PRINCIPAL_ENV: ""}, clear=False):
            with self.assertRaisesRegex(sa.AdjudicationError, "host configuration"):
                sa.compile_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
