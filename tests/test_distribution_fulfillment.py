import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from concierge import distribution_fulfillment as d

NOW = "2026-09-13T10:00:00Z"
SOURCE = "https://github.com/Scottcjn/rustchain-bounties/issues/315"


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def request(*, capture=True, host="x.com", resource="x.com/status/123"):
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
            "content_sha256": h("live-content"),
            "verifier": "public-web-capture/v1",
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


def bindings(req):
    return d._bindings_for_fixture(copy.deepcopy(req))


def rehash(req):
    core = dict(req["live_capture"])
    core.pop("capture_sha256")
    req["live_capture"]["capture_sha256"] = d._hash(core)


def request_for(issue, reward="10", capture=False, *, resource=None, content=None):
    req = request(capture=capture, resource=resource or f"x.com/status/{issue}")
    url = f"https://github.com/Scottcjn/rustchain-bounties/issues/{issue}"
    req["canonical_source_url"] = url
    req["advertised_reward"] = reward
    req["source"]["snapshot_sha256"] = h(str(issue))
    req["submission_packet"]["canonical_source_url"] = url
    req["maintainer_rule"]["comment_url"] = url + f"#issuecomment-{issue}"
    if capture:
        req["live_capture"]["canonical_source_url"] = url
        req["live_capture"]["live_url"] = f"https://x.com/tokenjunkie/status/{issue}"
        req["live_capture"]["resolved_url"] = f"https://x.com/tokenjunkie/status/{issue}"
        if content is not None:
            req["live_capture"]["content_sha256"] = content
        rehash(req)
    return req


class DistributionFulfillmentTests(unittest.TestCase):
    def test_ready_is_evidence_only_and_deterministic(self):
        req = request(); auth = bindings(req)
        one = d.evaluate_one(req, bindings=auth, trusted_at=NOW)
        self.assertEqual(one, d.evaluate_one(req, bindings=auth, trusted_at=NOW))
        self.assertEqual(one["disposition"], "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION")
        self.assertEqual(one["reason_codes"], [])
        self.assertEqual(one["schema"], d.RECEIPT_SCHEMA)
        self.assertEqual(one["valid_until"], "2026-09-20T09:30:00Z")
        self.assertFalse(one["authority"]["external_post_performed"])
        self.assertFalse(one["authority"]["claim_submission_authorized"])
        self.assertFalse(one["authority"]["maintainer_acceptance_inferred"])
        self.assertFalse(one["authority"]["payout_inferred"])
        self.assertFalse(one["authority"]["revenue_recognized"])
        self.assertTrue(one["authority"]["human_submission_review_required"])
        self.assertTrue(d.verify_receipt(req, one, bindings=auth, trusted_at=NOW))

    def test_verify_is_type_exact(self):
        req = request(); auth = bindings(req); one = d.evaluate_one(req, bindings=auth, trusted_at=NOW)
        mutations = [
            lambda r: r["authority"].update(payout_inferred=0),
            lambda r: r["authority"].update(human_submission_review_required=1),
            lambda r: r["live_capture"].update(http_status=200.0),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(one); mutate(candidate)
                unsigned = dict(candidate); unsigned.pop("receipt_sha256")
                candidate["receipt_sha256"] = d._hash(unsigned)
                self.assertFalse(d.verify_receipt(req, candidate, bindings=auth, trusted_at=NOW))

    def test_business_holds_and_freshness(self):
        cases = [
            (lambda r: r["source"].update(state="closed"), "SOURCE_NOT_OPEN"),
            (lambda r: r["source"].update(labels=["bounty"]), "DISTRIBUTION_LABEL_ABSENT"),
            (lambda r: r["maintainer_rule"].update(requires_live_url=False), "LIVE_URL_RULE_NOT_REQUIRED"),
            (lambda r: r["submission_packet"].update(disposition="HOLD"), "BASE_SUBMISSION_PACKET_HOLD"),
            (lambda r: r.update(live_capture=None), "LIVE_URL_MISSING"),
            (lambda r: (r["live_capture"].update(observed_at="2026-09-13T10:01:00Z"), rehash(r)), "LIVE_CAPTURE_FROM_FUTURE"),
            (lambda r: (r["live_capture"].update(observed_at="2026-09-06T09:59:59Z"), rehash(r)), "LIVE_CAPTURE_STALE"),
            (lambda r: r["source"].update(captured_at="2026-09-06T09:59:59Z", updated_at="2026-09-06T09:59:58Z"), "SOURCE_SNAPSHOT_STALE"),
            (lambda r: r["maintainer_rule"].update(captured_at="2026-09-06T09:59:59Z"), "MAINTAINER_RULE_STALE"),
            (lambda r: (r["live_capture"].update(http_status=404), rehash(r)), "LIVE_URL_NOT_SUCCESSFUL"),
            (lambda r: (r["live_capture"].update(publicly_resolvable=False), rehash(r)), "LIVE_URL_NOT_PUBLIC"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                req = request(); mutate(req); auth = bindings(req)
                receipt = d.evaluate_one(req, bindings=auth, trusted_at=NOW)
                self.assertEqual(receipt["disposition"], "HOLD")
                self.assertIn(reason, receipt["reason_codes"])

    def test_capture_must_follow_authorizing_evidence(self):
        req = request(); req["live_capture"]["observed_at"] = "2026-09-13T09:29:00Z"; rehash(req)
        receipt = d.evaluate_one(req, bindings=bindings(req), trusted_at=NOW)
        self.assertIn("LIVE_CAPTURE_PREDATES_SOURCE", receipt["reason_codes"])
        self.assertIn("LIVE_CAPTURE_PREDATES_RULE", receipt["reason_codes"])

    def test_receipt_expires_against_current_trusted_time(self):
        req = request(); auth = bindings(req)
        receipt = d.evaluate_one(req, bindings=auth, trusted_at=NOW)
        self.assertTrue(d.verify_receipt(req, receipt, bindings=auth, trusted_at="2026-09-20T09:30:00Z"))
        self.assertFalse(d.verify_receipt(req, receipt, bindings=auth, trusted_at="2026-09-20T09:30:01Z"))

    def test_out_of_band_bindings_reject_same_opaque_digest_semantic_tamper(self):
        req = request(); auth = bindings(req)
        for mutate in (
            lambda r: r["source"].update(state="closed"),
            lambda r: r["maintainer_rule"].update(requires_live_url=False),
            lambda r: r["submission_packet"].update(disposition="HOLD"),
        ):
            with self.subTest(mutate=mutate):
                forged = copy.deepcopy(req); mutate(forged)
                with self.assertRaisesRegex(d.DistributionFulfillmentError, "does not bind decision evidence"):
                    d.evaluate_one(forged, bindings=auth, trusted_at=NOW)

    def test_forged_self_hash_capture_cannot_substitute_for_trusted_binding(self):
        req = request(); auth = bindings(req)
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
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "live_capture_decision_sha256"):
            d.evaluate_one(forged, bindings=auth, trusted_at=NOW)

    def test_capture_integrity_url_and_types_fail_closed(self):
        req = request(); req["live_capture"]["content_sha256"] = h("tamper")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "does not bind"):
            d.evaluate_one(req, bindings=bindings(request()), trusted_at=NOW)
        req = request(); req["live_capture"]["http_status"] = True; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "http_status"):
            d._bindings_for_fixture(req)
        req = request(); req["live_capture"]["live_url"] = "https://github.com/example/post"; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "off-platform"):
            d._bindings_for_fixture(req)
        req = request(); req["live_capture"]["resource_identity"] = "../bad"; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "resource_identity"):
            d._bindings_for_fixture(req)

    def test_queue_keeps_single_receipts_immutable_and_verifiable(self):
        first = request()
        second = request_for(316, capture=True, resource="x.com/status/123")
        second["live_capture"]["resolved_url"] = "https://x.com/tokenjunkie/status/123?ref=alias"
        second["live_capture"]["content_sha256"] = first["live_capture"]["content_sha256"]
        rehash(second)
        auth = {first["canonical_source_url"]: bindings(first), second["canonical_source_url"]: bindings(second)}
        queue = d.build_queue([first, second], bindings_by_source=auth, trusted_at=NOW)
        self.assertEqual((queue["ready_count"], queue["hold_count"]), (0, 2))
        for item in queue["hold"]:
            self.assertTrue(d.verify_receipt(
                first if item["canonical_source_url"] == first["canonical_source_url"] else second,
                item["receipt"],
                bindings=auth[item["canonical_source_url"]],
                trusted_at=NOW,
            ))
            self.assertIn("LIVE_RESOURCE_REUSED_ACROSS_CLAIMS", item["queue_reason_codes"])
            self.assertIn("LIVE_CONTENT_REUSED_ACROSS_CLAIMS", item["queue_reason_codes"])
        self.assertTrue(d.verify_queue([first, second], queue, bindings_by_source=auth, trusted_at=NOW))

    def test_queue_url_reuse_and_reward_priority(self):
        missing = request(capture=False)
        lower = request_for(316, reward="10", capture=False)
        auth = {missing["canonical_source_url"]: bindings(missing), lower["canonical_source_url"]: bindings(lower)}
        queue = d.build_queue([lower, missing], bindings_by_source=auth, trusted_at=NOW)
        self.assertEqual([x["advertised_reward"] for x in queue["action_queue"]], ["45", "10"])
        self.assertEqual(queue["action_queue"][0]["next_action"], "PUBLISH_OR_CAPTURE_LIVE_URL")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "duplicate canonical"):
            d.build_queue([missing, missing], bindings_by_source=auth, trusted_at=NOW)

    def test_host_and_redirect_policy(self):
        req = request(host="example.com")
        bad = d.evaluate_one(req, bindings=bindings(req), trusted_at=NOW)
        self.assertIn("LIVE_URL_HOST_NOT_ALLOWED", bad["reason_codes"])
        self.assertIn("RESOLVED_URL_HOST_NOT_ALLOWED", bad["reason_codes"])
        req = request(host="m.youtube.com")
        good = d.evaluate_one(req, bindings=bindings(req), trusted_at=NOW)
        self.assertEqual(good["disposition"], "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION")

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
            with self.assertRaisesRegex(d.DistributionFulfillmentError, "duplicate JSON key"):
                d._load(str(path))

    def test_authority_file_must_exactly_cover_queue_sources(self):
        req = request(); other = request_for(316, capture=False)
        auth = {req["canonical_source_url"]: bindings(req)}
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "exactly cover"):
            d.build_queue([req, other], bindings_by_source=auth, trusted_at=NOW)

    def test_canonical_json_rejects_nonfinite(self):
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "canonical JSON"):
            d._hash({"x": float("nan")})


if __name__ == "__main__":
    unittest.main()
