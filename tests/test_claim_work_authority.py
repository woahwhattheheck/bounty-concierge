from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import inspect
import json
import os
import unittest
from unittest.mock import patch

from concierge import claim_work_authority as cwa
from concierge.sponsor_adjudication import compile_manifest
from concierge.sponsor_adjudication import authority as sa_authority


SPONSOR_KEY = "11" * 32
SPONSOR_PROVIDER = "gmail"
SPONSOR_PRINCIPAL = hashlib.sha256(b"sponsor-principal").hexdigest()
RELATION_KEY = "22" * 32
RECEIPT_KEY = "33" * 32
RELATION_PROVIDER = "owner-host"
RELATION_PRINCIPAL = hashlib.sha256(b"claim-work-owner").hexdigest()
HEAD = "a" * 40
MERGE = "b" * 40
API_URL = "https://api.github.com/repos/acme/widget/pulls/42"


def h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def base_manifest() -> dict:
    return {
        "schema_version": 1,
        "program": {
            "program_id": "program-1",
            "sponsor": "Synthetic Sponsor",
            "source_ref": "https://example.invalid/program",
        },
        "findings": [
            {
                "finding_id": "f-1",
                "fingerprint_sha256": h("f-1"),
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
                "receipt_ref": "mail:m1",
                "receipt_sha256": h("mail:m1"),
            }
        ],
        "sponsor_events": [
            {
                "event_id": "e-1",
                "event_type": "SPONSOR_VERIFIED",
                "event_at": "2026-09-13T21:00:00Z",
                "source_ref": "https://sponsor.example/verified",
                "source_sha256": h("verified"),
                "claim_unit_id": "unit-1",
                "finding_ids": ["f-1"],
                "submission_ids": ["s-1"],
            }
        ],
    }


def signed_report() -> dict:
    manifest = base_manifest()
    captured = datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    manifest["sponsor_authority"] = sa_authority.sign_for_test_or_host_fixture(
        manifest,
        manifest["program"],
        manifest["sponsor_events"],
        captured_at=captured,
    )
    return compile_manifest(manifest)


def work(head: str = HEAD) -> dict:
    return {"repo": "acme/widget", "pr": 42, "head_sha": head}


def relation(ref: str = "owner-review:ticket-42") -> dict:
    return {"ref": ref, "sha256": h(ref)}


def relation_authority(
    report: dict,
    *,
    work_record=None,
    relation_record=None,
    captured_at=None,
) -> dict:
    work_record = work() if work_record is None else work_record
    relation_record = relation() if relation_record is None else relation_record
    scope = cwa.compute_relation_scope(
        report, "unit-1", work_record, relation_record
    )["scope_sha256"]
    if captured_at is None:
        captured_at = datetime.now(timezone.utc).replace(microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    message = {
        "schema": cwa.RELATION_AUTH_SCHEMA,
        "provider": RELATION_PROVIDER,
        "principal_sha256": RELATION_PRINCIPAL,
        "captured_at": captured_at,
        "scope_sha256": scope,
    }
    signature = hmac.new(
        bytes.fromhex(RELATION_KEY), canonical(message), hashlib.sha256
    ).hexdigest()
    return {
        "provider": RELATION_PROVIDER,
        "principal_sha256": RELATION_PRINCIPAL,
        "captured_at": captured_at,
        "scope_sha256": scope,
        "signature_sha256": signature,
    }


def bind_payload(report: dict, *, work_record=None, relation_record=None, authority=None) -> dict:
    work_record = work() if work_record is None else work_record
    relation_record = relation() if relation_record is None else relation_record
    if authority is None:
        authority = relation_authority(
            report,
            work_record=work_record,
            relation_record=relation_record,
        )
    return {
        "schema": cwa.INPUT_SCHEMA,
        "adjudication_report": report,
        "claim_unit_id": "unit-1",
        "work": work_record,
        "relation_evidence": relation_record,
        "relation_authority": authority,
    }


def merged_payload(*, head=HEAD, **overrides) -> dict:
    payload = {
        "html_url": "https://github.com/acme/widget/pull/42",
        "number": 42,
        "state": "closed",
        "merged": True,
        "head": {"sha": head},
        "base": {"repo": {"full_name": "acme/widget"}},
        "merge_commit_sha": MERGE,
        "merged_at": "2026-09-13T21:30:00Z",
    }
    payload.update(overrides)
    return payload


class FakeResponse:
    def __init__(self, payload, *, status_code=200, url=API_URL):
        self._payload = payload
        self.status_code = status_code
        self.url = url

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class ClaimWorkAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                sa_authority.KEY_ENV: SPONSOR_KEY,
                sa_authority.PROVIDER_ENV: SPONSOR_PROVIDER,
                sa_authority.PRINCIPAL_ENV: SPONSOR_PRINCIPAL,
                sa_authority.TEST_UNSIGNED_ENV: "0",
                cwa.KEY_ENV: RELATION_KEY,
                cwa.RECEIPT_KEY_ENV: RECEIPT_KEY,
                cwa.PROVIDER_ENV: RELATION_PROVIDER,
                cwa.PRINCIPAL_ENV: RELATION_PRINCIPAL,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.report = signed_report()

    def bind(self, payload=None, provider_payload=None):
        payload = bind_payload(self.report) if payload is None else payload
        response = FakeResponse(
            merged_payload() if provider_payload is None else provider_payload
        )
        with patch.object(cwa.requests, "get", return_value=response) as get:
            receipt = cwa.bind_claim_work(payload)
        return receipt, get

    def test_valid_binding_requires_host_relation_live_merge_and_separate_receipt_key(self):
        receipt, get = self.bind()
        self.assertEqual(receipt["disposition"], "MERGED_WORK_BOUND")
        self.assertEqual(receipt["claim_unit"]["claim_unit_id"], "unit-1")
        self.assertEqual(receipt["work"]["canonical_url"], "https://github.com/acme/widget/pull/42")
        self.assertEqual(receipt["work"]["merge_commit_sha"], MERGE)
        self.assertTrue(cwa.verify_claim_work_receipt(receipt))
        get.assert_called_once()
        args, kwargs = get.call_args
        self.assertEqual(args, (API_URL,))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 15)

    def test_public_transport_injection_is_not_an_entrypoint(self):
        self.assertEqual(list(inspect.signature(cwa.bind_claim_work).parameters), ["payload"])
        with self.assertRaises(TypeError):
            cwa.bind_claim_work(bind_payload(self.report), session=object())
        self.assertEqual(
            list(inspect.signature(cwa.verify_claim_work_current).parameters),
            ["payload", "receipt"],
        )

    def test_relation_signer_cannot_forge_provider_receipt(self):
        receipt, _ = self.bind()
        forged = copy.deepcopy(receipt)
        core = {
            key: copy.deepcopy(value)
            for key, value in forged.items()
            if key not in {"host_receipt_hmac_sha256", "receipt_sha256"}
        }
        forged["host_receipt_hmac_sha256"] = hmac.new(
            bytes.fromhex(RELATION_KEY),
            b"bounty-claim-work-live-binding/v1\x00" + canonical(core),
            hashlib.sha256,
        ).hexdigest()
        body = {key: value for key, value in forged.items() if key != "receipt_sha256"}
        forged["receipt_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
        self.assertFalse(cwa.verify_claim_work_receipt(forged))

    def test_relation_and_receipt_keys_must_be_distinct(self):
        with patch.dict(os.environ, {cwa.RECEIPT_KEY_ENV: RELATION_KEY}, clear=False):
            with patch.object(cwa.requests, "get", return_value=FakeResponse(merged_payload())):
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "must be distinct"):
                    cwa.bind_claim_work(bind_payload(self.report))

    def test_receipt_key_is_required_and_not_derived_from_relation_key(self):
        with patch.dict(os.environ, {cwa.RECEIPT_KEY_ENV: "not-hex"}, clear=False):
            with patch.object(cwa.requests, "get", return_value=FakeResponse(merged_payload())):
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, cwa.RECEIPT_KEY_ENV):
                    cwa.bind_claim_work(bind_payload(self.report))

    def test_unsigned_sponsor_test_mode_cannot_cross_claim_work_boundary(self):
        receipt, _ = self.bind()
        payload = bind_payload(self.report)
        with patch.dict(os.environ, {sa_authority.TEST_UNSIGNED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "unsigned-test mode"):
                cwa.compute_relation_scope(self.report, "unit-1", work(), relation())
            with patch.object(cwa.requests, "get") as get:
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "unsigned-test mode"):
                    cwa.bind_claim_work(payload)
                get.assert_not_called()
            self.assertFalse(cwa.verify_claim_work_receipt(receipt))
            with patch.object(cwa.requests, "get") as get:
                self.assertFalse(cwa.verify_claim_work_current(payload, receipt))
                get.assert_not_called()

    def test_literal_zero_does_not_enable_unsigned_mode(self):
        with patch.dict(os.environ, {sa_authority.TEST_UNSIGNED_ENV: "0"}, clear=False):
            receipt, _ = self.bind()
        self.assertTrue(cwa.verify_claim_work_receipt(receipt))

    def test_invalid_relation_hmac_rejects_before_network(self):
        authority = relation_authority(self.report)
        authority["signature_sha256"] = "0" * 64
        with patch.object(cwa.requests, "get") as get:
            with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "HMAC"):
                cwa.bind_claim_work(bind_payload(self.report, authority=authority))
            get.assert_not_called()

    def test_stale_relation_authority_rejects_before_network(self):
        captured = (datetime.now(timezone.utc) - timedelta(minutes=6)).replace(
            microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        authority = relation_authority(self.report, captured_at=captured)
        with patch.object(cwa.requests, "get") as get:
            with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "stale"):
                cwa.bind_claim_work(bind_payload(self.report, authority=authority))
            get.assert_not_called()

    def test_open_unmerged_and_head_drift_reject(self):
        bad_payloads = [
            (merged_payload(state="open", merged=False), "not merged"),
            (merged_payload(state="closed", merged=False), "not merged"),
            (merged_payload(head="c" * 40), "head does not match"),
        ]
        for provider_payload, phrase in bad_payloads:
            with self.subTest(phrase=phrase):
                with patch.object(
                    cwa.requests, "get", return_value=FakeResponse(provider_payload)
                ):
                    with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, phrase):
                        cwa.bind_claim_work(bind_payload(self.report))

    def test_redirect_non_200_and_response_url_drift_reject(self):
        cases = [
            (FakeResponse(merged_payload(), status_code=301), "not HTTP 200"),
            (FakeResponse(merged_payload(), status_code=404), "not HTTP 200"),
            (
                FakeResponse(
                    merged_payload(),
                    url="https://api.github.com/repos/other/widget/pulls/42",
                ),
                "URL drifted",
            ),
        ]
        for response, phrase in cases:
            with self.subTest(phrase=phrase):
                with patch.object(cwa.requests, "get", return_value=response):
                    with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, phrase):
                        cwa.bind_claim_work(bind_payload(self.report))

    def test_provider_json_exception_text_is_not_exported(self):
        secret = "SECRET_PROVIDER_TEXT"
        with patch.object(
            cwa.requests,
            "get",
            return_value=FakeResponse(RuntimeError(secret)),
        ):
            with self.assertRaises(cwa.ClaimWorkAuthorityError) as caught:
                cwa.bind_claim_work(bind_payload(self.report))
        self.assertNotIn(secret, str(caught.exception))

    def test_github_token_is_request_only(self):
        secret = "ghp_DO_NOT_EXPORT"
        with patch.dict(os.environ, {cwa.GITHUB_TOKEN_ENV: secret}, clear=False):
            receipt, get = self.bind()
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], f"Bearer {secret}")
        self.assertNotIn(secret, json.dumps(receipt, sort_keys=True))

    def test_historical_receipt_tamper_cannot_be_publicly_resealed(self):
        receipt, _ = self.bind()
        tampered = copy.deepcopy(receipt)
        tampered["work"]["head_sha"] = "c" * 40
        body = {key: value for key, value in tampered.items() if key != "receipt_sha256"}
        tampered["receipt_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
        self.assertFalse(cwa.verify_claim_work_receipt(tampered))

    def test_current_verification_reacquires_provider(self):
        payload = bind_payload(self.report)
        receipt, _ = self.bind(payload)
        with patch.object(cwa.requests, "get", return_value=FakeResponse(merged_payload())) as get:
            self.assertTrue(cwa.verify_claim_work_current(payload, receipt))
            get.assert_called_once()
        with patch.object(
            cwa.requests,
            "get",
            return_value=FakeResponse(merged_payload(head="c" * 40)),
        ):
            self.assertFalse(cwa.verify_claim_work_current(payload, receipt))

    def test_bool_pr_and_caller_written_state_are_rejected(self):
        bad = work()
        bad["pr"] = True
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "bool invalid"):
            cwa.compute_relation_scope(self.report, "unit-1", bad, relation())
        extra = work()
        extra["state"] = "MERGED"
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "exactly"):
            cwa.compute_relation_scope(self.report, "unit-1", extra, relation())

    def test_authority_ceiling_remains_read_only(self):
        receipt, _ = self.bind()
        authority = receipt["authority"]
        self.assertTrue(authority["github_read_only"])
        self.assertTrue(authority["claim_work_relation_host_authorized"])
        for key, value in authority.items():
            if key in {"github_read_only", "claim_work_relation_host_authorized"}:
                continue
            self.assertFalse(value, key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
