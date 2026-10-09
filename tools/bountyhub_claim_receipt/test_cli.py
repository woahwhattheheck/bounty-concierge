# SPDX-License-Identifier: MIT
"""One focused offline receipt-status regression, no provider or broad tests."""
import copy
import unittest
from datetime import datetime, timezone
from cli import assessment

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
BASE = {
 "schema":"bountyhub-claim-receipt/v1", "actor_login":"woahwhattheheck",
 "listing":{"url":"https://www.bountyhub.dev/en/bounty/view/ef91cb1e-0000-4000-8000-000000000000", "issue_url":"https://github.com/microg/GmsCore/issues/580", "issue_state":"OPEN", "observed_at":"2026-10-09T08:59:00Z"},
 "funding":{"pledges":[{"pledge_id":"p1","amount_usd":"150.00","payment_status":"PAID","retracted":False}, {"pledge_id":"p2","amount_usd":"250.00","payment_status":"PROMISED","retracted":False}],"advertised_total_usd":"400.00","observed_at":"2026-10-09T08:59:00Z"},
 "claim":{"claim_id":"c1","claimant_login":"woahwhattheheck","source_pr_url":"https://github.com/microg/GmsCore/pull/3845","status":"PENDING","provider_marked_paid":False,"observed_at":"2026-10-09T08:59:00Z"},
 "pull_request":{"url":"https://github.com/microg/GmsCore/pull/3845","author_login":"woahwhattheheck","head_sha":HEAD,"state":"OPEN","observed_at":"2026-10-09T08:59:00Z"},
 "claim_request":{"evidence_url":"https://github.com/microg/GmsCore/pull/3845","affirmative":True,"waived":False},
 "max_snapshot_age_seconds":300}

class ReceiptAcceptance(unittest.TestCase):
 def test_original_author_pending_and_unfunded_promise(self):
  r = assessment(copy.deepcopy(BASE), NOW)
  self.assertEqual(r["disposition"], "CLAIM_REGISTERED_AWAIT_REVIEW")
  self.assertEqual(r["amounts_usd_reported"]["promised_unfunded"], "250.00")
  self.assertEqual(r["amounts_usd_reported"]["cash_received_by_contributor"], "UNVERIFIED")

 def test_paid_marker_never_claims_cash_received(self):
  payload = copy.deepcopy(BASE); payload["claim"].update(status="PAID", provider_marked_paid=True)
  r = assessment(payload, NOW)
  self.assertEqual(r["disposition"], "PROVIDER_PAID_MARKED_VERIFY_ACTUAL_SETTLEMENT")
  self.assertEqual(r["amounts_usd_reported"]["cash_received_by_contributor"], "UNVERIFIED")

 def test_mismatched_claimant_holds_payment(self):
  payload = copy.deepcopy(BASE); payload["claim"]["claimant_login"] = "otherperson"
  r = assessment(payload, NOW)
  self.assertEqual(r["disposition"], "HOLD")
  self.assertIn("PORTAL_CLAIMANT_MISMATCH", r["reason_codes"])

if __name__ == "__main__": unittest.main()
