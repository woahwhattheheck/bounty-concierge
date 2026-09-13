import copy
import json
import tempfile
import unittest
from pathlib import Path

from concierge.feedback_remediation import (
    ADVANCE_SCHEMA,
    INPUT_SCHEMA,
    FeedbackRemediationError,
    advance_remediation,
    compile_remediation,
    load_json,
    strict_json_loads,
    verify_chain,
    verify_packet,
)


class FeedbackRemediationTests(unittest.TestCase):
    def submission(self, *, revision=1, head="a", artifact="b", submission_id="SUB-42-v1"):
        return {
            "submission_id": submission_id,
            "canonical_source": "github:acme/widgets#42",
            "submitted_head_sha": head * 40,
            "artifact_revision": revision,
            "artifact_evidence_digest": artifact * 64,
            "submission_digest": "c" * 64,
        }

    def event(
        self,
        *,
        event_id="review-101",
        revision=1,
        head="a",
        evidence="d",
        disposition="REQUIRED",
        kind="CHANGES_REQUESTED",
        summary="Add a regression test for the null response.",
        hints=None,
    ):
        return {
            "source_event_id": event_id,
            "authority": "GITHUB_MEMBER",
            "kind": kind,
            "occurred_at": "2026-09-13T14:20:00Z",
            "evidence_digest": evidence * 64,
            "summary": summary,
            "disposition": disposition,
            "target_head_sha": head * 40,
            "target_artifact_revision": revision,
            "hints": hints if hints is not None else [
                {"path": "src/client.py", "line_start": 40, "line_end": 45}
            ],
        }

    def compile_input(self, events=None):
        return {
            "schema": INPUT_SCHEMA,
            "submission": self.submission(),
            "feedback": events if events is not None else [self.event()],
        }

    def successor(self, *, revision=2, head="e", artifact="f", submission_id="SUB-42-v2"):
        return self.submission(
            revision=revision,
            head=head,
            artifact=artifact,
            submission_id=submission_id,
        )

    def advance_input(self, prior, *, addressed=None, new_feedback=None, successor=None):
        return {
            "schema": ADVANCE_SCHEMA,
            "prior_packet": prior,
            "successor_submission": successor if successor is not None else self.successor(),
            "addressed": addressed if addressed is not None else [],
            "new_feedback": new_feedback if new_feedback is not None else [],
        }

    def test_compile_binds_explicit_feedback(self):
        packet = compile_remediation(self.compile_input())
        self.assertEqual(packet["generation"], 1)
        self.assertIsNone(packet["predecessor_packet_digest"])
        self.assertEqual(packet["counts"]["open_required"], 1)
        obligation = packet["obligations"][0]
        self.assertEqual(obligation["state"], "OPEN")
        self.assertTrue(obligation["blocking"])
        self.assertEqual(obligation["target_head_sha"], "a" * 40)
        self.assertFalse(packet["authority"]["acceptance_inferred"])
        self.assertFalse(packet["authority"]["revenue_recognized"])
        self.assertEqual(verify_packet(packet), packet)

    def test_deterministic_event_order(self):
        first = self.event(event_id="z-event", evidence="d")
        first["occurred_at"] = "2026-09-13T14:21:00Z"
        second = self.event(event_id="a-event", evidence="e")
        second["occurred_at"] = "2026-09-13T14:20:00+00:00"
        p1 = compile_remediation(self.compile_input([first, second]))
        p2 = compile_remediation(self.compile_input([second, first]))
        self.assertEqual(p1, p2)
        self.assertEqual(
            [item["source_event_id"] for item in p1["obligations"]],
            ["a-event", "z-event"],
        )

    def test_exact_duplicate_event_is_idempotent(self):
        event = self.event()
        packet = compile_remediation(self.compile_input([event, copy.deepcopy(event)]))
        self.assertEqual(packet["counts"]["total"], 1)

    def test_conflicting_duplicate_event_fails(self):
        event = self.event()
        changed = copy.deepcopy(event)
        changed["summary"] = "Different requested change"
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(self.compile_input([event, changed]))

    def test_feedback_must_target_exact_head(self):
        event = self.event(head="e")
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(self.compile_input([event]))

    def test_feedback_must_target_exact_revision(self):
        event = self.event(revision=2)
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(self.compile_input([event]))

    def test_authority_is_explicit_enum(self):
        value = self.compile_input()
        value["feedback"][0]["authority"] = "RANDOM_USER"
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)

    def test_kind_is_explicit_enum(self):
        value = self.compile_input()
        value["feedback"][0]["kind"] = "LOOKS_BAD"
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)

    def test_disposition_is_not_inferred(self):
        value = self.compile_input()
        value["feedback"][0]["disposition"] = "MODEL_DECIDES"
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)

    def test_non_blocking_is_preserved_as_non_blocking(self):
        packet = compile_remediation(
            self.compile_input([self.event(disposition="NON_BLOCKING")])
        )
        self.assertFalse(packet["obligations"][0]["blocking"])
        self.assertEqual(packet["counts"]["open_non_blocking"], 1)

    def test_clarify_is_preserved(self):
        packet = compile_remediation(
            self.compile_input([self.event(disposition="CLARIFY")])
        )
        self.assertEqual(packet["counts"]["open_clarify"], 1)

    def test_hint_path_traversal_rejected(self):
        value = self.compile_input()
        value["feedback"][0]["hints"] = [
            {"path": "../secret", "line_start": 1, "line_end": 1}
        ]
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)

    def test_hint_line_range_rejected(self):
        value = self.compile_input()
        value["feedback"][0]["hints"] = [
            {"path": "src/x.py", "line_start": 9, "line_end": 2}
        ]
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)

    def test_tamper_digest_rejected(self):
        packet = compile_remediation(self.compile_input())
        packet["obligations"][0]["summary"] = "tampered"
        with self.assertRaises(FeedbackRemediationError):
            verify_packet(packet)

    def test_authority_escalation_rejected_even_if_digest_rehashed(self):
        packet = compile_remediation(self.compile_input())
        packet["authority"]["payment_inferred"] = True
        packet["packet_digest"] = "0" * 64
        with self.assertRaises(FeedbackRemediationError):
            verify_packet(packet)

    def test_advance_carries_unresolved_obligation(self):
        prior = compile_remediation(self.compile_input())
        successor = advance_remediation(self.advance_input(prior))
        self.assertEqual(successor["generation"], 2)
        self.assertEqual(successor["predecessor_packet_digest"], prior["packet_digest"])
        self.assertEqual(successor["counts"]["open"], 1)
        self.assertEqual(successor["obligations"][0]["state"], "OPEN")
        self.assertEqual(verify_chain([prior, successor]), [prior, successor])

    def test_advance_address_requires_evidence_and_preserves_history(self):
        prior = compile_remediation(self.compile_input())
        obligation_id = prior["obligations"][0]["obligation_id"]
        addressed = [{
            "obligation_id": obligation_id,
            "evidence_digest": "1" * 64,
            "note": "Regression test and null guard added.",
        }]
        successor = advance_remediation(self.advance_input(prior, addressed=addressed))
        obligation = successor["obligations"][0]
        self.assertEqual(obligation["state"], "ADDRESSED")
        self.assertEqual(obligation["resolution"]["successor_head_sha"], "e" * 40)
        self.assertEqual(obligation["resolution"]["successor_artifact_revision"], 2)
        self.assertEqual(successor["counts"]["addressed"], 1)
        self.assertEqual(successor["counts"]["open"], 0)

    def test_unknown_obligation_cannot_be_addressed(self):
        prior = compile_remediation(self.compile_input())
        addressed = [{
            "obligation_id": "obl-000000000000000000000000",
            "evidence_digest": "1" * 64,
            "note": None,
        }]
        with self.assertRaises(FeedbackRemediationError):
            advance_remediation(self.advance_input(prior, addressed=addressed))

    def test_obligation_cannot_be_addressed_twice(self):
        prior = compile_remediation(self.compile_input())
        obligation_id = prior["obligations"][0]["obligation_id"]
        addressed = [{
            "obligation_id": obligation_id,
            "evidence_digest": "1" * 64,
            "note": None,
        }]
        successor = advance_remediation(self.advance_input(prior, addressed=addressed))
        third = self.successor(revision=3, head="2", artifact="3", submission_id="SUB-42-v3")
        with self.assertRaises(FeedbackRemediationError):
            advance_remediation(self.advance_input(successor, addressed=addressed, successor=third))

    def test_successor_revision_must_advance(self):
        prior = compile_remediation(self.compile_input())
        same_revision = self.successor(revision=1)
        with self.assertRaises(FeedbackRemediationError):
            advance_remediation(self.advance_input(prior, successor=same_revision))

    def test_successor_head_must_change(self):
        prior = compile_remediation(self.compile_input())
        same_head = self.successor(head="a")
        with self.assertRaises(FeedbackRemediationError):
            advance_remediation(self.advance_input(prior, successor=same_head))

    def test_successor_artifact_evidence_must_change(self):
        prior = compile_remediation(self.compile_input())
        same_artifact = self.successor(artifact="b")
        with self.assertRaises(FeedbackRemediationError):
            advance_remediation(self.advance_input(prior, successor=same_artifact))

    def test_canonical_source_cannot_drift(self):
        prior = compile_remediation(self.compile_input())
        successor = self.successor()
        successor["canonical_source"] = "github:evil/other#9"
        with self.assertRaises(FeedbackRemediationError):
            advance_remediation(self.advance_input(prior, successor=successor))

    def test_new_feedback_targets_successor_and_is_added(self):
        prior = compile_remediation(self.compile_input())
        new_event = self.event(
            event_id="inline-202",
            revision=2,
            head="e",
            evidence="4",
            kind="ACTIONABLE_COMMENT",
            disposition="CLARIFY",
            summary="Clarify whether the timeout is configurable.",
        )
        successor = advance_remediation(self.advance_input(prior, new_feedback=[new_event]))
        self.assertEqual(successor["counts"]["total"], 2)
        self.assertEqual(successor["counts"]["open_required"], 1)
        self.assertEqual(successor["counts"]["open_clarify"], 1)

    def test_source_event_id_cannot_be_rebound_in_successor(self):
        prior = compile_remediation(self.compile_input())
        rebound = self.event(event_id="review-101", revision=2, head="e", evidence="5")
        with self.assertRaises(FeedbackRemediationError):
            advance_remediation(self.advance_input(prior, new_feedback=[rebound]))

    def test_chain_detects_wrong_predecessor(self):
        prior = compile_remediation(self.compile_input())
        successor = advance_remediation(self.advance_input(prior))
        successor["predecessor_packet_digest"] = "9" * 64
        cloned = copy.deepcopy(successor)
        cloned["packet_digest"] = ""
        import hashlib
        encoded = json.dumps(
            cloned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        successor["packet_digest"] = hashlib.sha256(encoded).hexdigest()
        self.assertEqual(verify_packet(successor), successor)
        with self.assertRaises(FeedbackRemediationError):
            verify_chain([prior, successor])

    def test_packet_cannot_claim_future_obligation(self):
        prior = compile_remediation(self.compile_input())
        tampered = copy.deepcopy(prior)
        obligation = tampered["obligations"][0]
        obligation["target_artifact_revision"] = 2
        with self.assertRaises(FeedbackRemediationError):
            verify_packet(tampered)

    def test_naive_time_rejected(self):
        value = self.compile_input()
        value["feedback"][0]["occurred_at"] = "2026-09-13T14:20:00"
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)

    def test_timezone_normalizes_to_utc(self):
        value = self.compile_input()
        value["feedback"][0]["occurred_at"] = "2026-09-13T10:20:00-04:00"
        packet = compile_remediation(value)
        self.assertEqual(packet["obligations"][0]["occurred_at"], "2026-09-13T14:20:00Z")

    def test_strict_json_rejects_duplicate_keys(self):
        with self.assertRaises(FeedbackRemediationError):
            strict_json_loads('{"schema":"x","schema":"y"}')

    def test_strict_json_rejects_nan(self):
        with self.assertRaises(FeedbackRemediationError):
            strict_json_loads('{"x":NaN}')

    def test_strict_json_rejects_floats(self):
        with self.assertRaises(FeedbackRemediationError):
            strict_json_loads('{"x":1.5}')

    def test_strict_json_rejects_oversize_input(self):
        with self.assertRaises(FeedbackRemediationError):
            strict_json_loads('{"x":"' + ("a" * 1_000_001) + '"}')

    def test_load_json_round_trip(self):
        value = self.compile_input()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(load_json(str(path)), value)

    def test_input_rejects_unknown_keys(self):
        value = self.compile_input()
        value["unexpected"] = True
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)

    def test_submission_digest_may_be_null_but_not_malformed(self):
        value = self.compile_input()
        value["submission"]["submission_digest"] = None
        packet = compile_remediation(value)
        self.assertIsNone(packet["submission"]["submission_digest"])
        value["submission"]["submission_digest"] = "bad"
        with self.assertRaises(FeedbackRemediationError):
            compile_remediation(value)


if __name__ == "__main__":
    unittest.main()
