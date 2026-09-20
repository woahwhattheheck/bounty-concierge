from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

from concierge.bounty_deadline_gate import compile_bounty_deadline_gate
from concierge.grantfox_deadline_activation import (
    AUTHORITY,
    GrantFoxDeadlineActivationInputError,
    compile_deadline_activation,
    verify_deadline_activation_receipt,
)


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 19, hour, minute, tzinfo=timezone.utc)


def live(disposition: str = "APPLY_ELIGIBLE", *, issue: int = 42) -> dict:
    actions = {
        "APPLY_ELIGIBLE": "APPLY_THROUGH_VERIFIED_PROVIDER_ROUTE",
        "REPLAN_BEFORE_APPLY": "REPLAN_FROM_PINNED_SOURCE_THEN_REFRESH_GATE",
        "WAIT_ASSIGNMENT": "WAIT_FOR_DURABLE_PROVIDER_ASSIGNMENT",
        "WAIT_DEPENDENCIES": "WAIT_FOR_PREREQUISITES_BEFORE_IMPLEMENTATION",
        "IMPLEMENT_ASSIGNED_SCOPE": "IMPLEMENT_ONLY_THE_ASSIGNED_PINNED_SCOPE",
        "HOLD_ECONOMICS": "REMOVE_FROM_ACTIVE_QUEUE_OR_REFRESH_LIVE_CASH",
        "HOLD_GRANTFOX_ACTIVATION": "RESOLVE_GRANTFOX_ACTIVATION_BEFORE_DISPATCH",
    }
    return {
        "schema": "grantfox-live-cash-activation-receipt/v1",
        "disposition": disposition,
        "advisory_next_action": actions[disposition],
        "identity": {
            "owner": "ExampleOrg",
            "repo": "demo",
            "issue_number": issue,
            "actor_login": "woahwhattheheck",
        },
        "receipt_sha256": "a" * 64,
    }


def deadline(
    *,
    issue: int = 42,
    value: str = "2026-09-19T23:00:00Z",
    evaluated_at: str = "2026-09-19T21:00:00Z",
    issue_observed_at: str = "2026-09-19T20:59:00Z",
    deadline_observed_at: str = "2026-09-19T20:58:00Z",
    max_age: int = 86400,
    extension_value: str | None = None,
) -> dict:
    issue_url = f"https://github.com/ExampleOrg/demo/issues/{issue}"
    extension = None
    if extension_value is not None:
        extension = {
            "kind": "INSTANT",
            "value": extension_value,
            "authority": "REPO_OWNER",
            "source_url": issue_url,
            "source_content_sha256": "3" * 64,
            "observed_at": deadline_observed_at,
        }
    return compile_bounty_deadline_gate(
        {
            "schema": "bounty-deadline-gate/v1",
            "issue": {
                "url": issue_url,
                "state": "OPEN",
                "observed_at": issue_observed_at,
                "source_content_sha256": "1" * 64,
            },
            "deadline_evidence": {
                "kind": "INSTANT",
                "value": value,
                "authority": "ISSUE_BODY",
                "source_url": issue_url,
                "source_content_sha256": "2" * 64,
                "observed_at": deadline_observed_at,
            },
            "extension_evidence": extension,
            "evaluated_at": evaluated_at,
            "max_observation_age_seconds": max_age,
        }
    )


def request(live_receipt: dict, deadline_receipt: dict) -> dict:
    return {
        "schema": "grantfox-deadline-activation/v1",
        "live_cash_activation_receipt": live_receipt,
        "deadline_receipt": deadline_receipt,
    }


class GrantFoxDeadlineActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live_verifier = patch(
            "concierge.grantfox_deadline_activation."
            "verify_live_cash_activation_receipt",
            return_value=True,
        )
        self.live_verifier.start()
        self.addCleanup(self.live_verifier.stop)

    def test_apply_survives_only_with_current_deadline(self) -> None:
        receipt = compile_deadline_activation(
            request(live(), deadline()), now=dt(21, 1)
        )
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertEqual(
            receipt["deadline"]["current_disposition"], "DEADLINE_CURRENT"
        )
        self.assertEqual(receipt["authority"], AUTHORITY)

    def test_assigned_implementation_is_blocked_after_deadline(self) -> None:
        source = deadline(value="2026-09-19T21:02:00Z")
        self.assertEqual(source["disposition"], "DEADLINE_CURRENT")
        receipt = compile_deadline_activation(
            request(live("IMPLEMENT_ASSIGNED_SCOPE"), source),
            now=dt(21, 3),
        )
        self.assertEqual(receipt["disposition"], "HOLD_DEADLINE")
        self.assertIn(
            "SPONSOR_OR_CAMPAIGN_DEADLINE_NOT_CURRENT",
            receipt["reason_codes"],
        )
        self.assertIn(
            "SPONSOR_DEADLINE_ELAPSED",
            receipt["deadline"]["current_reason_codes"],
        )

    def test_historically_green_receipt_is_reaged_not_replayed(self) -> None:
        source = deadline(value="2026-09-19T21:01:30Z")
        self.assertEqual(source["disposition"], "DEADLINE_CURRENT")
        receipt = compile_deadline_activation(
            request(live(), source), now=dt(21, 2)
        )
        self.assertEqual(receipt["disposition"], "HOLD_DEADLINE")
        self.assertEqual(
            receipt["deadline"]["relation_at_composition"], "ELAPSED"
        )

    def test_stale_deadline_observation_holds_even_before_cutoff(self) -> None:
        source = deadline(
            value="2026-09-19T23:00:00Z",
            max_age=120,
            issue_observed_at="2026-09-19T20:59:00Z",
            deadline_observed_at="2026-09-19T20:59:00Z",
        )
        receipt = compile_deadline_activation(
            request(live(), source), now=dt(21, 3)
        )
        self.assertEqual(receipt["disposition"], "HOLD_DEADLINE")
        self.assertIn(
            "ISSUE_OBSERVATION_STALE",
            receipt["deadline"]["current_reason_codes"],
        )
        self.assertIn(
            "DEADLINE_OBSERVATION_STALE",
            receipt["deadline"]["current_reason_codes"],
        )

    def test_valid_owner_extension_remains_actionable_until_extended_cutoff(self) -> None:
        source = deadline(
            value="2026-09-19T20:30:00Z",
            extension_value="2026-09-19T23:30:00Z",
        )
        self.assertEqual(source["disposition"], "DEADLINE_CURRENT")
        receipt = compile_deadline_activation(
            request(live("IMPLEMENT_ASSIGNED_SCOPE"), source),
            now=dt(21, 4),
        )
        self.assertEqual(receipt["disposition"], "IMPLEMENT_ASSIGNED_SCOPE")
        self.assertEqual(receipt["deadline"]["extension_status"], "ACCEPTED")
        self.assertEqual(
            receipt["deadline"]["effective_value"], "2026-09-19T23:30:00Z"
        )

    def test_identity_mismatch_rejected(self) -> None:
        with self.assertRaisesRegex(
            GrantFoxDeadlineActivationInputError, "different issues"
        ):
            compile_deadline_activation(
                request(live(issue=42), deadline(issue=43)),
                now=dt(21, 1),
            )

    def test_tampered_deadline_receipt_rejected(self) -> None:
        source = deadline()
        with patch(
            "concierge.grantfox_deadline_activation.verify_deadline_receipt",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                GrantFoxDeadlineActivationInputError, "does not verify"
            ):
                compile_deadline_activation(
                    request(live(), source), now=dt(21, 1)
                )

    def test_upstream_hold_dominates_green_deadline(self) -> None:
        receipt = compile_deadline_activation(
            request(live("HOLD_ECONOMICS"), deadline()),
            now=dt(21, 1),
        )
        self.assertEqual(receipt["disposition"], "HOLD_UPSTREAM")
        self.assertIn(
            "GRANTFOX_LIVE_CASH_ACTIVATION_NOT_ACTIONABLE",
            receipt["reason_codes"],
        )

    def test_unknown_request_key_rejected(self) -> None:
        payload = request(live(), deadline())
        payload["authority_override"] = True
        with self.assertRaisesRegex(
            GrantFoxDeadlineActivationInputError, "request keys must be exact"
        ):
            compile_deadline_activation(payload, now=dt(21, 1))

    def test_composite_tamper_fails_verifier(self) -> None:
        receipt = compile_deadline_activation(
            request(live(), deadline()), now=dt(21, 1)
        )
        self.assertTrue(
            verify_deadline_activation_receipt(receipt, now=dt(21, 2))
        )
        tampered = copy.deepcopy(receipt)
        tampered["deadline"]["effective_value"] = "2099-01-01T00:00:00Z"
        self.assertFalse(
            verify_deadline_activation_receipt(tampered, now=dt(21, 2))
        )

    def test_rehashed_authority_escalation_still_fails(self) -> None:
        receipt = compile_deadline_activation(
            request(live(), deadline()), now=dt(21, 1)
        )
        forged = copy.deepcopy(receipt)
        forged["authority"]["submission_authority"] = True
        body = dict(forged)
        body.pop("receipt_sha256", None)
        forged["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        self.assertFalse(
            verify_deadline_activation_receipt(forged, now=dt(21, 2))
        )

    def test_composite_receipt_ages_out(self) -> None:
        receipt = compile_deadline_activation(
            request(live(), deadline()), now=dt(21, 1)
        )
        self.assertFalse(
            verify_deadline_activation_receipt(receipt, now=dt(21, 7))
        )

    def test_verifier_catches_deadline_crossing_while_receipt_is_fresh(self) -> None:
        source = deadline(value="2026-09-19T21:02:00Z")
        receipt = compile_deadline_activation(
            request(live(), source), now=dt(21, 1)
        )
        self.assertEqual(receipt["disposition"], "APPLY_ELIGIBLE")
        self.assertFalse(
            verify_deadline_activation_receipt(receipt, now=dt(21, 3))
        )

    def test_live_cash_child_must_verify_current_state(self) -> None:
        with patch(
            "concierge.grantfox_deadline_activation."
            "verify_live_cash_activation_receipt",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                GrantFoxDeadlineActivationInputError,
                "current canonical state",
            ):
                compile_deadline_activation(
                    request(live(), deadline()), now=dt(21, 1)
                )


if __name__ == "__main__":
    unittest.main()
