import hashlib
import tempfile
import unittest
from pathlib import Path

from concierge import distribution_fulfillment as d

NOW = "2026-09-13T10:00:00Z"
SOURCE = "https://github.com/Scottcjn/rustchain-bounties/issues/315"


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def request(*, capture=True, host="x.com"):
    cap = None
    if capture:
        core = {
            "canonical_source_url": SOURCE,
            "live_url": f"https://{host}/tokenjunkie/status/123",
            "resolved_url": f"https://{host}/tokenjunkie/status/123",
            "observed_at": "2026-09-13T09:45:00Z",
            "http_status": 200,
            "publicly_resolvable": True,
            "content_sha256": h("live-content"),
            "verifier": "public-web-capture/v1",
        }
        cap = {**core, "capture_sha256": d._hash(core)}
    return {
        "schema": "distribution-fulfillment-request/v1",
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


def rehash(req):
    core = dict(req["live_capture"])
    core.pop("capture_sha256")
    req["live_capture"]["capture_sha256"] = d._hash(core)


def request_for(issue, reward="10", capture=False):
    req = request(capture=capture)
    url = f"https://github.com/Scottcjn/rustchain-bounties/issues/{issue}"
    req["canonical_source_url"] = url
    req["advertised_reward"] = reward
    req["source"]["snapshot_sha256"] = h(str(issue))
    req["submission_packet"]["canonical_source_url"] = url
    req["maintainer_rule"]["comment_url"] = url + f"#issuecomment-{issue}"
    if capture:
        req["live_capture"]["canonical_source_url"] = url
        rehash(req)
    return req


class DistributionFulfillmentTests(unittest.TestCase):
    def test_ready_is_evidence_only_and_deterministic(self):
        req = request()
        one = d.evaluate_one(req, evaluated_at=NOW)
        self.assertEqual(one, d.evaluate_one(req, evaluated_at=NOW))
        self.assertEqual(one["disposition"], "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION")
        self.assertEqual(one["reason_codes"], [])
        self.assertFalse(one["authority"]["external_post_performed"])
        self.assertFalse(one["authority"]["claim_submission_authorized"])
        self.assertFalse(one["authority"]["maintainer_acceptance_inferred"])
        self.assertFalse(one["authority"]["payout_inferred"])
        self.assertFalse(one["authority"]["revenue_recognized"])
        self.assertTrue(one["authority"]["human_submission_review_required"])
        self.assertTrue(d.verify_receipt(req, one, evaluated_at=NOW))
        one["authority"]["payout_inferred"] = True
        self.assertFalse(d.verify_receipt(req, one, evaluated_at=NOW))

    def test_business_holds(self):
        cases = [
            (lambda r: r["source"].update(state="closed"), "SOURCE_NOT_OPEN"),
            (lambda r: r["source"].update(labels=["bounty"]), "DISTRIBUTION_LABEL_ABSENT"),
            (lambda r: r["maintainer_rule"].update(requires_live_url=False), "LIVE_URL_RULE_NOT_REQUIRED"),
            (lambda r: r["submission_packet"].update(disposition="HOLD"), "BASE_SUBMISSION_PACKET_HOLD"),
            (lambda r: r.update(live_capture=None), "LIVE_URL_MISSING"),
            (lambda r: (r["live_capture"].update(observed_at="2026-09-13T10:01:00Z"), rehash(r)), "LIVE_CAPTURE_FROM_FUTURE"),
            (lambda r: (r["live_capture"].update(observed_at="2026-09-06T09:59:59Z"), rehash(r)), "LIVE_CAPTURE_STALE"),
            (lambda r: (r["live_capture"].update(http_status=404), rehash(r)), "LIVE_URL_NOT_SUCCESSFUL"),
            (lambda r: (r["live_capture"].update(publicly_resolvable=False), rehash(r)), "LIVE_URL_NOT_PUBLIC"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                req = request(); mutate(req)
                receipt = d.evaluate_one(req, evaluated_at=NOW)
                self.assertEqual(receipt["disposition"], "HOLD")
                self.assertIn(reason, receipt["reason_codes"])

    def test_host_and_redirect_policy(self):
        bad = d.evaluate_one(request(host="example.com"), evaluated_at=NOW)
        self.assertIn("LIVE_URL_HOST_NOT_ALLOWED", bad["reason_codes"])
        self.assertIn("RESOLVED_URL_HOST_NOT_ALLOWED", bad["reason_codes"])
        good = d.evaluate_one(request(host="m.youtube.com"), evaluated_at=NOW)
        self.assertEqual(good["disposition"], "READY_FOR_HUMAN_DISTRIBUTION_SUBMISSION")

    def test_malformed_authority_fails_closed(self):
        cases = [
            (lambda r: r.update(surprise=True), "undeclared"),
            (lambda r: r.update(advertised_reward=True), "decimal"),
            (lambda r: r.update(advertised_reward="NaN"), "finite"),
            (lambda r: r["source"].update(labels=["distribution", "Distribution"]), "duplicates"),
            (lambda r: r["source"].update(captured_at="2026-09-13T10:01:00Z"), "future"),
            (lambda r: r["maintainer_rule"].update(captured_at="2026-09-13T10:01:00Z"), "future"),
            (lambda r: r["maintainer_rule"].update(allowed_hosts=["x.com", "x.com"]), "duplicates"),
            (lambda r: r["maintainer_rule"].update(allowed_hosts=["github.com"]), "off-platform"),
            (lambda r: r["maintainer_rule"].update(comment_url="https://github.com/Other/repo/issues/315#issuecomment-1"), "belong"),
            (lambda r: r["submission_packet"].update(canonical_source_url="https://github.com/Scottcjn/rustchain-bounties/issues/316"), "does not match"),
            (lambda r: r["submission_packet"]["authority"].update(cash_claim=0), "authority"),
        ]
        for mutate, pattern in cases:
            with self.subTest(pattern=pattern):
                req = request(); mutate(req)
                with self.assertRaisesRegex(d.DistributionFulfillmentError, pattern):
                    d.evaluate_one(req, evaluated_at=NOW)

    def test_capture_integrity_and_url_shape_fail_closed(self):
        req = request(); req["live_capture"]["content_sha256"] = h("tamper")
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "does not bind"):
            d.evaluate_one(req, evaluated_at=NOW)
        req = request(); req["live_capture"]["http_status"] = True; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "http_status"):
            d.evaluate_one(req, evaluated_at=NOW)
        req = request(); req["live_capture"]["live_url"] = "https://github.com/example/post"; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "off-platform"):
            d.evaluate_one(req, evaluated_at=NOW)
        req = request(); req["live_capture"]["live_url"] = "https://x.com"; rehash(req)
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "canonical"):
            d.evaluate_one(req, evaluated_at=NOW)

    def test_queue_prioritizes_reward_and_prevents_live_url_reuse(self):
        queue = d.build_queue([request_for(316), request(capture=False)], evaluated_at=NOW)
        self.assertEqual([x["advertised_reward"] for x in queue["action_queue"]], ["45", "10"])
        self.assertEqual(queue["action_queue"][0]["next_action"], "PUBLISH_OR_CAPTURE_LIVE_URL")
        reused = d.build_queue([request(), request_for(316, capture=True)], evaluated_at=NOW)
        self.assertEqual((reused["ready_count"], reused["hold_count"]), (0, 2))
        for row in reused["hold"]:
            self.assertIn("LIVE_URL_REUSED_ACROSS_CLAIMS", row["reason_codes"])
        with self.assertRaisesRegex(d.DistributionFulfillmentError, "duplicate canonical"):
            d.build_queue([request(), request()], evaluated_at=NOW)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
            with self.assertRaisesRegex(d.DistributionFulfillmentError, "duplicate JSON key"):
                d._load(str(path))


if __name__ == "__main__":
    unittest.main()
