# SPDX-License-Identifier: MIT
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import swarm_claim_reservation as reservation


class PostWriteExpiryTest(unittest.TestCase):
    def test_slow_publication_fails_closed_for_all_active_writes(self):
        start = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
        for operation in ("acquire", "takeover", "renew"):
            for elapsed, expected, code in (
                (0, "RENEWED" if operation == "renew" else "ACQUIRED", 0),
                (1, "EXPIRED", 3),
            ):
                with self.subTest(operation=operation, elapsed=elapsed):
                    clock = [start]
                    key = "w2-postwrite-expiry"
                    branch = reservation._branch(key)
                    current = reservation._new_state(
                        work_key=key, branch=branch, owner="w2", event_id="prior",
                        generation=1, lease_seconds=1,
                        now=start - timedelta(seconds=2), artifact=None,
                    )

                    class SlowGitHub:
                        published = None

                        def read_state_if_exists(self, branch):
                            return None if operation == "acquire" else (current, "a" * 40)

                        def ref_sha(self, branch):
                            return "b" * 40

                        def read_state(self, branch, ref=None):
                            return current, "a" * 40

                        def build_commit(self, parent, state, message):
                            self.published = state
                            return "c" * 40

                        def create_ref(self, branch, sha):
                            clock[0] = start + timedelta(seconds=elapsed)
                            return True

                        fast_forward_ref = create_ref

                    github = SlowGitHub()
                    args = dict(work_key=key, owner="w2", event_id="new",
                                lease_seconds=1, artifact=None)
                    with patch.object(reservation, "_now", side_effect=lambda: clock[0]):
                        if operation == "renew":
                            result, actual_code = reservation.renew(github, **args)
                        else:
                            result, actual_code = reservation.reserve(github, **args, base_branch="main")
                    self.assertEqual(result["disposition"], expected)
                    self.assertEqual(actual_code, code)
                    self.assertEqual(result["commit_sha"], "c" * 40)
                    self.assertEqual(github.published["status"], "ACTIVE")
                    self.assertEqual(github.published["lease_expires_at"],
                                     reservation._time(start + timedelta(seconds=1)))


if __name__ == "__main__":
    unittest.main()
