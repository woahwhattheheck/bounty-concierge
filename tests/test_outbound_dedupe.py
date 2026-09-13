from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "outbound_dedupe_v2", HERE / "concierge" / "outbound_dedupe.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

NOW = datetime(2026, 9, 13, 10, 30, 0, tzinfo=timezone.utc)
RECIPIENT = "buyer@example.com"
OFFER = "agent-rescue-v2"


def observation(
    *,
    state="sent",
    message_id="msg-1",
    recipient=RECIPIENT,
    offer_key=OFFER,
    observed_at="2026-09-13T10:28:30Z",
):
    return {
        "state": state,
        "message_id": message_id,
        "recipient": recipient,
        "offer_key": offer_key,
        "observed_at": observed_at,
    }


def query(
    provider,
    *,
    recipient=RECIPIENT,
    offer_key=OFFER,
    complete=True,
    completed_at="2026-09-13T10:29:00Z",
    observations=None,
):
    return {
        "provider": provider,
        "recipient": recipient,
        "offer_key": offer_key,
        "complete": complete,
        "completed_at": completed_at,
        "observations": [] if observations is None else observations,
    }


def request(*queries, recipient=RECIPIENT, offer_key=OFFER):
    return {
        "recipient": recipient,
        "offer_key": offer_key,
        "provider_queries": list(queries),
    }


class OutboundDedupeV2Tests(unittest.TestCase):
    def evaluate(self, payload, required=("gmail",), **kwargs):
        return module.evaluate_outbound_dedupe(
            payload,
            required_providers=required,
            now=NOW,
            **kwargs,
        )

    def test_clear_requires_exact_complete_fresh_required_provider_set(self):
        result = self.evaluate(request(query("gmail")))
        self.assertEqual(result["disposition"], "CLEAR")
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["reason_codes"], ["NO_PROVIDER_SENT_MATCH"])
        self.assertEqual(result["signals"]["required_providers"], ["gmail"])
        self.assertEqual(result["signals"]["queried_providers"], ["gmail"])

    def test_missing_required_provider_holds(self):
        result = self.evaluate(request(query("gmail")), required=("gmail", "outlook"))
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["signals"]["missing_providers"], ["outlook"])
        self.assertIn("REQUIRED_PROVIDER_MISSING", result["reason_codes"])

    def test_incomplete_required_provider_holds(self):
        result = self.evaluate(request(query("gmail", complete=False)))
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("PROVIDER_QUERY_INCOMPLETE", result["reason_codes"])

    def test_stale_required_provider_holds(self):
        result = self.evaluate(request(query("gmail", completed_at="2026-09-13T10:24:59Z")))
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("PROVIDER_QUERY_STALE", result["reason_codes"])

    def test_query_exactly_at_age_boundary_is_fresh(self):
        result = self.evaluate(request(query("gmail", completed_at="2026-09-13T10:25:00Z")))
        self.assertEqual(result["disposition"], "CLEAR")

    def test_query_one_second_past_age_boundary_is_stale(self):
        result = self.evaluate(request(query("gmail", completed_at="2026-09-13T10:24:59Z")))
        self.assertEqual(result["disposition"], "HOLD")
        self.assertEqual(result["signals"]["stale_providers"], ["gmail"])

    def test_sent_match_returns_dnr(self):
        result = self.evaluate(request(query("gmail", observations=[observation()])))
        self.assertEqual(result["disposition"], "DNR")
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["reason_codes"], ["PROVIDER_SENT_MATCH"])
        self.assertEqual(result["signals"]["matching_sent_count"], 1)

    def test_dnr_wins_over_missing_sibling_provider(self):
        result = self.evaluate(
            request(query("gmail", observations=[observation()])),
            required=("gmail", "outlook"),
        )
        self.assertEqual(result["disposition"], "DNR")
        self.assertEqual(result["signals"]["missing_providers"], ["outlook"])

    def test_dnr_wins_over_incomplete_sibling_provider(self):
        result = self.evaluate(
            request(
                query("gmail", observations=[observation()]),
                query("outlook", complete=False),
            ),
            required=("gmail", "outlook"),
        )
        self.assertEqual(result["disposition"], "DNR")
        self.assertEqual(result["signals"]["incomplete_providers"], ["outlook"])

    def test_dnr_wins_over_stale_sibling_provider(self):
        result = self.evaluate(
            request(
                query("gmail", observations=[observation()]),
                query("outlook", completed_at="2026-09-13T10:00:00Z"),
            ),
            required=("gmail", "outlook"),
        )
        self.assertEqual(result["disposition"], "DNR")
        self.assertEqual(result["signals"]["stale_providers"], ["outlook"])

    def test_unexpected_dummy_provider_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("dummy")))

    def test_duplicate_provider_query_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail"), query("gmail")))

    def test_query_target_recipient_mismatch_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail", recipient="other@example.com")))

    def test_query_target_offer_mismatch_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail", offer_key="different-v1")))

    def test_observation_target_recipient_mismatch_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail", observations=[observation(recipient="other@example.com")])))

    def test_observation_target_offer_mismatch_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail", observations=[observation(offer_key="different-v1")])))

    def test_observation_after_query_completion_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query(
                "gmail",
                completed_at="2026-09-13T10:29:00Z",
                observations=[observation(observed_at="2026-09-13T10:29:01Z")],
            )))

    def test_future_query_timestamp_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail", completed_at="2026-09-13T10:30:01Z")))

    def test_future_observation_timestamp_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query(
                "gmail",
                completed_at="2026-09-13T10:30:00Z",
                observations=[observation(observed_at="2026-09-13T10:30:01Z")],
            )))

    def test_request_extra_field_rejected(self):
        payload = request(query("gmail"))
        payload["note"] = "raw text should not be accepted"
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(payload)

    def test_query_arbitrary_extra_field_rejected(self):
        q = query("gmail")
        q["body"] = "correspondence"
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(q))

    def test_observation_arbitrary_extra_field_rejected(self):
        obs = observation()
        obs["snippet"] = "correspondence"
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail", observations=[obs])))

    def test_conflicting_duplicate_message_id_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail", observations=[
                observation(message_id="same", state="sent"),
                observation(message_id="same", state="draft"),
            ])))

    def test_exact_duplicate_observation_is_deduplicated(self):
        obs = observation()
        result = self.evaluate(request(query("gmail", observations=[obs, copy.deepcopy(obs)])))
        self.assertEqual(result["disposition"], "DNR")
        self.assertEqual(result["signals"]["provider_observation_count"], 1)
        self.assertEqual(result["signals"]["matching_sent_count"], 1)

    def test_draft_and_received_do_not_block(self):
        result = self.evaluate(request(query("gmail", observations=[
            observation(message_id="draft-1", state="draft"),
            observation(message_id="recv-1", state="received"),
        ])))
        self.assertEqual(result["disposition"], "CLEAR")

    def test_email_domain_and_local_case_canonicalize_to_same_identity(self):
        result_a = self.evaluate(request(
            query("gmail", recipient="Buyer@EXAMPLE.com"),
            recipient="Buyer@EXAMPLE.com",
        ))
        result_b = self.evaluate(request(query("gmail"), recipient="buyer@example.com"))
        self.assertEqual(result_a["recipient_id"], result_b["recipient_id"])

    def test_uppercase_provider_id_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("GMAIL")))

    def test_offer_key_uppercase_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(
                query("gmail", offer_key="Agent-Rescue-V2"),
                offer_key="Agent-Rescue-V2",
            ))

    def test_duplicate_required_provider_inventory_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail")), required=("gmail", "gmail"))

    def test_empty_required_provider_inventory_rejected(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(), required=())

    def test_required_provider_string_is_not_treated_as_iterable_inventory(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail")), required="gmail")

    def test_bad_query_age_policy_rejects_bool(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail")), max_query_age_seconds=True)

    def test_bad_query_age_policy_rejects_too_small(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail")), max_query_age_seconds=29)

    def test_bad_query_age_policy_rejects_too_large(self):
        with self.assertRaises(module.OutboundDedupeInputError):
            self.evaluate(request(query("gmail")), max_query_age_seconds=3601)

    def test_receipt_does_not_emit_raw_recipient_or_message_id(self):
        result = self.evaluate(request(query(
            "gmail",
            observations=[observation(message_id="secret-provider-id")],
        )))
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(RECIPIENT, serialized)
        self.assertNotIn("secret-provider-id", serialized)
        self.assertEqual(result["authority"]["provider_message_ids_retained"], False)
        self.assertEqual(result["authority"]["raw_correspondence_retained"], False)

    def test_matching_evidence_order_is_deterministic(self):
        result = self.evaluate(
            request(
                query("gmail", observations=[
                    observation(message_id="later", observed_at="2026-09-13T10:28:59Z"),
                    observation(message_id="earlier", observed_at="2026-09-13T10:27:00Z"),
                ]),
                query("outlook", observations=[
                    observation(message_id="outlook-mid", observed_at="2026-09-13T10:28:00Z")
                ]),
            ),
            required=("outlook", "gmail"),
        )
        evidence = result["signals"]["matching_sent_evidence"]
        self.assertEqual(
            [row["observed_at"] for row in evidence],
            ["2026-09-13T10:27:00Z", "2026-09-13T10:28:00Z", "2026-09-13T10:28:59Z"],
        )
        self.assertEqual(result["signals"]["required_providers"], ["gmail", "outlook"])

    def test_strict_json_loader_rejects_duplicate_top_level_key(self):
        raw = ('{"recipient":"buyer@example.com",'
               '"recipient":"other@example.com",'
               '"offer_key":"agent-rescue-v2","provider_queries":[]}')
        with self.assertRaises(module.OutboundDedupeInputError):
            module._strict_json_loads(raw)

    def test_strict_json_loader_rejects_duplicate_nested_key(self):
        raw = ('{"recipient":"buyer@example.com","offer_key":"agent-rescue-v2",'
               '"provider_queries":[{"provider":"gmail","provider":"outlook",'
               '"recipient":"buyer@example.com","offer_key":"agent-rescue-v2",'
               '"complete":true,"completed_at":"2026-09-13T10:29:00Z",'
               '"observations":[]}]}')
        with self.assertRaises(module.OutboundDedupeInputError):
            module._strict_json_loads(raw)

    def test_cli_load_request_uses_duplicate_key_rejection(self):
        raw = ('{"recipient":"buyer@example.com","offer_key":"agent-rescue-v2",'
               '"offer_key":"other-v1","provider_queries":[]}')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "request.json"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(module.OutboundDedupeInputError):
                module._load_request(str(path))

    def test_provider_evidence_digest_is_order_independent(self):
        q_gmail = query("gmail", observations=[
            observation(message_id="b", observed_at="2026-09-13T10:28:20Z"),
            observation(message_id="a", observed_at="2026-09-13T10:28:10Z"),
        ])
        q_outlook = query("outlook", observations=[
            observation(message_id="c", state="draft", observed_at="2026-09-13T10:28:30Z")
        ])
        first = self.evaluate(request(q_gmail, q_outlook), required=("gmail", "outlook"))
        q_gmail_reordered = copy.deepcopy(q_gmail)
        q_gmail_reordered["observations"].reverse()
        second = self.evaluate(request(q_outlook, q_gmail_reordered), required=("outlook", "gmail"))
        self.assertEqual(first["provider_evidence_sha256"], second["provider_evidence_sha256"])
        self.assertEqual(first["receipt_sha256"], second["receipt_sha256"])

    def test_provider_evidence_digest_changes_when_provider_identity_changes(self):
        first = self.evaluate(request(query("gmail", observations=[observation(message_id="msg-a")])))
        second = self.evaluate(request(query("gmail", observations=[observation(message_id="msg-b")])))
        self.assertNotEqual(first["provider_evidence_sha256"], second["provider_evidence_sha256"])

    def test_receipt_digest_matches_exact_receipt_core(self):
        import hashlib
        result = self.evaluate(request(query("gmail")))
        expected = result["receipt_sha256"]
        core = {key: value for key, value in result.items() if key != "receipt_sha256"}
        encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), expected)
        self.assertFalse(result["authority"]["integrity_digest_is_external_authenticity"])

    def test_summary_uses_recipient_digest_not_email(self):
        result = self.evaluate(request(query("gmail")))
        summary = module.format_summary(result)
        self.assertNotIn(RECIPIENT, summary)
        self.assertIn(result["recipient_id"], summary)


if __name__ == "__main__":
    unittest.main()
