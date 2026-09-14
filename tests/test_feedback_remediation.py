from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import concierge.feedback_remediation as fr


H1 = "1" * 40
H2 = "2" * 40
H3 = "3" * 40
E1 = "a" * 64
E2 = "b" * 64
E3 = "c" * 64
F1 = "d" * 64
F2 = "e" * 64


def submission(rev=1, head=H1, evidence=E1):
    return {
        "submission_id": "submission-%d" % rev,
        "canonical_source": "github:acme/widgets#17",
        "submitted_head_sha": head,
        "artifact_revision": rev,
        "artifact_evidence_digest": evidence,
        "submission_digest": None,
    }


def feedback(sub=None, event_id="review-1", evidence=F1, disposition="REQUIRED", summary="Fix the edge case"):
    sub = sub or submission()
    return {
        "source_event_id": event_id,
        "authority": "GITHUB_OWNER",
        "kind": "CHANGES_REQUESTED",
        "occurred_at": "2026-09-13T14:00:00Z",
        "evidence_digest": evidence,
        "summary": summary,
        "disposition": disposition,
        "target_head_sha": sub["submitted_head_sha"],
        "target_artifact_revision": sub["artifact_revision"],
        "hints": [{"path": "concierge/a.py", "line_start": 10, "line_end": 12}],
    }


def compile_one():
    sub = submission()
    return fr.compile_remediation({"schema": fr.INPUT_SCHEMA, "submission": sub, "feedback": [feedback(sub)]})


def advance_input(chain, successor=None, addressed=None, new_feedback=None):
    successor = successor or submission(2, H2, E2)
    return {
        "schema": fr.ADVANCE_SCHEMA,
        "prior_chain": copy.deepcopy(chain),
        "successor_submission": successor,
        "addressed": addressed or [],
        "new_feedback": new_feedback or [],
    }


def reseal(packet):
    packet = copy.deepcopy(packet)
    packet["counts"] = fr.counts(packet["obligations"])
    packet["packet_digest"] = ""
    packet["packet_digest"] = fr.digest(packet)
    return packet


class FeedbackRemediationTests(unittest.TestCase):
    def test_compile_emits_sealed_generation_one(self):
        packet = compile_one()
        self.assertEqual(packet["schema"], fr.PACKET_SCHEMA)
        self.assertEqual(packet["generation"], 1)
        self.assertIsNone(packet["predecessor_packet_digest"])
        self.assertEqual(packet["counts"]["open_required"], 1)
        self.assertEqual(fr.verify_packet(packet)["packet_digest"], packet["packet_digest"])

    def test_authority_ceiling_is_fail_closed(self):
        packet = compile_one()
        for key, value in packet["authority"].items():
            if key == "chain_head_is_external_authority":
                self.assertTrue(value)
            else:
                self.assertFalse(value)

    def test_compile_requires_explicit_feedback(self):
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.compile_remediation({"schema": fr.INPUT_SCHEMA, "submission": submission(), "feedback": []})

    def test_event_must_match_submission_head_and_revision(self):
        item = feedback(submission())
        item["target_head_sha"] = H2
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.compile_remediation({"schema": fr.INPUT_SCHEMA, "submission": submission(), "feedback": [item]})

    def test_conflicting_duplicate_source_event_id_fails(self):
        sub = submission()
        first = feedback(sub, summary="one")
        second = feedback(sub, summary="two")
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.compile_remediation({"schema": fr.INPUT_SCHEMA, "submission": sub, "feedback": [first, second]})

    def test_exact_duplicate_source_event_is_idempotent(self):
        sub = submission()
        event = feedback(sub)
        packet = fr.compile_remediation({"schema": fr.INPUT_SCHEMA, "submission": sub, "feedback": [event, copy.deepcopy(event)]})
        self.assertEqual(packet["counts"]["total"], 1)

    def test_path_traversal_hint_is_rejected(self):
        sub = submission()
        event = feedback(sub)
        event["hints"][0]["path"] = "../secret"
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.compile_remediation({"schema": fr.INPUT_SCHEMA, "submission": sub, "feedback": [event]})

    def test_packet_tamper_breaks_integrity(self):
        packet = compile_one()
        packet["counts"]["open"] = 0
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.verify_packet(packet)

    def test_valid_successor_without_resolution_verifies_as_chain(self):
        g1 = compile_one()
        g2 = fr.advance_remediation(advance_input([g1]), g1["packet_digest"])
        chain = fr.verify_chain([g1, g2], g2["packet_digest"])
        self.assertEqual([p["generation"] for p in chain], [1, 2])
        self.assertEqual(chain[-1]["counts"]["open_required"], 1)

    def test_addressing_requires_successor_artifact_evidence_digest(self):
        g1 = compile_one()
        oid = g1["obligations"][0]["obligation_id"]
        bad = [{"obligation_id": oid, "successor_artifact_evidence_digest": "f" * 64, "note": "fixed"}]
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "not the successor artifact evidence digest"):
            fr.advance_remediation(advance_input([g1], addressed=bad), g1["packet_digest"])

    def test_addressing_with_successor_artifact_manifest_digest_passes(self):
        g1 = compile_one()
        oid = g1["obligations"][0]["obligation_id"]
        addressed = [{"obligation_id": oid, "successor_artifact_evidence_digest": E2, "note": "covered by successor manifest"}]
        g2 = fr.advance_remediation(advance_input([g1], addressed=addressed), g1["packet_digest"])
        self.assertEqual(g2["obligations"][0]["state"], "ADDRESSED")
        self.assertEqual(g2["obligations"][0]["resolution"]["successor_artifact_evidence_digest"], E2)
        fr.verify_chain([g1, g2], g2["packet_digest"])

    def test_unrelated_successor_manifest_digest_cannot_close_obligation(self):
        g1 = compile_one()
        oid = g1["obligations"][0]["obligation_id"]
        addressed = [{"obligation_id": oid, "successor_artifact_evidence_digest": E3, "note": None}]
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.advance_remediation(advance_input([g1], successor=submission(2, H2, E2), addressed=addressed), g1["packet_digest"])

    def test_readdressing_already_addressed_obligation_fails(self):
        g1 = compile_one()
        oid = g1["obligations"][0]["obligation_id"]
        addressed = [{"obligation_id": oid, "successor_artifact_evidence_digest": E2, "note": None}]
        g2 = fr.advance_remediation(advance_input([g1], addressed=addressed), g1["packet_digest"])
        again = [{"obligation_id": oid, "successor_artifact_evidence_digest": E3, "note": None}]
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.advance_remediation(advance_input([g1, g2], submission(3, H3, E3), again), g2["packet_digest"])

    def test_successor_head_must_change(self):
        g1 = compile_one()
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.advance_remediation(advance_input([g1], submission(2, H1, E2)), g1["packet_digest"])

    def test_successor_artifact_evidence_must_change(self):
        g1 = compile_one()
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.advance_remediation(advance_input([g1], submission(2, H2, E1)), g1["packet_digest"])

    def test_expected_prior_head_digest_is_required_authority(self):
        g1 = compile_one()
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "independently retained expected digest"):
            fr.advance_remediation(advance_input([g1]), "0" * 64)

    def test_new_feedback_must_bind_successor_submission(self):
        g1 = compile_one()
        stale_event = feedback(submission(), event_id="review-2", evidence=F2)
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.advance_remediation(advance_input([g1], new_feedback=[stale_event]), g1["packet_digest"])

    def test_new_feedback_is_added_open(self):
        g1 = compile_one()
        sub2 = submission(2, H2, E2)
        event2 = feedback(sub2, event_id="review-2", evidence=F2, disposition="CLARIFY")
        g2 = fr.advance_remediation(advance_input([g1], sub2, new_feedback=[event2]), g1["packet_digest"])
        self.assertEqual(g2["counts"]["total"], 2)
        self.assertEqual(g2["counts"]["open"], 2)
        fr.verify_chain([g1, g2], g2["packet_digest"])

    def test_current_revision_resolution_digest_must_equal_submission_manifest(self):
        g1 = compile_one()
        oid = g1["obligations"][0]["obligation_id"]
        addressed = [{"obligation_id": oid, "successor_artifact_evidence_digest": E2, "note": None}]
        g2 = fr.advance_remediation(advance_input([g1], addressed=addressed), g1["packet_digest"])
        forged = copy.deepcopy(g2)
        forged["obligations"][0]["resolution"]["successor_artifact_evidence_digest"] = E3
        forged = reseal(forged)
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "resolution evidence differs"):
            fr.verify_packet(forged)

    def test_strict_json_rejects_duplicate_keys(self):
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.strict_json_loads('{"a":1,"a":2}')

    def test_strict_json_rejects_float_and_nonfinite(self):
        for raw in ('{"a":1.5}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(fr.FeedbackRemediationError):
                fr.strict_json_loads(raw)

    def test_load_json_rejects_oversized_file_with_bounded_read(self):
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(b"x" * (fr.MAX_JSON_BYTES + 1))
            with self.assertRaisesRegex(fr.FeedbackRemediationError, "byte limit"):
                fr.load_json(path)
        finally:
            os.unlink(path)

    def test_verify_cli_requires_chain_not_single_packet(self):
        g1 = compile_one()
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            json.dump(g1, handle)
            path = handle.name
        try:
            self.assertEqual(fr.main(["verify", path, "--expected-head-digest", g1["packet_digest"]]), 2)
        finally:
            os.unlink(path)

    def test_verify_cli_accepts_complete_chain_with_anchor(self):
        g1 = compile_one()
        g2 = fr.advance_remediation(advance_input([g1]), g1["packet_digest"])
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            json.dump([g1, g2], handle)
            path = handle.name
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                code = fr.main(["verify", path, "--expected-head-digest", g2["packet_digest"]])
            self.assertEqual(code, 0)
            rendered = json.loads(out.getvalue())
            self.assertTrue(rendered["valid"])
            self.assertEqual(rendered["generation"], 2)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
