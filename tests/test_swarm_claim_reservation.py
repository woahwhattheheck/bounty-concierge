"""Focused regression checks for snapshot-consistent swarm reservation reads."""
import base64
from datetime import timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "swarm_claim_reservation",
    Path(__file__).parents[1] / "tools/swarm_claim_reservation.py",
)
reservation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reservation)


def active_state(work_key="github:owner/repo#1"):
    branch = reservation._branch(work_key)
    return reservation._new_state(
        work_key=work_key,
        branch=branch,
        owner="SOL-LOOMSPARK-1907",
        event_id="snapshot-pin-test",
        generation=1,
        lease_seconds=900,
        now=reservation._now(),
        artifact=None,
    )


class Response:
    status_code = 200

    def __init__(self, state):
        self._payload = {
            "sha": "b" * 40,
            "encoding": "base64",
            "content": base64.b64encode(
                (json.dumps(state) + "\n").encode("utf-8")
            ).decode("ascii"),
        }

    def json(self):
        return self._payload


class SnapshotReadTest(unittest.TestCase):
    def test_read_state_uses_explicit_commit_ref(self):
        state = active_state()
        branch = state["branch"]
        observed_head = "a" * 40
        github = reservation.GitHub("owner/repo", "test-token")

        with patch.object(
            github, "request", return_value=Response(state)
        ) as request:
            parsed, blob_sha = github.read_state(
                branch, ref=observed_head
            )

        self.assertEqual(parsed["owner"], state["owner"])
        self.assertEqual(blob_sha, "b" * 40)
        self.assertEqual(
            request.call_args.kwargs["params"],
            {"ref": observed_head},
        )

    def test_status_pairs_state_with_observed_head(self):
        state = active_state()
        observed_head = "c" * 40

        class MovingRefGitHub:
            seen_ref = None

            def ref_sha(self, branch):
                return observed_head

            def read_state(self, branch, *, ref=None):
                self.seen_ref = ref
                return state, "d" * 40

        github = MovingRefGitHub()
        result, code = reservation.status(
            github, work_key=state["work_key"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(github.seen_ref, observed_head)
        self.assertEqual(result["commit_sha"], observed_head)


if __name__ == "__main__":
    unittest.main()
