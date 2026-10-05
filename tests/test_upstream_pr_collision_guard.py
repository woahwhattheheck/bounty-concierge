"""Focused checks for early canonical issue pruning."""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "upstream_pr_collision_guard",
    Path(__file__).parents[1] / "tools/upstream_pr_collision_guard.py",
)
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)

NOW = datetime(2026, 10, 5, 23, 45, tzinfo=timezone.utc)
ISSUE_KEY = "Acme/widget#12"
HEAD = "a" * 40


def snapshot(*, state="open", captured_at=None, include_pulls=True):
    payload = {
        "captured_at": (captured_at or NOW).isoformat().replace("+00:00", "Z"),
        "issue": {"repository": "Acme/widget", "number": 12, "state": state},
    }
    if include_pulls:
        payload["pulls"] = []
    return payload


class UpstreamPrCollisionGuardEarlyPruneTest(unittest.TestCase):
    def test_closed_issue_does_not_require_pull_snapshot(self):
        report = guard.evaluate(
            snapshot(state="closed", include_pulls=False),
            expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD,
            now=NOW,
        )
        self.assertEqual(report["status"], "ISSUE_NOT_OPEN")
        self.assertFalse(report["publish_allowed"])

    def test_stale_issue_snapshot_does_not_require_pull_snapshot(self):
        report = guard.evaluate(
            snapshot(
                captured_at=NOW - timedelta(seconds=301),
                include_pulls=False,
            ),
            expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD,
            now=NOW,
        )
        self.assertEqual(report["status"], "STALE_SNAPSHOT")
        self.assertFalse(report["publish_allowed"])

    def test_open_issue_still_fails_closed_without_pull_snapshot(self):
        with self.assertRaisesRegex(guard.GuardError, "snapshot.pulls must be a list"):
            guard.evaluate(
                snapshot(include_pulls=False),
                expected_issue_key=ISSUE_KEY,
                self_head_sha=HEAD,
                now=NOW,
            )

    def test_open_issue_with_empty_pull_snapshot_can_publish(self):
        report = guard.evaluate(
            snapshot(),
            expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD,
            now=NOW,
        )
        self.assertEqual(report["status"], "PUBLISH_ALLOWED")
        self.assertTrue(report["publish_allowed"])


if __name__ == "__main__":
    unittest.main()
