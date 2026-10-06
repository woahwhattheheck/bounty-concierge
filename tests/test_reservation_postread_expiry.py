import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import swarm_claim_reservation as reservation


class PostReadExpiryTest(unittest.TestCase):
    def test_lease_expiring_during_read_cannot_return_owned(self):
        start = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
        after_read = start + timedelta(seconds=2)
        key = "w2-expiry-regression"
        branch = reservation._branch(key)
        current = reservation._new_state(
            work_key=key, branch=branch, owner="w2", event_id="prior",
            generation=1, lease_seconds=1, now=start, artifact=None,
        )

        class SlowGitHub:
            built_state = None

            def read_state_if_exists(self, branch):
                return current, "a" * 40

            def ref_sha(self, branch):
                return "b" * 40

            def read_state(self, branch, ref=None):
                return current, "a" * 40

            def build_commit(self, parent, state, message):
                self.built_state = state
                return "c" * 40

            def fast_forward_ref(self, branch, sha):
                return True

        github = SlowGitHub()
        with patch.object(reservation, "_now", side_effect=[start, after_read, after_read]):
            result, code = reservation.reserve(
                github, work_key=key, owner="w2", event_id="new",
                lease_seconds=60, artifact=None, base_branch="main",
            )
        self.assertEqual(code, 0)
        self.assertEqual(result["disposition"], "ACQUIRED")
        self.assertEqual(result["generation"], 2)
        self.assertEqual(result["event_id"], "new")
        self.assertIsNotNone(github.built_state)


if __name__ == "__main__":
    unittest.main()
