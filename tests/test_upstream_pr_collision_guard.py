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
CANDIDATE_FILES = [
    {"filename": "src/service.py", "sha": "1" * 40},
    {"filename": "tests/test_service.py", "sha": "2" * 40},
]


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


    def test_provider_duplicate_422_overrides_false_negative_pull_search(self):
        payload = snapshot()
        payload["create_error"] = {
            "status": 422,
            "message": "Validation Failed",
            "errors": [
                {
                    "resource": "PullRequest",
                    "code": "custom",
                    "message": (
                        "A pull request already exists for "
                        "woahwhattheheck:sol56/example-branch."
                    ),
                }
            ],
        }
        report = guard.evaluate(
            payload,
            expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD,
            now=NOW,
        )
        self.assertEqual(report["status"], "PROVIDER_COLLISION")
        self.assertFalse(report["publish_allowed"])
        self.assertFalse(report["retry_create"])
        self.assertEqual(
            report["provider_collision_head"],
            "woahwhattheheck:sol56/example-branch",
        )
        self.assertIn("do not retry", report["instruction"])

    def test_unrelated_422_does_not_masquerade_as_collision(self):
        payload = snapshot()
        payload["create_error"] = {
            "status": 422,
            "message": "Validation Failed",
            "errors": [
                {
                    "resource": "PullRequest",
                    "code": "custom",
                    "message": "base is invalid",
                }
            ],
        }
        with self.assertRaisesRegex(
            guard.GuardError,
            "not a recognized duplicate-pull-request collision",
        ):
            guard.evaluate(
                payload,
                expected_issue_key=ISSUE_KEY,
                self_head_sha=HEAD,
                now=NOW,
            )


    def test_identical_content_blocks_cross_issue_duplicate(self):
        payload = snapshot()
        payload["candidate_files"] = CANDIDATE_FILES
        payload["pulls"] = [
            {
                "number": 99,
                "state": "open",
                "head_sha": "b" * 40,
                "title": "Fix a different child issue",
                "body": "Closes #98",
                "files": list(reversed(CANDIDATE_FILES)),
            }
        ]
        report = guard.evaluate(
            payload,
            expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD,
            now=NOW,
        )
        self.assertEqual(report["status"], "CONTENT_COLLISION")
        self.assertFalse(report["publish_allowed"])
        self.assertEqual(report["competitors"][0]["number"], 99)
        self.assertEqual(
            report["competitors"][0]["match_reason"],
            "content_fingerprint",
        )
        self.assertEqual(
            report["competitors"][0]["content_fingerprint"],
            report["content_fingerprint"],
        )

    def test_different_cross_issue_content_remains_publishable(self):
        payload = snapshot()
        payload["candidate_files"] = CANDIDATE_FILES
        payload["pulls"] = [
            {
                "number": 99,
                "state": "open",
                "head_sha": "b" * 40,
                "title": "Fix a different child issue",
                "body": "Closes #98",
                "files": [
                    {"filename": "src/service.py", "sha": "3" * 40},
                    {"filename": "tests/test_service.py", "sha": "2" * 40},
                ],
            }
        ]
        report = guard.evaluate(
            payload,
            expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD,
            now=NOW,
        )
        self.assertEqual(report["status"], "PUBLISH_ALLOWED")
        self.assertTrue(report["publish_allowed"])


    def test_qualified_issue_reference_matches_only_expected_repository(self):
        for body, expected in (
            ("Closes Acme/widget#12", "COLLISION"),
            ("Fixes acme/WIDGET#12", "COLLISION"),
            ("Refs: Acme/widget#12", "COLLISION"),
            ("Closes Other/widget#12", "PUBLISH_ALLOWED"),
            ("Closes Acme/other#12", "PUBLISH_ALLOWED"),
            ("Closes Acme/widget#123", "PUBLISH_ALLOWED"),
        ):
            with self.subTest(body=body):
                payload = snapshot()
                payload["pulls"] = [{
                    "number": 99, "state": "open", "head_sha": "b" * 40,
                    "title": "Existing carrier", "body": body,
                }]
                report = guard.evaluate(
                    payload, expected_issue_key=ISSUE_KEY,
                    self_head_sha=HEAD, now=NOW,
                )
                self.assertEqual(report["status"], expected)


    def test_related_pull_requires_known_state_before_publish(self):
        for state, expected in (
            (None, "ERROR"), ("unknown", "ERROR"), ("open", "COLLISION"),
            ("closed", "PUBLISH_ALLOWED"),
        ):
            with self.subTest(state=state):
                payload = snapshot()
                payload["pulls"] = [{
                    "number": 99, "state": state, "head_sha": "b" * 40,
                    "title": "Existing carrier", "body": "Closes #12",
                }]
                if expected == "ERROR":
                    with self.assertRaisesRegex(guard.GuardError, "needs state open or closed"):
                        guard.evaluate(
                            payload, expected_issue_key=ISSUE_KEY,
                            self_head_sha=HEAD, now=NOW,
                        )
                else:
                    report = guard.evaluate(
                        payload, expected_issue_key=ISSUE_KEY,
                        self_head_sha=HEAD, now=NOW,
                    )
                    self.assertEqual(report["status"], expected)
        payload = snapshot()
        payload["pulls"] = [{"number": 100, "body": "Closes #99"}]
        self.assertTrue(guard.evaluate(
            payload, expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD, now=NOW,
        )["publish_allowed"])
        payload["pulls"] = [{"number": 99, "body": "Closes #12", "merged": True}]
        self.assertEqual(guard.evaluate(
            payload, expected_issue_key=ISSUE_KEY,
            self_head_sha=HEAD, now=NOW,
        )["status"], "COLLISION")


if __name__ == "__main__":
    unittest.main()
