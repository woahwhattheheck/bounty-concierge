# SPDX-License-Identifier: MIT
"""Focused state-transition checks for the durable GitHub write-intent ledger."""

import tempfile
from pathlib import Path
import unittest

from concierge.github_write_intent import GitHubWriteIntentLedger, WriteIntentError


HEAD_A = "a" * 40
HEAD_B = "b" * 40


class GitHubWriteIntentLedgerTests(unittest.TestCase):
    def ledger(self, root: str) -> GitHubWriteIntentLedger:
        return GitHubWriteIntentLedger(Path(root) / "write-intents.sqlite")

    def enqueue(
        self,
        ledger: GitHubWriteIntentLedger,
        *,
        operation_id: str = "pr-988-body-r1",
        body: str = "current technical body",
        now_epoch: float = 100.0,
    ):
        return ledger.enqueue_pr_body(
            operation_id=operation_id,
            repository="Centurylong/sanctifier",
            pull_number=988,
            expected_head=HEAD_A,
            body=body,
            now_epoch=now_epoch,
        )

    def test_enqueue_is_idempotent_but_operation_id_cannot_change_payload(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            first = self.enqueue(ledger)
            replay = self.enqueue(ledger, now_epoch=500.0)

            self.assertEqual(replay, first)
            self.assertEqual(first.state, "PENDING")
            self.assertEqual(first.created_epoch, 100.0)

            with self.assertRaises(WriteIntentError):
                self.enqueue(ledger, body="different body")

    def test_different_operation_ids_for_same_live_pr_head_deduplicate(self):
        with tempfile.TemporaryDirectory() as root:
            first_ledger = self.ledger(root)
            second_ledger = self.ledger(root)  # A different publisher process.
            first = self.enqueue(first_ledger, operation_id="seat-a-body")
            duplicate = self.enqueue(
                second_ledger, operation_id="seat-b-body", now_epoch=101.0
            )
            self.assertEqual(duplicate.operation_id, first.operation_id)
            self.assertEqual(len(first_ledger.records()), 1)
            owner = second_ledger.claim_ready("publisher-b", now_epoch=102.0)
            self.assertEqual(owner.operation_id, first.operation_id)
            self.assertIsNone(
                first_ledger.claim_ready("publisher-a", now_epoch=102.5)
            )

    def test_conflicting_live_body_fails_closed_but_done_head_is_reusable(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            first = self.enqueue(ledger, operation_id="first", body="A")
            with self.assertRaisesRegex(WriteIntentError, "different body"):
                self.enqueue(
                    ledger, operation_id="other-seat", body="B", now_epoch=101.0
                )
            self.assertEqual(len(ledger.records()), 1)

            leased = ledger.claim_ready("publisher-a", now_epoch=102.0)
            ledger.complete(
                leased.operation_id, "publisher-a", observed_head=HEAD_A
            )
            follow_up = self.enqueue(
                ledger, operation_id="second-revision", body="B",
                now_epoch=105.0
            )
            self.assertEqual(follow_up.state, "PENDING")
            self.assertEqual(len(ledger.records()), 2)

    def test_case_insensitive_repository_identity_reuses_active_intent(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            first = self.enqueue(ledger, operation_id="source")
            replay = ledger.enqueue_pr_body(
                operation_id="different-operation",
                repository="centurylong/SANCTIFIER",
                pull_number=988,
                expected_head=HEAD_A,
                body="current technical body",
                now_epoch=150.0,
            )
            self.assertEqual(replay.operation_id, first.operation_id)
            self.assertEqual(len(ledger.records()), 1)

    def test_pending_different_head_cannot_enqueue_same_pr_target(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            original = self.enqueue(ledger, operation_id="seat-a-head-a")
            with self.assertRaisesRegex(WriteIntentError, "different expected head"):
                ledger.enqueue_pr_body(
                    operation_id="seat-b-head-b",
                    repository="centurylong/SANCTIFIER",
                    pull_number=988,
                    expected_head=HEAD_B,
                    body="new head body",
                    now_epoch=101.0,
                )
            self.assertEqual(len(ledger.records()), 1)
            self.assertEqual(ledger.records()[0].operation_id, original.operation_id)

    def test_leased_cross_head_is_blocked_then_done_allows_new_head(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            self.enqueue(ledger, operation_id="old-head")
            original = ledger.claim_ready("publisher-a", now_epoch=102.0)
            self.assertIsNotNone(original)
            with self.assertRaisesRegex(WriteIntentError, "different expected head"):
                ledger.enqueue_pr_body(
                    operation_id="new-head",
                    repository="Centurylong/sanctifier",
                    pull_number=988,
                    expected_head=HEAD_B,
                    body="new head body",
                    now_epoch=103.0,
                )
            self.assertIsNone(ledger.claim_ready("publisher-b", now_epoch=103.0))
            ledger.complete(original.operation_id, "publisher-a", observed_head=HEAD_A)
            follow_up = ledger.enqueue_pr_body(
                operation_id="new-head",
                repository="Centurylong/sanctifier",
                pull_number=988,
                expected_head=HEAD_B,
                body="new head body",
                now_epoch=104.0,
            )
            self.assertEqual(follow_up.state, "PENDING")
            self.assertEqual(follow_up.expected_head, HEAD_B)
            self.assertEqual(len(ledger.records()), 2)

    def test_claim_serializes_publishers_and_expired_lease_is_reclaimable(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            self.enqueue(ledger)

            owned = ledger.claim_ready(
                "publisher-a", now_epoch=110.0, lease_seconds=10.0
            )
            self.assertIsNotNone(owned)
            self.assertEqual(owned.lease_owner, "publisher-a")
            self.assertEqual(owned.lease_until_epoch, 120.0)

            self.assertIsNone(
                ledger.claim_ready(
                    "publisher-b", now_epoch=119.0, lease_seconds=10.0
                )
            )

            reclaimed = ledger.claim_ready(
                "publisher-b", now_epoch=120.0, lease_seconds=15.0
            )
            self.assertIsNotNone(reclaimed)
            self.assertEqual(reclaimed.operation_id, owned.operation_id)
            self.assertEqual(reclaimed.lease_owner, "publisher-b")
            self.assertEqual(reclaimed.lease_until_epoch, 135.0)

    def test_defer_honors_provider_retry_floor_before_replay(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            self.enqueue(ledger)
            owned = ledger.claim_ready("publisher-a", now_epoch=110.0)
            self.assertIsNotNone(owned)

            deferred = ledger.defer(
                owned.operation_id,
                "publisher-a",
                not_before_epoch=300.0,
                error_code="PRIMARY_RATE_LIMIT",
            )
            self.assertEqual(deferred.state, "PENDING")
            self.assertEqual(deferred.not_before_epoch, 300.0)
            self.assertEqual(deferred.last_error_code, "PRIMARY_RATE_LIMIT")
            self.assertIsNone(ledger.claim_ready("publisher-b", now_epoch=299.9))

            replay = ledger.claim_ready("publisher-b", now_epoch=300.0)
            self.assertIsNotNone(replay)
            self.assertEqual(replay.operation_id, owned.operation_id)
            self.assertEqual(replay.lease_owner, "publisher-b")

    def test_integration_403_routes_to_matching_token_actor_without_app_replay(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            original = self.enqueue(ledger, operation_id="sponsor-body")
            app = ledger.claim_ready("managed-app", now_epoch=110.0)
            self.assertEqual(app.operation_id, original.operation_id)

            routed = ledger.handoff_integration_denial(
                app.operation_id,
                "managed-app",
                required_actor="woahwhattheheck",
                provider_status=403,
                provider_message="Resource not accessible by integration",
            )
            self.assertEqual(routed.state, "TOKEN_REQUIRED")
            self.assertEqual(routed.required_actor, "woahwhattheheck")
            self.assertEqual(routed.last_error_code, "INTEGRATION_FORBIDDEN")
            self.assertIsNone(ledger.claim_ready("another-app", now_epoch=111.0))

            duplicate = self.enqueue(
                ledger, operation_id="duplicate-app", now_epoch=111.0
            )
            self.assertEqual(duplicate.operation_id, routed.operation_id)
            self.assertIsNone(
                ledger.claim_token_ready(
                    "wrong-token-seat",
                    actor="tokenjunkielabs",
                    now_epoch=112.0,
                )
            )

            token = ledger.claim_token_ready(
                "original-token-seat",
                actor="woahwhattheheck",
                now_epoch=112.0,
            )
            self.assertEqual(token.state, "TOKEN_LEASED")
            self.assertEqual(token.lease_owner, "original-token-seat")
            done = ledger.complete(
                token.operation_id,
                "original-token-seat",
                observed_head=HEAD_A,
            )
            self.assertEqual(done.state, "DONE")

    def test_complete_fails_closed_on_head_drift_without_losing_lease(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = self.ledger(root)
            self.enqueue(ledger)
            owned = ledger.claim_ready("publisher-a", now_epoch=110.0)
            self.assertIsNotNone(owned)

            with self.assertRaisesRegex(WriteIntentError, "head drifted"):
                ledger.complete(
                    owned.operation_id,
                    "publisher-a",
                    observed_head=HEAD_B,
                )

            still_owned = ledger.claim_ready("publisher-a", now_epoch=111.0)
            self.assertIsNotNone(still_owned)
            self.assertEqual(still_owned.state, "LEASED")
            self.assertEqual(still_owned.lease_owner, "publisher-a")

            done = ledger.complete(
                owned.operation_id,
                "publisher-a",
                observed_head=HEAD_A,
            )
            self.assertEqual(done.state, "DONE")
            self.assertIsNone(done.lease_owner)
            self.assertIsNone(ledger.claim_ready("publisher-b", now_epoch=500.0))

    def test_supersede_head_releases_only_owned_active_drifted_intent(self):
        for token_route in (False, True):
            with self.subTest(token_route=token_route), tempfile.TemporaryDirectory() as root:
                ledger = self.ledger(root)
                original = self.enqueue(ledger, operation_id="obsolete")
                with self.assertRaisesRegex(WriteIntentError, "active lease"):
                    ledger.supersede_head(
                        original.operation_id, "publisher", observed_head=HEAD_B,
                        now_epoch=101.0,
                    )
                owned = ledger.claim_ready("publisher", now_epoch=110.0, lease_seconds=10)
                if token_route:
                    ledger.handoff_integration_denial(
                        owned.operation_id, "publisher",
                        required_actor="woahwhattheheck", provider_status=403,
                        provider_message="Resource not accessible by integration",
                    )
                    owned = ledger.claim_token_ready(
                        "publisher", actor="woahwhattheheck",
                        now_epoch=110.0, lease_seconds=10,
                    )

                for owner, head, now, message in (
                    ("other", HEAD_B, 111.0, "active lease"),
                    ("publisher", HEAD_A, 111.0, "has not drifted"),
                    ("publisher", HEAD_B, 120.0, "active lease"),
                ):
                    with self.assertRaisesRegex(WriteIntentError, message):
                        ledger.supersede_head(
                            owned.operation_id, owner, observed_head=head, now_epoch=now
                        )
                    self.assertEqual(ledger.records()[0], owned)

                retired = ledger.supersede_head(
                    owned.operation_id, "publisher", observed_head=HEAD_B, now_epoch=119.0
                )
                self.assertEqual(retired.state, "SUPERSEDED")
                self.assertEqual(retired.expected_head, HEAD_A)
                self.assertEqual(retired.body, original.body)
                self.assertEqual(retired.payload_sha256, original.payload_sha256)
                self.assertEqual(retired.required_actor, owned.required_actor)
                self.assertEqual(retired.last_error_code, "HEAD_DRIFT:" + HEAD_B)
                self.assertIsNone(retired.lease_owner)
                self.assertIsNone(retired.lease_until_epoch)
                self.assertEqual(self.enqueue(ledger, operation_id="obsolete"), retired)
                self.assertIsNone(ledger.claim_ready("next", now_epoch=121.0))
                self.assertIsNone(ledger.claim_token_ready(
                    "next", actor="woahwhattheheck", now_epoch=121.0
                ))

                replacement = ledger.enqueue_pr_body(
                    operation_id="fresh-head", repository="Centurylong/sanctifier",
                    pull_number=988, expected_head=HEAD_B, body="fresh head body",
                    now_epoch=122.0,
                )
                self.assertEqual(replacement.state, "PENDING")
                self.assertEqual(ledger.claim_ready("next", now_epoch=123.0),
                                 ledger.records()[1])


if __name__ == "__main__":
    unittest.main()
