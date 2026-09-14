import copy
import hashlib
import json
import unittest

from concierge import sponsor_adjudication as sa


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def canonical_sha(value):
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def empty_event_manifest():
    return {
        "schema_version": 1,
        "program": {
            "program_id": "synthetic-bounty",
            "sponsor": "Synthetic Sponsor",
            "source_ref": "https://example.invalid/program",
        },
        "findings": [
            {
                "finding_id": "f-1",
                "fingerprint_sha256": h("finding"),
                "title": "Synthetic finding",
                "submitted_by": "tester",
                "evidence_ref": "repo#1",
            }
        ],
        "submissions": [
            {
                "submission_id": "s-1",
                "finding_ids": ["f-1"],
                "submitted_at": "2026-09-13T20:00:00Z",
                "channel": "email",
                "receipt_ref": "mail:submission-1",
                "receipt_sha256": h("submission-1"),
            }
        ],
        "sponsor_events": [],
    }


class SponsorAdjudicationZeroEventAuthorityTests(unittest.TestCase):
    def test_resealed_reward_posture_without_sponsor_events_fails_closed(self):
        forged = copy.deepcopy(sa.compile_manifest(empty_event_manifest()))
        forged["claim_units"] = [
            {
                "claim_unit_id": "forged-unit",
                "status": "reward_offered",
                "action": "OWNER_REVIEW",
                "cash_status": "not_inferred",
            }
        ]
        forged["summary"]["claim_unit_count"] = 1
        core = {k: copy.deepcopy(v) for k, v in forged.items() if k != "report_sha256"}
        forged["report_sha256"] = canonical_sha(core)
        with self.assertRaisesRegex(sa.AdjudicationError, "without sponsor events"):
            sa.verify_report(forged)

    def test_resealed_sponsor_finding_posture_without_events_fails_closed(self):
        forged = copy.deepcopy(sa.compile_manifest(empty_event_manifest()))
        forged["findings"][0]["status"] = "reward_offered"
        forged["findings"][0]["canonical_claim_unit_id"] = "forged-unit"
        core = {k: copy.deepcopy(v) for k, v in forged.items() if k != "report_sha256"}
        forged["report_sha256"] = canonical_sha(core)
        with self.assertRaisesRegex(sa.AdjudicationError, "without sponsor events"):
            sa.verify_report(forged)


if __name__ == "__main__":
    unittest.main()
