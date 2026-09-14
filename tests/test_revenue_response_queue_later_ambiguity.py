import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).parents[1] / "concierge" / "revenue_response_queue.py"
spec = importlib.util.spec_from_file_location("revenue_response_queue", MODULE_PATH)
rq = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(rq)

BUYER = "buyer@example.com"
SENT = "sent-1"
THREAD = "thread-1"


def dt(hour: int) -> datetime:
    return datetime(2026, 9, 13, hour, 0, 0, tzinfo=timezone.utc)


def message(message_id, kind, hour, seq, *, related=None, from_route=BUYER, to_routes=()):
    return {
        "id": message_id,
        "kind": kind,
        "occurred_at": dt(hour),
        "from_route": from_route,
        "to_routes": tuple(to_routes),
        "related_message_id": related,
        "sequence": seq,
    }


def row():
    return {
        "engagement_id": "eng-1",
        "provider_thread_id": THREAD,
        "sent_message_id": SENT,
        "buyer_route": BUYER,
        "authorized_reply_routes": [BUYER],
        "offer_key": "offer-1",
        "contact_policy": "follow_up_allowed",
        "follow_up_after_hours": 24,
        "manifest_row_sha256": "a" * 64,
    }


def snapshot(*extra):
    anchor = message(
        SENT,
        "outbound",
        10,
        10,
        from_route="seller@example.com",
        to_routes=(BUYER,),
    )
    messages = [anchor, *extra]
    return {"thread_id": THREAD, "fetched_at": dt(13), "messages": messages}


class LaterAmbiguityPrecedenceTests(unittest.TestCase):
    def classify(self, *events):
        return rq._classify(row(), snapshot(*events), now=dt(13), auto_ack_grace_hours=4)

    def test_newer_unlinked_outbound_overrides_older_linked_human(self):
        linked_human = message("human", "human_inbound", 11, 20, related=SENT)
        newer_unlinked_outbound = message(
            "outbound-2",
            "outbound",
            12,
            30,
            related=None,
            from_route="seller@example.com",
            to_routes=(BUYER,),
        )
        item = self.classify(linked_human, newer_unlinked_outbound)
        self.assertEqual(item["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(item["reason_codes"], ["UNLINKED_OR_AMBIGUOUS_SAME_THREAD_ACTIVITY"])

    def test_newer_unlinked_authorized_inbound_overrides_older_linked_human(self):
        linked_human = message("human", "human_inbound", 11, 20, related=SENT)
        newer_unlinked_human = message("human-2", "human_inbound", 12, 30, related=None)
        item = self.classify(linked_human, newer_unlinked_human)
        self.assertEqual(item["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(item["reason_codes"], ["UNLINKED_OR_AMBIGUOUS_SAME_THREAD_ACTIVITY"])

    def test_newer_linked_human_can_supersede_older_unlinked_noise(self):
        older_unlinked_human = message("noise", "human_inbound", 11, 20, related=None)
        newer_linked_human = message("human", "human_inbound", 12, 30, related=SENT)
        item = self.classify(older_unlinked_human, newer_linked_human)
        self.assertEqual(item["state"], "HUMAN_REPLY")
        self.assertEqual(item["reason_codes"], ["AUTHORIZED_LINKED_HUMAN_REPLY"])


if __name__ == "__main__":
    unittest.main()
