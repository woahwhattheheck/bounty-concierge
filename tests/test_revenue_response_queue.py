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
BUYER = "buyer@example.com"; THREAD = "thr-1"; SENT = "sent-1"

def policy(**kw):
    v = {"schema": rq._POLICY_SCHEMA, "max_snapshot_age_seconds": 300, "auto_ack_grace_hours": 24}; v.update(kw); return v

def row(**kw):
    v = {"engagement_id":"eng-1","provider_thread_id":THREAD,"sent_message_id":SENT,"buyer_route":BUYER,"authorized_reply_routes":[BUYER],"offer_key":"offer-v1","contact_policy":"follow_up_allowed","follow_up_after_hours":24}; v.update(kw); return v

def msg(mid, kind, at, *, from_route="seller@example.net", to_routes=None, related=None, seq=10):
    if to_routes is None: to_routes = [BUYER] if kind == "outbound" else ["seller@example.net"]
    return {"id":mid,"kind":kind,"occurred_at":at,"from_route":from_route,"to_routes":to_routes,"related_message_id":related,"sequence":seq}

def anchor(at="2026-09-12T10:00:00Z", *, to_routes=None, seq=10):
    return msg(SENT,"outbound",at,to_routes=to_routes or [BUYER],seq=seq)

def snap(*messages, thread=THREAD, fetched="2026-09-13T13:10:00.300000Z", complete=True):
    return {"thread_id":thread,"fetched_at":fetched,"complete":complete,"messages":list(messages)}

def digests(manifest, pol):
    rows = rq._manifest_rows(manifest)
    return rq._sha_json(rq._scope_material(rows)), rq._sha_json(rq._validate_policy(pol))

def batch(manifest, snapshots, *, pol=None, captured="2026-09-13T13:10:00.400000Z", key=KEY, key_id=KEY_ID, scope=None, pdigest=None):
    pol = pol or policy(); scope0, pd0 = digests(manifest, pol)
    unsigned = {"schema":rq._BATCH_SCHEMA,"key_id":key_id,"provider":"gmail","authenticated_principal":"acct-1","scope_sha256":scope or scope0,"policy_sha256":pdigest or pd0,"captured_at":captured,"snapshots":snapshots}
    sig = hmac.new(key, rq._canonical_json(unsigned).encode(), hashlib.sha256).hexdigest()
    return {**unsigned,"hmac_sha256":sig}

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.clock = rq._current_utc; rq._current_utc = lambda: NOW
        self.oldk = os.environ.get(rq._ATTESTATION_KEY_ENV); self.oldid = os.environ.get(rq._ATTESTATION_KEY_ID_ENV)
        os.environ[rq._ATTESTATION_KEY_ENV] = base64.b64encode(KEY).decode(); os.environ[rq._ATTESTATION_KEY_ID_ENV] = KEY_ID
    def tearDown(self):
        rq._current_utc = self.clock
        if self.oldk is None: os.environ.pop(rq._ATTESTATION_KEY_ENV, None)
        else: os.environ[rq._ATTESTATION_KEY_ENV] = self.oldk
        if self.oldid is None: os.environ.pop(rq._ATTESTATION_KEY_ID_ENV, None)
        else: os.environ[rq._ATTESTATION_KEY_ID_ENV] = self.oldid
    def compile(self, manifest, snapshots, *, pol=None, **kw):
        pol = pol or policy(); return rq.compile_revenue_response_queue(manifest, batch(manifest,snapshots,pol=pol,**kw), policy=pol)

    def test_callback_is_not_authority(self):
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue([row()], lambda _: snap(anchor()), policy=policy())
    def test_missing_or_wrong_host_key_fails(self):
        m=[row()]; b=batch(m,[snap(anchor())]); os.environ.pop(rq._ATTESTATION_KEY_ENV,None)
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m,b,policy=policy())
        os.environ[rq._ATTESTATION_KEY_ENV]=base64.b64encode(KEY).decode(); b["hmac_sha256"]="0"*64
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m,b,policy=policy())
    def test_scope_and_policy_replay_fail(self):
        m1=[row()]; m2=[row(buyer_route="other@example.com",authorized_reply_routes=["other@example.com"])]
        b=batch(m1,[snap(anchor(to_routes=[BUYER,"other@example.com"]))])
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m2,b,policy=policy())
        p1=policy(auto_ack_grace_hours=24); b=batch(m1,[snap(anchor())],pol=p1)
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m1,b,policy=policy(auto_ack_grace_hours=48))
    def test_buyer_and_authorized_route_scope_are_receipt_bound(self):
        s=snap(anchor(to_routes=[BUYER,"other@example.com"]))
        a=self.compile([row()],[copy.deepcopy(s)]); b=self.compile([row(buyer_route="other@example.com",authorized_reply_routes=["other@example.com"])],[copy.deepcopy(s)])
        self.assertNotEqual(a["scope_sha256"],b["scope_sha256"]); self.assertNotEqual(a["evidence_sha256"],b["evidence_sha256"])
        c=self.compile([row(authorized_reply_routes=[BUYER,"rep@example.com"])],[snap(anchor())])
        self.assertNotEqual(a["items"][0]["authorized_reply_routes_sha256"],c["items"][0]["authorized_reply_routes_sha256"])
    def test_post_read_time_and_batch_freshness(self):
        self.assertEqual(self.compile([row()],[snap(anchor())])["items"][0]["state"],"FOLLOW_UP_DUE")
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(),fetched="2026-09-13T13:10:00.450000Z")],captured="2026-09-13T13:10:00.400000Z")
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(),fetched="2026-09-13T13:09:00Z")],pol=policy(max_snapshot_age_seconds=30),captured="2026-09-13T13:09:00Z")
    def test_exact_thread_set_and_completeness(self):
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor()),snap(anchor(),thread="extra")])
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(),complete=False)])
    def test_human_reply_and_unbound_human(self):
        human=msg("human","human_inbound","2026-09-13T12:00:00Z",from_route=BUYER,seq=20)
        self.assertEqual(self.compile([row()],[snap(anchor(),human)])["items"][0]["state"],"HUMAN_REPLY")
        other=msg("other","human_inbound","2026-09-13T12:00:00Z",from_route="rep@example.com",seq=20)
        self.assertEqual(self.compile([row()],[snap(anchor(),other)])["items"][0]["state"],"HUMAN_REVIEW_REQUIRED")
    def test_bounce_and_new_generation(self):
        bounce=msg("b","bounce","2026-09-13T12:00:00Z",from_route="mailer-daemon@example.net",related=SENT,seq=20)
        self.assertEqual(self.compile([row()],[snap(anchor(),bounce)])["items"][0]["state"],"ROUTE_REPAIR")
        newer=msg("sent-2","outbound","2026-09-12T10:00:00Z",to_routes=[BUYER],seq=30)
        oldreply=msg("old","human_inbound","2026-09-10T11:00:00Z",from_route=BUYER,seq=20)
        item=self.compile([row(follow_up_after_hours=72)],[snap(anchor("2026-09-10T10:00:00Z"),oldreply,newer)])["items"][0]
        self.assertEqual(item["state"],"WAIT"); self.assertEqual(item["latest_outbound_at"],"2026-09-12T10:00:00Z")
    def test_dnr_and_auto_ack_policy(self):
        dnr=[row(contact_policy="wait_for_buyer_event",follow_up_after_hours=None)]
        self.assertEqual(self.compile(dnr,[snap(anchor("2026-08-01T10:00:00Z"))])["items"][0]["state"],"WAIT_BUYER_EVENT")
        ack=msg("ack","automated_inbound","2026-09-13T10:00:00Z",from_route=BUYER,seq=20)
        item=self.compile([row()],[snap(anchor("2026-09-11T10:00:00Z"),ack)])["items"][0]
        self.assertEqual(item["state"],"WAIT_AUTO_ACK"); self.assertEqual(item["follow_up_due_at"],"2026-09-14T10:00:00Z")
    def test_anchor_and_sequence_fences(self):
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(to_routes=["other@example.com"]))])
        with self.assertRaises(rq.RevenueResponseQueueError): self.compile([row()],[snap(anchor(seq=10),msg("human","human_inbound","2026-09-13T12:00:00Z",from_route=BUYER,seq=10))])
        item=self.compile([row()],[snap(anchor("2026-09-13T12:00:00Z",seq=10),msg("h","human_inbound","2026-09-13T12:00:00Z",from_route=BUYER,seq=11))])["items"][0]
        self.assertEqual(item["state"],"HUMAN_REPLY")
    def test_output_is_identity_minimized_and_action_ceiling_is_false(self):
        r=self.compile([row()],[snap(anchor())]); wire=rq._canonical_json(r)
        for text in (BUYER,THREAD,SENT,"gmail","acct-1"): self.assertNotIn(text,wire)
        self.assertTrue(r["authority"]["provider_read_attested"]); self.assertTrue(r["authority"]["receipt_host_attested"])
        for k in ("send_message","reply_to_buyer","mutate_provider","change_contact_policy","recognize_revenue","recognize_payment"): self.assertFalse(r["authority"][k])
    def test_receipt_hmac_verifies_and_plain_sha_reseal_does_not(self):
        r=self.compile([row()],[snap(anchor())]); self.assertTrue(rq.verify_revenue_response_queue_receipt(r))
        forged=copy.deepcopy(r); forged["items"][0]["state"]="HUMAN_REPLY"
        signed=dict(forged); signed.pop("host_attestation_hmac_sha256"); sem=dict(signed); sem.pop("evidence_sha256"); forged["evidence_sha256"]=rq._sha_json(sem)
        self.assertFalse(rq.verify_revenue_response_queue_receipt(forged))
    def test_receipt_staleness_and_input_order(self):
        r=self.compile([row()],[snap(anchor())]); rq._current_utc=lambda: datetime(2026,9,13,13,16,0,tzinfo=timezone.utc)
        self.assertFalse(rq.verify_revenue_response_queue_receipt(r)); rq._current_utc=lambda: NOW
        r2=row(engagement_id="eng-2",provider_thread_id="thr-2",sent_message_id="sent-2",buyer_route="two@example.com",authorized_reply_routes=["two@example.com"],offer_key="offer-2",follow_up_after_hours=72)
        s2=snap(msg("sent-2","outbound","2026-09-13T10:00:00Z",to_routes=["two@example.com"],seq=10),thread="thr-2")
        self.assertEqual(self.compile([row(),r2],[snap(anchor()),s2]),self.compile([r2,row()],[s2,snap(anchor())]))
    def test_strict_policy_manifest_and_batch_shapes(self):
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue([row()],{},policy=policy(max_snapshot_age_seconds=True))
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue([row(contact_policy="wait_for_buyer_event",follow_up_after_hours=72)],{},policy=policy())
        dup=[row(),row(engagement_id="eng-2",provider_thread_id="thr-2",sent_message_id="sent-2")]
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(dup,{},policy=policy())
        m=[row()]; b=batch(m,[snap(anchor())]); b["extra"]="no"
        with self.assertRaises(rq.RevenueResponseQueueError): rq.compile_revenue_response_queue(m,b,policy=policy())

if __name__ == "__main__": unittest.main()
