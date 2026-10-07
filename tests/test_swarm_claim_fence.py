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

    def test_multisource_merge_catches_late_visible_take_and_deduplicates_overlap(self):
        later = {
            "message_ts": "1791246406.838289",
            "text": "TAKE · SOL-TORSION · model\nStellar-PocketPay/pocketpay-mobile#320",
        }
        earlier = {
            "message_ts": "1791246328.000000",
            "text": "TAKE · MERIDIAN · model\nStellar-PocketPay/pocketpay-mobile#320",
        }

        search_stream = load([later])
        channel_tail_stream = load([earlier, later])
        events, duplicates = fence.merge_event_streams([search_stream, channel_tail_stream])
        report = fence.reconcile(events)

        key = "github:stellar-pocketpay/pocketpay-mobile#320"
        self.assertEqual(duplicates, 1)
        self.assertEqual([event.claim_id for event in events], ["MERIDIAN", "SOL-TORSION"])
        self.assertEqual(report["active"][key]["claim_id"], "MERIDIAN")
        self.assertEqual(report["counts"]["conflicts"], 1)

    def test_multisource_merge_deduplicates_release_when_permalink_is_missing_in_one_view(self):
        take = {
            "message_ts": "1791253200.000000",
            "text": "TAKE · OP-1 · model\nAcme/widget#12",
        }
        release = {
            "message_ts": "1791253297.846179",
            "permalink": "https://example.slack.com/archives/C123/p1791253297846179",
            "text": "BLOCKED / RELEASE · OP-1 · model\nAcme/widget#12",
        }
        release_without_permalink = dict(release)
        release_without_permalink.pop("permalink")

        search_stream = load([release_without_permalink])
        channel_tail_stream = load([take, release])
        events, duplicates = fence.merge_event_streams([search_stream, channel_tail_stream])
        report = fence.reconcile(events)

        self.assertEqual(duplicates, 1)
        self.assertEqual(report["counts"]["releases"], 1)
        self.assertEqual(report["counts"]["unmatched_releases"], 0)
        self.assertEqual(report["counts"]["active_claims"], 0)

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

    def test_github_pull_url_is_canonicalized(self):
        events = load([
            "TAKE · OP-PR-URL · model\nhttps://github.com/Acme/Widget/pull/0007"
        ])
        self.assertEqual(events[0].work_key, "github:acme/widget#7")

    def test_preflight_allows_empty_lane_and_recognizes_owner(self):
        report = fence.reconcile(load(["TAKE · OP-A · model\nAcme/a#1"]))
        self.assertEqual(fence.preflight(report, "Acme/b#2", "OP-B")["status"], "TAKE_ALLOWED")
        self.assertEqual(fence.preflight(report, "Acme/a#1", "OP-A")["status"], "ALREADY_OWNED")

    def test_explicit_operation_key_fences_non_github_work(self):
        rows = [
            "TAKE · COMP-A · model work_key=op:COMP-AMAZON-ALEXA\ncompetition packet",
            "TAKE · COMP-B · model work_key=operation:comp-amazon-alexa\nsame packet",
        ]
        report = fence.reconcile(load(rows))
        key = "operation:comp-amazon-alexa"
        self.assertEqual(report["active"][key]["claim_id"], "COMP-A")
        self.assertEqual(report["counts"]["conflicts"], 1)
        self.assertEqual(
            fence.preflight(report, "op:COMP-AMAZON-ALEXA", "COMP-C")["status"],
            "COLLISION",
        )


    def test_durable_frontend71_incomplete_history_fails_closed(self):
        # Reproduces the shallow-history failure mode: seeing only our later TAKE
        # is not enough to authorize a build when an older owner may be missing.
        rows = [{
            "message_ts": "1791314416.325179",
            "text": (
                "TAKE · GF-WAVELUM-FRONTEND71-I18N-CHECK-R1 · model\n"
                "stellar-network-builders/wavelum-frontend#71"
            ),
        }]
        events, ignored = fence.load_events(json.dumps(rows).encode())
        durable = fence.durable_report(
            events,
            history_complete=False,
            ignored=ignored,
        )
        decision = fence.durable_preflight(
            durable,
            work_key="stellar-network-builders/wavelum-frontend#71",
            claim_id="GF-WAVELUM-FRONTEND71-I18N-CHECK-R1",
        )
        self.assertEqual(decision["status"], "HISTORY_INCOMPLETE")
        self.assertFalse(decision["build_allowed"])

    def test_durable_core30_full_lifecycle_releases_then_allows_exact_new_owner(self):
        rows = [
            {
                "message_ts": "1791314000.000001",
                "text": (
                    "TAKE · GF-WAVELUM-CORE30-R1 · model\n"
                    "stellar-network-builders/wavelum-core#30"
                ),
            },
            {
                "message_ts": "1791314010.000001",
                "text": (
                    "SOURCE COMPLETE · GF-WAVELUM-CORE30-R1 · model\n"
                    "stellar-network-builders/wavelum-core#30"
                ),
            },
            {
                "message_ts": "1791314020.000001",
                "text": (
                    "PUBLISHER HANDOFF / RELEASE · GF-WAVELUM-CORE30-R1 · model\n"
                    "stellar-network-builders/wavelum-core#30"
                ),
            },
            {
                "message_ts": "1791314030.000001",
                "text": (
                    "TAKE · GF-WAVELUM-CORE30-R2 · model\n"
                    "stellar-network-builders/wavelum-core#30"
                ),
            },
        ]
        events, ignored = fence.load_events(json.dumps(rows).encode())
        self.assertEqual(
            [fence.lifecycle_stage(event.text) for event in events],
            ["TAKE", "SOURCE_COMPLETE", "RELEASE", "TAKE"],
        )
        self.assertEqual(
            [event.action for event in events],
            ["TAKE", "PROGRESS", "RELEASE", "TAKE"],
        )

        orphan_events, orphan_ignored = fence.load_events(json.dumps([rows[1]]).encode())
        orphan = fence.durable_report(
            orphan_events,
            history_complete=True,
            ignored=orphan_ignored,
        )
        self.assertNotIn(
            "github:stellar-network-builders/wavelum-core#30",
            orphan["active"],
        )
        self.assertEqual(orphan["counts"]["orphan_progress"], 1)

        durable = fence.durable_report(
            events,
            history_complete=True,
            ignored=ignored,
        )
        decision = fence.durable_preflight(
            durable,
            work_key="stellar-network-builders/wavelum-core#30",
            claim_id="GF-WAVELUM-CORE30-R2",
        )
        self.assertEqual(decision["status"], "BUILD_ALLOWED")
        self.assertTrue(decision["build_allowed"])
        self.assertEqual(
            durable["active"]["github:stellar-network-builders/wavelum-core#30"]["claim_id"],
            "GF-WAVELUM-CORE30-R2",
        )


if __name__ == "__main__":
    unittest.main()
