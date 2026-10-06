import tempfile
from pathlib import Path
import unittest

from concierge.github_breaker import GitHubBreakerLedger


class GitHubBreakerLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "breaker.sqlite"
        self.ledger = GitHubBreakerLedger(
            self.path,
            stale_ttl_seconds=300,
            denial_ttl_seconds=20,
            recovery_lease_seconds=10,
        )

    def test_one_recovery_probe_for_one_hundred_workers(self):
        route = "token:personal"
        self.ledger.record(
            route,
            "search",
            error_class="SECONDARY_RATE_LIMIT",
            observed_epoch=100,
            retry_after_seconds=10,
        )

        decisions = [
            self.ledger.admit(
                route, "search", owner=f"worker-{index}", now_epoch=111
            )
            for index in range(100)
        ]

        probes = [decision for decision in decisions if decision.action == "PROBE"]
        self.assertEqual(len(probes), 1)
        self.assertEqual(
            sum(
                decision.reason == "RECOVERY_PROBE_LEASED"
                for decision in decisions
            ),
            99,
        )
        self.assertTrue(
            self.ledger.complete_probe(
                route, "search", owner="worker-0", success=True
            )
        )
        self.assertEqual(
            self.ledger.admit(route, "search", now_epoch=112).action,
            "ALLOW",
        )

    def test_token_primary_limit_does_not_freeze_healthy_app_route(self):
        self.ledger.record(
            "token:personal",
            "read-known-coordinate",
            error_class="PRIMARY_RATE_LIMIT",
            observed_epoch=100,
            reset_epoch=200,
        )

        self.assertEqual(
            self.ledger.admit(
                "token:personal", "read-known-coordinate", now_epoch=150
            ).action,
            "SKIP",
        )
        self.assertEqual(
            self.ledger.admit(
                "app:installation-155467469",
                "read-known-coordinate",
                now_epoch=150,
            ).action,
            "ALLOW",
        )

    def test_integration_scope_denial_is_not_a_quota_breaker(self):
        result = self.ledger.record(
            "app:installation-155467469",
            "write",
            error_class="INTEGRATION_SCOPE_DENIED",
            observed_epoch=100,
            reset_epoch=999,
            retry_after_seconds=999,
        )

        self.assertEqual(result.state, "SCOPE_DENIED")
        self.assertIsNone(result.deadline_epoch)
        admission = self.ledger.admit(
            "app:installation-155467469",
            "write",
            now_epoch=101,
        )
        self.assertEqual(admission.state, "SCOPE_DENIED")
        self.assertEqual(admission.reason, "INTEGRATION_SCOPE_DENIED")

    def test_later_longer_reset_extends_deadline_monotonically(self):
        route = "token:personal"
        first = self.ledger.record(
            route,
            "search",
            error_class="SECONDARY_RATE_LIMIT",
            observed_epoch=100,
            reset_epoch=120,
        )
        second = self.ledger.record(
            route,
            "search",
            error_class="SECONDARY_RATE_LIMIT",
            observed_epoch=105,
            reset_epoch=180,
        )
        third = self.ledger.record(
            route,
            "search",
            error_class="SECONDARY_RATE_LIMIT",
            observed_epoch=110,
            reset_epoch=130,
        )

        self.assertEqual(first.deadline_epoch, 120)
        self.assertEqual(second.deadline_epoch, 180)
        self.assertEqual(third.deadline_epoch, 180)

    def test_terminal_state_expires_and_cleans_up(self):
        self.ledger.record(
            "app:installation-155467469",
            "fork",
            error_class="AUTH_FAILED",
            observed_epoch=100,
        )
        self.assertEqual(self.ledger.cleanup(now_epoch=119), 0)
        self.assertEqual(self.ledger.cleanup(now_epoch=121), 1)
        self.assertEqual(
            self.ledger.admit(
                "app:installation-155467469",
                "fork",
                now_epoch=121,
            ).action,
            "ALLOW",
        )


if __name__ == "__main__":
    unittest.main()
