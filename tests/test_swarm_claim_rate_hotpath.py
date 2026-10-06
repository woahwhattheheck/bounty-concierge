"""Focused API-budget regressions for swarm reservation hot paths."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "swarm_claim_reservation_rate_hotpath",
    Path(__file__).parents[1] / "tools/swarm_claim_reservation.py",
)
reservation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reservation)


def active_state():
    work_key = "github:owner/repo#rate-hotpath"
    return reservation._new_state(
        work_key=work_key,
        branch=reservation._branch(work_key),
        owner="SOL-OWNER-1940",
        event_id="rate-hotpath-fixture",
        generation=1,
        lease_seconds=900,
        now=reservation._now(),
        artifact=None,
    )


class ReservationRateHotPathTest(unittest.TestCase):
    def test_status_uses_one_contents_read(self):
        state = active_state()

        class GitHub:
            reads = 0

            def read_state_if_exists(self, branch, *, ref=None):
                self.reads += 1
                if ref is not None:
                    raise AssertionError("status must read by deterministic branch")
                return state, "d" * 40

            def ref_sha(self, branch):
                raise AssertionError("status must not read the ref hot path")

        github = GitHub()
        result, code = reservation.status(
            github, work_key=state["work_key"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(github.reads, 1)
        self.assertEqual(result["disposition"], "ACTIVE")
        self.assertNotIn("commit_sha", result)

    def test_busy_reserve_uses_one_contents_read(self):
        state = active_state()

        class GitHub:
            reads = 0

            def read_state_if_exists(self, branch, *, ref=None):
                self.reads += 1
                if ref is not None:
                    raise AssertionError("busy reserve must read by branch")
                return state, "d" * 40

            def ref_sha(self, branch):
                raise AssertionError("busy reserve must not read the ref")

        github = GitHub()
        result, code = reservation.reserve(
            github,
            work_key=state["work_key"],
            owner="SOL-OTHER-1940",
            event_id="busy-one-read-test",
            lease_seconds=900,
            artifact=None,
            base_branch="main",
        )

        self.assertEqual(code, 3)
        self.assertEqual(github.reads, 1)
        self.assertEqual(result["disposition"], "BUSY")
        self.assertEqual(result["owner"], state["owner"])
        self.assertNotIn("commit_sha", result)


if __name__ == "__main__":
    unittest.main()
