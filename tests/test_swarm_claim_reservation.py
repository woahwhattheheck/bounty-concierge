"""Focused regression checks for snapshot-consistent swarm reservation reads."""
import base64
import copy
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

TAKE_SPEC = importlib.util.spec_from_file_location(
    "swarm_claim_take",
    Path(__file__).parents[1] / "tools/swarm_claim_take.py",
)
take = importlib.util.module_from_spec(TAKE_SPEC)
TAKE_SPEC.loader.exec_module(take)

FRESH_SPEC = importlib.util.spec_from_file_location(
    "bountyhub_fresh_targets",
    Path(__file__).parents[1] / "concierge/bountyhub_fresh_targets.py",
)
fresh_targets = importlib.util.module_from_spec(FRESH_SPEC)
FRESH_SPEC.loader.exec_module(fresh_targets)


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

    def test_takeover_rereads_state_at_observed_head(self):
        stale = active_state()
        stale["status"] = "RELEASED"
        stale["lease_expires_at"] = stale["updated_at"]
        fresh = active_state()
        observed_head = "c" * 40

        class MovingRefGitHub:
            seen_ref = None
            initial_reads = 0

            def read_state_if_exists(self, branch, *, ref=None):
                self.initial_reads += 1
                if ref is not None:
                    raise AssertionError("initial read must use deterministic branch")
                return stale, "b" * 40

            def ref_sha(self, branch):
                return observed_head

            def read_state(self, branch, *, ref=None):
                self.seen_ref = ref
                return fresh, "d" * 40

        github = MovingRefGitHub()
        result, code = reservation.reserve(
            github,
            work_key=stale["work_key"],
            owner="SOL-OTHER-1945",
            event_id="takeover-snapshot-test",
            lease_seconds=900,
            artifact=None,
            base_branch="main",
        )

        self.assertEqual(code, 3)
        self.assertEqual(github.initial_reads, 1)
        self.assertEqual(github.seen_ref, observed_head)
        self.assertEqual(result["disposition"], "BUSY")
        self.assertEqual(result["commit_sha"], observed_head)

    def test_fresh_target_annotations_match_custody_helper_without_mutation(self):
        selected = {
            "listing_ids_by_issue": {"owner/repo#7": ["listing-1"]},
            "targets": [
                {
                    "repo": "Owner/Repo",
                    "number": 7,
                    "listing_ids": ["listing-1"],
                }
            ],
        }
        report = {
            "listings": [
                {
                    "listing_id": "listing-1",
                    "repo": "Owner/Repo",
                    "number": 7,
                    "assignment_type": "open",
                    "has_assignee": False,
                }
            ]
        }
        original = copy.deepcopy(selected)

        result = fresh_targets._exclude_assigned_exclusive(report, selected)
        annotation = result["targets"][0]["swarm_reservation"]

        self.assertEqual(selected, original)
        self.assertEqual(
            result["swarm_reservation_schema"], reservation.SCHEMA
        )
        self.assertEqual(annotation["schema"], reservation.SCHEMA)
        self.assertEqual(annotation["work_key"], "swarm:build:github:owner/repo#7")
        self.assertEqual(
            annotation["branch"],
            reservation._branch(annotation["work_key"]),
        )
        self.assertEqual(
            annotation["tool"], "tools/swarm_claim_reservation.py"
        )
        self.assertEqual(annotation["take_lane"], "build")
        self.assertEqual(annotation["take_resource"], "github:owner/repo#7")
        self.assertEqual(annotation["canonicalization_status"], "NORMALIZED")


class CanonicalTakeKeyTest(unittest.TestCase):
    def test_issue_aliases_and_repo_casefold_share_identity(self):
        values = [
            "Owner/Repo#17",
            "github:OWNER/rePO#17",
            "https://github.com/Owner/Repo/issues/17",
            {"repository": "OWNER/REPO", "issue_number": 17},
        ]
        self.assertEqual(
            {take.canonicalize_resource(value)["resource"] for value in values},
            {"github:owner/repo#17"},
        )

    def test_pull_aliases_share_identity(self):
        values = [
            "Owner/Repo!21",
            "github:OWNER/rePO!21",
            "https://github.com/Owner/Repo/pull/21",
            {"repository": "OWNER/REPO", "pull_number": 21},
        ]
        self.assertEqual(
            {take.canonicalize_resource(value)["resource"] for value in values},
            {"github:owner/repo!21"},
        )

    def test_lane_separation_and_mutation_collision(self):
        build = take._reservation_identity(
            "Owner/Repo#17", lane="build", mutation_resource=None
        )
        qa = take._reservation_identity(
            "owner/repo#17", lane="qa", mutation_resource=None
        )
        self.assertEqual(build["reservation_key"], "swarm:build:github:owner/repo#17")
        self.assertEqual(qa["reservation_key"], "swarm:qa:github:owner/repo#17")

        shared_build = take._reservation_identity(
            "Owner/Repo#17",
            lane="build",
            mutation_resource="Owner/Repo!21",
        )
        shared_repair = take._reservation_identity(
            "Owner/Repo!21",
            lane="repair",
            mutation_resource={"repository": "owner/repo", "pull_number": 21},
        )
        self.assertEqual(
            shared_build["reservation_key"],
            shared_repair["reservation_key"],
        )
        self.assertEqual(
            shared_build["reservation_key"],
            "swarm:mutation:github:owner/repo!21",
        )

    def test_nonmutation_lane_rejects_mutation_resource(self):
        for lane in ("qa", "metadata", "publish", "claim"):
            with self.subTest(lane=lane):
                with self.assertRaises(take.TakeError):
                    take._reservation_identity(
                        "Owner/Repo#17",
                        lane=lane,
                        mutation_resource="Owner/Repo!21",
                    )

    def test_free_form_key_is_preserved_and_visible(self):
        result = take._reservation_identity(
            "GF-RAEGIS145-ADMIN-TX-PREVIEW-R5",
            lane="repair",
            mutation_resource=None,
        )
        self.assertEqual(result["status"], "UNNORMALIZED_KEY")
        self.assertEqual(
            result["reservation_key"],
            "GF-RAEGIS145-ADMIN-TX-PREVIEW-R5",
        )

    def test_aliases_reach_same_reservation_key(self):
        seen = []

        def fake_reserve(
            github,
            *,
            work_key,
            owner,
            event_id,
            lease_seconds,
            artifact,
            base_branch,
        ):
            seen.append(work_key)
            return (
                {
                    "disposition": "ACQUIRED",
                    "event_id": event_id,
                    "work_key": work_key,
                    "owner": owner,
                    "lease_expires_at": "2026-10-06T18:00:00Z",
                    "branch": "swarm-custody/v1/test",
                    "commit_sha": "a" * 40,
                },
                0,
            )

        kwargs = {
            "github": object(),
            "owner": "sol-test",
            "event_id": "canonical-test",
            "identity": "GPT-5.6 Sol / test / cloud",
            "summary": "Focused canonicalization regression.",
            "lease_seconds": 900,
            "artifact": None,
            "base_branch": "main",
            "lane": "build",
        }
        with patch.object(take.reservation, "reserve", side_effect=fake_reserve):
            first, first_code = take.acquire_take(
                work_key="Owner/Repo#17", **kwargs
            )
            second, second_code = take.acquire_take(
                work_key="https://github.com/owner/repo/issues/17", **kwargs
            )

        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(
            seen,
            [
                "swarm:build:github:owner/repo#17",
                "swarm:build:github:owner/repo#17",
            ],
        )
        self.assertIn("canonicalization=NORMALIZED", first["slack_text"])


if __name__ == "__main__":
    unittest.main()
