"""Small, fully offline regression set for sharing observed issue state."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("bountyhub_reconcile", Path(__file__).parents[1] / "tools/bountyhub_reconcile.py")
reconcile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile)
STAMP = "2026-10-04T23:00:00Z"
NOW = reconcile.timestamp(STAMP)


def catalog():
    return {"schema": "bountyhub-public-intake/compact-v1", "rows": [
        {"work_key": "github:owner/repo#1", "catalog_candidate": True,
         "advertised_usd": "250.00", "provider_funding_status": "PROMISED"},
        {"work_key": "github:owner/repo#1", "catalog_candidate": True,
         "advertised_usd": "150.00", "provider_funding_status": "PAID"},
        {"work_key": "github:owner/repo#2", "catalog_candidate": True},
    ]}


def issue(number=1, **changes):
    result = {"html_url": f"https://github.com/Owner/Repo/issues/{number}",
              "url": f"https://api.github.com/repos/owner/repo/issues/{number}",
              "number": number, "state": "open", "assignees": []}
    result.update(changes)
    return result


def run(*issues, **kwargs):
    return reconcile.reconcile(catalog(), {"retrieved_at": STAMP, "issues": list(issues)},
                               as_of=NOW, **kwargs)


class BountyHubReconcileTest(unittest.TestCase):
    def test_closed_duplicates_preserve_funding_and_do_not_mutate_input(self):
        original = catalog()
        snapshot = {"retrieved_at": STAMP, "issues": [issue(state="closed", body="private-body")]}
        result = reconcile.reconcile(original, snapshot, as_of=NOW)
        self.assertEqual([row["github_observation"]["status"] for row in result["rows"]],
                         ["closed", "closed", "not_observed"])
        self.assertFalse(any(row["reconciled_candidate"] for row in result["rows"]))
        self.assertEqual([row["advertised_usd"] for row in result["rows"][:2]], ["250.00", "150.00"])
        self.assertEqual([row["provider_funding_status"] for row in result["rows"][:2]], ["PROMISED", "PAID"])
        self.assertNotIn("github_observation", original["rows"][0])
        self.assertNotIn("private-body", json.dumps(result))

    def test_assignment_and_incomplete_evidence_are_not_open_unassigned(self):
        result = run(issue(assignees=[{"login": "person", "email": "private-email"}]), issue(2))
        self.assertEqual([row["reconciled_candidate"] for row in result["rows"]], [False, False, True])
        self.assertEqual(result["github_reconciliation"]["row_status_counts"], {"assigned": 2, "open_unassigned": 1})
        self.assertNotIn("private-email", json.dumps(result))
        for changes in ({"state": None}, {"assignees": None}, {"assignees": [{}]}):
            with self.subTest(changes=changes):
                self.assertFalse(run(issue(**changes))["rows"][0]["reconciled_candidate"])

    def test_stale_future_and_conflicting_captures_stay_visible(self):
        for stamp, status in (("2026-10-04T22:00:00Z", "stale"), ("2026-10-04T23:01:00Z", "future")):
            result = reconcile.reconcile(catalog(), {"retrieved_at": stamp, "issues": [issue()]}, as_of=NOW)
            self.assertEqual(result["rows"][0]["github_observation"]["status"], status)
            self.assertFalse(result["rows"][0]["reconciled_candidate"])
            self.assertEqual(len(result["rows"]), 3)
        for entries in ((issue(), issue(state="closed")), (issue(state="closed"), issue())):
            result = run(*entries)
            self.assertEqual(result["rows"][0]["github_observation"]["status"], "conflicting_observations")
            self.assertFalse(result["rows"][0]["reconciled_candidate"])
        self.assertTrue(run(issue(), issue())["rows"][0]["reconciled_candidate"])

    def test_rejects_mismatched_identity_and_naive_timestamps(self):
        invalid = [issue(number=True), issue(url="https://api.github.com/repos/other/repo/issues/1"),
                   issue(html_url="https://example.com/owner/repo/issues/1"),
                   issue(url="https://api.github.com/owner/repo/issues/1"),
                   issue(pull_request={}), issue(html_url="https://github.com/owner/repo/issues/2")]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(reconcile.ReconcileError):
                run(value)
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.timestamp("2026-10-04T23:00:00")
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.reconcile({"schema": [], "rows": []}, {"issues": []})

    def test_sharder_consumes_overlay_and_rechecks_age(self):
        spec = importlib.util.spec_from_file_location("bountyhub_shard_overlay", Path(__file__).parents[1] / "tools/bountyhub_shard.py")
        shard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shard)
        result = run(issue(state="closed"), issue(2))
        with patch.object(shard, "datetime", wraps=reconcile.datetime) as clock:
            clock.now.return_value = NOW
            shared = shard.shard(result, "worker", 1)
            self.assertEqual(shared["github_reconciliation_status"], "fresh")
            self.assertEqual([item["work_key"] for item in shared["work"]], ["github:owner/repo#2"])
            indexed = shard.shard_indexed(result, 0, 1)
            self.assertEqual(indexed["github_reconciliation_status"], "fresh")
            self.assertEqual([item["work_key"] for item in indexed["work"]], ["github:owner/repo#2"])
            clock.now.return_value = reconcile.timestamp("2026-10-04T23:16:00Z")
            shared = shard.shard(result, "worker", 1)
            self.assertEqual(shared["github_reconciliation_status"], "stale")
            self.assertEqual(shared["work"], [])
            indexed = shard.shard_indexed(result, 0, 1)
            self.assertEqual(indexed["github_reconciliation_status"], "stale")
            self.assertEqual(indexed["work"], [])
            self.assertEqual(len(result["rows"]), 3)

    def test_cli_output_binds_captures_and_leaves_them_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, evidence, output = (root / name for name in ("catalog.json", "issues.json", "result.json"))
            source.write_text(json.dumps(catalog()), encoding="utf-8")
            evidence.write_text(json.dumps({"retrieved_at": STAMP, "issues": [issue()]}), encoding="utf-8")
            original = source.read_bytes()
            args = ["--input", str(source), "--github-snapshot", str(evidence), "--as-of", STAMP, "--output", str(output)]
            self.assertEqual(reconcile.main(args), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(result["github_reconciliation"]["catalog_sha256"]), 64)
            self.assertEqual(result["github_reconciliation"]["candidate_row_count"], 2)
            self.assertEqual(source.read_bytes(), original)
            args[-1] = str(source)
            self.assertEqual(reconcile.main(args), 1)
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
