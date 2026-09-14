from __future__ import annotations
import base64, copy, hashlib, hmac, importlib.util, os, unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rq", ROOT / "concierge" / "revenue_response_queue.py")
assert SPEC and SPEC.loader
rq = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(rq)
NOW = datetime(2026, 9, 13, 13, 10, 0, 500000, tzinfo=timezone.utc)
KEY = b"test-only-provider-attestation-key!"; KEY_ID = "host-v1"
BUYER = "buyer@example.com"; THREAD = "thr-1"; SENT = "sent-1"; PROVIDER = "gmail"; PRINCIPAL = "acct-1"

def policy(**kw):
    v={"schema":rq._POLICY_SCHEMA,"max_snapshot_age_seconds":300,"auto_ack_grace_hours":24}; v.update(kw); return v

def row(**kw):
    v={"engagement_id":"eng-1","provider_thread_id":THREAD,"sent_message_id":SENT,"buyer_route":BUYER,"authorized_reply_routes":[BUYER],"offer_key":"offer-v1","contact_policy":"follow_up_allowed","follow_up_after_hours":24}; v.update(kw); return v

def msg(mid,kind,at,*,from_route="seller@example.net",to_routes=None,related=None,seq=10):
    if to_routes is None: to_routes=[BUYER] if kind=="outbound" else ["seller@example.net"]
    return {"id":mid,"kind":kind,"occurred_at":at,"from_route":from_route,"to_routes":to_routes,"related_message_id":related,"sequence":seq}

def anchor(at="2026-09-12T10:00:00Z",*,to_routes=None,seq=10): return msg(SENT,"outbound",at,to_routes=to_routes or [BUYER],seq=seq)
def snap(*messages,thread=THREAD,fetched="2026-09-13T13:10:00.300000Z",complete=True): return {"thread_id":thread,"fetched_at":fetched,"complete":complete,"messages":list(messages)}
def digests(manifest,pol):
    rows=rq._manifest_rows(manifest); return rq._sha_json(rq._scope_material(rows)),rq._sha_json(rq._validate_policy(pol))
def batch(manifest,snapshots,*,pol=None,captured="2026-09-13T13:10:00.400000Z",key=KEY,key_id=KEY_ID,provider=PROVIDER,principal=PRINCIPAL,scope=None,pdigest=None):
    pol=pol or policy(); scope0,pd0=digests(manifest,pol)
    unsigned={"schema":rq._BATCH_SCHEMA,"key_id":key_id,"provider":provider,"authenticated_principal":principal,"scope_sha256":scope or scope0,"policy_sha256":pdigest or pd0,"captured_at":captured,"snapshots":snapshots}
    sig=hmac.new(key,rq._canonical_json(unsigned).encode(),hashlib.sha256).hexdigest(); return {**unsigned,"hmac_sha256":sig}

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.clock=rq._current_utc; rq._current_utc=lambda:NOW
        self.old={name:os.environ.get(name) for name in (rq._ATTESTATION_KEY_ENV,rq._ATTESTATION_KEY_ID_ENV,rq._AUTHORIZED_PROVIDER_ENV,rq._AUTHORIZED_PRINCIPAL_SHA256_ENV)}
        os.environ[rq._ATTESTATION_KEY_ENV]=base64.b64encode(KEY).decode(); os.environ[rq._ATTESTATION_KEY_ID_ENV]=KEY_ID
        os.environ[rq._AUTHORIZED_PROVIDER_ENV]=PROVIDER; os.environ[rq._AUTHORIZED_PRINCIPAL_SHA256_ENV]=hashlib.sha256(PRINCIPAL.encode()).hexdigest()
    def tearDown(self):
        rq._current_utc=self.clock
        for name,value in self.old.items():
            if value is None: os.environ.pop(name,None)
            else: os.environ[name]=value
    def compile(self,manifest,snapshots,*,pol=None,**kw):
        pol=pol or policy(); return rq.compile_revenue_response_queue(manifest,batch(manifest,snapshots,pol=pol,**kw),policy=pol)

    def test_callback_is_not_authority_and_key_is_required(self):
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue([row()],lambda _:snap(anchor()),policy=policy())
        b=batch([row()],[snap(anchor())]); os.environ.pop(rq._ATTESTATION_KEY_ENV,None)
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue([row()],b,policy=policy())
    def test_validly_signed_wrong_provider_or_principal_is_rejected(self):
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor())],provider="outlook")
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor())],principal="acct-2")
    def test_scope_and_policy_replay_fail(self):
        m1=[row()]; m2=[row(buyer_route="other@example.com",authorized_reply_routes=["other@example.com"])]
        b=batch(m1,[snap(anchor(to_routes=[BUYER,"other@example.com"]))])
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m2,b,policy=policy())
        b=batch(m1,[snap(anchor())],pol=policy(auto_ack_grace_hours=24))
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m1,b,policy=policy(auto_ack_grace_hours=48))
    def test_buyer_and_authorized_route_scope_are_receipt_bound(self):
        s=snap(anchor(to_routes=[BUYER,"other@example.com"]))
        a=self.compile([row()],[copy.deepcopy(s)]); b=self.compile([row(buyer_route="other@example.com",authorized_reply_routes=["other@example.com"])],[copy.deepcopy(s)])
        self.assertNotEqual(a["scope_sha256"],b["scope_sha256"]); self.assertNotEqual(a["evidence_sha256"],b["evidence_sha256"])
        c=self.compile([row(authorized_reply_routes=[BUYER,"rep@example.com"])],[snap(anchor())]); self.assertNotEqual(a["items"][0]["authorized_reply_routes_sha256"],c["items"][0]["authorized_reply_routes_sha256"])
    def test_post_read_time_and_exact_thread_set(self):
        self.assertEqual(self.compile([row()],[snap(anchor())])["items"][0]["state"],"FOLLOW_UP_DUE")
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(),fetched="2026-09-13T13:10:00.450000Z")],captured="2026-09-13T13:10:00.400000Z")
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor()),snap(anchor(),thread="extra")])
    def test_linked_human_reply_and_unbound_route(self):
        human=msg("human","human_inbound","2026-09-13T12:00:00Z",from_route=BUYER,related=SENT,seq=20)
        self.assertEqual(self.compile([row()],[snap(anchor(),human)])["items"][0]["state"],"HUMAN_REPLY")
        other=msg("other","human_inbound","2026-09-13T12:00:00Z",from_route="rep@example.com",related=SENT,seq=20)
        self.assertEqual(self.compile([row()],[snap(anchor(),other)])["items"][0]["state"],"HUMAN_REVIEW_REQUIRED")
    def test_unlinked_human_is_review_not_authoritative_reply(self):
        human=msg("human","human_inbound","2026-09-13T12:00:00Z",from_route=BUYER,related=None,seq=20)
        item=self.compile([row()],[snap(anchor(),human)])["items"][0]
        self.assertEqual(item["state"],"HUMAN_REVIEW_REQUIRED"); self.assertIn("UNLINKED",item["reason_codes"][0])
    def test_bounce_is_bound_to_current_generation(self):
        bounce=msg("b","bounce","2026-09-13T12:00:00Z",from_route="mailer-daemon@example.net",related=SENT,seq=20)
        self.assertEqual(self.compile([row()],[snap(anchor(),bounce)])["items"][0]["state"],"ROUTE_REPAIR")
    def test_unrelated_same_thread_outbound_does_not_reset_offer_clock(self):
        unrelated=msg("sent-2","outbound","2026-09-13T10:00:00Z",to_routes=[BUYER],related=None,seq=30)
        item=self.compile([row(follow_up_after_hours=24)],[snap(anchor("2026-09-11T10:00:00Z"),unrelated)])["items"][0]
        self.assertEqual(item["state"],"HUMAN_REVIEW_REQUIRED"); self.assertEqual(item["latest_outbound_at"],"2026-09-11T10:00:00Z")
    def test_linked_successor_outbound_advances_generation(self):
        linked=msg("sent-2","outbound","2026-09-12T10:00:00Z",to_routes=[BUYER],related=SENT,seq=30)
        item=self.compile([row(follow_up_after_hours=72)],[snap(anchor("2026-09-10T10:00:00Z"),linked)])["items"][0]
        self.assertEqual(item["state"],"WAIT"); self.assertEqual(item["latest_outbound_at"],"2026-09-12T10:00:00Z")
    def test_ambiguous_linked_outbound_branch_requires_review(self):
        a=msg("sent-a","outbound","2026-09-12T11:00:00Z",to_routes=[BUYER],related=SENT,seq=20)
        b=msg("sent-b","outbound","2026-09-12T12:00:00Z",to_routes=[BUYER],related=SENT,seq=30)
        self.assertEqual(self.compile([row()],[snap(anchor(),a,b)])["items"][0]["state"],"HUMAN_REVIEW_REQUIRED")
    def test_dnr_and_linked_auto_ack_policy(self):
        dnr=[row(contact_policy="wait_for_buyer_event",follow_up_after_hours=None)]
        self.assertEqual(self.compile(dnr,[snap(anchor("2026-08-01T10:00:00Z"))])["items"][0]["state"],"WAIT_BUYER_EVENT")
        ack=msg("ack","automated_inbound","2026-09-13T10:00:00Z",from_route=BUYER,related=SENT,seq=20)
        item=self.compile([row()],[snap(anchor("2026-09-11T10:00:00Z"),ack)])["items"][0]
        self.assertEqual(item["state"],"WAIT_AUTO_ACK"); self.assertEqual(item["follow_up_due_at"],"2026-09-14T10:00:00Z")
    def test_sequence_anchor_and_strict_shape_fences(self):
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(to_routes=["other@example.com"]))])
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(seq=10),msg("h","human_inbound","2026-09-13T12:00:00Z",from_route=BUYER,related=SENT,seq=10))])
        m=[row()]; b=batch(m,[snap(anchor())]); b["extra"]="no"
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m,b,policy=policy())
    def test_output_identity_minimized_and_authority_ceiling(self):
        r=self.compile([row()],[snap(anchor())]); wire=rq._canonical_json(r)
        for text in (BUYER,THREAD,SENT,PROVIDER,PRINCIPAL): self.assertNotIn(text,wire)
        self.assertTrue(r["authority"]["provider_identity_authorized"]); self.assertTrue(r["authority"]["receipt_host_attested"])
        for k in ("send_message","reply_to_buyer","mutate_provider","change_contact_policy","recognize_revenue","recognize_payment"): self.assertFalse(r["authority"][k])
    def test_receipt_verifier_checks_hmac_identity_and_freshness(self):
        r=self.compile([row()],[snap(anchor())]); self.assertTrue(rq.verify_revenue_response_queue_receipt(r))
        forged=copy.deepcopy(r); forged["items"][0]["state"]="HUMAN_REPLY"
        signed=dict(forged); signed.pop("host_attestation_hmac_sha256"); sem=dict(signed); sem.pop("evidence_sha256"); forged["evidence_sha256"]=rq._sha_json(sem)
        self.assertFalse(rq.verify_revenue_response_queue_receipt(forged))
        os.environ[rq._AUTHORIZED_PRINCIPAL_SHA256_ENV]=hashlib.sha256(b"acct-2").hexdigest(); self.assertFalse(rq.verify_revenue_response_queue_receipt(r))
        os.environ[rq._AUTHORIZED_PRINCIPAL_SHA256_ENV]=hashlib.sha256(PRINCIPAL.encode()).hexdigest(); rq._current_utc=lambda:datetime(2026,9,13,13,16,0,tzinfo=timezone.utc)
        self.assertFalse(rq.verify_revenue_response_queue_receipt(r))
    def test_input_order_deterministic_and_manifest_collision_rejected(self):
        r2=row(engagement_id="eng-2",provider_thread_id="thr-2",sent_message_id="sent-2",buyer_route="two@example.com",authorized_reply_routes=["two@example.com"],offer_key="offer-2",follow_up_after_hours=72)
        s2=snap(msg("sent-2","outbound","2026-09-13T10:00:00Z",to_routes=["two@example.com"],seq=10),thread="thr-2")
        self.assertEqual(self.compile([row(),r2],[snap(anchor()),s2]),self.compile([r2,row()],[s2,snap(anchor())]))
        dup=[row(),row(engagement_id="eng-2",provider_thread_id="thr-2",sent_message_id="sent-2")]
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(dup,{},policy=policy())
    def test_post_attestation_mutation_cannot_change_semantics(self):
        manifest=[row()]; signed_batch=batch(manifest,[snap(anchor())])
        def mutate_original_then_now():
            signed_batch["snapshots"][0]["messages"].append(
                msg("human","human_inbound","2026-09-13T12:00:00Z",from_route=BUYER,related=SENT,seq=20))
            return NOW
        rq._current_utc=mutate_original_then_now
        result=rq.compile_revenue_response_queue(manifest,signed_batch,policy=policy())
        self.assertEqual(result["items"][0]["state"],"FOLLOW_UP_DUE")
    def test_receipt_freshness_tracks_underlying_provider_evidence(self):
        result=self.compile([row()],[snap(anchor(),fetched="2026-09-13T13:05:01.000000Z")])
        self.assertEqual(result["provider_authority"]["oldest_snapshot_fetched_at"],"2026-09-13T13:05:01Z")
        self.assertTrue(rq.verify_revenue_response_queue_receipt(result))
        rq._current_utc=lambda:datetime(2026,9,13,13,10,2,tzinfo=timezone.utc)
        self.assertFalse(rq.verify_revenue_response_queue_receipt(result))

if __name__=="__main__": unittest.main()
