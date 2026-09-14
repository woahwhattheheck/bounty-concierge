from __future__ import annotations

import copy
import unittest

import concierge.feedback_remediation as fr
from tests.test_feedback_remediation import (
    E2, E3, F2, H2, H3, advance_input, compile_one, feedback, reseal, submission,
)


class ChainHostileTests(unittest.TestCase):
    def test_dropped_history_self_sealed_successor_fails_chain(self):
        g1 = compile_one()
        g2 = fr.advance_remediation(advance_input([g1]), g1["packet_digest"])
        forged = copy.deepcopy(g2)
        forged["obligations"] = []
        forged = reseal(forged)
        # Self-integrity alone is intentionally not continuity authority.
        self.assertEqual(fr.verify_packet(forged)["packet_digest"], forged["packet_digest"])
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "dropped a prior obligation"):
            fr.verify_chain([g1, forged], forged["packet_digest"])

    def test_rewritten_history_self_sealed_successor_fails_chain(self):
        g1 = compile_one()
        g2 = fr.advance_remediation(advance_input([g1]), g1["packet_digest"])
        forged = copy.deepcopy(g2)
        item = forged["obligations"][0]
        item["summary"] = "rewritten history"
        event = {
            k: item[k]
            for k in (
                "source_event_id", "authority", "kind", "occurred_at", "evidence_digest", "summary",
                "disposition", "target_head_sha", "target_artifact_revision", "hints",
            )
        }
        item["event_fingerprint"] = fr.event_fp(event)
        forged = reseal(forged)
        self.assertEqual(fr.verify_packet(forged)["packet_digest"], forged["packet_digest"])
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "rewrote prior obligation evidence"):
            fr.verify_chain([g1, forged], forged["packet_digest"])

    def test_addressed_history_cannot_reopen(self):
        g1 = compile_one()
        oid = g1["obligations"][0]["obligation_id"]
        addressed = [{"obligation_id": oid, "successor_artifact_evidence_digest": E2, "note": None}]
        g2 = fr.advance_remediation(advance_input([g1], addressed=addressed), g1["packet_digest"])
        g3 = fr.advance_remediation(advance_input([g1, g2], submission(3, H3, E3)), g2["packet_digest"])
        forged = copy.deepcopy(g3)
        forged["obligations"][0]["state"] = "OPEN"
        forged["obligations"][0]["resolution"] = None
        forged = reseal(forged)
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "rewrote an addressed obligation"):
            fr.verify_chain([g1, g2, forged], forged["packet_digest"])

    def test_backdated_injection_fails_chain(self):
        g1 = compile_one()
        g2 = fr.advance_remediation(advance_input([g1]), g1["packet_digest"])
        forged = copy.deepcopy(g2)
        old_sub = submission()
        old_event = feedback(old_sub, event_id="late-injected", evidence=F2)
        old_event_norm = fr.norm_event(old_event, fr.norm_submission(old_sub))
        forged["obligations"].append(fr.obligation(old_sub["canonical_source"], old_event_norm))
        forged["obligations"].sort(
            key=lambda x: (x["target_artifact_revision"], x["occurred_at"], x["source_event_id"], x["obligation_id"])
        )
        forged = reseal(forged)
        self.assertEqual(fr.verify_packet(forged)["packet_digest"], forged["packet_digest"])
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "backdated obligation"):
            fr.verify_chain([g1, forged], forged["packet_digest"])

    def test_minted_addressed_transition_with_wrong_manifest_fails_chain(self):
        g1 = compile_one()
        g2 = fr.advance_remediation(advance_input([g1]), g1["packet_digest"])
        forged = copy.deepcopy(g2)
        item = forged["obligations"][0]
        item["state"] = "ADDRESSED"
        item["resolution"] = {
            "successor_artifact_evidence_digest": "f" * 64,
            "successor_head_sha": H2,
            "successor_artifact_revision": 2,
            "note": "self minted",
        }
        forged = reseal(forged)
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.verify_chain([g1, forged], forged["packet_digest"])

    def test_truncated_chain_cannot_advance_against_retained_head(self):
        g1 = compile_one()
        g2 = fr.advance_remediation(advance_input([g1]), g1["packet_digest"])
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "expected digest"):
            fr.advance_remediation(advance_input([g1], submission(3, H3, E3)), g2["packet_digest"])

    def test_forged_prior_head_cannot_advance_against_retained_head(self):
        g1 = compile_one()
        forged = copy.deepcopy(g1)
        forged["obligations"] = []
        forged = reseal(forged)
        with self.assertRaisesRegex(fr.FeedbackRemediationError, "expected digest"):
            fr.advance_remediation(advance_input([forged]), g1["packet_digest"])

    def test_three_generation_valid_chain_remains_verifiable(self):
        g1 = compile_one()
        oid = g1["obligations"][0]["obligation_id"]
        g2 = fr.advance_remediation(
            advance_input(
                [g1],
                addressed=[{"obligation_id": oid, "successor_artifact_evidence_digest": E2, "note": "fixed"}],
            ),
            g1["packet_digest"],
        )
        sub3 = submission(3, H3, E3)
        event3 = feedback(sub3, event_id="review-3", evidence=F2, disposition="NON_BLOCKING")
        g3 = fr.advance_remediation(advance_input([g1, g2], sub3, new_feedback=[event3]), g2["packet_digest"])
        chain = fr.verify_chain([g1, g2, g3], g3["packet_digest"])
        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[-1]["counts"]["addressed"], 1)
        self.assertEqual(chain[-1]["counts"]["open_non_blocking"], 1)

    def test_wrong_anchor_rejects_otherwise_valid_chain(self):
        g1 = compile_one()
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.verify_chain([g1], "f" * 64)

    def test_generation_numbers_must_start_at_one_and_be_contiguous(self):
        g1 = compile_one()
        forged = copy.deepcopy(g1)
        forged["generation"] = 2
        forged["predecessor_packet_digest"] = "a" * 64
        forged = reseal(forged)
        with self.assertRaises(fr.FeedbackRemediationError):
            fr.verify_chain([forged], forged["packet_digest"])


if __name__ == "__main__":
    unittest.main()
