# SPDX-License-Identifier: MIT
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from concierge.github_comment_carrier import import_comment_carrier, resolve_comment_carrier
from concierge.github_intake_cache import GitHubIntakeCache


def capture():
    return {
        "issue_url": "https://github.com/example/project/issues/70",
        "observed_epoch": 1000,
        "comments": [{
            "url": "https://github.com/example/project/issues/70#issuecomment-123",
            "body": "The existing implementation is https://github.com/example/project/pull/129. Please review it.",
        }],
        "pull_requests": [{
            "url": "https://github.com/example/project/pull/129", "number": 129,
            "state": "open", "head_sha": "a" * 40, "body": "Closes #70\n\nOriginal contribution.",
        }],
    }


class CommentCarrierTests(unittest.TestCase):
    def test_real_sqlite_cache_preserves_binding_expiry_and_newer_observations(self):
        with tempfile.TemporaryDirectory() as root:
            cache = GitHubIntakeCache(Path(root) / "intake.db")
            result = import_comment_carrier(cache, capture(), now_epoch=1001)
            self.assertTrue(result["cache_written"])
            row = cache.lookup("EXAMPLE/PROJECT", 70, now_epoch=1002)
            self.assertEqual((row["pr_number"], row["head_sha"], row["expires_epoch"]), (129, "a" * 40, 2800))
            self.assertNotIn("body", row)
            cache.put("example/project", 70, disposition="CARRIER", source="github", reason_code="NEWER", pr_number=130, head_sha="b" * 40, observed_epoch=1010)
            result = import_comment_carrier(cache, capture(), now_epoch=1011)
            self.assertEqual(result["status"], "NEWER_OBSERVATION_RETAINED")
            self.assertFalse(result["cache_written"])
            self.assertEqual(result["record"]["pr_number"], 130)
            self.assertIsNone(cache.lookup("example/project", 70, now_epoch=2810))

    def test_price_mentions_quotes_partial_searches_and_ambiguity_do_not_create_carriers(self):
        f = capture()
        for body in ("Advertised $70 bounty", "> Closes #70", "```text\nCloses #70\n```", "Closes #700"):
            f["pull_requests"][0]["body"] = body
            self.assertIsNone(resolve_comment_carrier(f, now_epoch=1001))
        f = capture()
        f["comments"] = []
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / "not-created.db"
            result = import_comment_carrier(GitHubIntakeCache(database), f, now_epoch=1001)
            self.assertFalse(result["absence_established"])
            self.assertFalse(database.exists())
        f = capture()
        f["pull_requests"][0]["state"] = "closed"
        self.assertIsNone(resolve_comment_carrier(f, now_epoch=1001))
        f = capture()
        other = copy.deepcopy(f["pull_requests"][0])
        other.update(number=130, url="https://github.com/example/project/pull/130")
        f["pull_requests"].append(other)
        f["comments"][0]["body"] += " https://github.com/example/project/pull/130"
        with self.assertRaisesRegex(ValueError, "multiple linked"):
            resolve_comment_carrier(f, now_epoch=1001)
        for now in (999, 2800):
            with self.assertRaisesRegex(ValueError, "future-dated or expired"):
                resolve_comment_carrier(capture(), now_epoch=now)

    def test_executable_cli_feeds_the_existing_cache_cli_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            f = capture()
            f["observed_epoch"] = time.time() - 1
            source = Path(root) / "capture.json"
            source.write_text(json.dumps(f), encoding="utf-8")
            database = str(Path(root) / "intake.db")
            inserted = subprocess.run([sys.executable, "-m", "concierge.github_comment_carrier", str(source), "--cache-file", database], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(inserted.stdout)["status"], "CARRIER_OBSERVED")
            queried = subprocess.run([sys.executable, "-m", "concierge.github_intake_cache", "get", "example/project", "70", "--cache-file", database], capture_output=True, text=True, check=True)
            row = json.loads(queried.stdout)
            self.assertTrue(row["hit"])
            self.assertEqual(row["pr_number"], 129)
            source.write_text('{"private-data":"do-not-echo-me"', encoding="utf-8")
            rejected = subprocess.run([sys.executable, "-m", "concierge.github_comment_carrier", str(source), "--cache-file", database], capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertNotIn("do-not-echo-me", rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
