import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from grantfox_oss_cash_guard import EvidenceError, assess


def fixture():
    return {
        'schema':'grantfox-oss-cash-evidence/v1','as_of':'2026-10-09T09:30:00Z',
        'sponsor_repo':'Quittance-Labs/Quittance0','original_contributor':'woahwhattheheck',
        'merged_pr_urls':['https://github.com/Quittance-Labs/Quittance0/pull/574'],
        'points':{'cumulative_awarded':350,'award_record_url':'https://github.com/Quittance-Labs/Quittance0/issues/443#issuecomment-5971364152'},
        'cash':{},
        'collection':{'request_comment_url':'https://github.com/Quittance-Labs/Quittance0/issues/443#issuecomment-6073925279',
                      'request_sent_at':'2026-10-09T05:00:00Z','response_requested_by':'2026-10-13T00:00:00Z'}
    }

class GrantFoxCashGuardTests(unittest.TestCase):
    def test_real_points_are_not_cash_or_new_build_admission(self):
        d=assess(fixture())
        self.assertEqual(d['status'],'POINTS_ONLY_CASH_AWARD_UNVERIFIED')
        self.assertEqual(d['points_awarded_recorded'],350)
        self.assertIsNone(d['cash_award_usdc_recorded'])
        self.assertIsNone(d['cash_released_usdc_recorded'])
        self.assertFalse(d['new_unpaid_builds_allowed'])
        self.assertIn('NO_DUPLICATE_PING',d['next_action'])

    def test_deny_unproven_points_to_dollars(self):
        d=fixture();d['points']['usd_per_point']='1.0'
        with self.assertRaises(EvidenceError):assess(d)
        d=fixture();d['cash']['released_payment_usdc']='350.00'
        with self.assertRaises(EvidenceError):assess(d)

    def test_reject_forged_foreign_issue_proof_and_duplicate_prs(self):
        d=fixture();d['points']['award_record_url']='https://github.com/other/project/issues/1#issuecomment-2'
        with self.assertRaises(EvidenceError):assess(d)
        d=fixture();d['merged_pr_urls']*=2
        with self.assertRaises(EvidenceError):assess(d)

    def test_separate_approved_cash_from_released_cash(self):
        d=fixture();d['cash']={'approved_award_usdc':'35.00','award_record_url':'https://contribute.grantfox.xyz/awards/123'}
        result=assess(d)
        self.assertEqual(result['status'],'CASH_AWARD_RECORDED_NOT_PAID')
        self.assertIsNone(result['cash_released_usdc_recorded'])

    def test_release_needs_escrow_and_verified_identity_readback(self):
        d=fixture();d['cash']={'released_payment_usdc':'35.00','release_record_url':'https://viewer.trustlesswork.com/escrows/123'}
        with self.assertRaises(EvidenceError):assess(d)
        d['cash'].update(recipient_verified=True,sponsor_verified=True,provider_readback_at='2026-10-09T09:20:00Z')
        result=assess(d)
        self.assertEqual(result['status'],'PROVIDER_RELEASE_RECORDED')
        self.assertTrue(result['provider_receipt_is_operator_supplied_not_independently_fetched'])

    def test_no_premature_escalation_and_stale_or_unsafe_source_links(self):
        d=fixture();d['as_of']='2026-10-14T00:00:00Z'
        self.assertIn('CHECK_EXISTING_REQUEST',assess(d)['next_action'])
        d=fixture();d['points']['award_record_url']='https://github.com/Quittance-Labs/Quittance0/issues/443?token=x#issuecomment-2'
        with self.assertRaises(EvidenceError):assess(d)

if __name__ == '__main__':unittest.main()
