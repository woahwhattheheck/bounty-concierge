# SPDX-License-Identifier: MIT
import sqlite3
import tempfile
import unittest
from pathlib import Path

from concierge.github_breaker_ledger import BreakerStateError, GitHubBreakerLedger
from concierge.github_rail_availability import availability_snapshot


class BreakerStatusTests(unittest.TestCase):
    def test_observation_is_read_only_and_operation_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "breaker.sqlite"
            ledger = GitHubBreakerLedger(path, provider_route="managed-app", credential="private")
            self.assertEqual(ledger.status_snapshot(now_epoch=100)["state"], "ABSENT")
            self.assertFalse(path.exists())
            ledger.record_receipt("search", "SECONDARY_RATE_LIMIT", observed_epoch=100, retry_after_seconds=60)
            ledger.record_receipt("write", "SCOPE_DENIED", observed_epoch=101)
            before = path.read_bytes()
            snapshot = ledger.status_snapshot(now_epoch=110)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(snapshot["operations"]["search"]["retry_after_seconds"], 50)
            self.assertEqual(snapshot["operations"]["search"]["last_error"], "SECONDARY_RATE_LIMIT")
            self.assertTrue(snapshot["operations"]["write"]["blocked"])
            self.assertNotIn("read-known-coordinate", snapshot["operations"])
            self.assertEqual(snapshot["request_budget"]["state"], "UNKNOWN")
            other = GitHubBreakerLedger(path, provider_route="contributor", credential="private")
            self.assertEqual(other.status_snapshot(now_epoch=110)["operations"], {})
            rail = availability_snapshot(Path(directory) / "absent.sqlite", "private", rail="managed-app", actor="owner", breaker_path=path, now_epoch=110)
            self.assertEqual(rail["state"], "ABSENT")
            self.assertEqual(rail["operation_breaker"], snapshot)
            self.assertFalse((Path(directory) / "absent.sqlite").exists())
            self.assertNotIn("private", str(snapshot))
            self.assertNotIn(ledger.route_key, str(snapshot))

    def test_expired_probe_and_corrupt_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "breaker.sqlite"
            ledger = GitHubBreakerLedger(path, provider_route="app")
            ledger.record_receipt("search", "PRIMARY_RATE_LIMIT", observed_epoch=100, retry_after_seconds=0)
            ledger.admit("search", owner="opaque-private-owner", now_epoch=101)
            before = path.read_bytes()
            status = ledger.status_snapshot(now_epoch=102)["operations"]["search"]
            self.assertTrue(status["recovery_probe_in_flight"])
            self.assertEqual(status["retry_after_seconds"], 14)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(ledger.status_snapshot(now_epoch=117)["operations"]["search"]["blocked"])
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE github_breaker_v1 SET reason='raw-sensitive-payload'")
            with self.assertRaises(BreakerStateError):
                ledger.status_snapshot(now_epoch=118)


if __name__ == "__main__":
    unittest.main()
