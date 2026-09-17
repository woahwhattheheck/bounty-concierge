# SPDX-License-Identifier: MIT
import copy, hashlib, hmac, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from concierge.reward_settlement_certifier import (
    CERT_SCHEMA, INPUT_SCHEMA, REGISTRY_SCHEMA, CertificationError, canonical, certify, verify_certificate,
)
KEY=b"test-secret-key-material-32-bytes!!"
H="a"*64

def src(sid, authority="PROVIDER", sha=H, ref=None, when="2026-09-17T00:01:00Z"):
    return {"source_id":sid,"source_ref":ref or f"fixture://{sid}","source_sha256":sha,"observed_at":when,"authority":authority}

def doc(events=None, work_source=None):
    return {"schema":INPUT_SCHEMA,"generated_at":"2026-09-17T00:30:00Z","cases":[{
        "case_id":"case-1","work":{"repo":"example/repo","pr":1,"merge_commit_sha":"1"*40,"merged_at":"2026-09-16T23:00:00Z","source":work_source or src("merge", "REPOSITORY")},
        "events":events or []}]}

def signed_registry(sources, key=KEY):
    body={"schema":REGISTRY_SCHEMA,"generated_at":"2026-09-17T00:30:00Z","sources":sources}
    return {**body,"signature_hmac_sha256":hmac.new(key,canonical(body),hashlib.sha256).hexdigest()}

def transfer(sid="paid", status="CONFIRMED", amount=100):
    return {"event_id":sid,"kind":"TRANSFER","source":src(sid),"transfer_id":"transfer-1","status":status,"direction":"INCOMING","amount_minor":amount,"currency":"USD"}

class CertifierTests(unittest.TestCase):
    def test_unsigned_or_wrong_key_registry_rejected(self):
        d=doc(); r=signed_registry([d["cases"][0]["work"]["source"]])
        r["signature_hmac_sha256"]="0"*64
        with self.assertRaises(CertificationError): certify(d,r,KEY)
        with self.assertRaises(CertificationError): certify(d,signed_registry([]),b"wrong-key-material-long-enough")

    def test_caller_declared_provider_cannot_self_certify(self):
        d=doc([transfer()]); r=signed_registry([d["cases"][0]["work"]["source"]])
        c=certify(d,r,KEY); row=c["records"][0]
        self.assertEqual(row["certified_settlement_state"],"MERGE_ONLY_CERTIFIED")
        self.assertFalse(row["truth"]["certified_paid"])
        self.assertEqual(row["certified_paid_by_currency"],{})
        self.assertIn("paid",c["aggregates"]["unbound_source_ids"])

    def test_exact_trusted_confirmed_transfer_certifies_paid(self):
        e=transfer(); d=doc([e]); r=signed_registry([d["cases"][0]["work"]["source"],e["source"]])
        c=certify(d,r,KEY); row=c["records"][0]
        self.assertEqual(row["certified_settlement_state"],"PAID_CERTIFIED")
        self.assertEqual(row["certified_paid_by_currency"],{"USD":100})
        self.assertTrue(verify_certificate(d,r,KEY,c))

    def test_repeated_confirmed_snapshots_same_transfer_count_once(self):
        a=transfer("a", status="CONFIRMED", amount=100); b=transfer("b", status="CONFIRMED", amount=100)
        b["transfer_id"]="transfer-1"; b["source"]["observed_at"]="2026-09-17T00:10:00Z"
        d=doc([a,b]); r=signed_registry([d["cases"][0]["work"]["source"],a["source"],b["source"]])
        c=certify(d,r,KEY); self.assertEqual(c["records"][0]["certified_paid_by_currency"],{"USD":100})

    def test_transfer_regression_and_amount_drift_rejected(self):
        a=transfer("a", status="CONFIRMED", amount=100); b=transfer("b", status="PENDING", amount=100)
        b["source"]["observed_at"]="2026-09-17T00:10:00Z"
        d=doc([a,b]); r=signed_registry([d["cases"][0]["work"]["source"],a["source"],b["source"]])
        with self.assertRaises(CertificationError): certify(d,r,KEY)
        b=transfer("b", status="CONFIRMED", amount=101); b["source"]["observed_at"]="2026-09-17T00:10:00Z"
        d=doc([a,b]); r=signed_registry([d["cases"][0]["work"]["source"],a["source"],b["source"]])
        with self.assertRaises(CertificationError): certify(d,r,KEY)

    def test_pending_transfer_not_paid(self):
        e=transfer(status="PENDING"); d=doc([e]); r=signed_registry([d["cases"][0]["work"]["source"],e["source"]])
        c=certify(d,r,KEY); self.assertEqual(c["records"][0]["certified_settlement_state"],"TRANSFER_EVIDENCE_CERTIFIED")

    def test_ticket_and_rail_do_not_promote_to_transfer(self):
        for kind,extra,state in (("PAYOUT_TICKET",{"ticket_id":"t-1"},"PAYOUT_TICKET_CERTIFIED"),("PAYOUT_RAIL",{"rail_ref":"rail-1"},"PAYOUT_RAIL_CERTIFIED")):
            e={"event_id":kind.lower(),"kind":kind,"source":src(kind.lower()),**extra}; d=doc([e]); r=signed_registry([d["cases"][0]["work"]["source"],e["source"]])
            with self.subTest(kind=kind): self.assertEqual(certify(d,r,KEY)["records"][0]["certified_settlement_state"],state)

    def test_merge_source_must_itself_be_trusted(self):
        d=doc([transfer()]); r=signed_registry([d["cases"][0]["events"][0]["source"]])
        c=certify(d,r,KEY); self.assertEqual(c["records"][0]["certified_settlement_state"],"UNTRUSTED_WORK_SOURCE")
        self.assertFalse(c["records"][0]["truth"]["certified_paid"])

    def test_exact_binding_rejects_same_id_different_digest(self):
        e=transfer(); d=doc([e]); trusted=copy.deepcopy(e["source"]); trusted["source_sha256"]="b"*64
        r=signed_registry([d["cases"][0]["work"]["source"],trusted]); c=certify(d,r,KEY)
        self.assertEqual(c["records"][0]["certified_settlement_state"],"MERGE_ONLY_CERTIFIED")

    def test_trusted_closure_conflicts_with_trusted_payment(self):
        p=transfer(); close={"event_id":"close","kind":"CLOSURE","source":src("close","SPONSOR"),"reason":"NO_REWARD"}; d=doc([p,close])
        r=signed_registry([d["cases"][0]["work"]["source"],p["source"],close["source"]])
        with self.assertRaises(CertificationError): certify(d,r,KEY)

    def test_duplicate_registry_source_or_remint_rejected(self):
        s=src("x")
        with self.assertRaises(CertificationError): certify(doc(),signed_registry([s,s]),KEY)
        s2=copy.deepcopy(s); s2["source_id"]="y"
        with self.assertRaises(CertificationError): certify(doc(),signed_registry([s,s2]),KEY)

    def test_certificate_tamper_fails_verifier(self):
        d=doc(); r=signed_registry([d["cases"][0]["work"]["source"]]); c=certify(d,r,KEY)
        c["records"][0]["certified_settlement_state"]="PAID_CERTIFIED"
        self.assertFalse(verify_certificate(d,r,KEY,c))

    def test_deterministic_and_authority_hard_false(self):
        d=doc(); r=signed_registry([d["cases"][0]["work"]["source"]]); a=certify(d,r,KEY); b=certify(copy.deepcopy(d),copy.deepcopy(r),KEY)
        self.assertEqual(a,b); self.assertEqual(a["schema"],CERT_SCHEMA); self.assertTrue(all(v is False for v in a["authority"].values()))

    def test_registry_timestamp_must_be_real_and_not_precede_source(self):
        d=doc(); work=d["cases"][0]["work"]["source"]
        bad=signed_registry([work]); bad["generated_at"]="2026-02-31T00:00:00Z"
        body={k:bad[k] for k in ("schema","generated_at","sources")}; bad["signature_hmac_sha256"]=hmac.new(KEY,canonical(body),hashlib.sha256).hexdigest()
        with self.assertRaises(CertificationError): certify(d,bad,KEY)
        future=copy.deepcopy(work); future["observed_at"]="2026-09-17T00:31:00Z"
        with self.assertRaises(CertificationError): certify(d,signed_registry([future]),KEY)

    def test_sibling_ledger_validation_is_required(self):
        e=transfer(); e["status"]="IMPOSSIBLE"; d=doc([e])
        r=signed_registry([d["cases"][0]["work"]["source"],e["source"]])
        with self.assertRaises(CertificationError): certify(d,r,KEY)

    def test_cli_symlink_output_fails_closed(self):
        d=doc(); r=signed_registry([d["cases"][0]["work"]["source"]])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); ip=root/"in.json"; rp=root/"reg.json"; target=root/"target"; cp=root/"cert.json"
            ip.write_bytes(canonical(d)); rp.write_bytes(canonical(r)); target.write_text("sentinel"); cp.symlink_to(target)
            env=dict(os.environ); env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]); env["REWARD_SETTLEMENT_TRUST_KEY"]=KEY.decode()
            cmd=[sys.executable,"-m","concierge.reward_settlement_certifier","--input",str(ip),"--registry",str(rp),"--certificate",str(cp)]
            run=subprocess.run(cmd,env=env,text=True,capture_output=True)
            self.assertNotEqual(run.returncode,0); self.assertEqual(target.read_text(),"sentinel")

    def test_cli_requires_secret_env_and_refuses_overwrite(self):
        d=doc(); r=signed_registry([d["cases"][0]["work"]["source"]])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); ip=root/"in.json"; rp=root/"reg.json"; cp=root/"cert.json"; mp=root/"cert.md"
            ip.write_bytes(canonical(d)); rp.write_bytes(canonical(r)); env=dict(os.environ); env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1])
            cmd=[sys.executable,"-m","concierge.reward_settlement_certifier","--input",str(ip),"--registry",str(rp),"--certificate",str(cp),"--markdown",str(mp)]
            bad=subprocess.run(cmd,env=env,text=True,capture_output=True); self.assertNotEqual(bad.returncode,0)
            env["REWARD_SETTLEMENT_TRUST_KEY"]=KEY.decode(); good=subprocess.run(cmd,env=env,text=True,capture_output=True); self.assertEqual(good.returncode,0,good.stderr)
            again=subprocess.run(cmd,env=env,text=True,capture_output=True); self.assertNotEqual(again.returncode,0)

if __name__=="__main__": unittest.main()
