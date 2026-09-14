from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from concierge import claim_work_authority as cwa
from concierge.sponsor_adjudication import compile_manifest
from concierge.sponsor_adjudication import authority as sa_authority


SPONSOR_KEY = "11" * 32
SPONSOR_PROVIDER = "gmail"
SPONSOR_PRINCIPAL = hashlib.sha256(b"sponsor-principal").hexdigest()

RELATION_KEY = "22" * 32
RELATION_PROVIDER = "owner-host"
RELATION_PRINCIPAL = hashlib.sha256(b"claim-work-owner").hexdigest()

HEAD = "a" * 40
MERGE = "b" * 40


def h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    captured = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
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


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def relation_authority(report: dict, *, work_record=None, relation_record=None, captured_at=None) -> dict:
    work_record = work() if work_record is None else work_record
    relation_record = relation() if relation_record is None else relation_record
    scope = cwa.compute_relation_scope(
        report, "unit-1", work_record, relation_record
    )["scope_sha256"]
    if captured_at is None:
        captured_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    message = {
        "schema": cwa.RELATION_AUTH_SCHEMA,
        "provider": RELATION_PROVIDER,
        "principal_sha256": RELATION_PRINCIPAL,
        "captured_at": captured_at,
        "scope_sha256": scope,
    }
    signature = hmac.new(bytes.fromhex(RELATION_KEY), canonical(message), hashlib.sha256).hexdigest()
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
    def __init__(self, payload, *, status_code=200, url="https://api.github.com/repos/acme/widget/pulls/42"):
        self._payload = payload
        self.status_code = status_code
        self.url = url

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


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
                cwa.PROVIDER_ENV: RELATION_PROVIDER,
                cwa.PRINCIPAL_ENV: RELATION_PRINCIPAL,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.report = signed_report()

    def session(self, payload=None, **kwargs):
        return FakeSession(FakeResponse(merged_payload() if payload is None else payload, **kwargs))

    def test_valid_binding_requires_both_host_relation_and_live_merge(self):
        session = self.session()
        receipt = cwa.bind_claim_work(bind_payload(self.report), session=session)
        self.assertEqual(receipt["disposition"], "MERGED_WORK_BOUND")
        self.assertEqual(receipt["claim_unit"]["claim_unit_id"], "unit-1")
        self.assertEqual(receipt["claim_unit"]["finding_ids"], ["f-1"])
        self.assertEqual(receipt["claim_unit"]["submission_ids"], ["s-1"])
        self.assertEqual(receipt["work"]["canonical_url"], "https://github.com/acme/widget/pull/42")
        self.assertEqual(receipt["work"]["head_sha"], HEAD)
        self.assertEqual(receipt["work"]["merge_commit_sha"], MERGE)
        self.assertTrue(receipt["authority"]["github_read_only"])
        self.assertTrue(receipt["authority"]["claim_work_relation_host_authorized"])
        self.assertTrue(cwa.verify_claim_work_receipt(receipt))
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_missing_or_invalid_relation_hmac_rejects_before_network(self):
        auth = relation_authority(self.report)
        auth["signature_sha256"] = "0" * 64
        session = self.session()
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "HMAC"):
            cwa.bind_claim_work(bind_payload(self.report, authority=auth), session=session)
        self.assertEqual(session.calls, [])

    def test_work_change_after_authorization_rejects_before_network(self):
        auth = relation_authority(self.report, work_record=work(HEAD))
        changed = work("c" * 40)
        session = self.session()
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "not bound"):
            cwa.bind_claim_work(
                bind_payload(self.report, work_record=changed, authority=auth),
                session=session,
            )
        self.assertEqual(session.calls, [])

    def test_relation_evidence_change_after_authorization_rejects_before_network(self):
        first = relation("owner-review:one")
        auth = relation_authority(self.report, relation_record=first)
        second = relation("owner-review:two")
        session = self.session()
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "not bound"):
            cwa.bind_claim_work(
                bind_payload(self.report, relation_record=second, authority=auth),
                session=session,
            )
        self.assertEqual(session.calls, [])

    def test_stale_and_future_relation_authority_reject_before_network(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for delta, phrase in (
            (timedelta(minutes=-6), "stale"),
            (timedelta(minutes=1), "future"),
        ):
            with self.subTest(phrase=phrase):
                captured = (now + delta).strftime("%Y-%m-%dT%H:%M:%SZ")
                auth = relation_authority(self.report, captured_at=captured)
                session = self.session()
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, phrase):
                    cwa.bind_claim_work(
                        bind_payload(self.report, authority=auth),
                        session=session,
                    )
                self.assertEqual(session.calls, [])

    def test_wrong_host_identity_rejects(self):
        auth = relation_authority(self.report)
        auth["provider"] = "other-host"
        message = {
            "schema": cwa.RELATION_AUTH_SCHEMA,
            "provider": auth["provider"],
            "principal_sha256": auth["principal_sha256"],
            "captured_at": auth["captured_at"],
            "scope_sha256": auth["scope_sha256"],
        }
        auth["signature_sha256"] = hmac.new(
            bytes.fromhex(RELATION_KEY), canonical(message), hashlib.sha256
        ).hexdigest()
        session = self.session()
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "not host-authorized"):
            cwa.bind_claim_work(bind_payload(self.report, authority=auth), session=session)
        self.assertEqual(session.calls, [])

    def test_open_or_unmerged_pull_rejects(self):
        for provider_payload in (
            merged_payload(state="open", merged=False),
            merged_payload(state="closed", merged=False),
        ):
            with self.subTest(provider_payload=provider_payload):
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "not merged"):
                    cwa.bind_claim_work(
                        bind_payload(self.report),
                        session=self.session(provider_payload),
                    )

    def test_head_mismatch_rejects(self):
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "head does not match"):
            cwa.bind_claim_work(
                bind_payload(self.report),
                session=self.session(merged_payload(head="c" * 40)),
            )

    def test_html_and_base_identity_mismatch_reject(self):
        bad_html = merged_payload(html_url="https://github.com/evil/widget/pull/42")
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "identity mismatch"):
            cwa.bind_claim_work(bind_payload(self.report), session=self.session(bad_html))
        bad_base = merged_payload(base={"repo": {"full_name": "evil/widget"}})
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "base repository"):
            cwa.bind_claim_work(bind_payload(self.report), session=self.session(bad_base))

    def test_redirect_or_non_200_rejects_without_following(self):
        redirect = self.session(
            merged_payload(),
            status_code=301,
            url="https://api.github.com/repositories/123/pulls/42",
        )
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "not HTTP 200"):
            cwa.bind_claim_work(bind_payload(self.report), session=redirect)
        self.assertFalse(redirect.calls[0][1]["allow_redirects"])
        missing = self.session(merged_payload(), status_code=404)
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "not HTTP 200"):
            cwa.bind_claim_work(bind_payload(self.report), session=missing)

    def test_response_url_drift_rejects(self):
        session = self.session(
            merged_payload(),
            status_code=200,
            url="https://api.github.com/repos/other/widget/pulls/42",
        )
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "URL drifted"):
            cwa.bind_claim_work(bind_payload(self.report), session=session)

    def test_future_merge_timestamp_rejects(self):
        future = (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "merged_at is in the future"):
            cwa.bind_claim_work(
                bind_payload(self.report),
                session=self.session(merged_payload(merged_at=future)),
            )

    def test_provider_json_failure_is_source_text_free(self):
        secret = "SECRET_PROVIDER_TEXT"
        session = self.session(RuntimeError(secret))
        with self.assertRaises(cwa.ClaimWorkAuthorityError) as caught:
            cwa.bind_claim_work(bind_payload(self.report), session=session)
        self.assertNotIn(secret, str(caught.exception))

    def test_github_token_is_used_but_not_exported(self):
        secret = "ghp_DO_NOT_EXPORT"
        with patch.dict(os.environ, {cwa.GITHUB_TOKEN_ENV: secret}, clear=False):
            session = self.session()
            receipt = cwa.bind_claim_work(bind_payload(self.report), session=session)
        self.assertEqual(
            session.calls[0][1]["headers"]["Authorization"],
            f"Bearer {secret}",
        )
        self.assertNotIn(secret, json.dumps(receipt, sort_keys=True))

    def test_historical_receipt_tamper_cannot_be_self_resealed(self):
        receipt = cwa.bind_claim_work(bind_payload(self.report), session=self.session())
        tampered = copy.deepcopy(receipt)
        tampered["work"]["head_sha"] = "c" * 40
        body = {k: v for k, v in tampered.items() if k != "receipt_sha256"}
        tampered["receipt_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
        self.assertFalse(cwa.verify_claim_work_receipt(tampered))

    def test_historical_verification_does_not_reapply_relation_freshness(self):
        receipt = cwa.bind_claim_work(bind_payload(self.report), session=self.session())
        old = copy.deepcopy(receipt)
        old["relation_authority"]["captured_at"] = "2020-01-01T00:00:00Z"
        core = {
            k: copy.deepcopy(v)
            for k, v in old.items()
            if k not in {"host_receipt_hmac_sha256", "receipt_sha256"}
        }
        old["host_receipt_hmac_sha256"] = cwa._receipt_hmac(core)
        body = {k: v for k, v in old.items() if k != "receipt_sha256"}
        old["receipt_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
        self.assertTrue(cwa.verify_claim_work_receipt(old))

    def test_current_verification_reacquires_provider_and_scope(self):
        payload = bind_payload(self.report)
        receipt = cwa.bind_claim_work(payload, session=self.session())
        self.assertTrue(
            cwa.verify_claim_work_current(payload, receipt, session=self.session())
        )
        moved = self.session(merged_payload(head="c" * 40))
        self.assertFalse(cwa.verify_claim_work_current(payload, receipt, session=moved))

    def test_bool_pr_and_extra_caller_state_are_rejected(self):
        bad = work()
        bad["pr"] = True
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "bool invalid"):
            cwa.compute_relation_scope(self.report, "unit-1", bad, relation())
        extra = work()
        extra["state"] = "MERGED"
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "exactly"):
            cwa.compute_relation_scope(self.report, "unit-1", extra, relation())

    def test_missing_claim_unit_rejects(self):
        with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "exactly one"):
            cwa.compute_relation_scope(self.report, "missing-unit", work(), relation())

    def test_authority_ceiling_is_explicit(self):
        receipt = cwa.bind_claim_work(bind_payload(self.report), session=self.session())
        authority = receipt["authority"]
        self.assertTrue(authority["github_read_only"])
        self.assertTrue(authority["claim_work_relation_host_authorized"])
        for key, value in authority.items():
            if key in {"github_read_only", "claim_work_relation_host_authorized"}:
                continue
            self.assertFalse(value, key)

    def test_load_json_rejects_duplicate_keys_and_floats(self):
        with tempfile.TemporaryDirectory() as tmp:
            duplicate = Path(tmp, "duplicate.json")
            duplicate.write_text('{"x":1,"x":2}', encoding="utf-8")
            with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "duplicate"):
                cwa.load_json(duplicate)
            flt = Path(tmp, "float.json")
            flt.write_text('{"x":1.2}', encoding="utf-8")
            with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "floats"):
                cwa.load_json(flt)


if __name__ == "__main__":
    unittest.main()
