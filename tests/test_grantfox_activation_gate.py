from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from concierge.grantfox_activation_gate import (
    AUTHORITY,
    GrantFoxActivationInputError,
    compile_activation,
    verify_activation_receipt,
)
from concierge.grantfox_application_continuity import compile_continuity
from concierge.grantfox_queue_gate import compile_grantfox_queue_gate
from concierge.grantfox_source_readiness import compile_grantfox_source_readiness


def request(queue_disposition="APPLY_ELIGIBLE", source_disposition="SOURCE_ALIGNED",
            dependency_disposition="DEPENDENCIES_CLEAR",
            continuity_state="DISCOVERED", continuity_disposition="CONTINUE"):
    queue = {
        "schema": "grantfox-queue-gate/v1",
        "disposition": queue_disposition,
        "receipt_sha256": "a" * 64,
        "identity": {
            "owner": "QuickLendX",
            "repo": "quicklendx-frontend",
            "issue_number": 16,
        },
        "provider_snapshot": {
            "actor_login": "woahwhattheheck",
        },
    }
    source = {
        "schema": "grantfox-source-readiness-receipt/v1",
        "source_disposition": source_disposition,
        "source_receipt_sha256": "b" * 64,
        "identity": {"owner": "quicklendx", "repo": "quicklendx-frontend", "issue_number": 16},
        "queue_receipt": deepcopy(queue),
    }
    dependency = {
        "schema": "grantfox-dependency-readiness-receipt/v1",
        "dependency_disposition": dependency_disposition,
        "dependency_receipt_sha256": "d" * 64,
        "identity": {
            "owner": "quicklendx",
            "repo": "quicklendx-frontend",
            "issue_number": 16,
        },
        "source_receipt": deepcopy(source),
    }
    continuity = {
        "schema": "grantfox-application-continuity/v1",
        "disposition": continuity_disposition,
        "state": continuity_state,
        "receipt_sha256": "c" * 64,
        "identity": {
            "owner": "QUICKLENDX",
            "repo": "quicklendx-frontend",
            "issue_number": 16,
            "actor_login": "WOAHWHATTHEHECK",
        },
        "queue_anchor": {"receipt_sha256": queue["receipt_sha256"]},
    }
    return {
        "schema": "grantfox-activation-gate/v1",
        "queue_receipt": queue,
        "source_receipt": source,
        "dependency_receipt": dependency,
        "continuity_receipt": continuity,
    }


def native_request(*, assigned=False):
    queue = compile_grantfox_queue_gate({
        "schema": "grantfox-queue-gate/v1",
        "listing_url": "https://contribute.grantfox.xyz/org/QuickLendX/repo/quicklendx-frontend/issue/16",
        "canonical_issue_url": "https://github.com/QuickLendX/quicklendx-frontend/issues/16",
        "issue_state": "open",
        "actor_login": "woahwhattheheck",
        "assigned_to": "woahwhattheheck" if assigned else None,
        "actor_applied": assigned,
        "application_count": 1 if assigned else 0,
        "application_pressure_threshold": 3,
        "linked_pr_urls": [],
        "labels": ["Maybe Rewarded", "GrantFox OSS"],
        "observed_at": "2026-09-19T22:00:00Z",
        "evaluated_at": "2026-09-19T22:00:10Z",
        "max_snapshot_age_seconds": 900,
    })
    source = compile_grantfox_source_readiness({
        "schema": "grantfox-source-readiness/v1",
        "queue_receipt": queue,
        "repository_snapshot": {
            "repository_full_name": "QuickLendX/quicklendx-frontend",
            "default_branch": "main",
            "commit_sha": "1" * 40,
            "observed_at": "2026-09-19T22:00:15Z",
        },
        "expectations": [{
            "kind": "path",
            "value": "src/app/page.tsx",
            "matches": [{"path": "src/app/page.tsx", "blob_sha": "2" * 40}],
        }],
        "replacements": [],
        "evaluated_at": "2026-09-19T22:00:20Z",
        "max_snapshot_age_seconds": 900,
    })
    if assigned:
        events = [
            {
                "event_id": "e1",
                "kind": "APPLICATION_RECEIPT",
                "observed_at": "2026-09-19T22:00:30Z",
                "receipt_url": "https://github.com/QuickLendX/quicklendx-frontend/issues/16#issuecomment-1001",
            },
            {
                "event_id": "e2",
                "kind": "ASSIGNMENT_RECEIPT",
                "observed_at": "2026-09-19T22:00:40Z",
                "receipt_url": "https://github.com/QuickLendX/quicklendx-frontend/issues/16#issuecomment-1002",
                "assigned_to": "woahwhattheheck",
            },
        ]
    else:
        events = [{
            "event_id": "e1",
            "kind": "SNAPSHOT",
            "observed_at": "2026-09-19T22:00:30Z",
            "issue_state": "open",
            "actor_applied": False,
            "assigned_to": None,
            "linked_pr_urls": [],
        }]
    continuity = compile_continuity({
        "schema": "grantfox-application-continuity/v1",
        "actor_login": "woahwhattheheck",
        "queue_receipt": queue,
        "events": events,
    })
    return {
        "schema": "grantfox-activation-gate/v1",
        "queue_receipt": queue,
        "source_receipt": source,
        "continuity_receipt": continuity,
    }


def rehash_activation(receipt):
    body = dict(receipt)
    body.pop("activation_receipt_sha256", None)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    receipt["activation_receipt_sha256"] = hashlib.sha256(raw.encode()).hexdigest()


class ActivationGateTests(unittest.TestCase):
    def compile(self, payload):
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_dependency_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            return compile_activation(payload)

    def verify(self, receipt):
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            return verify_activation_receipt(receipt)

    def test_discovered_aligned_can_apply(self):
        receipt = self.compile(request())
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertTrue(self.verify(receipt))
        self.assertEqual(receipt["authority"], AUTHORITY)

    def test_native_discovered_receipts_compose_without_mocked_verifiers(self):
        payload = native_request()
        receipt = compile_activation(payload)
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertEqual(receipt["identity"]["actor_login"], "woahwhattheheck")
        self.assertTrue(verify_activation_receipt(receipt))

    def test_native_assigned_receipts_are_the_only_implementation_activation(self):
        payload = native_request(assigned=True)
        receipt = compile_activation(payload)
        self.assertEqual(receipt["disposition"], "IMPLEMENT_ASSIGNED_SCOPE")
        self.assertEqual(receipt["inputs"]["queue_disposition"], "IMPLEMENTATION_ELIGIBLE")
        self.assertEqual(receipt["inputs"]["continuity_state"], "ASSIGNED")
        self.assertEqual(receipt["evidence"]["queue_receipt"], payload["queue_receipt"])
        self.assertEqual(receipt["evidence"]["source_receipt"], payload["source_receipt"])
        self.assertEqual(
            receipt["evidence"]["continuity_receipt"],
            payload["continuity_receipt"],
        )
        self.assertTrue(verify_activation_receipt(receipt))

    def test_native_rehashed_semantic_forgery_is_rejected(self):
        receipt = compile_activation(native_request())
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        forged = deepcopy(receipt)
        forged["disposition"] = "IMPLEMENT_ASSIGNED_SCOPE"
        forged["advisory_next_action"] = "IMPLEMENT_ONLY_THE_ASSIGNED_PINNED_SCOPE"
        forged["inputs"]["queue_disposition"] = "IMPLEMENTATION_ELIGIBLE"
        forged["inputs"]["continuity_state"] = "ASSIGNED"
        rehash_activation(forged)
        self.assertFalse(verify_activation_receipt(forged))

    def test_rehashed_unknown_semantic_field_is_rejected(self):
        receipt = compile_activation(native_request())
        forged = deepcopy(receipt)
        forged["implementation_authorized"] = True
        rehash_activation(forged)
        self.assertFalse(verify_activation_receipt(forged))

    def test_source_drift_requires_replan_before_apply(self):
        receipt = self.compile(request(source_disposition="SOURCE_DRIFT_REPLAN"))
        self.assertEqual(receipt["disposition"], "REPLAN_BEFORE_APPLY")

    def test_applied_waits_even_if_point_snapshot_still_says_apply(self):
        receipt = self.compile(request(continuity_state="APPLIED"))
        self.assertEqual(receipt["disposition"], "WAIT_ASSIGNMENT")

    def test_only_assigned_aligned_implementation_snapshot_activates(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            source_disposition="SOURCE_ALIGNED",
            continuity_state="ASSIGNED",
        ))
        self.assertEqual(receipt["disposition"], "IMPLEMENT_ASSIGNED_SCOPE")

    def test_assigned_with_open_prerequisite_waits(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            dependency_disposition="DEPENDENCY_WAIT",
            continuity_state="ASSIGNED",
        ))
        self.assertEqual(receipt["disposition"], "WAIT_DEPENDENCIES")
        self.assertIn("PREREQUISITE_ISSUES_OPEN", receipt["reason_codes"])

    def test_dependency_hold_blocks_activation(self):
        receipt = self.compile(request(dependency_disposition="HOLD"))
        self.assertEqual(receipt["disposition"], "HOLD_DEPENDENCIES")
        self.assertIn("DEPENDENCY_READINESS_HOLD", receipt["reason_codes"])

    def test_assigned_but_source_drift_refuses_implementation(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            source_disposition="SOURCE_DRIFT_REPLAN",
            continuity_state="ASSIGNED",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_SOURCE")
        self.assertIn("ASSIGNED_SCOPE_SOURCE_NOT_ALIGNED", receipt["reason_codes"])

    def test_assignment_and_queue_contradiction_holds(self):
        receipt = self.compile(request(continuity_state="ASSIGNED"))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("ASSIGNMENT_QUEUE_CONTRADICTION", receipt["reason_codes"])

    def test_submitted_never_regresses_to_implementation(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            continuity_state="SUBMITTED",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("LIFECYCLE_ALREADY_PAST_IMPLEMENTATION", receipt["reason_codes"])

    def test_paid_never_regresses_to_implementation(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            continuity_state="PAID",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")

    def test_continuity_reconcile_dominates(self):
        receipt = self.compile(request(
            queue_disposition="IMPLEMENTATION_ELIGIBLE",
            continuity_state="ASSIGNED",
            continuity_disposition="HOLD_RECONCILE",
        ))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("CONTINUITY_RECONCILE_REQUIRED", receipt["reason_codes"])

    def test_source_hold_dominates(self):
        receipt = self.compile(request(source_disposition="HOLD"))
        self.assertEqual(receipt["disposition"], "HOLD_SOURCE")

    def test_actor_swap_fails_closed(self):
        payload = request()
        payload["continuity_receipt"]["identity"]["actor_login"] = "someone-else"
        with self.assertRaisesRegex(GrantFoxActivationInputError, "actors differ"):
            self.compile(payload)

    def test_real_queue_actor_location_is_used(self):
        payload = request()
        self.assertNotIn("actor_login", payload["queue_receipt"]["identity"])
        payload["queue_receipt"]["provider_snapshot"]["actor_login"] = "WOAHWHATTHEHECK"
        receipt = self.compile(payload)
        self.assertEqual(receipt["identity"]["actor_login"], "woahwhattheheck")

    def test_issue_swap_fails_closed(self):
        payload = request()
        payload["source_receipt"]["identity"]["issue_number"] = 99
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different issues"):
            self.compile(payload)

    def test_source_queue_digest_swap_fails_closed(self):
        payload = request()
        payload["source_receipt"]["queue_receipt"]["receipt_sha256"] = "d" * 64
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different queue receipt"):
            self.compile(payload)

    def test_dependency_issue_swap_fails_closed(self):
        payload = request()
        payload["dependency_receipt"]["identity"]["issue_number"] = 99
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different issues"):
            self.compile(payload)

    def test_dependency_source_swap_fails_closed(self):
        payload = request()
        payload["dependency_receipt"]["source_receipt"]["source_receipt_sha256"] = "e" * 64
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different source receipt"):
            self.compile(payload)

    def test_continuity_queue_digest_swap_fails_closed(self):
        payload = request()
        payload["continuity_receipt"]["queue_anchor"]["receipt_sha256"] = "d" * 64
        with self.assertRaisesRegex(GrantFoxActivationInputError, "different queue receipt"):
            self.compile(payload)

    def test_unverified_input_is_rejected(self):
        payload = request()
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=False),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_dependency_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            with self.assertRaisesRegex(GrantFoxActivationInputError, "queue_receipt does not verify"):
                compile_activation(payload)

    def test_unverified_dependency_is_rejected(self):
        payload = request()
        with (
            patch("concierge.grantfox_activation_gate.verify_queue_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_source_readiness_receipt", return_value=True),
            patch("concierge.grantfox_activation_gate.verify_dependency_readiness_receipt", return_value=False),
            patch("concierge.grantfox_activation_gate.verify_continuity_receipt", return_value=True),
        ):
            with self.assertRaisesRegex(GrantFoxActivationInputError, "dependency_receipt does not verify"):
                compile_activation(payload)

    def test_receipt_digest_detects_tamper(self):
        receipt = self.compile(request())
        receipt["disposition"] = "IMPLEMENT_ASSIGNED_SCOPE"
        self.assertFalse(self.verify(receipt))

    def test_rehashed_forgery_cannot_override_evidence_state_machine(self):
        receipt = self.compile(request())
        forged = deepcopy(receipt)
        forged["disposition"] = "IMPLEMENT_ASSIGNED_SCOPE"
        forged["advisory_next_action"] = "IMPLEMENT_ONLY_THE_ASSIGNED_PINNED_SCOPE"
        forged["inputs"]["queue_disposition"] = "IMPLEMENTATION_ELIGIBLE"
        forged["inputs"]["continuity_state"] = "ASSIGNED"
        rehash_activation(forged)
        self.assertFalse(self.verify(forged))


class ActivationGateProductionIntegrationTests(unittest.TestCase):
    def test_real_receipt_compilers_unlock_only_assigned_aligned_scope(self):
        from concierge.grantfox_application_continuity import compile_continuity
        from concierge.grantfox_dependency_readiness import compile_grantfox_dependency_readiness
        from concierge.grantfox_queue_gate import compile_grantfox_queue_gate
        from concierge.grantfox_source_readiness import compile_grantfox_source_readiness

        queue = compile_grantfox_queue_gate({
            "schema": "grantfox-queue-gate/v1",
            "listing_url": "https://contribute.grantfox.xyz/org/QuickLendX/repo/quicklendx-frontend/issue/16",
            "canonical_issue_url": "https://github.com/QuickLendX/quicklendx-frontend/issues/16",
            "issue_state": "open",
            "actor_login": "woahwhattheheck",
            "assigned_to": "woahwhattheheck",
            "actor_applied": True,
            "application_count": 1,
            "application_pressure_threshold": 3,
            "linked_pr_urls": [],
            "labels": ["GrantFox OSS"],
            "observed_at": "2026-09-19T21:00:00Z",
            "evaluated_at": "2026-09-19T21:01:00Z",
            "max_snapshot_age_seconds": 900,
        })
        source = compile_grantfox_source_readiness({
            "schema": "grantfox-source-readiness/v1",
            "queue_receipt": queue,
            "repository_snapshot": {
                "repository_full_name": "QuickLendX/quicklendx-frontend",
                "default_branch": "main",
                "commit_sha": "1" * 40,
                "observed_at": "2026-09-19T21:00:30Z",
            },
            "expectations": [{
                "kind": "path",
                "value": "README.md",
                "matches": [{"path": "README.md", "blob_sha": "2" * 40}],
            }],
            "replacements": [],
            "evaluated_at": "2026-09-19T21:01:00Z",
            "max_snapshot_age_seconds": 900,
        })
        dependency = compile_grantfox_dependency_readiness({
            "schema": "grantfox-dependency-readiness/v1",
            "source_receipt": source,
            "dependencies": [],
            "evaluated_at": "2026-09-19T21:01:00Z",
            "max_snapshot_age_seconds": 900,
        })
        continuity = compile_continuity({
            "schema": "grantfox-application-continuity/v1",
            "queue_receipt": queue,
            "actor_login": "woahwhattheheck",
            "events": [
                {
                    "event_id": "application",
                    "kind": "APPLICATION_RECEIPT",
                    "observed_at": "2026-09-19T21:00:10Z",
                    "receipt_url": "https://github.com/QuickLendX/quicklendx-frontend/issues/16#issuecomment-101",
                },
                {
                    "event_id": "assignment",
                    "kind": "ASSIGNMENT_RECEIPT",
                    "observed_at": "2026-09-19T21:00:20Z",
                    "receipt_url": "https://github.com/QuickLendX/quicklendx-frontend/issues/16#issuecomment-102",
                    "assigned_to": "woahwhattheheck",
                },
            ],
        })
        receipt = compile_activation({
            "schema": "grantfox-activation-gate/v1",
            "queue_receipt": queue,
            "source_receipt": source,
            "dependency_receipt": dependency,
            "continuity_receipt": continuity,
        })
        self.assertEqual(receipt["disposition"], "IMPLEMENT_ASSIGNED_SCOPE")
        self.assertTrue(verify_activation_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
