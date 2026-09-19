from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.grantfox_application_continuity import (
    GrantFoxContinuityInputError,
    compile_continuity,
    verify_continuity_receipt,
)
from concierge.grantfox_queue_gate import compile_grantfox_queue_gate


def sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def queue_receipt(**identity_overrides):
    identity = {
        "owner": "gryd-lock",
        "repo": "grydlock-testkit",
        "issue_number": 33,
        "listing_url": "https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/33",
        "canonical_issue_url": "https://github.com/Gryd-lock/grydlock-testkit/issues/33",
    }
    identity.update(identity_overrides)
    body = {
        "schema": "grantfox-queue-gate/v1",
        "disposition": "APPLY_ELIGIBLE",
        "advisory_next_action": "APPLY_THROUGH_PROVIDER_ROUTE",
        "reason_codes": [],
        "identity": identity,
        "provider_snapshot": {},
        "reward": {},
        "authority": {
            "advisory_only": True,
            "provider_application_authority": False,
            "implementation_write_authority": False,
            "submission_authority": False,
            "payment_or_wallet_authority": False,
        },
    }
    return {**body, "receipt_sha256": sha(body)}


def ev(kind, n, **extra):
    payload = {
        "event_id": f"e{n}",
        "kind": kind,
        "observed_at": f"2026-09-19T21:{n:02d}:00Z",
    }
    payload.update(extra)
    return payload


def request(events, **overrides):
    payload = {
        "schema": "grantfox-application-continuity/v1",
        "actor_login": "woahwhattheheck",
        "queue_receipt": queue_receipt(),
        "events": events,
    }
    payload.update(overrides)
    return payload


APPLICATION = "https://github.com/Gryd-lock/grydlock-testkit/issues/33#issuecomment-1001"
ASSIGNMENT = "https://github.com/Gryd-lock/grydlock-testkit/issues/33#issuecomment-1002"
ADJUDICATION = "https://github.com/Gryd-lock/grydlock-testkit/issues/33#issuecomment-1003"
PR = "https://github.com/Gryd-lock/grydlock-testkit/pull/44"
PAYMENT = "https://stellar.expert/explorer/public/tx/abc123"


class GrantFoxContinuityTests(unittest.TestCase):
    def test_snapshot_only_is_discovered(self):
        receipt = compile_continuity(request([
            ev("SNAPSHOT", 1, issue_state="open", actor_applied=False, assigned_to=None, linked_pr_urls=[])
        ]))
        self.assertEqual(receipt["state"], "DISCOVERED")
        self.assertEqual(receipt["disposition"], "CONTINUE")
        self.assertEqual(receipt["advisory_next_action"], "USE_FRESH_QUEUE_GATE_BEFORE_APPLYING")

    def test_application_receipt_is_monotonic_applied(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)
        ]))
        self.assertEqual(receipt["state"], "APPLIED")
        self.assertTrue(receipt["lifecycle"]["applied"])

    def test_application_regression_holds_without_erasing_receipt(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("SNAPSHOT", 2, issue_state="open", actor_applied=False, assigned_to=None, linked_pr_urls=[]),
        ]))
        self.assertEqual(receipt["state"], "APPLIED")
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("SNAPSHOT_APPLICATION_REGRESSION", receipt["reason_codes"])

    def test_assignment_requires_prior_application_receipt(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "prior application"):
            compile_continuity(request([
                ev("ASSIGNMENT_RECEIPT", 1, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck")
            ]))

    def test_assignment_must_name_actor(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "configured actor"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
                ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="someone-else"),
            ]))

    def test_assignment_regression_holds_without_downgrade(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="WoahWhatTheHeck"),
            ev("SNAPSHOT", 3, issue_state="open", actor_applied=True, assigned_to=None, linked_pr_urls=[]),
        ]))
        self.assertEqual(receipt["state"], "ASSIGNED")
        self.assertIn("SNAPSHOT_ASSIGNMENT_REGRESSION", receipt["reason_codes"])

    def test_snapshot_assignment_to_other_is_conflict(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("SNAPSHOT", 2, issue_state="open", actor_applied=True, assigned_to="other-user", linked_pr_urls=[]),
        ]))
        self.assertEqual(receipt["state"], "APPLIED")
        self.assertIn("ASSIGNMENT_CONFLICT", receipt["reason_codes"])

    def test_submission_requires_assignment(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "prior durable assignment"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
                ev("SUBMISSION_RECEIPT", 2, pr_url=PR),
            ]))

    def test_submission_pr_must_match_issue_repository(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "different repository"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
                ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
                ev("SUBMISSION_RECEIPT", 3, pr_url="https://github.com/other/repo/pull/1"),
            ]))

    def test_submission_is_not_approval_or_payment(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
            ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
        ]))
        self.assertEqual(receipt["state"], "SUBMITTED")
        self.assertIsNone(receipt["lifecycle"]["adjudication"])
        self.assertFalse(receipt["lifecycle"]["payment_sent"])

    def test_snapshot_different_linked_pr_holds(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
            ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
            ev("SNAPSHOT", 4, issue_state="open", actor_applied=True, assigned_to="woahwhattheheck",
               linked_pr_urls=["https://github.com/Gryd-lock/grydlock-testkit/pull/45"]),
        ]))
        self.assertIn("PR_CONFLICT", receipt["reason_codes"])
        self.assertEqual(receipt["state"], "SUBMITTED")

    def test_adjudication_requires_submission(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "prior submission"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
                ev("ADJUDICATION_RECEIPT", 2, receipt_url=ADJUDICATION, outcome="APPROVED"),
            ]))

    def test_approved_amount_does_not_imply_payment(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
            ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
            ev("ADJUDICATION_RECEIPT", 4, receipt_url=ADJUDICATION, outcome="APPROVED", amount="125.00", currency="USDC"),
        ]))
        self.assertEqual(receipt["state"], "APPROVED")
        self.assertEqual(receipt["lifecycle"]["approved_amount"], "125.00")
        self.assertFalse(receipt["lifecycle"]["payment_received"])

    def test_rejected_is_terminal_adjudication_state(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
            ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
            ev("ADJUDICATION_RECEIPT", 4, receipt_url=ADJUDICATION, outcome="REJECTED"),
        ]))
        self.assertEqual(receipt["state"], "REJECTED")
        self.assertIn("APPEAL", receipt["advisory_next_action"])

    def test_payment_requires_approved_adjudication(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "APPROVED adjudication"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
                ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
                ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
                ev("PAYMENT_RECEIPT", 4, status="SENT", receipt_url=PAYMENT, receipt_ref="tx-1", amount="125", currency="USDC"),
            ]))

    def test_payment_sent_and_received_states(self):
        prefix = [
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
            ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
            ev("ADJUDICATION_RECEIPT", 4, receipt_url=ADJUDICATION, outcome="APPROVED", amount="125", currency="USDC"),
        ]
        sent = compile_continuity(request(prefix + [
            ev("PAYMENT_RECEIPT", 5, status="SENT", receipt_url=PAYMENT, receipt_ref="tx-1", amount="125", currency="USDC")
        ]))
        self.assertEqual(sent["state"], "PAYMENT_SENT")

        received = compile_continuity(request(prefix + [
            ev("PAYMENT_RECEIPT", 5, status="RECEIVED", receipt_url=PAYMENT, receipt_ref="tx-1", amount="125", currency="USDC")
        ]))
        self.assertEqual(received["state"], "PAID")
        self.assertTrue(received["lifecycle"]["payment_received"])

    def test_payment_amount_mismatch_holds(self):
        receipt = compile_continuity(request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
            ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
            ev("ADJUDICATION_RECEIPT", 4, receipt_url=ADJUDICATION, outcome="APPROVED", amount="125", currency="USDC"),
            ev("PAYMENT_RECEIPT", 5, status="SENT", receipt_url=PAYMENT, receipt_ref="tx-1", amount="100", currency="USDC"),
        ]))
        self.assertEqual(receipt["state"], "PAYMENT_SENT")
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")
        self.assertIn("PAYMENT_AMOUNT_DIFFERS_FROM_APPROVED", receipt["reason_codes"])

    def test_received_cannot_regress_to_sent(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "cannot regress"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
                ev("ASSIGNMENT_RECEIPT", 2, receipt_url=ASSIGNMENT, assigned_to="woahwhattheheck"),
                ev("SUBMISSION_RECEIPT", 3, pr_url=PR),
                ev("ADJUDICATION_RECEIPT", 4, receipt_url=ADJUDICATION, outcome="APPROVED"),
                ev("PAYMENT_RECEIPT", 5, status="RECEIVED", receipt_url=PAYMENT, receipt_ref="tx-1", amount="125", currency="USDC"),
                ev("PAYMENT_RECEIPT", 6, status="SENT", receipt_url="https://stellar.expert/explorer/public/tx/def456",
                   receipt_ref="tx-2", amount="125", currency="USDC"),
            ]))

    def test_timestamp_rollback_is_rejected(self):
        bad = request([
            ev("APPLICATION_RECEIPT", 2, receipt_url=APPLICATION),
            ev("SNAPSHOT", 1, issue_state="open", actor_applied=True, assigned_to=None, linked_pr_urls=[]),
        ])
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "strictly increasing"):
            compile_continuity(bad)

    def test_duplicate_event_id_is_rejected(self):
        first = ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)
        second = ev("SNAPSHOT", 2, issue_state="open", actor_applied=True, assigned_to=None, linked_pr_urls=[])
        second["event_id"] = first["event_id"]
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "event_id"):
            compile_continuity(request([first, second]))

    def test_duplicate_durable_receipt_is_rejected(self):
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "receipt URLs"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
                ev("ASSIGNMENT_RECEIPT", 2, receipt_url=APPLICATION, assigned_to="woahwhattheheck"),
            ]))

    def test_queue_actor_mismatch_is_rejected_when_observed(self):
        q = queue_receipt()
        q["provider_snapshot"] = {"actor_login": "someone-else"}
        body = dict(q); body.pop("receipt_sha256")
        q["receipt_sha256"] = sha(body)
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "differs from queue receipt actor"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)
            ], queue_receipt=q))

    def test_noncanonical_github_owner_segment_is_rejected(self):
        q = queue_receipt(canonical_issue_url="https://github.com/Gryd-lock@alias/grydlock-testkit/issues/33")
        body = dict(q); body.pop("receipt_sha256")
        q["receipt_sha256"] = sha(body)
        with self.assertRaises(GrantFoxContinuityInputError):
            compile_continuity(request([ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)], queue_receipt=q))


    def test_queue_receipt_tamper_is_rejected(self):
        q = queue_receipt()
        q["identity"]["issue_number"] = 99
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "digest"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)
            ], queue_receipt=q))

    def test_queue_identity_alias_is_rejected(self):
        q = queue_receipt(canonical_issue_url="https://github.com/Gryd-lock/grydlock-testkit/issues/34")
        body = dict(q)
        body.pop("receipt_sha256")
        q["receipt_sha256"] = sha(body)
        with self.assertRaisesRegex(GrantFoxContinuityInputError, "different issue"):
            compile_continuity(request([
                ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)
            ], queue_receipt=q))

    def test_receipt_is_deterministic_and_tamper_evident(self):
        payload = request([ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)])
        first = compile_continuity(payload)
        second = compile_continuity(payload)
        self.assertEqual(first, second)
        self.assertTrue(verify_continuity_receipt(first))
        changed = deepcopy(first)
        changed["state"] = "PAID"
        self.assertFalse(verify_continuity_receipt(changed))

    def test_contextual_verifier_rejects_rehashed_lifecycle_forgery(self):
        q = compile_grantfox_queue_gate(
            {
                "schema": "grantfox-queue-gate/v1",
                "listing_url": (
                    "https://contribute.grantfox.xyz/org/Gryd-lock/"
                    "repo/grydlock-testkit/issue/33"
                ),
                "canonical_issue_url": (
                    "https://github.com/Gryd-lock/grydlock-testkit/issues/33"
                ),
                "issue_state": "open",
                "actor_login": "woahwhattheheck",
                "assigned_to": None,
                "actor_applied": True,
                "application_count": 1,
                "application_pressure_threshold": 3,
                "linked_pr_urls": [],
                "labels": ["GrantFox OSS"],
                "observed_at": "2026-09-19T21:00:00Z",
                "evaluated_at": "2026-09-19T21:00:10Z",
                "max_snapshot_age_seconds": 900,
            }
        )
        receipt = compile_continuity(
            request(
                [ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION)],
                queue_receipt=q,
            )
        )
        self.assertTrue(verify_continuity_receipt(receipt, q))

        forged = deepcopy(receipt)
        forged["state"] = "ASSIGNED"
        forged["advisory_next_action"] = "IMPLEMENT_ASSIGNED_SCOPE"
        forged["lifecycle"]["assigned"] = True
        body = dict(forged)
        body.pop("receipt_sha256", None)
        forged["receipt_sha256"] = sha(body)

        self.assertTrue(verify_continuity_receipt(forged))
        self.assertFalse(verify_continuity_receipt(forged, q))

    def test_cli_hold_exit_code_and_json_roundtrip(self):
        payload = request([
            ev("APPLICATION_RECEIPT", 1, receipt_url=APPLICATION),
            ev("SNAPSHOT", 2, issue_state="open", actor_applied=False, assigned_to=None, linked_pr_urls=[]),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "concierge.grantfox_application_continuity", str(path), "--json"],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertTrue(verify_continuity_receipt(receipt))
        self.assertEqual(receipt["disposition"], "HOLD_RECONCILE")


if __name__ == "__main__":
    unittest.main()
