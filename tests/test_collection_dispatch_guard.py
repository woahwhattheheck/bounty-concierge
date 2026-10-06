"""Offline regression for provider evidence lifetime at collection lease acquisition."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from concierge.collection_request import compile_collection_request
from concierge.collection_dispatch_guard import (
    CollectionDispatchGuardError, acquire_guarded_local_lease,
    authorize_collection_dispatch, derive_collection_dispatch_identity,
)


class CollectionGuardEvidenceExpiryTest(unittest.TestCase):
    def test_oldest_provider_deadline_is_enforced_before_local_mutation(self):
        now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        packet = compile_collection_request({
            "schema": "bounty-collection-request-input/v1",
            "sponsor_name": "Synthetic Sponsor",
            "work": {"repo": "example/project", "pr": 17,
                     "canonical_url": "https://github.com/example/project/pull/17",
                     "head_sha": "a" * 40, "state": "MERGED",
                     "advertised_amount": "25", "currency": "USD"},
            "payout_route": {"type": "HOSTED_HANDLE", "value": "synthetic-handle"},
            "acceptance": {"kind": "NONE", "evidence_ref": None,
                           "evidence_sha256": None},
        })
        identity = derive_collection_dispatch_identity(packet)
        queries = [
            {"provider": provider, "recipient": "reviewer@example.test",
             "offer_key": identity["offer_key"], "complete": True,
             "completed_at": (now - timedelta(seconds=age)).isoformat(),
             "observations": []}
            for provider, age in (("gmail", 60), ("slack", 20))
        ]
        receipt = authorize_collection_dispatch(
            packet, recipient="reviewer@example.test", provider_queries=queries,
            required_providers=["gmail", "slack"], owner="seat",
            claim_event_id="operation", snapshot_complete=True, now=now,
            shared_events=[{"operation_key": identity["operation_key"],
                            "kind": "CLAIM", "owner": "seat",
                            "event_id": "operation", "order": 1}],
        )
        self.assertTrue(receipt["dispatch"])
        self.assertEqual(receipt["provider_evidence_valid_until"],
                         (now + timedelta(seconds=240)).isoformat())
        for offset, error in ((0, None), (240, None),
                              (241, "GUARD_PROVIDER_EVIDENCE_STALE"),
                              (-1, "GUARD_EVIDENCE_TIME_INVALID")):
            with self.subTest(offset=offset), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "leases"
                if error is None:
                    acquire_guarded_local_lease(receipt, root=root, owner="seat",
                                               now=now + timedelta(seconds=offset))
                else:
                    with self.assertRaises(CollectionDispatchGuardError) as raised:
                        acquire_guarded_local_lease(
                            receipt, root=root, owner="seat",
                            now=now + timedelta(seconds=offset))
                    self.assertEqual(raised.exception.code, error)
                    self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
