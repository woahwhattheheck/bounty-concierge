# SPDX-License-Identifier: MIT
from __future__ import annotations

import inspect
from tempfile import TemporaryDirectory
import unittest

from concierge.outbound_email_identity import (
    EMAIL_TOUCH_PROVIDER,
    EmailTouchIdentityError,
    acquire_email_touch,
    email_touch_operation_key,
    normalize_email_touch_identity,
)
from concierge.outbound_singlewriter import (
    LeaseBusy,
    OutboundSingleWriter,
    evaluate_shared_claim,
    normalize_identity,
)


class EmailTouchIdentityTests(unittest.TestCase):
    def test_case_aliases_converge_to_one_operation_key(self) -> None:
        first = normalize_email_touch_identity(
            recipient="Buyer@EXAMPLE.COM",
            offer_key="pilot:q4",
            action="Initial-Outreach",
        )
        second = normalize_email_touch_identity(
            recipient="buyer@example.com",
            offer_key="pilot:q4",
            action="initial-outreach",
        )
        self.assertEqual(first, second)
        self.assertEqual(first.provider, EMAIL_TOUCH_PROVIDER)
        self.assertEqual(first.destination, "buyer@example.com")
        self.assertEqual(first.thread, "offer:pilot:q4")
        self.assertEqual(first.operation, "initial-outreach")
        self.assertEqual(first.key, second.key)

    def test_idna_domain_aliases_converge(self) -> None:
        unicode_key = email_touch_operation_key(
            recipient="buyer@bücher.example",
            offer_key="paid-pilot",
            action="proposal",
        )
        ascii_key = email_touch_operation_key(
            recipient="buyer@xn--bcher-kva.example",
            offer_key="paid-pilot",
            action="proposal",
        )
        self.assertEqual(unicode_key, ascii_key)

    def test_identity_is_provider_neutral_for_email_transport(self) -> None:
        identity = normalize_email_touch_identity(
            recipient="buyer@example.com",
            offer_key="paid-pilot",
            action="proposal",
        )
        self.assertEqual(identity.provider, "email-outreach")
        self.assertNotIn("gmail", identity.canonical.values())

    def test_distinct_business_touch_dimensions_remain_distinct(self) -> None:
        baseline = email_touch_operation_key(
            recipient="buyer@example.com",
            offer_key="pilot-a",
            action="proposal",
        )
        self.assertNotEqual(
            baseline,
            email_touch_operation_key(
                recipient="other@example.com",
                offer_key="pilot-a",
                action="proposal",
            ),
        )
        self.assertNotEqual(
            baseline,
            email_touch_operation_key(
                recipient="buyer@example.com",
                offer_key="pilot-b",
                action="proposal",
            ),
        )
        self.assertNotEqual(
            baseline,
            email_touch_operation_key(
                recipient="buyer@example.com",
                offer_key="pilot-a",
                action="follow-up",
            ),
        )

    def test_generic_identity_remains_conservative(self) -> None:
        upper = normalize_identity(
            provider="gmail",
            destination="Buyer@EXAMPLE.COM",
            thread="lead-7",
            operation="proposal",
        )
        lower = normalize_identity(
            provider="gmail",
            destination="buyer@example.com",
            thread="lead-7",
            operation="proposal",
        )
        self.assertNotEqual(upper.key, lower.key)

    def test_local_lease_domain_collapses_email_aliases(self) -> None:
        with TemporaryDirectory() as root:
            guard = OutboundSingleWriter(root)
            first = acquire_email_touch(
                guard,
                recipient="Buyer@EXAMPLE.COM",
                offer_key="pilot:q4",
                action="Initial-Outreach",
                owner="seat-a",
            )
            self.assertEqual(first["operation_key"], email_touch_operation_key(
                recipient="buyer@example.com",
                offer_key="pilot:q4",
                action="initial-outreach",
            ))
            with self.assertRaises(LeaseBusy):
                acquire_email_touch(
                    guard,
                    recipient="buyer@example.com",
                    offer_key="pilot:q4",
                    action="initial-outreach",
                    owner="seat-b",
                )

    def test_production_wrapper_has_no_caller_clock_authority(self) -> None:
        self.assertNotIn("now", inspect.signature(acquire_email_touch).parameters)
        self.assertNotIn("as_of", inspect.signature(acquire_email_touch).parameters)
        with TemporaryDirectory() as root:
            guard = OutboundSingleWriter(root)
            acquire_email_touch(
                guard,
                recipient="buyer@example.com",
                offer_key="pilot:q4",
                action="proposal",
                owner="seat-a",
            )
            with self.assertRaises(TypeError):
                acquire_email_touch(
                    guard,
                    recipient="buyer@example.com",
                    offer_key="pilot:q4",
                    action="proposal",
                    owner="seat-b",
                    now="2099-01-01T00:00:00Z",  # type: ignore[call-arg]
                )
            with self.assertRaises(LeaseBusy):
                acquire_email_touch(
                    guard,
                    recipient="buyer@example.com",
                    offer_key="pilot:q4",
                    action="proposal",
                    owner="seat-b",
                )

    def test_shared_election_cannot_split_on_recipient_case(self) -> None:
        key_a = email_touch_operation_key(
            recipient="Buyer@EXAMPLE.COM",
            offer_key="pilot:q4",
            action="proposal",
        )
        key_b = email_touch_operation_key(
            recipient="buyer@example.com",
            offer_key="pilot:q4",
            action="PROPOSAL",
        )
        self.assertEqual(key_a, key_b)
        events = [
            {"operation_key": key_a, "kind": "CLAIM", "owner": "seat-a", "event_id": "claim-a", "order": 1},
            {"operation_key": key_b, "kind": "CLAIM", "owner": "seat-b", "event_id": "claim-b", "order": 2},
        ]
        first = evaluate_shared_claim(
            operation_key=key_a,
            owner="seat-a",
            claim_event_id="claim-a",
            events=events,
            snapshot_complete=True,
        )
        second = evaluate_shared_claim(
            operation_key=key_b,
            owner="seat-b",
            claim_event_id="claim-b",
            events=events,
            snapshot_complete=True,
        )
        self.assertTrue(first.authorized)
        self.assertFalse(second.authorized)
        self.assertEqual(second.winner_owner, "seat-a")

    def test_invalid_recipient_offer_and_action_fail_closed(self) -> None:
        cases = [
            {"recipient": " buyer@example.com", "offer_key": "pilot", "action": "proposal"},
            {"recipient": "buyer@example.com", "offer_key": "Pilot", "action": "proposal"},
            {"recipient": "buyer@example.com", "offer_key": "pilot", "action": " proposal"},
            {"recipient": "buyer@example.com", "offer_key": "pilot", "action": ""},
        ]
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(EmailTouchIdentityError):
                    normalize_email_touch_identity(**values)

    def test_guard_type_is_fail_closed(self) -> None:
        with self.assertRaises(EmailTouchIdentityError):
            acquire_email_touch(  # type: ignore[arg-type]
                object(),
                recipient="buyer@example.com",
                offer_key="pilot",
                action="proposal",
                owner="seat-a",
            )


if __name__ == "__main__":
    unittest.main()
