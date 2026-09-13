from __future__ import annotations

from datetime import datetime, timezone
import unittest

from concierge.outbound_dedupe import OutboundDedupeInputError, evaluate_outbound_dedupe, format_summary


NOW = datetime(2026, 9, 13, 9, 30, tzinfo=timezone.utc)


def request(*, complete=True, observations=None, recipient="Carrie.Rampp@FandM.edu", offer_key="banner-saas-parity-v1"):
    if observations is None:
        observations = []
    return {
        "recipient": recipient,
        "offer_key": offer_key,
        "provider_queries": [
            {
                "provider": "GMAIL",
                "complete": complete,
                "observations": observations,
            }
        ],
    }


def observation(
    *,
    message_id="1a099a8e5597f623",
    state="sent",
    recipient="carrie.rampp@fandm.edu",
    offer_key="banner-saas-parity-v1",
    observed_at="2026-09-13T07:26:17Z",
    **extra,
):
    value = {
        "message_id": message_id,
        "state": state,
        "recipient": recipient,
        "offer_key": offer_key,
        "observed_at": observed_at,
    }
    value.update(extra)
    return value


class OutboundDedupeTests(unittest.TestCase):
    def test_matching_provider_sent_receipt_is_hard_dnr(self):
        result = evaluate_outbound_dedupe(
            request(observations=[observation()]),
            now=NOW,
        )
        self.assertEqual(result["disposition"], "DNR")
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["reason_codes"], ["PROVIDER_SENT_MATCH"])
        self.assertEqual(result["recipient"], "carrie.rampp@fandm.edu")
        self.assertEqual(result["signals"]["matching_sent_count"], 1)
        evidence = result["signals"]["matching_sent_evidence"][0]
        self.assertEqual(evidence["provider"], "gmail")
        self.assertNotIn("1a099a8e5597f623", repr(result))

    def test_complete_provider_query_without_matching_sent_is_clear(self):
        result = evaluate_outbound_dedupe(
            request(
                observations=[
                    observation(recipient="someone.else@example.com"),
                    observation(message_id="draft-1", state="draft"),
                    observation(message_id="incoming-1", state="received"),
                ]
            ),
            now=NOW,
        )
        self.assertEqual(result["disposition"], "CLEAR")
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["signals"]["provider_observation_count"], 3)

    def test_same_recipient_different_offer_does_not_block(self):
        result = evaluate_outbound_dedupe(
            request(observations=[observation(offer_key="other-offer-v2")]),
            now=NOW,
        )
        self.assertTrue(result["dispatch"])

    def test_incomplete_provider_query_holds_even_with_zero_matches(self):
        result = evaluate_outbound_dedupe(request(complete=False), now=NOW)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["signals"]["incomplete_providers"], ["gmail"])

    def test_matching_sent_dominates_incomplete_other_provider(self):
        value = request(observations=[observation()])
        value["provider_queries"].append(
            {"provider": "outlook", "complete": False, "observations": []}
        )
        result = evaluate_outbound_dedupe(value, now=NOW)
        self.assertEqual(result["disposition"], "DNR")
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["signals"]["incomplete_providers"], ["outlook"])

    def test_duplicate_identical_provider_receipt_is_collapsed(self):
        item = observation()
        result = evaluate_outbound_dedupe(
            request(observations=[item, dict(item)]),
            now=NOW,
        )
        self.assertEqual(result["signals"]["provider_observation_count"], 1)
        self.assertEqual(result["signals"]["matching_sent_count"], 1)

    def test_conflicting_same_provider_message_id_fails_closed(self):
        first = observation()
        second = observation(recipient="other@example.com")
        with self.assertRaisesRegex(OutboundDedupeInputError, "conflicting observations"):
            evaluate_outbound_dedupe(
                request(observations=[first, second]),
                now=NOW,
            )

    def test_raw_correspondence_fields_are_rejected(self):
        with self.assertRaisesRegex(OutboundDedupeInputError, "raw correspondence"):
            evaluate_outbound_dedupe(
                request(observations=[observation(subject="Parity proof")]),
                now=NOW,
            )

    def test_future_provider_timestamp_fails_closed(self):
        with self.assertRaisesRegex(OutboundDedupeInputError, "must not be in the future"):
            evaluate_outbound_dedupe(
                request(
                    observations=[
                        observation(observed_at="2026-09-13T09:36:00Z")
                    ]
                ),
                now=NOW,
            )

    def test_duplicate_provider_query_fails_closed(self):
        value = request()
        value["provider_queries"].append(
            {"provider": "gmail", "complete": True, "observations": []}
        )
        with self.assertRaisesRegex(OutboundDedupeInputError, "at most once"):
            evaluate_outbound_dedupe(value, now=NOW)

    def test_offer_key_is_deliberately_lowercase_stable_identity(self):
        with self.assertRaisesRegex(OutboundDedupeInputError, "lowercase stable key"):
            evaluate_outbound_dedupe(
                request(offer_key="Banner-SaaS-Parity"),
                now=NOW,
            )

    def test_summary_contains_no_provider_message_id(self):
        result = evaluate_outbound_dedupe(
            request(observations=[observation()]),
            now=NOW,
        )
        summary = format_summary(result)
        self.assertIn("disposition=DNR", summary)
        self.assertNotIn("1a099a8e5597f623", summary)


if __name__ == "__main__":
    unittest.main()
