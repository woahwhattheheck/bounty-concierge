import copy
import json
from pathlib import Path
import tempfile
import unittest

from concierge import bounty_cash_admission as gate
AS_OF='2026-09-19T23:00:00Z'; OBS='2026-09-19T22:30:00Z'; ISSUE='https://github.com/acme/widget/issues/42'

def ev(amount='50', currency='USD', *, scope='ISSUE', authority='FIRST_PARTY', kind='EXPLICIT_AMOUNT', guaranteed=True, at=OBS, source=ISSUE, target=ISSUE):
    row={'scope':scope,'authority':authority,'kind':kind,'guaranteed_for_candidate':guaranteed,'observed_at':at,'source_url':source,'target_url':target}
    if kind=='EXPLICIT_AMOUNT': row.update(amount=amount,currency=currency)
    return row

def req(rows=None, *, state='OPEN_UNASSIGNED', avail_at=OBS):
    return {'schema':gate.SCHEMA,'as_of':AS_OF,'candidate':{'work_id':'acme-42','canonical_source_url':ISSUE,'availability':{'state':state,'observed_at':avail_at,'source_url':ISSUE},'reward_evidence':rows or [ev()]}}

class T(unittest.TestCase):
    def c(self, rows=None, **kw): return gate.compile_bounty_cash_admission(req(rows,**kw))
    def test_floor_routing(self):
        for amount,disp,target in [('50','ACTIVE','#bug-bounty'),('49.99','MAYBE_SAVE_UP','#bounty-pile-10-49'),('10','MAYBE_SAVE_UP','#bounty-pile-10-49'),('9.99','PRUNE',None)]:
            with self.subTest(amount=amount):
                r=self.c([ev(amount)]); self.assertEqual((r['disposition'],r['target_channel']),(disp,target)); self.assertTrue(gate.verify_receipt(r))
        self.assertFalse(self.c([ev('50')])['authority']['external_claim_authority'])
    def test_unpriced_maybe_and_tokens_hold(self):
        for row,reason in [(ev(kind='UNPRICED',guaranteed=False),'REWARD_VALUE_UNCONFIRMED'),(ev(kind='MAYBE_REWARDED',guaranteed=False),'REWARD_VALUE_UNCONFIRMED'),(ev('1000','RTC'),'NON_USD_DENOMINATION_NO_CONVERSION')]:
            with self.subTest(reason=reason): self.assertEqual(self.c([row])['reason_codes'],[reason])
        usdc=self.c([ev('50','USDC')]); self.assertEqual(usdc['disposition'],'ACTIVE'); self.assertFalse(usdc['authority']['fx_conversion'])
    def test_issue_specific_precedence(self):
        r=self.c([ev('50',scope='PROGRAM',source='https://sponsor.example/table'),ev('20')]); self.assertEqual((r['disposition'],r['selected_reward']['amount']),('MAYBE_SAVE_UP','20'))
        r=self.c([ev('100',scope='PROGRAM',source='https://sponsor.example/table'),ev(kind='MAYBE_REWARDED',guaranteed=False)]); self.assertEqual(r['reason_codes'],['REWARD_VALUE_UNCONFIRMED'])
    def test_untrusted_conflicting_or_unbound_value_holds(self):
        self.assertEqual(self.c([ev('1000',authority='OTHER')])['reason_codes'],['NO_TRUSTED_REWARD_AUTHORITY'])
        self.assertEqual(self.c([ev('500',scope='PROGRAM',guaranteed=False,source='https://sponsor.example/table')])['reason_codes'],['REWARD_NOT_BOUND_TO_CANDIDATE'])
        rows=[ev('50'),ev('75',source='https://mirror.example/proof')]; self.assertEqual(self.c(rows)['reason_codes'],['CONFLICTING_TOP_PRIORITY_REWARD_EVIDENCE'])
    def test_availability_precedes_reward(self):
        self.assertEqual(self.c([ev('9999')],state='CLOSED')['reason_codes'],['TERMINAL_CLOSED'])
        self.assertEqual(self.c([ev('9999')],state='OPEN_ASSIGNED_OTHER')['reason_codes'],['ASSIGNED_TO_OTHER'])
        self.assertEqual(self.c([ev('9999')],state='UNKNOWN')['reason_codes'],['AVAILABILITY_UNKNOWN'])
        self.assertEqual(self.c([ev('9999')],avail_at='2026-09-18T22:59:59Z')['reason_codes'],['AVAILABILITY_EVIDENCE_STALE'])
    def test_stale_future_and_target_binding(self):
        self.assertEqual(self.c([ev('100',at='2026-09-18T22:59:59Z')])['reason_codes'],['REWARD_EVIDENCE_STALE'])
        with self.assertRaisesRegex(gate.BountyCashAdmissionError,'future'): self.c([ev('100',at='2026-09-19T23:00:01Z')])
        with self.assertRaisesRegex(gate.BountyCashAdmissionError,'target_url'): self.c([ev('500',target='https://github.com/acme/widget/issues/99')])
    def test_schema_money_and_tamper_fail_closed(self):
        bad=req(); bad['candidate']['mystery']=True
        with self.assertRaises(gate.BountyCashAdmissionError): gate.compile_bounty_cash_admission(bad)
        bad=req(); bad['candidate']['reward_evidence'][0]['amount']=50
        with self.assertRaises(gate.BountyCashAdmissionError): gate.compile_bounty_cash_admission(bad)
        r=self.c([ev('75')]); changed=copy.deepcopy(r); changed['disposition']='PRUNE'; self.assertFalse(gate.verify_receipt(changed))
    def test_request_and_receipt_deterministic(self):
        p=req([ev('75')]); self.assertEqual(gate.compile_bounty_cash_admission(p),gate.compile_bounty_cash_admission(copy.deepcopy(p)))

if __name__=='__main__': unittest.main()
