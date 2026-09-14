from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE_SPEC = importlib.util.spec_from_file_location(
    "response_queue_base_tests", ROOT / "tests" / "test_revenue_response_queue.py"
)
assert BASE_SPEC and BASE_SPEC.loader
base = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(base)
rq = base.rq


class DecisiveEventPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.clock = rq._current_utc
        rq._current_utc = lambda: base.NOW
        self.old = {
            name: os.environ.get(name)
            for name in (
                rq._ATTESTATION_KEY_ENV,
                rq._ATTESTATION_KEY_ID_ENV,
                rq._AUTHORIZED_PROVIDER_ENV,
                rq._AUTHORIZED_PRINCIPAL_SHA256_ENV,
            )
        }
        os.environ[rq._ATTESTATION_KEY_ENV] = base.base64.b64encode(base.KEY).decode()
        os.environ[rq._ATTESTATION_KEY_ID_ENV] = base.KEY_ID
        os.environ[rq._AUTHORIZED_PROVIDER_ENV] = base.PROVIDER
        os.environ[rq._AUTHORIZED_PRINCIPAL_SHA256_ENV] = hashlib.sha256(base.PRINCIPAL.encode()).hexdigest()

    def tearDown(self):
        rq._current_utc = self.clock
        for name, value in self.old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def compile(self, messages):
        manifest = [base.row()]
        snapshots = [base.snap(*messages)]
        pol = base.policy()
        return rq.compile_revenue_response_queue(
            manifest,
            base.batch(manifest, snapshots, pol=pol),
            policy=pol,
        )["items"][0]

    def test_newer_unlinked_outbound_overrides_older_linked_human(self):
        human = base.msg(
            "human", "human_inbound", "2026-09-13T11:00:00Z",
            from_route=base.BUYER, related=base.SENT, seq=20,
        )
        unrelated = base.msg(
            "sent-2", "outbound", "2026-09-13T12:00:00Z",
            to_routes=[base.BUYER], related=None, seq=30,
        )
        item = self.compile([base.anchor(), human, unrelated])
        self.assertEqual(item["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(item["reason_codes"], ["UNLINKED_OR_AMBIGUOUS_SAME_THREAD_ACTIVITY"])
        self.assertEqual(item["evidence_message_id_sha256"], rq._hash_text("sent-2"))

    def test_newer_unlinked_authorized_inbound_overrides_older_linked_human(self):
        human = base.msg(
            "human", "human_inbound", "2026-09-13T11:00:00Z",
            from_route=base.BUYER, related=base.SENT, seq=20,
        )
        unrelated = base.msg(
            "other", "human_inbound", "2026-09-13T12:00:00Z",
            from_route=base.BUYER, related=None, seq=30,
        )
        item = self.compile([base.anchor(), human, unrelated])
        self.assertEqual(item["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(item["evidence_message_id_sha256"], rq._hash_text("other"))

    def test_newer_linked_human_resolves_older_unlinked_activity(self):
        unrelated = base.msg(
            "sent-2", "outbound", "2026-09-13T11:00:00Z",
            to_routes=[base.BUYER], related=None, seq=20,
        )
        human = base.msg(
            "human", "human_inbound", "2026-09-13T12:00:00Z",
            from_route=base.BUYER, related=base.SENT, seq=30,
        )
        item = self.compile([base.anchor(), unrelated, human])
        self.assertEqual(item["state"], "HUMAN_REPLY")
        self.assertEqual(item["evidence_message_id_sha256"], rq._hash_text("human"))

    def test_newer_decisive_event_orders_linked_human_and_bounce(self):
        human = base.msg(
            "human", "human_inbound", "2026-09-13T11:00:00Z",
            from_route=base.BUYER, related=base.SENT, seq=20,
        )
        bounce = base.msg(
            "bounce", "bounce", "2026-09-13T12:00:00Z",
            from_route="mailer-daemon@example.net", related=base.SENT, seq=30,
        )
        self.assertEqual(self.compile([base.anchor(), human, bounce])["state"], "ROUTE_REPAIR")

        early_bounce = base.msg(
            "bounce", "bounce", "2026-09-13T11:00:00Z",
            from_route="mailer-daemon@example.net", related=base.SENT, seq=20,
        )
        late_human = base.msg(
            "human", "human_inbound", "2026-09-13T12:00:00Z",
            from_route=base.BUYER, related=base.SENT, seq=30,
        )
        self.assertEqual(self.compile([base.anchor(), early_bounce, late_human])["state"], "HUMAN_REPLY")


if __name__ == "__main__":
    unittest.main()
