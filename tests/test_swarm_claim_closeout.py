"""Focused regressions for provider claim closeout serialization."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

SPEC = importlib.util.spec_from_file_location(
    "swarm_claim_closeout", TOOLS / "swarm_claim_closeout.py"
)
closeout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(closeout)


class SwarmClaimCloseoutTest(unittest.TestCase):
    def test_winner_gets_canonical_key_and_one_claim_command(self):
        captured = {}

        def reserve(_github, **kwargs):
            captured.update(kwargs)
            return {
                "disposition": "ACQUIRED",
                "owner": kwargs["owner"],
                "event_id": kwargs["event_id"],
            }, 0

        with patch.object(closeout.reservation, "reserve", side_effect=reserve):
            result, code = closeout.acquire_closeout(
                object(),
                issue="GitHubOrg/Repo#00042",
                owner="sol56-closeout",
                event_id="GF-42-R1",
                lease_seconds=900,
                base_branch="main",
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "CLAIM_READY")
        self.assertTrue(result["post_claim"])
        self.assertEqual(result["claim_text"], "/claim #42")
        self.assertEqual(result["work_key"], "swarm:claim:github:githuborg/repo#42")
        self.assertEqual(captured["work_key"], "swarm:claim:github:githuborg/repo#42")
        self.assertTrue(result["fresh_provider_fence_required"])
        self.assertEqual(result["resource"], "github:githuborg/repo#42")

    def test_busy_lane_never_emits_claim_text(self):
        with patch.object(
            closeout.reservation,
            "reserve",
            return_value=(
                {"disposition": "BUSY", "owner": "other-seat"},
                3,
            ),
        ):
            result, code = closeout.acquire_closeout(
                object(),
                issue="github:Owner/Repo#7",
                owner="sol56-closeout",
                event_id="GF-7-R1",
                lease_seconds=900,
                base_branch="main",
            )

        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "COLLISION")
        self.assertFalse(result["post_claim"])
        self.assertNotIn("claim_text", result)


    def test_same_seat_different_operation_cannot_reuse_closeout(self):
        for observed_event, expected_code in (("GF-7-R1", 0), ("GF-7-OTHER", 3), (None, 3)):
            with self.subTest(observed_event=observed_event), patch.object(
                closeout.reservation, "reserve", return_value=(
                    {"disposition": "OWNED", "owner": "same-seat", "event_id": observed_event}, 0
                ),
            ):
                result, code = closeout.acquire_closeout(
                    object(), issue="Owner/Repo#7", owner="same-seat",
                    event_id="GF-7-R1", lease_seconds=900, base_branch="main",
                )
            self.assertEqual(code, expected_code)
            self.assertEqual(result["post_claim"], expected_code == 0)
            if expected_code:
                self.assertEqual(result["status"], "COLLISION")
                self.assertNotIn("claim_text", result)


if __name__ == "__main__":
    unittest.main()
