from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.grantfox_queue_gate import (
    GrantFoxQueueInputError,
    compile_grantfox_queue_gate,
    verify_receipt,
)


def snapshot(**overrides):
    payload = {
        "schema": "grantfox-queue-gate/v1",
        "listing_url": (
            "https://contribute.grantfox.xyz/org/Gryd-lock/"
            "repo/grydlock-testkit/issue/33"
        ),
        "canonical_issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/33",
        "issue_state": "open",
        "actor_login": "woahwhattheheck",
        "assigned_to": None,
        "actor_applied": False,
        "application_count": 1,
        "application_pressure_threshold": 3,
        "linked_pr_urls": [],
        "labels": [
            "enhancement",
            "Maybe Rewarded",
            "GrantFox OSS",
            "Official Campaign | FWC26",
        ],
        "observed_at": "2026-09-19T21:36:00Z",
        "evaluated_at": "2026-09-19T21:38:00Z",
        "max_snapshot_age_seconds": 900,
    }
    payload.update(overrides)
    return payload


class GrantFoxQueueGateTests(unittest.TestCase):
    def test_unassigned_low_pressure_issue_is_apply_eligible_but_advisory_only(self):
        receipt = compile_grantfox_queue_gate(snapshot())
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertEqual(receipt["advisory_next_action"], "APPLY_THROUGH_PROVIDER_ROUTE")
        self.assertFalse(receipt["authority"]["provider_application_authority"])
        self.assertFalse(receipt["authority"]["submission_authority"])

    def test_actor_already_applied_waits_for_assignment(self):
        receipt = compile_grantfox_queue_gate(snapshot(actor_applied=True))
        self.assertEqual(receipt["disposition"], "WAIT_ASSIGNMENT")
        self.assertEqual(receipt["reason_codes"], [])

    def test_actor_assignment_unlocks_implementation_advisory(self):
        receipt = compile_grantfox_queue_gate(
            snapshot(assigned_to="WoahWhatTheHeck", actor_applied=True)
        )
        self.assertEqual(receipt["disposition"], "IMPLEMENTATION_ELIGIBLE")
        self.assertEqual(receipt["advisory_next_action"], "IMPLEMENT_ASSIGNED_SCOPE")
        self.assertFalse(receipt["authority"]["implementation_write_authority"])

    def test_assignment_to_other_holds(self):
        receipt = compile_grantfox_queue_gate(snapshot(assigned_to="other-contributor"))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("ASSIGNED_TO_OTHER", receipt["reason_codes"])

    def test_linked_pr_holds_to_prevent_duplicate_build(self):
        receipt = compile_grantfox_queue_gate(
            snapshot(
                linked_pr_urls=["https://github.com/Flux-DeFi/LiquidFlow/pull/216"]
            )
        )
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("LINKED_PR_PRESENT", receipt["reason_codes"])

    def test_application_pressure_holds_before_new_application(self):
        receipt = compile_grantfox_queue_gate(snapshot(application_count=3))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("APPLICATION_PRESSURE_HIGH", receipt["reason_codes"])

    def test_existing_actor_application_is_not_rejected_for_pressure(self):
        receipt = compile_grantfox_queue_gate(
            snapshot(actor_applied=True, application_count=9)
        )
        self.assertEqual(receipt["disposition"], "WAIT_ASSIGNMENT")
        self.assertNotIn("APPLICATION_PRESSURE_HIGH", receipt["reason_codes"])

    def test_closed_and_stale_snapshot_collect_all_hold_reasons(self):
        receipt = compile_grantfox_queue_gate(
            snapshot(
                issue_state="closed",
                observed_at="2026-09-19T20:00:00Z",
                evaluated_at="2026-09-19T21:00:01Z",
                max_snapshot_age_seconds=3600,
            )
        )
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("CANONICAL_ISSUE_NOT_OPEN", receipt["reason_codes"])
        self.assertIn("SNAPSHOT_STALE", receipt["reason_codes"])

    def test_listing_and_canonical_identity_mismatch_fails_closed(self):
        with self.assertRaisesRegex(
            GrantFoxQueueInputError, "identify different issues"
        ):
            compile_grantfox_queue_gate(
                snapshot(
                    canonical_issue_url=(
                        "https://github.com/Gryd-lock/grydlock-testkit/issues/34"
                    )
                )
            )

    def test_url_aliases_userinfo_queries_and_ports_are_rejected(self):
        hostile = (
            "https://contribute.grantfox.xyz/org/Gryd-lock/repo/"
            "grydlock-testkit/issue/33?x=1",
            "https://user@contribute.grantfox.xyz/org/Gryd-lock/repo/"
            "grydlock-testkit/issue/33",
            "https://contribute.grantfox.xyz:443/org/Gryd-lock/repo/"
            "grydlock-testkit/issue/33",
            "https://contribute.grantfox.xyz/org/Gryd-lock/repo/"
            "grydlock-testkit/issue/%33%33",
        )
        for listing_url in hostile:
            with self.subTest(listing_url=listing_url):
                with self.assertRaises(GrantFoxQueueInputError):
                    compile_grantfox_queue_gate(snapshot(listing_url=listing_url))

    def test_bool_is_not_accepted_as_application_count(self):
        with self.assertRaisesRegex(
            GrantFoxQueueInputError, "application_count must be"
        ):
            compile_grantfox_queue_gate(snapshot(application_count=True))

    def test_maybe_rewarded_never_becomes_award_or_payment(self):
        receipt = compile_grantfox_queue_gate(snapshot())
        self.assertEqual(receipt["reward"]["status"], "POSSIBLE_DISCRETIONARY")
        self.assertFalse(receipt["reward"]["explicit_amount_verified"])
        self.assertFalse(receipt["reward"]["award_verified"])
        self.assertFalse(receipt["reward"]["payment_verified"])

    def test_without_maybe_rewarded_label_reward_is_unverified(self):
        receipt = compile_grantfox_queue_gate(
            snapshot(labels=["GrantFox OSS", "Official Campaign | FWC26"])
        )
        self.assertEqual(receipt["reward"]["status"], "UNVERIFIED")

    def test_receipt_is_deterministic_and_tamper_evident(self):
        first = compile_grantfox_queue_gate(snapshot())
        second = compile_grantfox_queue_gate(snapshot())
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))
        changed = deepcopy(first)
        changed["provider_snapshot"]["application_count"] = 99
        self.assertFalse(verify_receipt(changed))

    def test_semantic_verifier_rejects_rehashed_queue_state_forgery(self):
        receipt = compile_grantfox_queue_gate(snapshot())
        self.assertTrue(verify_receipt(receipt, semantic=True))

        forged = deepcopy(receipt)
        forged["disposition"] = "IMPLEMENTATION_ELIGIBLE"
        forged["advisory_next_action"] = "IMPLEMENT_ASSIGNED_SCOPE"
        body = dict(forged)
        body.pop("receipt_sha256", None)
        forged["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()

        self.assertTrue(verify_receipt(forged))
        self.assertFalse(verify_receipt(forged, semantic=True))

    def test_cli_round_trip_and_hold_exit_code(self):
        payload = snapshot(
            linked_pr_urls=["https://github.com/Flux-DeFi/LiquidFlow/pull/216"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.grantfox_queue_gate",
                    str(path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertTrue(verify_receipt(receipt))
        self.assertEqual(receipt["disposition"], "HOLD")


if __name__ == "__main__":
    unittest.main()
