from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.bounty_canonical_viability import (
    BountyCanonicalViabilityInputError,
    compile_bounty_canonical_viability,
    verify_receipt,
)


def snapshot(**overrides):
    base = {
        "schema": "bounty-canonical-viability/v2",
        "value_gate": {
            "work_id": "algora-acme-42",
            "canonical_source_url": "https://github.com/acme/widget/issues/42",
            "disposition": "VALUE_50_PLUS",
            "receipt_sha256": "a" * 64,
        },
        "actor_login": "woahwhattheheck",
        "canonical_issue_url": "https://github.com/acme/widget/issues/42",
        "listing": {
            "url": "https://algora.io/acme/bounties/42",
            "state": "OPEN",
            "observed_at": "2026-09-19T23:50:00Z",
        },
        "repository": {
            "full_name": "acme/widget",
            "archived": False,
            "observed_at": "2026-09-19T23:50:00Z",
        },
        "issue": {
            "state": "OPEN",
            "state_reason": None,
            "acceptance": "ACCEPTED",
            "assignees": [],
            "observed_at": "2026-09-19T23:50:00Z",
        },
        "reward": {
            "payment_path": "VERIFIED",
            "assignment_required": False,
            "actor_applied": False,
            "observed_at": "2026-09-19T23:50:00Z",
        },
        "collisions": {
            "open_prs": [],
            "active_claim_count": 0,
            "maintainer_confirmed_residual": False,
            "observed_at": "2026-09-19T23:52:00Z",
        },
        "evaluated_at": "2026-09-19T23:55:00Z",
        "max_snapshot_age_seconds": 900,
        "claim_pressure_threshold": 3,
    }
    base.update(overrides)
    return base


class BountyCanonicalViabilityTests(unittest.TestCase):
    def test_clean_verified_open_issue_routes_to_claim_review(self):
        receipt = compile_bounty_canonical_viability(snapshot())
        self.assertEqual(receipt["disposition"], "READY_FOR_CLAIM_REVIEW")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertFalse(receipt["authority"]["claim_authority"])

    def test_stale_board_open_but_canonical_closed_is_pruned(self):
        issue = deepcopy(snapshot()["issue"])
        issue.update(state="CLOSED", state_reason="NOT_PLANNED")
        receipt = compile_bounty_canonical_viability(snapshot(issue=issue))
        self.assertEqual(receipt["disposition"], "PRUNE")
        self.assertIn("LISTING_CANONICAL_STATE_MISMATCH", receipt["reason_codes"])
        self.assertIn("CANONICAL_ISSUE_NOT_OPEN", receipt["reason_codes"])
        self.assertTrue(receipt["state"]["canonical_state_mismatch"])

    def test_closed_or_unknown_listing_never_routes_ready(self):
        closed = deepcopy(snapshot()["listing"])
        closed["state"] = "CLOSED"
        receipt = compile_bounty_canonical_viability(snapshot(listing=closed))
        self.assertEqual(receipt["disposition"], "PRUNE")
        self.assertIn("LISTING_NOT_OPEN", receipt["reason_codes"])

        unknown = deepcopy(snapshot()["listing"])
        unknown["state"] = "UNKNOWN"
        receipt = compile_bounty_canonical_viability(snapshot(listing=unknown))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("LISTING_STATE_UNKNOWN", receipt["reason_codes"])

    def test_archived_repository_is_pruned_even_if_issue_says_open(self):
        repo = deepcopy(snapshot()["repository"])
        repo["archived"] = True
        receipt = compile_bounty_canonical_viability(snapshot(repository=repo))
        self.assertEqual(receipt["disposition"], "PRUNE")
        self.assertIn("CANONICAL_REPOSITORY_ARCHIVED", receipt["reason_codes"])

    def test_unaccepted_feature_proposal_holds(self):
        issue = deepcopy(snapshot()["issue"])
        issue["acceptance"] = "UNACCEPTED_PROPOSAL"
        receipt = compile_bounty_canonical_viability(snapshot(issue=issue))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("PROPOSAL_NOT_ACCEPTED", receipt["reason_codes"])

    def test_unknown_acceptance_holds_fail_closed(self):
        issue = deepcopy(snapshot()["issue"])
        issue["acceptance"] = "UNKNOWN"
        receipt = compile_bounty_canonical_viability(snapshot(issue=issue))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("CANONICAL_ACCEPTANCE_UNKNOWN", receipt["reason_codes"])

    def test_other_assignee_holds(self):
        issue = deepcopy(snapshot()["issue"])
        issue["assignees"] = ["another-user"]
        receipt = compile_bounty_canonical_viability(snapshot(issue=issue))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("ASSIGNED_TO_OTHER", receipt["reason_codes"])

    def test_actor_assignment_unlocks_implementation_review(self):
        issue = deepcopy(snapshot()["issue"])
        issue["assignees"] = ["WoahWhatTheHeck"]
        reward = deepcopy(snapshot()["reward"])
        reward["assignment_required"] = True
        receipt = compile_bounty_canonical_viability(snapshot(issue=issue, reward=reward))
        self.assertEqual(receipt["disposition"], "READY_FOR_IMPLEMENTATION_REVIEW")
        self.assertFalse(receipt["authority"]["implementation_authority"])

    def test_assignment_required_and_applied_waits_without_implementation(self):
        reward = deepcopy(snapshot()["reward"])
        reward.update(assignment_required=True, actor_applied=True)
        receipt = compile_bounty_canonical_viability(snapshot(reward=reward))
        self.assertEqual(receipt["disposition"], "WAIT_ASSIGNMENT")
        self.assertEqual(receipt["advisory_next_action"], "WAIT_FOR_ASSIGNMENT_WITHOUT_IMPLEMENTATION")

    def test_assignment_required_unapplied_routes_claim_review(self):
        reward = deepcopy(snapshot()["reward"])
        reward["assignment_required"] = True
        receipt = compile_bounty_canonical_viability(snapshot(reward=reward))
        self.assertEqual(receipt["disposition"], "READY_FOR_CLAIM_REVIEW")

    def test_external_full_or_unknown_open_pr_holds_without_residual(self):
        for overlap in ("FULL", "UNKNOWN"):
            with self.subTest(overlap=overlap):
                collisions = deepcopy(snapshot()["collisions"])
                collisions["open_prs"] = [{
                    "url": "https://github.com/acme/widget/pull/99",
                    "author_login": "other-user",
                    "overlap": overlap,
                    "observed_at": "2026-09-19T23:52:00Z",
                }]
                receipt = compile_bounty_canonical_viability(snapshot(collisions=collisions))
                self.assertEqual(receipt["disposition"], "HOLD")
                self.assertIn("OVERLAPPING_OPEN_PR_PRESENT", receipt["reason_codes"])

    def test_partial_pr_does_not_block_by_itself(self):
        collisions = deepcopy(snapshot()["collisions"])
        collisions["open_prs"] = [{
            "url": "https://github.com/acme/widget/pull/99",
            "author_login": "other-user",
            "overlap": "PARTIAL",
            "observed_at": "2026-09-19T23:52:00Z",
        }]
        receipt = compile_bounty_canonical_viability(snapshot(collisions=collisions))
        self.assertEqual(receipt["disposition"], "READY_FOR_CLAIM_REVIEW")

    def test_maintainer_confirmed_residual_overrides_collision_and_pressure_only(self):
        collisions = deepcopy(snapshot()["collisions"])
        collisions.update(active_claim_count=20, maintainer_confirmed_residual=True)
        collisions["open_prs"] = [{
            "url": "https://github.com/acme/widget/pull/99",
            "author_login": "other-user",
            "overlap": "FULL",
            "observed_at": "2026-09-19T23:52:00Z",
        }]
        receipt = compile_bounty_canonical_viability(snapshot(collisions=collisions))
        self.assertEqual(receipt["disposition"], "READY_FOR_CLAIM_REVIEW")
        self.assertNotIn("OVERLAPPING_OPEN_PR_PRESENT", receipt["reason_codes"])
        self.assertNotIn("CLAIM_PRESSURE_HIGH", receipt["reason_codes"])

    def test_claim_pressure_holds_without_confirmed_residual(self):
        collisions = deepcopy(snapshot()["collisions"])
        collisions["active_claim_count"] = 3
        receipt = compile_bounty_canonical_viability(snapshot(collisions=collisions))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("CLAIM_PRESSURE_HIGH", receipt["reason_codes"])

    def test_payment_route_unverified_holds(self):
        reward = deepcopy(snapshot()["reward"])
        reward["payment_path"] = "UNVERIFIED"
        receipt = compile_bounty_canonical_viability(snapshot(reward=reward))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("PAYMENT_PATH_UNVERIFIED", receipt["reason_codes"])

    def test_selection_gate_holds_until_actor_is_assigned(self):
        reward = deepcopy(snapshot()["reward"])
        reward["payment_path"] = "SELECTION_GATED"
        receipt = compile_bounty_canonical_viability(snapshot(reward=reward))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("SELECTION_GATE_NOT_SATISFIED", receipt["reason_codes"])

    def test_report_only_is_pruned_from_implementation_queue(self):
        reward = deepcopy(snapshot()["reward"])
        reward["payment_path"] = "REPORT_ONLY"
        receipt = compile_bounty_canonical_viability(snapshot(reward=reward))
        self.assertEqual(receipt["disposition"], "PRUNE")
        self.assertIn("REPORT_ONLY_NOT_IMPLEMENTATION_BOUNTY", receipt["reason_codes"])

    def test_stale_canonical_snapshot_holds(self):
        issue = deepcopy(snapshot()["issue"])
        issue["observed_at"] = "2026-09-19T23:00:00Z"
        receipt = compile_bounty_canonical_viability(snapshot(issue=issue))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("SNAPSHOT_STALE", receipt["reason_codes"])

    def test_stale_collision_snapshot_holds(self):
        collisions = deepcopy(snapshot()["collisions"])
        collisions["open_prs"] = [{
            "url": "https://github.com/acme/widget/pull/99",
            "author_login": "woahwhattheheck",
            "overlap": "PARTIAL",
            "observed_at": "2026-09-19T23:00:00Z",
        }]
        receipt = compile_bounty_canonical_viability(snapshot(collisions=collisions))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("COLLISION_SNAPSHOT_STALE", receipt["reason_codes"])

    def test_value_gate_canonical_source_must_match_canonical_issue(self):
        value = deepcopy(snapshot()["value_gate"])
        value["canonical_source_url"] = "https://github.com/other/widget/issues/42"
        with self.assertRaisesRegex(BountyCanonicalViabilityInputError, "canonical issue"):
            compile_bounty_canonical_viability(snapshot(value_gate=value))

    def test_stale_claim_pressure_snapshot_holds_without_open_prs(self):
        collisions = deepcopy(snapshot()["collisions"])
        collisions["observed_at"] = "2026-09-19T23:00:00Z"
        receipt = compile_bounty_canonical_viability(snapshot(collisions=collisions))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("SNAPSHOT_STALE", receipt["reason_codes"])

    def test_non_50_plus_value_binding_is_rejected(self):
        value = deepcopy(snapshot()["value_gate"])
        value["disposition"] = "PILE_10_49"
        with self.assertRaisesRegex(BountyCanonicalViabilityInputError, "VALUE_50_PLUS"):
            compile_bounty_canonical_viability(snapshot(value_gate=value))

    def test_repo_identity_mismatch_and_cross_repo_pr_fail_closed(self):
        repo = deepcopy(snapshot()["repository"])
        repo["full_name"] = "evil/widget"
        with self.assertRaises(BountyCanonicalViabilityInputError):
            compile_bounty_canonical_viability(snapshot(repository=repo))

        collisions = deepcopy(snapshot()["collisions"])
        collisions["open_prs"] = [{
            "url": "https://github.com/other/repo/pull/1",
            "author_login": "other-user",
            "overlap": "FULL",
            "observed_at": "2026-09-19T23:52:00Z",
        }]
        with self.assertRaises(BountyCanonicalViabilityInputError):
            compile_bounty_canonical_viability(snapshot(collisions=collisions))

    def test_url_aliases_and_duplicate_prs_are_rejected(self):
        bad = deepcopy(snapshot()["listing"])
        bad["url"] = "https://algora.io/acme/%62ounties"
        with self.assertRaises(BountyCanonicalViabilityInputError):
            compile_bounty_canonical_viability(snapshot(listing=bad))

        collisions = deepcopy(snapshot()["collisions"])
        row = {
            "url": "https://github.com/acme/widget/pull/99",
            "author_login": "other-user",
            "overlap": "FULL",
            "observed_at": "2026-09-19T23:52:00Z",
        }
        collisions["open_prs"] = [row, deepcopy(row)]
        with self.assertRaises(BountyCanonicalViabilityInputError):
            compile_bounty_canonical_viability(snapshot(collisions=collisions))

    def test_receipt_is_deterministic_tamper_evident_and_semantically_verified(self):
        first = compile_bounty_canonical_viability(snapshot())
        second = compile_bounty_canonical_viability(snapshot())
        self.assertEqual(first, second)
        self.assertTrue(verify_receipt(first))

        forged = deepcopy(first)
        forged["disposition"] = "READY_FOR_IMPLEMENTATION_REVIEW"
        body = dict(forged)
        body.pop("receipt_sha256", None)
        forged["receipt_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
        ).hexdigest()
        self.assertTrue(verify_receipt(forged, semantic=False))
        self.assertFalse(verify_receipt(forged, semantic=True))

    def test_cli_round_trip_and_hold_exit_code(self):
        reward = deepcopy(snapshot()["reward"])
        reward["payment_path"] = "UNVERIFIED"
        payload = snapshot(reward=reward)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "concierge.bounty_canonical_viability", str(path), "--json"],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertTrue(verify_receipt(receipt))
        self.assertEqual(receipt["disposition"], "HOLD")


if __name__ == "__main__":
    unittest.main()
