import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from concierge import distribution_fulfillment as d

NOW = "2026-09-13T10:00:00Z"
ISSUED = "2026-09-13T09:46:00Z"
SOURCE = "https://github.com/scottcjn/rustchain-bounties/issues/315"
KEY = bytes.fromhex("11" * 32)
ATTACKER_KEY = bytes.fromhex("22" * 32)
KEY_ID = "ops-2026-09"


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def request(*, capture=True, host="x.com", resource="x.com/status/123", verifier="public-web-capture/v1", content=None):
    cap = None
    if capture:
        core = {
            "canonical_source_url": SOURCE,
            "live_url": f"https://{host}/tokenjunkie/status/123",
            "resolved_url": f"https://{host}/tokenjunkie/status/123",
            "resource_identity": resource,
            "observed_at": "2026-09-13T09:45:00Z",
            "http_status": 200,
            "publicly_resolvable": True,
            "content_sha256": content or h("live-content"),
            "verifier": verifier,
        }
        cap = {**core, "capture_sha256": d._hash(core)}
    return {
        "schema": d.REQUEST_SCHEMA,
        "canonical_source_url": SOURCE,
        "advertised_reward": "45",
        "source": {
            "state": "open",
            "labels": ["bounty", "distribution"],
            "updated_at": "2026-09-11T14:20:27Z",
            "captured_at": "2026-09-13T09:30:00Z",
            "snapshot_sha256": h("source"),
        },
        "maintainer_rule": {
            "comment_url": SOURCE + "#issuecomment-5459964069",
            "comment_sha256": h("comment"),
            "captured_at": "2026-09-13T09:31:00Z",
            "requires_live_url": True,
            "allowed_hosts": ["x.com", "bottube.ai", "youtube.com", "youtu.be"],
            "rule_text_sha256": h("rule"),
        },
        "submission_packet": {
            "schema": "bounty-submission-packet-item/v1",
            "canonical_source_url": SOURCE,
            "disposition": "READY_FOR_HUMAN_SUBMISSION",
            "packet_sha256": h("packet"),
            "authority": {
                "submission": "human_only",
                "acceptance": "not_inferred",
                "payout": "not_inferred",
                "cash_claim": False,
            },
        },
        "live_capture": cap,
    }


def authority(req, *, provider="social:x", key=KEY, key_id=KEY_ID, issued_at=ISSUED):
    return d.issue_authority_record(
        copy.deepcopy(req),
        provider_namespace=provider if req["live_capture"] is not None else None,
        trusted_key=key,
        key_id=key_id,
        issued_at=issued_at,
    )


def rehash(req):
    core = dict(req["live_capture"])
    core.pop("capture_sha256")
    req["live_capture"]["capture_sha256"] = d._hash(core)


def request_for(issue, reward="10", capture=False, *, resource=None, verifier="public-web-capture/v1", content=None):
    req = request(
        capture=capture,
        resource=resource or f"x.com/status/{issue}",
        verifier=verifier,
        content=content,
    )
    url = f"https://github.com/scottcjn/rustchain-bounties/issues/{issue}"
    req["canonical_source_url"] = url
    req["advertised_reward"] = reward
    req["source"]["snapshot_sha256"] = h(str(issue))
    req["submission_packet"]["canonical_source_url"] = url
    req["maintainer_rule"]["comment_url"] = url + f"#issuecomment-{issue}"
    if capture:
        req["live_capture"]["canonical_source_url"] = url
        req["live_capture"]["live_url"] = f"https://x.com/tokenjunkie/status/{issue}"
        req["live_capture"]["resolved_url"] = f"https://x.com/tokenjunkie/status/{issue}"
        rehash(req)
    return req


class DistributionFulfillmentTests(unittest.TestCase):
    def evaluate(self, req, auth=None, at=NOW):
        return d.evaluate_one(
            req,
            authority=auth or authority(req),
            trusted_key=KEY,
            trusted_key_id=KEY_ID,
            trusted_at=at,
        )

    def verify(self, req, receipt, auth=None, at=NOW):
        return d.verify_receipt(
            req,
            receipt,
            authority=auth or authority(req),
            trusted_key=KEY,
            trusted_key_id=KEY_ID,
            trusted_at=at,
        )

    def test_ready_is_mac_authenticated_evidence_only_and_deterministic(self):
        req = request(); auth = authority(req)
        one = self.evaluate(req, auth)
        self.assertEqual(one, self.evaluate(req, auth))
        self.assertEqual(one["disposition"], "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION")
        self.assertEqual(one["reason_codes"], [])
        self.assertEqual(one["schema"], d.RECEIPT_SCHEMA)
        self.assertEqual(one["valid_until"], "2026-09-20T09:30:00Z")
        self.assertEqual(one["capture_scope"]["provider_namespace"], "social:x")
        self.assertFalse(one["authority"]["external_post_performed"])
        self.assertFalse(one["authority"]["claim_submission_authorized"])
        self.assertFalse(one["authority"]["maintainer_acceptance_inferred"])
        self.assertFalse(one["authority"]["payout_inferred"])
        self.assertFalse(one["authority"]["revenue_recognized"])
        self.assertTrue(one["authority"]["human_submission_review_required"])
        self.assertTrue(self.verify(req, one, auth))

    def test_forged_request_plus_authority_with_attacker_key_is_rejected(self):
        req = request()
        forged = copy.deepcopy(req)
        forged["source"]["state"] = "closed"
        attacker_auth = authority(forged, key=ATTACKER_KEY)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "MAC authentication failed"):
            self.evaluate(forged, attacker_auth)

    def test_advertised_reward_is_authenticated_and_exponent_bomb_rejected(self):
        req = request(); auth = authority(req)
        forged = copy.deepcopy(req)
        forged["advertised_reward"] = "999999"
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "MAC authentication failed"):
            self.evaluate(forged, auth)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "plain decimal"):
            d._reward("1e100000000")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "plain decimal"):
            d._reward("0." + "1" * 127)

    def test_host_key_id_is_not_caller_selected(self):
        req = request()
        auth = authority(req, key_id="attacker-key")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "active trusted key"):
            self.evaluate(req, auth)

    def test_retained_authority_rejects_semantic_tamper(self):
        req = request(); auth = authority(req)
        for mutate in (
            lambda r: r["source"].update(state="closed"),
            lambda r: r["maintainer_rule"].update(requires_live_url=False),
            lambda r: r["submission_packet"].update(disposition="HOLD"),
        ):
            forged = copy.deepcopy(req); mutate(forged)
            with self.assertRaisesRegex(d.DistributionFulfillmentError, "does not bind decision evidence|MAC authentication"):
                self.evaluate(forged, auth)

    def test_forged_self_hash_capture_cannot_replace_retained_authority(self):
        req = request(); auth = authority(req)
        forged = copy.deepcopy(req)
        forged["live_capture"].update(
            live_url="https://x.com/tokenjunkie/status/999",
            resolved_url="https://x.com/tokenjunkie/status/999",
            resource_identity="x.com/status/999",
            content_sha256=h("fabricated"),
            http_status=200,
            publicly_resolvable=True,
        )
        rehash(forged)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "does not bind decision evidence|MAC authentication"):
            self.evaluate(forged, auth)

    def test_verify_is_type_exact(self):
        req = request(); auth = authority(req); one = self.evaluate(req, auth)
        mutations = [
            lambda r: r["authority"].update(payout_inferred=0),
            lambda r: r["authority"].update(human_submission_review_required=1),
            lambda r: r["live_capture"].update(http_status=200.0),
        ]
        for mutate in mutations:
            candidate = copy.deepcopy(one); mutate(candidate)
            unsigned = dict(candidate); unsigned.pop("receipt_sha256")
            candidate["receipt_sha256"] = d._hash(unsigned)
            self.assertFalse(self.verify(req, candidate, auth))

    def test_business_holds_and_stale_evidence(self):
        cases = [
            (lambda r: r["source"].update(state="closed"), "SOURCE_NOT_OPEN"),
            (lambda r: r["source"].update(labels=["bounty"]), "DISTRIBUTION_LABEL_ABSENT"),
            (lambda r: r["maintainer_rule"].update(requires_live_url=False), "LIVE_URL_RULE_NOT_REQUIRED"),
            (lambda r: r["submission_packet"].update(disposition="HOLD"), "BASE_SUBMISSION_PACKET_HOLD"),
            (lambda r: r.update(live_capture=None), "LIVE_URL_MISSING"),
            (lambda r: (r["live_capture"].update(observed_at="2026-09-06T09:59:59Z"), rehash(r)), "LIVE_CAPTURE_STALE"),
            (lambda r: r["source"].update(captured_at="2026-09-06T09:59:59Z", updated_at="2026-09-06T09:59:58Z"), "SOURCE_SNAPSHOT_STALE"),
            (lambda r: r["maintainer_rule"].update(captured_at="2026-09-06T09:59:59Z"), "MAINTAINER_RULE_STALE"),
            (lambda r: (r["live_capture"].update(http_status=404), rehash(r)), "LIVE_URL_NOT_SUCCESSFUL"),
            (lambda r: (r["live_capture"].update(publicly_resolvable=False), rehash(r)), "LIVE_URL_NOT_PUBLIC"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                req = request(); mutate(req); auth = authority(req)
                receipt = self.evaluate(req, auth)
                self.assertEqual(receipt["disposition"], "HOLD")
                self.assertIn(reason, receipt["reason_codes"])

    def test_stale_holds_are_immediately_and_later_integrity_verifiable(self):
        mutators = [
            lambda r: r["source"].update(captured_at="2026-09-06T09:59:59Z", updated_at="2026-09-06T09:59:58Z"),
            lambda r: r["maintainer_rule"].update(captured_at="2026-09-06T09:59:59Z"),
            lambda r: (r["live_capture"].update(observed_at="2026-09-06T09:59:59Z"), rehash(r)),
        ]
        for mutate in mutators:
            req = request(); mutate(req); auth = authority(req)
            receipt = self.evaluate(req, auth)
            self.assertEqual(receipt["disposition"], "HOLD")
            self.assertLess(receipt["valid_until"], NOW)
            self.assertTrue(self.verify(req, receipt, auth, at=NOW))
            self.assertTrue(self.verify(req, receipt, auth, at="2026-10-01T00:00:00Z"))
            queue = d.build_queue(
                [req],
                authorities_by_source={SOURCE: auth},
                trusted_key=KEY,
                trusted_key_id=KEY_ID,
                trusted_at=NOW,
            )
            self.assertTrue(d.verify_queue(
                [req],
                queue,
                authorities_by_source={SOURCE: auth},
                trusted_key=KEY,
                trusted_key_id=KEY_ID,
                trusted_at=NOW,
            ))

    def test_ready_receipt_expires_but_cannot_turn_into_hold_or_back(self):
        req = request(); auth = authority(req); receipt = self.evaluate(req, auth)
        self.assertTrue(self.verify(req, receipt, auth, at="2026-09-20T09:30:00Z"))
        self.assertFalse(self.verify(req, receipt, auth, at="2026-09-20T09:30:01Z"))
        forged = copy.deepcopy(receipt)
        forged["disposition"] = "HOLD"
        forged["reason_codes"] = ["SOURCE_SNAPSHOT_STALE"]
        unsigned = dict(forged); unsigned.pop("receipt_sha256")
        forged["receipt_sha256"] = d._hash(unsigned)
        self.assertFalse(self.verify(req, forged, auth, at=NOW))

    def test_capture_must_follow_authorizing_evidence(self):
        req = request(); req["live_capture"]["observed_at"] = "2026-09-13T09:29:00Z"; rehash(req)
        auth = authority(req)
        receipt = self.evaluate(req, auth)
        self.assertIn("LIVE_CAPTURE_PREDATES_SOURCE", receipt["reason_codes"])
        self.assertIn("LIVE_CAPTURE_PREDATES_RULE", receipt["reason_codes"])

    def test_authority_must_follow_evidence_and_not_be_future(self):
        req = request()
        early = authority(req, issued_at="2026-09-13T09:44:00Z")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "predates live capture"):
            self.evaluate(req, early)
        future = authority(req, issued_at="2026-09-13T10:00:01Z")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "issued in the future"):
            self.evaluate(req, future)

    def test_two_verifier_aliases_same_provider_resource_collide(self):
        first = request(resource="x.com/status/123", verifier="verifier-a")
        second = request_for(
            316,
            capture=True,
            resource="x.com/status/123",
            verifier="verifier-b",
            content=first["live_capture"]["content_sha256"],
        )
        second["live_capture"]["resolved_url"] = "https://x.com/tokenjunkie/status/316?alias=1"
        rehash(second)
        auths = {
            first["canonical_source_url"]: authority(first, provider="social:x"),
            second["canonical_source_url"]: authority(second, provider="social:x"),
        }
        queue = d.build_queue(
            [first, second],
            authorities_by_source=auths,
            trusted_key=KEY,
            trusted_key_id=KEY_ID,
            trusted_at=NOW,
        )
        self.assertEqual((queue["ready_count"], queue["hold_count"]), (0, 2))
        for item in queue["hold"]:
            self.assertIn("LIVE_RESOURCE_REUSED_ACROSS_CLAIMS", item["queue_reason_codes"])
            self.assertIn("LIVE_CONTENT_REUSED_ACROSS_CLAIMS", item["queue_reason_codes"])
            self.assertNotIn("LIVE_URL_REUSED_ACROSS_CLAIMS", item["queue_reason_codes"])
        self.assertTrue(d.verify_queue(
            [first, second],
            queue,
            authorities_by_source=auths,
            trusted_key=KEY,
            trusted_key_id=KEY_ID,
            trusted_at=NOW,
        ))

    def test_provider_namespace_is_authenticated_not_capture_derived(self):
        req = request(); auth = authority(req, provider="social:x")
        forged = copy.deepcopy(auth)
        forged["provider_namespace"] = "other:provider"
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "MAC authentication failed"):
            self.evaluate(req, forged)

    def test_github_case_aliases_share_one_canonical_claim_identity(self):
        req = request()
        alias = copy.deepcopy(req)
        alias["canonical_source_url"] = "https://github.com/SCOTTCJN/RustChain-Bounties/issues/315"
        self.assertEqual(d._issue(req["canonical_source_url"]), d._issue(alias["canonical_source_url"]))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(d._authority_record_path(req["canonical_source_url"], root), d._authority_record_path(alias["canonical_source_url"], root))
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "duplicate canonical"):
            d.build_queue(
                [req, alias],
                authorities_by_source={SOURCE: authority(req)},
                trusted_key=KEY,
                trusted_key_id=KEY_ID,
                trusted_at=NOW,
            )

    def test_queue_prioritizes_reward_and_exact_authority_coverage(self):
        missing = request(capture=False)
        lower = request_for(316, reward="10", capture=False)
        auths = {
            missing["canonical_source_url"]: authority(missing),
            lower["canonical_source_url"]: authority(lower),
        }
        queue = d.build_queue(
            [lower, missing],
            authorities_by_source=auths,
            trusted_key=KEY,
            trusted_key_id=KEY_ID,
            trusted_at=NOW,
        )
        self.assertEqual([x["advertised_reward"] for x in queue["action_queue"]], ["45", "10"])
        self.assertEqual(queue["action_queue"][0]["next_action"], "PUBLISH_OR_CAPTURE_LIVE_URL")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "duplicate canonical"):
            d.build_queue(
                [missing, missing],
                authorities_by_source=auths,
                trusted_key=KEY,
                trusted_key_id=KEY_ID,
                trusted_at=NOW,
            )
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "exactly cover"):
            d.build_queue(
                [missing, lower],
                authorities_by_source={missing["canonical_source_url"]: authority(missing)},
                trusted_key=KEY,
                trusted_key_id=KEY_ID,
                trusted_at=NOW,
            )

    def test_host_and_redirect_policy(self):
        req = request(host="example.com"); auth = authority(req)
        bad = self.evaluate(req, auth)
        self.assertIn("LIVE_URL_HOST_NOT_ALLOWED", bad["reason_codes"])
        self.assertIn("RESOLVED_URL_HOST_NOT_ALLOWED", bad["reason_codes"])
        req = request(host="m.youtube.com"); auth = authority(req, provider="video:youtube")
        good = self.evaluate(req, auth)
        self.assertEqual(good["disposition"], "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION")

    def test_capture_integrity_url_resource_and_types_fail_closed(self):
        req = request(); req["live_capture"]["content_sha256"] = h("tamper")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "does not bind"):
            authority(req)
        req = request(); req["live_capture"]["http_status"] = True; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "http_status"):
            authority(req)
        req = request(); req["live_capture"]["live_url"] = "https://github.com/example/post"; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "off-platform"):
            authority(req)
        req = request(); req["live_capture"]["resource_identity"] = "../bad"; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "resource_identity"):
            authority(req)

    def test_duplicate_json_keys_and_nonfinite_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
            with self.assertRaisesRegex(d.DistributionFulfillmentError, "duplicate JSON key"):
                d._load(str(path))
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "canonical JSON"):
            d._hash({"x": float("nan")})

    def test_cli_has_no_authority_or_key_argument(self):
        with self.assertRaises(SystemExit) as caught:
            d.main(["evaluate", "request.json", "attacker-authority.json"])
        self.assertEqual(caught.exception.code, 2)

    @unittest.skipUnless(os.name == "posix", "POSIX permission fence")
    def test_trusted_key_and_record_require_private_regular_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            key_path = root / "key.json"
            key_path.write_text(json.dumps({
                "schema": d.AUTHORITY_KEY_SCHEMA,
                "key_id": KEY_ID,
                "key_hex": KEY.hex(),
            }), encoding="utf-8")
            os.chmod(key_path, 0o600)
            self.assertEqual(d._load_trusted_key(key_path), (KEY_ID, KEY))
            os.chmod(key_path, 0o644)
            with self.assertRaisesRegex(d.DistributionFulfillmentError, "permissions"):
                d._load_trusted_key(key_path)

            link = root / "link.json"
            link.symlink_to(key_path)
            with self.assertRaisesRegex(d.DistributionFulfillmentError, "symlink"):
                d._read_private_file(link, name="authority key")

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor fence")
    def test_private_read_retains_parent_generation_across_path_rebind(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "trusted"
            old = base / "trusted-old"
            root.mkdir(mode=0o700)
            key_path = root / "key.json"
            key_path.write_text(json.dumps({
                "schema": d.AUTHORITY_KEY_SCHEMA,
                "key_id": KEY_ID,
                "key_hex": KEY.hex(),
            }), encoding="utf-8")
            os.chmod(key_path, 0o600)

            real_open = os.open
            swapped = False

            def adversarial_open(path, flags, *args, **kwargs):
                nonlocal swapped
                fd = real_open(path, flags, *args, **kwargs)
                if not swapped and kwargs.get("dir_fd") is None and os.fspath(path) == os.fspath(root):
                    swapped = True
                    root.rename(old)
                    root.mkdir(mode=0o700)
                    attacker = root / "key.json"
                    attacker.write_text(json.dumps({
                        "schema": d.AUTHORITY_KEY_SCHEMA,
                        "key_id": "attacker-key",
                        "key_hex": ATTACKER_KEY.hex(),
                    }), encoding="utf-8")
                    os.chmod(attacker, 0o600)
                return fd

            with mock.patch.object(d.os, "open", side_effect=adversarial_open):
                self.assertEqual(d._load_trusted_key(key_path), (KEY_ID, KEY))
            self.assertTrue(swapped)

    def test_record_path_is_source_derived_not_caller_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = d._authority_record_path(SOURCE, root)
            self.assertEqual(path.parent, root)
            self.assertRegex(path.name, r"^[0-9a-f]{64}\.json$")
            self.assertNotIn("315", path.name)


if __name__ == "__main__":
    unittest.main()
