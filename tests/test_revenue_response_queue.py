from __future__ import annotations

import copy
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "revenue_response_queue_v1",
    HERE / "concierge" / "revenue_response_queue.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
BUYER = "buyer@example.com"
THREAD = "thr-1"
SENT = "sent-1"


def manifest_row(
    *,
    engagement_id="eng-1",
    provider_thread_id=THREAD,
    sent_message_id=SENT,
    buyer_route=BUYER,
    authorized_reply_routes=None,
    offer_key="parity-proof-v1",
    contact_policy="follow_up_allowed",
    follow_up_after_hours=48,
):
    if authorized_reply_routes is None:
        authorized_reply_routes = [buyer_route]
    return {
        "engagement_id": engagement_id,
        "provider_thread_id": provider_thread_id,
        "sent_message_id": sent_message_id,
        "buyer_route": buyer_route,
        "authorized_reply_routes": authorized_reply_routes,
        "offer_key": offer_key,
        "contact_policy": contact_policy,
        "follow_up_after_hours": follow_up_after_hours,
    }


def message(
    message_id,
    kind,
    occurred_at,
    *,
    from_route="seller@example.net",
    to_routes=None,
    related_message_id=None,
    sequence=None,
):
    if to_routes is None:
        to_routes = [BUYER] if kind == "outbound" else ["seller@example.net"]
    if sequence is None:
        # Stable fixture ordinal: explicit values are used where same-time ordering matters.
        sequence = sum((index + 1) * ord(ch) for index, ch in enumerate(message_id))
    return {
        "id": message_id,
        "kind": kind,
        "occurred_at": occurred_at,
        "from_route": from_route,
        "to_routes": to_routes,
        "related_message_id": related_message_id,
        "sequence": sequence,
    }


def snapshot(*messages, thread_id=THREAD, fetched_at="2026-09-13T11:59:00Z", complete=True):
    return {
        "thread_id": thread_id,
        "fetched_at": fetched_at,
        "complete": complete,
        "messages": list(messages),
    }


def anchor(at="2026-09-11T10:00:00Z"):
    return message(SENT, "outbound", at)


class Fetcher:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.calls = []

    def __call__(self, thread_id):
        self.calls.append(thread_id)
        value = self.snapshots[thread_id]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class RevenueResponseQueueTests(unittest.TestCase):
    def compile(self, rows, provider, **kwargs):
        return module.compile_revenue_response_queue(
            rows,
            provider,
            now=NOW,
            **kwargs,
        )

    def test_human_reply_after_latest_outbound_is_top_action(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "human-1",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route=BUYER,
                ),
            )
        })
        result = self.compile([manifest_row()], provider)
        item = result["items"][0]
        self.assertEqual(item["state"], "HUMAN_REPLY")
        self.assertEqual(item["priority"], 0)
        self.assertEqual(
            item["reason_codes"],
            ["AUTHORIZED_HUMAN_REPLY_AFTER_LATEST_OUTBOUND"],
        )
        self.assertEqual(provider.calls, [THREAD])

    def test_newer_outbound_consumes_old_human_reply_and_resets_followup_clock(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor("2026-09-10T10:00:00Z"),
                message(
                    "human-1",
                    "human_inbound",
                    "2026-09-10T11:00:00Z",
                    from_route=BUYER,
                ),
                message(
                    "sent-2",
                    "outbound",
                    "2026-09-12T10:00:00Z",
                ),
            )
        })
        result = self.compile(
            [manifest_row(follow_up_after_hours=72)],
            provider,
        )
        item = result["items"][0]
        self.assertEqual(item["state"], "WAIT")
        self.assertEqual(item["latest_outbound_at"], "2026-09-12T10:00:00Z")
        self.assertEqual(
            item["follow_up_due_at"],
            "2026-09-15T10:00:00Z",
        )

    def test_unbound_human_reply_is_surfaced_for_review_not_silently_ignored(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "human-unknown",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route="rep@example.com",
                ),
            )
        })
        item = self.compile([manifest_row()], provider)["items"][0]
        self.assertEqual(item["state"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(item["reason_codes"], ["HUMAN_REPLY_FROM_UNBOUND_ROUTE"])

    def test_authorized_alternate_reply_route_can_mint_human_reply(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "human-alt",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route="rep@example.com",
                ),
            )
        })
        row = manifest_row(
            authorized_reply_routes=[BUYER, "rep@example.com"],
        )
        item = self.compile([row], provider)["items"][0]
        self.assertEqual(item["state"], "HUMAN_REPLY")

    def test_bounce_bound_to_latest_outbound_requires_route_repair(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "bounce-1",
                    "bounce",
                    "2026-09-13T11:00:00Z",
                    from_route="mailer-daemon@example.net",
                    related_message_id=SENT,
                ),
            )
        })
        item = self.compile([manifest_row()], provider)["items"][0]
        self.assertEqual(item["state"], "ROUTE_REPAIR")
        self.assertEqual(item["reason_codes"], ["LATEST_OUTBOUND_BOUNCED"])

    def test_bounce_for_older_outbound_does_not_poison_newer_outbound(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor("2026-09-10T10:00:00Z"),
                message("sent-2", "outbound", "2026-09-12T10:00:00Z"),
                message(
                    "bounce-old",
                    "bounce",
                    "2026-09-12T11:00:00Z",
                    from_route="mailer-daemon@example.net",
                    related_message_id=SENT,
                ),
            )
        })
        item = self.compile(
            [manifest_row(follow_up_after_hours=72)],
            provider,
        )["items"][0]
        self.assertEqual(item["state"], "WAIT")

    def test_followup_due_from_latest_outbound(self):
        provider = Fetcher({THREAD: snapshot(anchor())})
        item = self.compile(
            [manifest_row(follow_up_after_hours=24)],
            provider,
        )["items"][0]
        self.assertEqual(item["state"], "FOLLOW_UP_DUE")
        self.assertEqual(
            item["follow_up_due_at"],
            "2026-09-12T10:00:00Z",
        )

    def test_before_followup_threshold_is_wait(self):
        provider = Fetcher({
            THREAD: snapshot(anchor("2026-09-13T10:00:00Z"))
        })
        item = self.compile(
            [manifest_row(follow_up_after_hours=4)],
            provider,
        )["items"][0]
        self.assertEqual(item["state"], "WAIT")
        self.assertEqual(
            item["follow_up_due_at"],
            "2026-09-13T14:00:00Z",
        )

    def test_automated_ack_extends_followup_grace(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor("2026-09-11T10:00:00Z"),
                message(
                    "ack-1",
                    "automated_inbound",
                    "2026-09-13T10:00:00Z",
                    from_route=BUYER,
                ),
            )
        })
        item = self.compile(
            [manifest_row(follow_up_after_hours=24)],
            provider,
            auto_ack_grace_hours=24,
        )["items"][0]
        self.assertEqual(item["state"], "WAIT_AUTO_ACK")
        self.assertEqual(
            item["follow_up_due_at"],
            "2026-09-14T10:00:00Z",
        )

    def test_expired_automated_ack_grace_becomes_followup_due(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor("2026-09-10T10:00:00Z"),
                message(
                    "ack-1",
                    "automated_inbound",
                    "2026-09-11T10:00:00Z",
                    from_route=BUYER,
                ),
            )
        })
        item = self.compile(
            [manifest_row(follow_up_after_hours=24)],
            provider,
            auto_ack_grace_hours=24,
        )["items"][0]
        self.assertEqual(item["state"], "FOLLOW_UP_DUE")
        self.assertIn("AUTOMATED_ACK_GRACE_EXPIRED", item["reason_codes"])

    def test_wait_for_buyer_event_never_auto_generates_followup(self):
        provider = Fetcher({THREAD: snapshot(anchor("2026-08-01T10:00:00Z"))})
        row = manifest_row(
            contact_policy="wait_for_buyer_event",
            follow_up_after_hours=None,
        )
        item = self.compile([row], provider)["items"][0]
        self.assertEqual(item["state"], "WAIT_BUYER_EVENT")
        self.assertIsNone(item["follow_up_due_at"])
        self.assertIn("OPERATOR_POLICY_DNR_UNTIL_BUYER_EVENT", item["reason_codes"])

    def test_auto_ack_does_not_release_wait_for_buyer_event_policy(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor("2026-09-10T10:00:00Z"),
                message(
                    "ack-1",
                    "automated_inbound",
                    "2026-09-13T10:00:00Z",
                    from_route=BUYER,
                ),
            )
        })
        row = manifest_row(
            contact_policy="wait_for_buyer_event",
            follow_up_after_hours=None,
        )
        item = self.compile([row], provider)["items"][0]
        self.assertEqual(item["state"], "WAIT_BUYER_EVENT")
        self.assertIn("AUTOMATED_ACK_PRESENT", item["reason_codes"])

    def test_real_human_reply_releases_wait_for_buyer_event_into_response_queue(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "human-1",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route=BUYER,
                ),
            )
        })
        row = manifest_row(
            contact_policy="wait_for_buyer_event",
            follow_up_after_hours=None,
        )
        item = self.compile([row], provider)["items"][0]
        self.assertEqual(item["state"], "HUMAN_REPLY")

    def test_bound_sent_message_must_exist(self):
        provider = Fetcher({
            THREAD: snapshot(message("different", "outbound", "2026-09-11T10:00:00Z"))
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_bound_sent_message_must_be_outbound(self):
        provider = Fetcher({
            THREAD: snapshot(
                message(
                    SENT,
                    "human_inbound",
                    "2026-09-11T10:00:00Z",
                    from_route=BUYER,
                )
            )
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_bound_outbound_must_target_buyer_route(self):
        provider = Fetcher({
            THREAD: snapshot(
                message(
                    SENT,
                    "outbound",
                    "2026-09-11T10:00:00Z",
                    to_routes=["other@example.com"],
                )
            )
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_snapshot_thread_binding_mismatch_fails_closed(self):
        provider = Fetcher({
            THREAD: snapshot(anchor(), thread_id="other-thread")
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_incomplete_snapshot_fails_closed(self):
        provider = Fetcher({THREAD: snapshot(anchor(), complete=False)})
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_stale_snapshot_fails_closed(self):
        provider = Fetcher({
            THREAD: snapshot(anchor(), fetched_at="2026-09-13T11:54:59Z")
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_snapshot_at_freshness_boundary_is_allowed(self):
        provider = Fetcher({
            THREAD: snapshot(anchor(), fetched_at="2026-09-13T11:55:00Z")
        })
        result = self.compile([manifest_row()], provider)
        self.assertEqual(len(result["items"]), 1)

    def test_future_snapshot_fails_closed(self):
        provider = Fetcher({
            THREAD: snapshot(anchor(), fetched_at="2026-09-13T12:00:01Z")
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_message_after_snapshot_fetch_time_fails_closed(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "human-future",
                    "human_inbound",
                    "2026-09-13T11:59:01Z",
                    from_route=BUYER,
                ),
                fetched_at="2026-09-13T11:59:00Z",
            )
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_conflicting_duplicate_provider_message_id_fails_closed(self):
        msg = anchor()
        conflicting = copy.deepcopy(msg)
        conflicting["occurred_at"] = "2026-09-11T10:00:01Z"
        provider = Fetcher({THREAD: snapshot(msg, conflicting)})
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_exact_duplicate_provider_rows_are_deduplicated(self):
        msg = anchor()
        provider = Fetcher({THREAD: snapshot(msg, copy.deepcopy(msg))})
        result = self.compile([manifest_row()], provider)
        self.assertEqual(result["items"][0]["state"], "FOLLOW_UP_DUE")

    def test_provider_callback_failure_fails_whole_compilation(self):
        provider = Fetcher({THREAD: RuntimeError("provider unavailable")})
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)
        self.assertEqual(provider.calls, [THREAD])

    def test_duplicate_buyer_offer_active_engagements_rejected(self):
        rows = [
            manifest_row(),
            manifest_row(
                engagement_id="eng-2",
                provider_thread_id="thr-2",
                sent_message_id="sent-2",
            ),
        ]
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile(rows, Fetcher({}))

    def test_duplicate_thread_binding_rejected_before_provider_io(self):
        rows = [
            manifest_row(),
            manifest_row(
                engagement_id="eng-2",
                sent_message_id="sent-2",
                buyer_route="other@example.com",
                authorized_reply_routes=["other@example.com"],
                offer_key="other-v1",
            ),
        ]
        provider = Fetcher({})
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile(rows, provider)
        self.assertEqual(provider.calls, [])

    def test_wait_policy_requires_null_followup_threshold(self):
        row = manifest_row(
            contact_policy="wait_for_buyer_event",
            follow_up_after_hours=72,
        )
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([row], Fetcher({}))

    def test_followup_policy_requires_real_integer_not_bool(self):
        row = manifest_row(follow_up_after_hours=True)
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([row], Fetcher({}))

    def test_buyer_route_must_be_in_authorized_reply_routes(self):
        row = manifest_row(authorized_reply_routes=["rep@example.com"])
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([row], Fetcher({}))

    def test_manifest_and_provider_extra_fields_rejected(self):
        row = manifest_row()
        row["notes"] = "do not accept arbitrary CRM prose"
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([row], Fetcher({}))

        snap = snapshot(anchor())
        snap["subject"] = "must not enter authority envelope"
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], Fetcher({THREAD: snap}))

    def test_message_body_subject_headers_cannot_enter_authority_envelope(self):
        msg = anchor()
        msg["body"] = "secret"
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile(
                [manifest_row()],
                Fetcher({THREAD: snapshot(msg)}),
            )

    def test_output_is_body_and_address_free(self):
        provider = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "human-1",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route=BUYER,
                ),
            )
        })
        result = self.compile([manifest_row()], provider)
        serialized = str(result)
        self.assertNotIn(BUYER, serialized)
        self.assertNotIn(THREAD, serialized)
        self.assertNotIn(SENT, serialized)
        self.assertNotIn("human-1", serialized)
        self.assertFalse(result["authority"]["send_message"])
        self.assertFalse(result["authority"]["reply_to_buyer"])
        self.assertFalse(result["authority"]["recognize_revenue"])

    def test_deterministic_receipt_independent_of_manifest_order(self):
        row_a = manifest_row()
        row_b = manifest_row(
            engagement_id="eng-2",
            provider_thread_id="thr-2",
            sent_message_id="sent-2",
            buyer_route="other@example.com",
            authorized_reply_routes=["other@example.com"],
            offer_key="other-v1",
        )
        snap_a = snapshot(anchor())
        snap_b = snapshot(
            message(
                "sent-2",
                "outbound",
                "2026-09-13T10:00:00Z",
                to_routes=["other@example.com"],
            ),
            thread_id="thr-2",
        )
        first = self.compile(
            [row_a, row_b],
            Fetcher({THREAD: snap_a, "thr-2": snap_b}),
        )
        second = self.compile(
            [row_b, row_a],
            Fetcher({THREAD: snap_a, "thr-2": snap_b}),
        )
        self.assertEqual(first, second)

    def test_priority_order_is_human_then_bounce_then_due_then_wait(self):
        rows = [
            manifest_row(),
            manifest_row(
                engagement_id="human",
                provider_thread_id="thr-human",
                sent_message_id="sent-human",
                buyer_route="human@example.com",
                authorized_reply_routes=["human@example.com"],
                offer_key="human-v1",
            ),
            manifest_row(
                engagement_id="bounce",
                provider_thread_id="thr-bounce",
                sent_message_id="sent-bounce",
                buyer_route="bounce@example.com",
                authorized_reply_routes=["bounce@example.com"],
                offer_key="bounce-v1",
            ),
            manifest_row(
                engagement_id="wait",
                provider_thread_id="thr-wait",
                sent_message_id="sent-wait",
                buyer_route="wait@example.com",
                authorized_reply_routes=["wait@example.com"],
                offer_key="wait-v1",
                follow_up_after_hours=72,
            ),
        ]
        provider = Fetcher({
            THREAD: snapshot(anchor()),
            "thr-human": snapshot(
                message(
                    "sent-human",
                    "outbound",
                    "2026-09-11T10:00:00Z",
                    to_routes=["human@example.com"],
                ),
                message(
                    "reply-human",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route="human@example.com",
                ),
                thread_id="thr-human",
            ),
            "thr-bounce": snapshot(
                message(
                    "sent-bounce",
                    "outbound",
                    "2026-09-11T10:00:00Z",
                    to_routes=["bounce@example.com"],
                ),
                message(
                    "dsn-bounce",
                    "bounce",
                    "2026-09-13T11:00:00Z",
                    from_route="mailer-daemon@example.net",
                    related_message_id="sent-bounce",
                ),
                thread_id="thr-bounce",
            ),
            "thr-wait": snapshot(
                message(
                    "sent-wait",
                    "outbound",
                    "2026-09-13T10:00:00Z",
                    to_routes=["wait@example.com"],
                ),
                thread_id="thr-wait",
            ),
        })
        result = self.compile(rows, provider)
        self.assertEqual(
            [item["state"] for item in result["items"]],
            ["HUMAN_REPLY", "ROUTE_REPAIR", "FOLLOW_UP_DUE", "WAIT"],
        )

    def test_summary_has_all_states_and_matches_items(self):
        provider = Fetcher({THREAD: snapshot(anchor())})
        result = self.compile([manifest_row()], provider)
        self.assertEqual(result["summary"]["FOLLOW_UP_DUE"], 1)
        self.assertEqual(sum(result["summary"].values()), 1)

    def test_receipt_hash_changes_if_authoritative_snapshot_changes(self):
        provider_a = Fetcher({THREAD: snapshot(anchor())})
        provider_b = Fetcher({
            THREAD: snapshot(
                anchor(),
                message(
                    "ack-1",
                    "automated_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route=BUYER,
                ),
            )
        })
        a = self.compile([manifest_row()], provider_a)
        b = self.compile([manifest_row()], provider_b)
        self.assertNotEqual(a["evidence_sha256"], b["evidence_sha256"])

    def test_non_callable_provider_rejected(self):
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], {"thread": "not callable"})

    def test_timestamp_requires_timezone(self):
        provider = Fetcher({
            THREAD: snapshot(anchor(), fetched_at="2026-09-13T11:59:00")
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)

    def test_same_timestamp_order_uses_provider_sequence_not_lexical_id(self):
        provider = Fetcher({
            THREAD: snapshot(
                message(
                    SENT,
                    "outbound",
                    "2026-09-13T11:00:00Z",
                    sequence=10,
                ),
                message(
                    "aaa-human",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route=BUYER,
                    sequence=11,
                ),
            )
        })
        item = self.compile([manifest_row()], provider)["items"][0]
        self.assertEqual(item["state"], "HUMAN_REPLY")

    def test_distinct_messages_cannot_share_provider_sequence(self):
        provider = Fetcher({
            THREAD: snapshot(
                message(SENT, "outbound", "2026-09-11T10:00:00Z", sequence=7),
                message(
                    "human-1",
                    "human_inbound",
                    "2026-09-13T11:00:00Z",
                    from_route=BUYER,
                    sequence=7,
                ),
            )
        })
        with self.assertRaises(module.RevenueResponseQueueError):
            self.compile([manifest_row()], provider)


if __name__ == "__main__":
    unittest.main()
