"""Focused checks for offline BountyHub work partitioning."""
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "bountyhub_shard", Path(__file__).parents[1] / "tools/bountyhub_shard.py"
)
shard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shard)


def snapshot():
    return {
        "schema": "bountyhub-public-intake/compact-v1",
        "retrieved_at": "2026-10-04T23:04:21+00:00",
        "raw_sha256": "abc123",
        "rows": [
            {"listing_id": "a", "work_key": "github:owner/a#1", "catalog_candidate": True},
            {"listing_id": "b", "work_key": "github:owner/a#1", "catalog_candidate": True},
            {"listing_id": "c", "work_key": "github:owner/b#2", "catalog_candidate": True},
            {"listing_id": "d", "work_key": "github:owner/c#3", "catalog_candidate": False},
        ],
    }


class BountyHubShardTest(unittest.TestCase):
    def test_all_slots_cover_unique_candidate_work_without_overlap(self):
        source = snapshot()
        groups = shard.candidate_groups(source)
        assigned = []
        for slot in range(4):
            assigned.extend(key for key in groups if shard._slot(key, 4) == slot)

        self.assertCountEqual(assigned, ["github:owner/a#1", "github:owner/b#2"])
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(len(groups["github:owner/a#1"]), 2)

    def test_worker_identity_is_hashed_and_duplicate_cards_stay_grouped(self):
        worker_key = "Sol-Bounty-Scout-1925"
        result = shard.shard(snapshot(), worker_key, 1)

        self.assertEqual(result["worker_slot"], 0)
        self.assertEqual(result["assignment_mode"], "hashed-worker-key")
        self.assertEqual(result["candidate_work_key_count"], 2)
        self.assertEqual(result["assigned_work_key_count"], 2)
        self.assertEqual(len(result["work"][0]["listings"]), 2)
        self.assertNotIn(worker_key, json.dumps(result))

    def test_explicit_worker_indexes_are_disjoint_and_cover_all_candidates(self):
        results = [shard.shard_indexed(snapshot(), slot, 4) for slot in range(4)]
        assigned = [
            item["work_key"]
            for result in results
            for item in result["work"]
        ]

        self.assertCountEqual(assigned, ["github:owner/a#1", "github:owner/b#2"])
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(
            [result["worker_index"] for result in results],
            [0, 1, 2, 3],
        )
        self.assertTrue(
            all(result["assignment_mode"] == "explicit-worker-index" for result in results)
        )

    def test_wave_plan_assigns_every_candidate_once_and_reports_occupancy(self):
        result = shard.plan_indexed(snapshot(), 4)
        assigned = [
            item["work_key"]
            for slot in result["slots"]
            for item in slot["work"]
        ]

        self.assertEqual(result["schema"], "bountyhub-wave-plan/v1")
        self.assertCountEqual(assigned, ["github:owner/a#1", "github:owner/b#2"])
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(sum(result["slot_work_key_counts"]), 2)
        self.assertEqual(
            result["nonempty_worker_count"],
            sum(count > 0 for count in result["slot_work_key_counts"]),
        )
        self.assertEqual(
            result["max_assigned_work_key_count"],
            max(result["slot_work_key_counts"]),
        )
        self.assertEqual(len(result["candidate_set_sha256"]), 64)
        self.assertEqual(
            [slot["worker_index"] for slot in result["slots"]],
            list(range(4)),
        )
        self.assertTrue(all(
            slot["assigned_work_key_count"] == len(slot["work"])
            for slot in result["slots"]
        ))

    def test_rejects_unrecognized_snapshots_and_invalid_worker_count_or_index(self):
        with self.assertRaises(shard.ShardError):
            shard.load_snapshot(json.dumps({"data": []}).encode())
        with self.assertRaises(shard.ShardError):
            shard.shard(snapshot(), "worker", 0)
        with self.assertRaises(shard.ShardError):
            shard.shard_indexed(snapshot(), 4, 4)
        with self.assertRaises(shard.ShardError):
            shard.shard_indexed(snapshot(), -1, 4)


if __name__ == "__main__":
    unittest.main()
