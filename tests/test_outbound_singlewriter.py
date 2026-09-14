# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from concierge.outbound_singlewriter import (
    AlreadySent, AmbiguousSend, CorruptState, LeaseBusy, LeaseLost,
    OutboundSingleWriter, SharedClaimBlocked, SharedClaimError,
    evaluate_shared_claim, normalize_identity,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 13, 22, 0, tzinfo=UTC)


def identity():
    return dict(provider="GMAIL", destination="lead@example.test", thread="thread-7", operation="proposal-followup")


def acquire_worker(root, gate, queue, owner):
    gate.wait()
    try:
        r = OutboundSingleWriter(root).acquire(**identity(), owner=owner, ttl_seconds=60, now=T0)
        queue.put(("won", r["owner"]))
    except LeaseBusy:
        queue.put(("busy", owner))


class IdentityTests(unittest.TestCase):
    def test_stable_normalization(self):
        a = normalize_identity(provider=" GMAIL ", destination="caf" + chr(0x00E9) + "@example.test", thread=" T ", operation="x")
        b = normalize_identity(provider="gmail", destination="cafe" + chr(0x0301) + "@example.test", thread="T", operation="x")
        self.assertEqual(a.key, b.key)
        self.assertEqual(a.provider, "gmail")

    def test_controls_rejected(self):
        with self.assertRaises(Exception):
            normalize_identity(provider="gmail", destination="a\n@example.test", thread="t", operation="x")


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.guard = OutboundSingleWriter(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def acquire(self, owner="a", when=T0, ttl=60):
        return self.guard.acquire(**identity(), owner=owner, ttl_seconds=ttl, now=when)

    def test_live_lease_blocks_peer(self):
        self.acquire()
        with self.assertRaises(LeaseBusy):
            self.acquire("b", T0 + timedelta(seconds=1))

    def test_expired_held_recovers_and_old_lease_loses(self):
        old = self.acquire(ttl=2)
        new = self.acquire("b", T0 + timedelta(seconds=3))
        self.assertEqual(new["generation"], 2)
        with self.assertRaises(LeaseLost):
            self.guard.prepare_send(operation_key=old["operation_key"], lease_id=old["lease_id"], owner="a", now=T0 + timedelta(seconds=3))

    def test_sending_never_auto_expires(self):
        held = self.acquire(ttl=1)
        sending = self.guard.prepare_send(operation_key=held["operation_key"], lease_id=held["lease_id"], owner="a", now=T0)
        self.assertEqual(sending["status"], "SENDING")
        with self.assertRaises(AmbiguousSend):
            self.acquire("b", T0 + timedelta(days=30))

    def test_finalize_is_idempotent_and_terminal(self):
        held = self.acquire(); key = held["operation_key"]
        send = self.guard.prepare_send(operation_key=key, lease_id=held["lease_id"], owner="a", now=T0)
        first = self.guard.finalize_sent(operation_key=key, permit_id=send["permit_id"], owner="a", provider_receipt_id="msg-123", now=T0)
        second = self.guard.finalize_sent(operation_key=key, permit_id=send["permit_id"], owner="a", provider_receipt_id="msg-123", now=T0)
        self.assertEqual(first, second)
        with self.assertRaises(AlreadySent):
            self.acquire("b", T0 + timedelta(days=1))
        with self.assertRaises(AlreadySent):
            self.guard.finalize_sent(operation_key=key, permit_id=send["permit_id"], owner="a", provider_receipt_id="different", now=T0)

    def test_abort_is_only_before_prepare(self):
        held = self.acquire(); key = held["operation_key"]
        self.guard.abort_held(operation_key=key, lease_id=held["lease_id"], owner="a", now=T0)
        self.assertEqual(self.acquire("b", T0)["generation"], 2)

    def test_reconcile_not_sent_requires_current_permit(self):
        held = self.acquire(); key = held["operation_key"]
        send = self.guard.prepare_send(operation_key=key, lease_id=held["lease_id"], owner="a", now=T0)
        with self.assertRaises(LeaseLost):
            self.guard.reconcile_not_sent(operation_key=key, permit_id="wrong", owner="a", evidence_reference="gmail-query:0", now=T0)
        released = self.guard.reconcile_not_sent(operation_key=key, permit_id=send["permit_id"], owner="a", evidence_reference="gmail-query:authoritative-zero", now=T0)
        self.assertEqual(released["status"], "RELEASED")
        self.assertEqual(self.acquire("b", T0)["generation"], 2)

    def test_tamper_fails_closed(self):
        held = self.acquire(); path = Path(self.tmp.name) / "state" / f"{held['operation_key']}.json"
        data = json.loads(path.read_text()); data["owner"] = "attacker"; path.write_text(json.dumps(data))
        with self.assertRaises(CorruptState):
            self.guard.inspect(held["operation_key"])

    def test_two_processes_exactly_one_winner(self):
        gate = multiprocessing.Event(); queue = multiprocessing.Queue()
        ps = [multiprocessing.Process(target=acquire_worker, args=(self.tmp.name, gate, queue, name)) for name in ("a", "b")]
        for p in ps: p.start()
        gate.set()
        results = [queue.get(timeout=5) for _ in ps]
        for p in ps: p.join(5); self.assertEqual(p.exitcode, 0)
        self.assertEqual(sum(r[0] == "won" for r in results), 1)
        self.assertEqual(sum(r[0] == "busy" for r in results), 1)


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.key = normalize_identity(**identity()).key

    def ev(self, kind, owner, eid, order):
        return {"operation_key": self.key, "kind": kind, "owner": owner, "event_id": eid, "order": order}

    def test_earliest_active_claim_wins(self):
        events = [self.ev("CLAIM", "a", "a1", 10), self.ev("CLAIM", "b", "b1", 11)]
        self.assertTrue(evaluate_shared_claim(operation_key=self.key, owner="a", claim_event_id="a1", events=events, snapshot_complete=True).authorized)
        b = evaluate_shared_claim(operation_key=self.key, owner="b", claim_event_id="b1", events=events, snapshot_complete=True)
        self.assertFalse(b.authorized); self.assertEqual(b.winner_owner, "a")

    def test_release_hands_off(self):
        events = [self.ev("CLAIM", "a", "a1", 1), self.ev("CLAIM", "b", "b1", 2), self.ev("RELEASE", "a", "a2", 3)]
        self.assertTrue(evaluate_shared_claim(operation_key=self.key, owner="b", claim_event_id="b1", events=events, snapshot_complete=True).authorized)

    def test_sent_is_terminal(self):
        events = [self.ev("CLAIM", "a", "a1", 1), self.ev("SENT", "a", "a2", 2), self.ev("CLAIM", "b", "b1", 3)]
        d = evaluate_shared_claim(operation_key=self.key, owner="b", claim_event_id="b1", events=events, snapshot_complete=True)
        self.assertEqual(d.disposition, "ALREADY_SENT"); self.assertFalse(d.authorized)

    def test_incomplete_or_missing_own_claim_blocks(self):
        events = [self.ev("CLAIM", "a", "a1", 1)]
        with self.assertRaises(SharedClaimBlocked):
            evaluate_shared_claim(operation_key=self.key, owner="a", claim_event_id="a1", events=events, snapshot_complete=False)
        with self.assertRaises(SharedClaimBlocked):
            evaluate_shared_claim(operation_key=self.key, owner="b", claim_event_id="b1", events=events, snapshot_complete=True)

    def test_duplicate_claim_and_orphan_sent_fail_closed(self):
        with self.assertRaises(SharedClaimError):
            evaluate_shared_claim(operation_key=self.key, owner="a", claim_event_id="a1", events=[self.ev("CLAIM", "a", "a1", 1), self.ev("CLAIM", "a", "a2", 2)], snapshot_complete=True)
        with self.assertRaises(SharedClaimError):
            evaluate_shared_claim(operation_key=self.key, owner="a", claim_event_id="a1", events=[self.ev("CLAIM", "a", "a1", 1), self.ev("RELEASE", "a", "a2", 2), self.ev("SENT", "a", "a3", 3)], snapshot_complete=True)

    def test_nonfinite_order_fails_closed(self):
        with self.assertRaises(SharedClaimError):
            evaluate_shared_claim(operation_key=self.key, owner="a", claim_event_id="a1", events=[self.ev("CLAIM", "a", "a1", "NaN")], snapshot_complete=True)


if __name__ == "__main__":
    unittest.main()
