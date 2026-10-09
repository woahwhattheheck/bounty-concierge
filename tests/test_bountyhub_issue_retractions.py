# SPDX-License-Identifier: MIT
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from concierge.bountyhub_issue_retractions import apply_withdrawals, RetractionError, REASON

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "policies" / "bountyhub_withdrawn_issues_v1.json").read_text())
READY = {"decision": "READY_FOR_NEW_BUILD", "reason_codes": [],
         "issue_url": "https://github.com/yiisoft/docs/issues/318", "listing_id": "bogus-active-card"}

class WithdrawalAdmissionTests(unittest.TestCase):
    def test_open_issue_cannot_override_original_poster_withdrawal(self):
        r = apply_withdrawals(READY, MANIFEST)
        self.assertEqual(r["decision"], "HOLD")
        self.assertIn(REASON, r["reason_codes"])
        self.assertEqual(len(r["withdrawal_evidence_urls"]), 2)
        self.assertTrue(r["existing_claims_preserved"])

    def test_different_repo_or_issue_never_inherits_retraction(self):
        for url in ("https://github.com/other/docs/issues/318",
                    "https://github.com/yiisoft/docs/issues/319"):
            with self.subTest(url=url):
                r = apply_withdrawals({**READY, "issue_url": url}, MANIFEST)
                self.assertEqual(r["decision"], "READY_FOR_NEW_BUILD")
                self.assertEqual(r["withdrawal_evidence_urls"], [])

    def test_preexisting_canonical_hold_is_never_upgraded(self):
        p = {**READY, "decision": "HOLD", "reason_codes": ["NO_PAID_HISTORY"]}
        r = apply_withdrawals(p, MANIFEST)
        self.assertEqual(r["decision"], "HOLD")
        self.assertEqual(r["reason_codes"], ["NO_PAID_HISTORY", REASON])

    def test_non_author_comment_and_cross_issue_comment_rejected(self):
        for field, value in (("comment_author_login", "random-researcher"),
                             ("comment_url", "https://github.com/yiisoft/docs/issues/319#issuecomment-3751164866")):
            data = copy.deepcopy(MANIFEST)
            data["records"][0][field] = value
            with self.subTest(field=field), self.assertRaises(RetractionError):
                apply_withdrawals(READY, data)

    def test_missing_attestation_and_duplicate_evidence_rejected(self):
        for kind in ("not-reviewed", "duplicate"):
            data = copy.deepcopy(MANIFEST)
            if kind == "not-reviewed":
                data["records"][0]["operator_verified_source"] = False
            else:
                data["records"].append(copy.deepcopy(data["records"][0]))
            with self.subTest(kind=kind), self.assertRaises(RetractionError):
                apply_withdrawals(READY, data)

if __name__ == "__main__":
    unittest.main()
