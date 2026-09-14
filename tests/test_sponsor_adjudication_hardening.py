import unittest

from concierge import sponsor_adjudication as sa
from tests.test_sponsor_adjudication import h, manifest


class SponsorAdjudicationCollapseHardeningTests(unittest.TestCase):
    def test_collapse_into_rewarded_target_is_rejected(self):
        m = manifest()
        m["sponsor_events"] = [
            {"event_id": "a", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:00Z", "source_ref": "mail:a", "source_sha256": h("a"), "claim_unit_id": "u-a", "finding_ids": ["f-1"], "submission_ids": ["s-1"]},
            {"event_id": "b", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:01Z", "source_ref": "mail:b", "source_sha256": h("b"), "claim_unit_id": "u-b", "finding_ids": ["f-2"], "submission_ids": ["s-1"]},
            {"event_id": "offer", "event_type": "REWARD_OFFERED", "event_at": "2026-09-13T21:00:02Z", "source_ref": "mail:o", "source_sha256": h("o"), "claim_unit_id": "u-a", "amount": "90.00", "currency": "USD"},
            {"event_id": "c", "event_type": "DUPLICATE_COLLAPSED", "event_at": "2026-09-13T21:01:00Z", "source_ref": "mail:c", "source_sha256": h("c"), "claim_unit_id": "u-a", "collapsed_claim_unit_ids": ["u-b"]},
        ]
        with self.assertRaisesRegex(sa.AdjudicationError, "target unit u-a already has reward authority"):
            sa.compile_manifest(m)

    def test_collapse_preserves_earliest_sponsor_event_timestamp(self):
        m = manifest()
        m["sponsor_events"] = [
            {"event_id": "early", "event_type": "SPONSOR_VERIFIED", "event_at": "2026-09-13T21:00:00Z", "source_ref": "mail:early", "source_sha256": h("early"), "claim_unit_id": "u-source", "finding_ids": ["f-2"], "submission_ids": ["s-1"]},
            {"event_id": "later", "event_type": "SPONSOR_RECEIVED", "event_at": "2026-09-13T21:00:10Z", "source_ref": "mail:later", "source_sha256": h("later"), "claim_unit_id": "u-target", "finding_ids": ["f-1"], "submission_ids": ["s-1"]},
            {"event_id": "collapse", "event_type": "DUPLICATE_COLLAPSED", "event_at": "2026-09-13T21:01:00Z", "source_ref": "mail:collapse", "source_sha256": h("collapse"), "claim_unit_id": "u-target", "collapsed_claim_unit_ids": ["u-source"]},
        ]
        units = {u["claim_unit_id"]: u for u in sa.compile_manifest(m)["claim_units"]}
        self.assertEqual(units["u-target"]["first_sponsor_event_at"], "2026-09-13T21:00:00Z")
        self.assertTrue(units["u-target"]["sponsor_verified"])


if __name__ == "__main__":
    unittest.main()
