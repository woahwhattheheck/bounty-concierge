# SPDX-License-Identifier: MIT
"""TAKE reuse must retain the caller's operation identity."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class TakeOperationIdentityTests(unittest.TestCase):
    def test_owned_reuse_requires_matching_owner_and_event(self):
        # Load the real wrapper with an inert reservation module. This test
        # cannot initialize a provider transport or perform a reservation.
        reservation = types.ModuleType("swarm_claim_reservation")
        source = Path(__file__).resolve().parents[1] / "tools" / "swarm_claim_take.py"
        spec = importlib.util.spec_from_file_location("take_identity_under_test", source)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "swarm_claim_reservation": reservation,
            "tools.swarm_claim_reservation": reservation,
        }):
            spec.loader.exec_module(wrapper)

        owned = {
            "disposition": "OWNED",
            "work_key": "swarm:build:github:owner/repo#17",
            "owner": "seat",
            "event_id": "operation-current",
            "lease_expires_at": "2026-10-06T19:00:00Z",
            "branch": "custody/example",
        }
        cases = [
            ("matching", {}, True),
            ("different-event", {"event_id": "operation-previous"}, False),
            ("missing-event", {"event_id": None}, False),
            ("different-owner", {"owner": "other-seat"}, False),
        ]
        for name, changes, allowed in cases:
            with self.subTest(name=name):
                receipt = dict(owned, **changes)
                if name == "missing-event":
                    receipt.pop("event_id")
                reservation.reserve = lambda *_args, **_kwargs: (receipt, 0)
                result, code = wrapper.acquire_take(
                    object(),
                    work_key="Owner/Repo#17",
                    owner="seat",
                    event_id="operation-current",
                    identity="worker",
                    summary="Current operation only",
                    lease_seconds=900,
                    artifact=None,
                    base_branch="main",
                )
                self.assertEqual(code, 0 if allowed else 3)
                self.assertEqual(result["status"], "TAKE_READY" if allowed else "COLLISION")
                self.assertEqual(result["post_take"], allowed)
                self.assertEqual(result["reservation"], receipt)
                if allowed:
                    self.assertIn("operation-current", result["slack_text"])
                else:
                    self.assertNotIn("slack_text", result)


if __name__ == "__main__":
    unittest.main()
