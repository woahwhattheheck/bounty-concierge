"""Focused checks for offline swarm claim deconfliction."""
import importlib.util
import json
import sys
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "swarm_claim_fence", Path(__file__).parents[1] / "tools/swarm_claim_fence.py"
)
fence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fence
SPEC.loader.exec_module(fence)


def load(rows):
    return fence.load_events(json.dumps(rows).encode())[0]


class SwarmClaimFenceTest(unittest.TestCase):
    def test_first_active_claim_wins_actual_stellargrid_shape(self):
        rows = [
            {"message_ts": "1791234443.368069", "text": "TAKE · GF-STELLARGRID8-RECONCILE-CRON-MESH17-20261005 · GPT-5.6 Sol\nFresh fence: Thalamii/StellarGrid#8 is OPEN."},
            {"message_ts": "1791234446.754579", "text": "TAKE · GF-STELLARGRID8-RECONCILE-CRON-SENTINEL-20261005 · GPT-5.6 Sol\nFresh fence: Thalamii/StellarGrid#8 OPEN."},
            {"message_ts": "1791234468.366909", "text": "TAKE · GF-STELLARGRID8-RECONCILE-CRON-RUNCARD-20261005 · GPT-5.6 Sol\nThalamii/StellarGrid#8 only."},
        ]
        report = fence.reconcile(load(rows))
        key = "github:thalamii/stellargrid#8"
        self.assertEqual(report["active"][key]["claim_id"], "GF-STELLARGRID8-RECONCILE-CRON-MESH17-20261005")
        self.assertEqual(report["counts"]["conflicts"], 2)
        self.assertEqual(fence.preflight(report, "Thalamii/StellarGrid#8", "new-seat")["status"], "COLLISION")

    def test_exact_release_opens_lane_for_next_claim(self):
        rows = [
            "TAKE · OP-1 · GPT-5.6 Sol\nTarget Acme/widget#12",
            "DONE / RELEASE · OP-1 · GPT-5.6 Sol\nTarget Acme/widget#12",
            "TAKE · OP-2 · GPT-5.6 Sol\nTarget Acme/widget#12",
        ]
        report = fence.reconcile(load(rows))
        self.assertEqual(report["active"]["github:acme/widget#12"]["claim_id"], "OP-2")
        self.assertEqual(report["counts"]["releases"], 1)
        self.assertEqual(report["counts"]["conflicts"], 0)

    def test_reassertion_is_not_a_conflict(self):
        rows = [
            "TAKE · OP-1 · model\nAcme/widget#12",
            "CLAIM · OP-1 · model\nAcme/widget#12",
        ]
        report = fence.reconcile(load(rows))
        self.assertEqual(report["counts"]["reassertions"], 1)
        self.assertEqual(report["counts"]["conflicts"], 0)

    def test_unmatched_release_fails_closed(self):
        rows = [
            "TAKE · OP-1 · model\nAcme/widget#12",
            "RELEASE · OP-OTHER · model\nAcme/widget#12",
        ]
        report = fence.reconcile(load(rows))
        self.assertIn("github:acme/widget#12", report["active"])
        self.assertEqual(report["counts"]["unmatched_releases"], 1)

    def test_explicit_key_resolves_ambiguous_message(self):
        text = (
            "TAKE · OP-1 · model work_key=Acme/widget#12\n"
            "Issue Acme/widget#12 references Other/repo#9."
        )
        events = load([text])
        self.assertEqual(events[0].work_key, "github:acme/widget#12")

    def test_ambiguous_key_is_ignored_without_explicit_key(self):
        events, ignored = fence.load_events(json.dumps([
            "TAKE · OP-1 · model\nAcme/widget#12 and Other/repo#9"
        ]).encode())
        self.assertEqual(events, [])
        self.assertIn("multiple", ignored[0]["reason"])

    def test_github_issue_url_is_canonicalized(self):
        events = load([
            "TAKE · OP-URL · model\nhttps://github.com/Acme/Widget/issues/0007"
        ])
        self.assertEqual(events[0].work_key, "github:acme/widget#7")

    def test_preflight_allows_empty_lane_and_recognizes_owner(self):
        report = fence.reconcile(load(["TAKE · OP-A · model\nAcme/a#1"]))
        self.assertEqual(fence.preflight(report, "Acme/b#2", "OP-B")["status"], "TAKE_ALLOWED")
        self.assertEqual(fence.preflight(report, "Acme/a#1", "OP-A")["status"], "ALREADY_OWNED")


if __name__ == "__main__":
    unittest.main()
