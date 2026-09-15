# SPDX-License-Identifier: MIT
"""Host-authorized sponsor claim-unit -> one owner-reviewed payment draft."""
from __future__ import annotations

import argparse, hashlib, json, os, re, stat, unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit
from .sponsor_adjudication import AdjudicationError, verify_report

INPUT_SCHEMA="bounty-adjudication-collection-input/v1"
OUTPUT_SCHEMA="bounty-adjudication-collection/v1"
_MAX=1024*1024; _MAX_TEXT=4096
_ID=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REPO=re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA40=re.compile(r"^[0-9a-f]{40}$"); _SHA256=re.compile(r"^[0-9a-f]{64}$")
_CUR=re.compile(r"^[A-Z]{3,12}$")
_AMT=re.compile(r"^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$")
_ROUTES={"HOSTED_HANDLE","PAYMENT_LINK","WALLET","OTHER"}

class AdjudicationCollectionError(ValueError): pass

def _json(v:Any)->bytes:
    try: raw=json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
    except (TypeError,ValueError) as e: raise AdjudicationCollectionError("value is not canonical JSON") from e
    if len(raw)>_MAX: raise AdjudicationCollectionError("canonical JSON is too large")
    return raw

def _hash(v:Any)->str: return hashlib.sha256(_json(v)).hexdigest()

def _obj(v:Any, keys:set[str], field:str)->dict[str,Any]:
    if type(v) is not dict or set(v)!=keys: raise AdjudicationCollectionError(f"{field} must contain exactly {sorted(keys)}")
    return v

def _text(v:Any, field:str, n:int=512)->str:
    if type(v) is not str or not v or v!=v.strip() or len(v)>n: raise AdjudicationCollectionError(f"{field} must be non-empty bounded text without edge whitespace")
    if any(unicodedata.category(c) in {"Cc","Cf","Cs","Zl","Zp"} for c in v): raise AdjudicationCollectionError(f"{field} contains unsafe control/format characters")
    return v

def _https(v:Any, field:str)->str:
    s=_text(v,field,2048); p=urlsplit(s)
    if p.scheme!="https" or not p.netloc or p.username is not None or p.password is not None or p.fragment: raise AdjudicationCollectionError(f"{field} must be an absolute credential-free fragment-free HTTPS URL")
    return s

def _https_ok(v:Any)->bool:
    try: _https(v,"sponsor reward evidence source_ref"); return True
    except AdjudicationCollectionError: return False

def _work(v:Any)->dict[str,Any]:
    o=_obj(v,{"repo","pr","canonical_url","head_sha","state"},"work")
    raw=_text(o["repo"],"work.repo",200)
    if not _REPO.fullmatch(raw) or any(p in {".",".."} for p in raw.split("/")): raise AdjudicationCollectionError("work.repo must be owner/name")
    pr=o["pr"]
    if type(pr) is not int or pr<=0: raise AdjudicationCollectionError("work.pr must be a positive integer (bool is invalid)")
    url=_https(o["canonical_url"],"work.canonical_url")
    if url!=f"https://github.com/{raw}/pull/{pr}": raise AdjudicationCollectionError("work.canonical_url must match repo/pr exactly")
    sha=_text(o["head_sha"],"work.head_sha",40)
    if not _SHA40.fullmatch(sha): raise AdjudicationCollectionError("work.head_sha must be lowercase 40-hex")
    if o["state"]!="MERGED": raise AdjudicationCollectionError("work.state must be MERGED")
    repo=raw.lower()
    return {"repo":repo,"pr":pr,"canonical_url":f"https://github.com/{repo}/pull/{pr}","head_sha":sha,"state":"MERGED"}

def _route(v:Any)->dict[str,str]:
    o=_obj(v,{"type","value"},"payout_route"); kind=_text(o["type"],"payout_route.type",32)
    if kind not in _ROUTES: raise AdjudicationCollectionError(f"payout_route.type must be one of {sorted(_ROUTES)}")
    val=_text(o["value"],"payout_route.value",512)
    if kind=="PAYMENT_LINK": _https(val,"payout_route.value")
    return {"type":kind,"value":val}

def _input(v:Any)->dict[str,Any]:
    o=_obj(v,{"schema","adjudication_report","claim_unit_id","work","payout_route"},"input")
    if o["schema"]!=INPUT_SCHEMA: raise AdjudicationCollectionError(f"input.schema must be {INPUT_SCHEMA}")
    if type(o["adjudication_report"]) is not dict: raise AdjudicationCollectionError("adjudication_report must be an object")
    _json(o["adjudication_report"]); uid=_text(o["claim_unit_id"],"claim_unit_id",128)
    if not _ID.fullmatch(uid): raise AdjudicationCollectionError("claim_unit_id contains unsupported characters")
    return {"schema":INPUT_SCHEMA,"adjudication_report":o["adjudication_report"],"claim_unit_id":uid,"work":_work(o["work"]),"payout_route":None if o["payout_route"] is None else _route(o["payout_route"])}

def _unit(report:dict[str,Any], uid:str)->dict[str,Any]:
    rows=[u for u in report.get("claim_units",[]) if type(u) is dict and u.get("claim_unit_id")==uid]
    if len(rows)!=1: raise AdjudicationCollectionError(f"claim_unit_id must select exactly one compiled sponsor claim unit: {uid}")
    return rows[0]

def _offer(amount:Any,currency:Any)->tuple[str,str]:
    a=_text(amount,"compiled reward amount",64); c=_text(currency,"compiled reward currency",12)
    if not _AMT.fullmatch(a): raise AdjudicationCollectionError("compiled reward amount is not canonical unsigned decimal text")
    try: n=Decimal(a)
    except InvalidOperation as e: raise AdjudicationCollectionError("compiled reward amount is invalid") from e
    if n<=0: raise AdjudicationCollectionError("compiled reward amount must be positive")
    if not _CUR.fullmatch(c): raise AdjudicationCollectionError("compiled reward currency is invalid")
    return a,c

def _draft(sponsor:str,work:dict[str,Any],route:dict[str,str],uid:str,amount:str,currency:str,evidence:str)->tuple[str,str]:
    subject=f"OWNER REVIEW — {work['repo']}#{work['pr']} — sponsor-offered {amount} {currency}"
    body=(f"OWNER REVIEW DRAFT — DO NOT SEND WITHOUT VERIFYING THE WORK REFERENCE BELOW.\n\nHi {sponsor},\n\nI’m preparing a request for payment of the {amount} {currency} reward offered for the sponsor claim unit below.\n\nCandidate work reference (caller-supplied; unverified): {work['canonical_url']}\nCandidate head (caller-supplied; unverified): {work['head_sha']}\nSponsor claim unit: {uid}\nReward-offer evidence: {evidence}\nPayout route: {route['type']} — {route['value']}\n\nBefore sending, independently verify that the candidate work reference is merged and mechanically belongs to this sponsor claim unit. After that verification, request the offered payout through the route above.\n\nThanks,\nBryce")
    if len(subject)>998 or len(body)>_MAX_TEXT: raise AdjudicationCollectionError("rendered payment request is too large")
    return subject,body

def compile_adjudication_collection(payload:Any)->dict[str,Any]:
    n=_input(payload)
    if os.environ.get("BOUNTY_SPONSOR_ADJUDICATION_TEST_ONLY_ALLOW_UNSIGNED")=="1": raise AdjudicationCollectionError("collection bridge refuses unsigned sponsor-adjudication test mode")
    try: report=verify_report(n["adjudication_report"])
    except AdjudicationError as e: raise AdjudicationCollectionError(f"invalid retained adjudication report: {e}") from e
    uid=n["claim_unit_id"]; unit=_unit(report,uid); events=report.get("sponsor_events")
    if type(events) is not list: raise AdjudicationCollectionError("verified adjudication report sponsor_events must be a list")
    status=unit.get("status"); canonical=unit.get("collapsed_into") or uid; program=report.get("program")
    if type(program) is not dict: raise AdjudicationCollectionError("compiled adjudication report is missing program identity")
    auth=program.get("sponsor_authority"); binding=report.get("sponsor_authority_binding")
    if type(auth) is not dict: raise AdjudicationCollectionError("retained adjudication report lacks host sponsor_authority")
    if type(binding) is not dict: raise AdjudicationCollectionError("retained adjudication report lacks host report-generation binding")
    sponsor=_text(program.get("sponsor"),"compiled program.sponsor",4096); pid=_text(program.get("program_id"),"compiled program.program_id",128); src=_text(program.get("source_ref"),"compiled program.source_ref",4096)
    disposition=reason=""; key=None; evidence=None; draft=None
    if status=="reward_offered":
        rewards=[e for e in events if type(e) is dict and e.get("claim_unit_id")==uid and e.get("event_type")=="REWARD_OFFERED"]
        if not rewards: raise AdjudicationCollectionError("compiled reward_offered unit has no REWARD_OFFERED evidence event")
        amount,currency=_offer(unit.get("reward_amount"),unit.get("reward_currency"))
        if any(e.get("amount")!=unit.get("reward_amount") or e.get("currency")!=unit.get("reward_currency") for e in rewards): raise AdjudicationCollectionError("compiled reward event disagrees with claim-unit reward")
        usable=[e for e in rewards if _https_ok(e.get("source_ref"))]
        if not usable:
            if n["payout_route"] is not None: raise AdjudicationCollectionError("payout_route must be null until a usable HTTPS reward-evidence event exists")
            disposition="HOLD_FOR_ACCEPTANCE_EVIDENCE_URL"; reason="Sponsor reward exists, but no retained REWARD_OFFERED event has a credential-free HTTPS evidence permalink."
        else:
            if n["payout_route"] is None: raise AdjudicationCollectionError("payout_route is required for an owner-review reward draft")
            e=usable[0]; ref=_https(e.get("source_ref"),"reward evidence source_ref"); dig=_text(e.get("source_sha256"),"reward evidence source_sha256",64)
            if not _SHA256.fullmatch(dig): raise AdjudicationCollectionError("reward evidence source_sha256 must be lowercase 64-hex")
            evidence={"event_id":_text(e.get("event_id"),"reward evidence event_id",200),"event_at":_text(e.get("event_at"),"reward evidence event_at",64),"source_ref":ref,"source_sha256":dig,"amount":amount,"currency":currency}
            generation={"program":{"program_id":pid,"sponsor":sponsor},"claim_unit_id":uid,"finding_ids":list(unit.get("finding_ids",[])),"submission_ids":list(unit.get("submission_ids",[])),"reward":{"amount":amount,"currency":currency}}
            key=_hash(generation); subject,body=_draft(sponsor,n["work"],n["payout_route"],uid,amount,currency,ref); draft={"subject":subject,"body":body}
            disposition="OWNER_REVIEW_REQUIRED"; reason="Sponsor-offered reward is host-authorized, but caller-supplied work identity is unverified and not mechanically bound to the sponsor claim unit."
    elif status=="paid_evidence_pending":
        if n["payout_route"] is not None: raise AdjudicationCollectionError("payout_route must be null unless an owner-review reward draft can be produced")
        disposition="HOLD_FOR_SETTLEMENT_EVIDENCE"; reason="Sponsor reports payment; reacquire independent settlement evidence instead of sending another payment request."
    elif status in {"sponsor_verified","adjudicating","submitted"}:
        if n["payout_route"] is not None: raise AdjudicationCollectionError("payout_route must be null unless an owner-review reward draft can be produced")
        disposition="WAIT_SPONSOR"; reason="Sponsor adjudication has not produced a retained reward offer."
    elif status in {"declined","duplicate"}:
        if n["payout_route"] is not None: raise AdjudicationCollectionError("payout_route must be null unless an owner-review reward draft can be produced")
        disposition="DO_NOT_REQUEST"; reason="Selected claim unit is terminal or noncanonical for collection."
    else: raise AdjudicationCollectionError(f"unsupported compiled claim-unit status: {status!r}")
    core={"schema":OUTPUT_SCHEMA,"disposition":disposition,"reason":reason,"program":{"program_id":pid,"sponsor":sponsor,"source_ref":src},"selected_claim_unit_id":uid,"canonical_claim_unit_id":canonical,"claim_unit_status":status,"finding_ids":list(unit.get("finding_ids",[])),"submission_ids":list(unit.get("submission_ids",[])),"adjudication_report_sha256":_text(report.get("report_sha256"),"compiled report_sha256",64),"work":n["work"],"payout_route_sha256":_hash(n["payout_route"]) if n["payout_route"] is not None else None,"reward_offer_evidence":evidence,"collection_key":key,"draft":draft,"sponsor_authority":{"provider":auth.get("provider"),"principal_sha256":auth.get("principal_sha256"),"event_scope_sha256":auth.get("event_scope_sha256"),"captured_at":auth.get("captured_at"),"manifest_sha256":binding.get("manifest_sha256"),"report_projection_sha256":binding.get("report_projection_sha256")},"authority":{"external_send":False,"provider_mutation":False,"wallet_mutation":False,"payout_initiated":False,"sponsor_authentication_inferred_from_digest":False,"work_state_independently_verified":False,"work_claim_unit_relation_verified":False,"reward_offer_is_debt":False,"reward_offer_is_cash":False,"payment_due_claim":False,"revenue_recognized":False,"owner_or_separately_authorized_sender_required":True}}
    if not _SHA256.fullmatch(core["adjudication_report_sha256"]): raise AdjudicationCollectionError("compiled adjudication report_sha256 is invalid")
    for name in ("manifest_sha256","report_projection_sha256"):
        v=core["sponsor_authority"].get(name)
        if type(v) is not str or not _SHA256.fullmatch(v): raise AdjudicationCollectionError(f"verified sponsor authority {name} is invalid")
    out=dict(core); out["receipt_sha256"]=_hash(core); return out

def verify_adjudication_collection(payload:Any,packet:Any)->bool:
    if type(packet) is not dict: return False
    try: return packet==compile_adjudication_collection(payload)
    except AdjudicationCollectionError: return False

def _pairs(pairs:list[tuple[str,Any]])->dict[str,Any]:
    out={}
    for k,v in pairs:
        if k in out: raise AdjudicationCollectionError(f"duplicate JSON key: {k}")
        out[k]=v
    return out

def load_json(path:Path)->Any:
    flags=os.O_RDONLY|(getattr(os,"O_NOFOLLOW",0))|(getattr(os,"O_NONBLOCK",0)); fd=os.open(path,flags)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode): raise AdjudicationCollectionError("input must be a regular file")
        if st.st_size>_MAX: raise AdjudicationCollectionError("input file is too large")
        chunks=[]; left=_MAX+1
        while left>0:
            b=os.read(fd,min(65536,left))
            if not b: break
            chunks.append(b); left-=len(b)
        raw=b"".join(chunks)
    finally: os.close(fd)
    if len(raw)>_MAX: raise AdjudicationCollectionError("input file is too large")
    try: return json.loads(raw.decode(),object_pairs_hook=_pairs,parse_int=lambda s:int(s) if len(s.lstrip("-"))<=32 else (_ for _ in ()).throw(AdjudicationCollectionError("JSON integer is too large")),parse_float=lambda s:(_ for _ in ()).throw(AdjudicationCollectionError("JSON floats are not accepted")),parse_constant=lambda s:(_ for _ in ()).throw(AdjudicationCollectionError(f"non-finite JSON constant: {s}")))
    except UnicodeDecodeError as e: raise AdjudicationCollectionError("input file must be UTF-8") from e
    except json.JSONDecodeError as e: raise AdjudicationCollectionError("input file must contain valid JSON") from e

def main(argv:Optional[list[str]]=None)->int:
    p=argparse.ArgumentParser(prog="python -m concierge.adjudication_collection"); p.add_argument("input",type=Path); p.add_argument("--verify",type=Path); a=p.parse_args(argv)
    try:
        payload=load_json(a.input)
        if a.verify is not None:
            ok=verify_adjudication_collection(payload,load_json(a.verify)); print(json.dumps({"verified":ok},sort_keys=True)); return 0 if ok else 3
        print(json.dumps(compile_adjudication_collection(payload),indent=2,sort_keys=True)); return 0
    except (AdjudicationCollectionError,OSError) as e: p.error(str(e))
    return 2

if __name__=="__main__": raise SystemExit(main())
