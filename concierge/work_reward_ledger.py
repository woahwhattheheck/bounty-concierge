# SPDX-License-Identifier: MIT
"""Evidence-only, cross-program paid-work lifecycle and settlement ledger.

Listings, claims, submissions, acceptance, awards, provider tickets and payment
rails are not cash.  Only explicit settlement receipts contribute paid value.
Denominations never cross-convert.  This module performs no provider, wallet,
payout, invoice, contact, submission, accounting or tax mutation/recognition.
"""
from __future__ import annotations

import argparse, copy, hashlib, json, re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

EVENTS_SCHEMA_VERSION = "bounty.work-reward-events/v1"
LEDGER_SCHEMA_VERSION = "bounty.work-reward-ledger/v1"
_MAX_JSON_BYTES, _MAX_EVENTS = 4_000_000, 20_000
_SHA = re.compile(r"^[0-9a-f]{64}$")
_AMT = re.compile(r"^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_PROGRESS = {"unknown":0,"advertised":1,"claimed":2,"submitted":3,"accepted":4,"awarded":5,"payment_pending":6,"paid":7}
_TERMINAL = {"rejected", "withdrawn"}
_STATES = set(_PROGRESS) | _TERMINAL
_REF_KINDS = {"claim","submission","award","ticket","other"}
_AUTHORITY = {
    "provider_mutation":False,"claim_submission":False,"external_contact":False,
    "invoice_creation":False,"payout_initiation":False,"transfer":False,
    "wallet_mutation":False,"fx_conversion":False,"accounting_recognition":False,
}

class WorkRewardLedgerError(ValueError): pass
class WorkRewardLedgerConflict(WorkRewardLedgerError): pass

def canonical_json(v: Any) -> str:
    try: return json.dumps(v, sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e: raise WorkRewardLedgerError("value is not canonical JSON") from e

def _hash(v: Any) -> str: return hashlib.sha256(canonical_json(v).encode()).hexdigest()

def _keys(v: Any, names: set, label: str) -> Dict[str, Any]:
    if not isinstance(v, dict) or set(v) != names: raise WorkRewardLedgerError(label+" fields do not match schema")
    return v

def _text(v: Any, label: str, maxlen: int = 512) -> str:
    if type(v) is not str or not v or len(v)>maxlen or v!=v.strip() or any(not c.isprintable() for c in v):
        raise WorkRewardLedgerError(label+" is malformed")
    return v

def _url(v: Any, label: str) -> str:
    raw=_text(v,label,2048)
    try: p=urlsplit(raw); port=p.port
    except ValueError as e: raise WorkRewardLedgerError(label+" is not a valid URL") from e
    if p.scheme.casefold()!="https" or not p.hostname or p.username is not None or p.password is not None or port not in (None,443):
        raise WorkRewardLedgerError(label+" must be a credential-free HTTPS URL")
    return raw

def _time(v: Any, label: str) -> Tuple[str, datetime]:
    if type(v) is not str or not _UTC.fullmatch(v): raise WorkRewardLedgerError(label+" must be canonical UTC")
    try: d=datetime.fromisoformat(v[:-1]+"+00:00").astimezone(timezone.utc)
    except ValueError as e: raise WorkRewardLedgerError(label+" must be canonical UTC") from e
    return v,d

def _sha(v: Any, label: str) -> str:
    if type(v) is not str or not _SHA.fullmatch(v): raise WorkRewardLedgerError(label+" must be lowercase SHA-256")
    return v

def _amount_text(v: Decimal) -> str:
    s=format(v,"f")
    if "." in s: s=s.rstrip("0").rstrip(".")
    return "0" if s in {"","-0"} else s

def _amount(v: Any, label: str) -> Tuple[Dict[str,str], Decimal]:
    r=_keys(v,{"value","unit"},label); raw=r["value"]
    if type(raw) is not str or not _AMT.fullmatch(raw): raise WorkRewardLedgerError(label+".value must be canonical positive decimal text")
    try: d=Decimal(raw)
    except InvalidOperation as e: raise WorkRewardLedgerError(label+".value is invalid") from e
    if not d.is_finite() or d<=0: raise WorkRewardLedgerError(label+".value must be positive")
    u=_text(r["unit"],label+".unit",24)
    if u!=u.upper() or not re.fullmatch(r"[A-Z][A-Z0-9._-]{0,23}",u): raise WorkRewardLedgerError(label+".unit must be canonical uppercase denomination")
    return {"value":_amount_text(d),"unit":u},d

def _opt_amount(v: Any, label: str): return (None,None) if v is None else _amount(v,label)

def _evidence(v: Any, synthetic: bool) -> Tuple[Dict[str,str], datetime]:
    r=_keys(v,{"source_uri","source_kind","observed_at","sha256"},"event.evidence")
    k=_text(r["source_kind"],"event.evidence.source_kind",64)
    if synthetic != (k=="synthetic_fixture"): raise WorkRewardLedgerError("synthetic evidence classification mismatch")
    t,dt=_time(r["observed_at"],"event.evidence.observed_at")
    return {"source_uri":_url(r["source_uri"],"event.evidence.source_uri"),"source_kind":k,"observed_at":t,"sha256":_sha(r["sha256"],"event.evidence.sha256")},dt

def _reference(v: Any):
    if v is None: return None
    r=_keys(v,{"kind","value"},"provider_reference"); k=_text(r["kind"],"provider_reference.kind",32)
    if k not in _REF_KINDS: raise WorkRewardLedgerError("provider_reference.kind is unsupported")
    return {"kind":k,"value":_text(r["value"],"provider_reference.value")}

def _settlement(v: Any, synthetic: bool):
    if v is None: return None,None,None
    r=_keys(v,{"receipt_id","amount","source_uri","observed_at","sha256"},"settlement")
    a,d=_amount(r["amount"],"settlement.amount"); t,dt=_time(r["observed_at"],"settlement.observed_at")
    out={"receipt_id":_text(r["receipt_id"],"settlement.receipt_id"),"amount":a,"source_uri":_url(r["source_uri"],"settlement.source_uri"),"observed_at":t,"sha256":_sha(r["sha256"],"settlement.sha256")}
    if synthetic and not out["source_uri"].startswith("https://example.invalid/"): raise WorkRewardLedgerError("synthetic settlement receipts must use example.invalid")
    return out,d,dt

def _event(raw: Any):
    names={"work_id","sequence","state","provider","program","synthetic","evidence","advertised_amount","award_amount","provider_reference","payment_rail","settlement"}
    r=_keys(raw,names,"event"); seq=r["sequence"]
    if type(seq) is not int or seq<=0: raise WorkRewardLedgerError("event.sequence must be a positive integer")
    state=_text(r["state"],"event.state",32)
    if state not in _STATES: raise WorkRewardLedgerError("event.state is unsupported")
    if type(r["synthetic"]) is not bool: raise WorkRewardLedgerError("event.synthetic must be boolean")
    syn=r["synthetic"]; ev,evdt=_evidence(r["evidence"],syn); adv,advd=_opt_amount(r["advertised_amount"],"advertised_amount"); award,awardd=_opt_amount(r["award_amount"],"award_amount")
    ref=_reference(r["provider_reference"]); rail=None if r["payment_rail"] is None else _text(r["payment_rail"],"payment_rail")
    settle,settled,setdt=_settlement(r["settlement"],syn)
    if adv is not None and state!="advertised": raise WorkRewardLedgerError("advertised_amount may appear only on advertised state")
    if state=="advertised" and adv is None: raise WorkRewardLedgerError("advertised state requires advertised_amount")
    if award is not None and state!="awarded": raise WorkRewardLedgerError("award_amount may appear only on awarded state")
    if state=="awarded" and award is None: raise WorkRewardLedgerError("awarded state requires award_amount")
    if rail is not None and state not in {"payment_pending","paid"}: raise WorkRewardLedgerError("payment_rail may appear only on payment_pending/paid states")
    if settle is not None and state!="paid": raise WorkRewardLedgerError("settlement receipt may appear only on paid state")
    if state=="paid" and settle is None: raise WorkRewardLedgerError("paid state requires a settlement receipt")
    if setdt is not None and setdt<evdt: raise WorkRewardLedgerError("settlement receipt predates its paid event evidence")
    out={"work_id":_text(r["work_id"],"event.work_id"),"sequence":seq,"state":state,"provider":_text(r["provider"],"event.provider"),"program":_text(r["program"],"event.program"),"synthetic":syn,"evidence":ev,"advertised_amount":adv,"award_amount":award,"provider_reference":ref,"payment_rail":rail,"settlement":settle}
    return out,{"observed":evdt,"settled":settled,"advertised":advd,"award":awardd}

def _payload(v: Any) -> List[Dict[str,Any]]:
    r=_keys(v,{"schema_version","events"},"event payload")
    if r["schema_version"]!=EVENTS_SCHEMA_VERSION: raise WorkRewardLedgerError("event payload schema is unsupported")
    xs=r["events"]
    if not isinstance(xs,list) or not xs or len(xs)>_MAX_EVENTS: raise WorkRewardLedgerError("events must be a non-empty bounded list")
    return [_event(x)[0] for x in xs]

def event_sha256(event: Any) -> str: return _hash(_event(event)[0])

def _transition(prev: Optional[str], cur: str):
    if prev is None: return
    if prev in _TERMINAL: raise WorkRewardLedgerConflict("terminal state cannot transition")
    if cur in _TERMINAL:
        if prev=="paid": raise WorkRewardLedgerConflict("paid work cannot regress into terminal non-payment state")
        return
    if _PROGRESS[cur]<_PROGRESS[prev]: raise WorkRewardLedgerConflict("state regression is forbidden")
    if _PROGRESS[cur]==_PROGRESS[prev] and cur!="paid": raise WorkRewardLedgerConflict("repeated lifecycle state is ambiguous")

def _compile(events: List[Dict[str,Any]]) -> Dict[str,Any]:
    norm=[]; meta={}; positions={}
    for raw in events:
        e,m=_event(raw); key=(e["work_id"],e["sequence"]); h=_hash(e)
        if key in positions: raise WorkRewardLedgerConflict("duplicate event position is not append-only" if positions[key]==h else "conflicting events occupy the same work sequence")
        positions[key]=h; norm.append(e); meta[key]=m
    norm.sort(key=lambda e:(e["work_id"],e["sequence"]))
    work=[]; totals={}; receipt_owner={}; i=0
    while i<len(norm):
        wid=norm[i]["work_id"]; group=[]
        while i<len(norm) and norm[i]["work_id"]==wid: group.append(norm[i]); i+=1
        if [e["sequence"] for e in group] != list(range(1,len(group)+1)): raise WorkRewardLedgerConflict("work event sequence must be contiguous from 1")
        first=group[0]; provider,program,syn=first["provider"],first["program"],first["synthetic"]
        prev=None; prevtime=None; adv=award=unit=None; refs=[]; rails=[]; settlements=[]; paid=Decimal("0"); evid=[]; edig=[]
        refset=set(); railset=set(); receipt_hashes=set()
        for e in group:
            if (e["provider"],e["program"],e["synthetic"])!=(provider,program,syn): raise WorkRewardLedgerConflict("work identity metadata changed across events")
            _transition(prev,e["state"]); prev=e["state"]; m=meta[(wid,e["sequence"])]
            if prevtime is not None and m["observed"]<prevtime: raise WorkRewardLedgerConflict("later lifecycle evidence is older than prior evidence")
            prevtime=m["observed"]
            if e["advertised_amount"] is not None:
                if adv is not None: raise WorkRewardLedgerConflict("advertised amount changed or repeated")
                adv=copy.deepcopy(e["advertised_amount"]); unit=adv["unit"]
            if e["award_amount"] is not None:
                if award is not None: raise WorkRewardLedgerConflict("award amount changed or repeated")
                award=copy.deepcopy(e["award_amount"])
                if unit is not None and award["unit"]!=unit: raise WorkRewardLedgerConflict("award denomination differs from advertised denomination")
                unit=unit or award["unit"]
            if e["provider_reference"] is not None:
                k=(e["provider_reference"]["kind"],e["provider_reference"]["value"])
                if k not in refset: refset.add(k); refs.append(copy.deepcopy(e["provider_reference"]))
            if e["payment_rail"] is not None and e["payment_rail"] not in railset: railset.add(e["payment_rail"]); rails.append(e["payment_rail"])
            s=e["settlement"]
            if s is not None:
                rid=s["receipt_id"]
                if rid in receipt_owner: raise WorkRewardLedgerConflict("settlement receipt_id is already bound to work "+receipt_owner[rid])
                if s["sha256"] in receipt_hashes: raise WorkRewardLedgerConflict("settlement receipt evidence is duplicated")
                if unit is not None and s["amount"]["unit"]!=unit: raise WorkRewardLedgerConflict("settlement denomination differs from reward denomination")
                unit=unit or s["amount"]["unit"]; receipt_owner[rid]=wid; receipt_hashes.add(s["sha256"]); paid+=m["settled"]; settlements.append(copy.deepcopy(s))
            evid.append(e["evidence"]["sha256"]); edig.append(_hash(e))
        if prev in _TERMINAL and settlements: raise WorkRewardLedgerConflict("terminal non-payment state contains settlement receipts")
        paid_amount=None
        if unit is not None:
            paid_amount={"value":_amount_text(paid),"unit":unit}; totals[unit]=totals.get(unit,Decimal("0"))+paid
        work.append({"work_id":wid,"provider":provider,"program":program,"synthetic":syn,"state":prev,"denomination":unit,"advertised_amount":adv,"award_amount":award,"provider_references":sorted(refs,key=lambda x:(x["kind"],x["value"])),"payment_rails":sorted(rails),"settlements":sorted(settlements,key=lambda x:x["receipt_id"]),"recognized_paid_amount":paid_amount,"event_sha256s":edig,"evidence_sha256s":evid})
    body={"schema_version":LEDGER_SCHEMA_VERSION,"events_schema_version":EVENTS_SCHEMA_VERSION,"events":norm,"work":work,"recognized_paid_totals":[{"unit":u,"amount":_amount_text(totals[u])} for u in sorted(totals)],"authority":copy.deepcopy(_AUTHORITY)}
    return {**body,"ledger_sha256":_hash(body)}

def compile_ledger(payload: Any) -> Dict[str,Any]: return _compile(_payload(payload))

def merge_event_streams(payloads: Iterable[Any]) -> Dict[str,Any]:
    combined={}; digests={}; found=False
    for payload in payloads:
        found=True
        for e in _payload(payload):
            k=(e["work_id"],e["sequence"]); h=_hash(e)
            if k in combined:
                if digests[k]!=h: raise WorkRewardLedgerConflict("merged streams conflict at one work sequence")
                continue
            combined[k]=e; digests[k]=h
    if not found or not combined: raise WorkRewardLedgerError("at least one event stream is required")
    return _compile(list(combined.values()))

def verify_ledger(raw: Any) -> Dict[str,Any]:
    names={"schema_version","events_schema_version","events","work","recognized_paid_totals","authority","ledger_sha256"}; r=_keys(raw,names,"ledger")
    if r["schema_version"]!=LEDGER_SCHEMA_VERSION or r["events_schema_version"]!=EVENTS_SCHEMA_VERSION: raise WorkRewardLedgerError("ledger schema is unsupported")
    if r["authority"]!=_AUTHORITY: raise WorkRewardLedgerError("ledger authority ceiling changed")
    digest=_sha(r["ledger_sha256"],"ledger_sha256"); body={k:copy.deepcopy(v) for k,v in r.items() if k!="ledger_sha256"}
    if _hash(body)!=digest: raise WorkRewardLedgerError("ledger digest mismatch")
    if canonical_json(_compile(r["events"]))!=canonical_json(r): raise WorkRewardLedgerError("ledger summary does not match embedded events")
    return copy.deepcopy(r)

def render_markdown(ledger: Any) -> str:
    l=verify_ledger(ledger)
    def md(v): return "—" if v is None else str(v).replace("\\","\\\\").replace("|","\\|").replace("\n"," ")
    def amt(v): return "—" if v is None else md(v["value"])+" "+md(v["unit"])
    lines=["# Work reward settlement ledger","","Ledger SHA-256: `"+l["ledger_sha256"]+"`","","| Work | Provider | Program | State | Advertised | Awarded | Recognized paid | Synthetic |","|---|---|---|---|---:|---:|---:|:---:|"]
    for r in l["work"]: lines.append("| "+" | ".join([md(r["work_id"]),md(r["provider"]),md(r["program"]),md(r["state"]),amt(r["advertised_amount"]),amt(r["award_amount"]),amt(r["recognized_paid_amount"]),"yes" if r["synthetic"] else "no"])+" |")
    lines += ["","## Recognized paid totals",""] + (["- "+md(x["unit"])+": "+md(x["amount"]) for x in l["recognized_paid_totals"]] or ["- none"])
    lines += ["","> Evidence ledger only. A listing, merge, acceptance, award, provider ticket, or payment rail is not cash. Only explicit settlement receipts contribute to recognized paid totals; denominations are never converted.",""]
    return "\n".join(lines)

def _pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise WorkRewardLedgerError("JSON contains duplicate object key")
        out[k]=v
    return out

def _constant(v): raise WorkRewardLedgerError("JSON contains non-finite number: "+v)
def _no_floats(v):
    if isinstance(v,float): raise WorkRewardLedgerError("JSON numbers must not use floating point")
    if isinstance(v,list):
        for x in v: _no_floats(x)
    elif isinstance(v,dict):
        for x in v.values(): _no_floats(x)

def load_json(path: str) -> Any:
    p=Path(path)
    try:
        if p.stat().st_size>_MAX_JSON_BYTES: raise WorkRewardLedgerError("JSON input is too large")
        v=json.loads(p.read_text(encoding="utf-8"),object_pairs_hook=_pairs,parse_constant=_constant)
    except WorkRewardLedgerError: raise
    except (OSError,UnicodeError,json.JSONDecodeError,ValueError) as e: raise WorkRewardLedgerError("JSON input is malformed") from e
    _no_floats(v); return v

def main(argv: Optional[List[str]]=None) -> int:
    p=argparse.ArgumentParser(prog="python -m concierge.work_reward_ledger"); sp=p.add_subparsers(dest="command",required=True)
    for name,help_text in (("compile","compile an event stream"),("verify","verify a sealed ledger")):
        q=sp.add_parser(name,help=help_text); q.add_argument("input"); q.add_argument("--format",choices=("json","markdown"),default="json")
    q=sp.add_parser("merge",help="merge compatible event streams"); q.add_argument("input",nargs="+"); q.add_argument("--format",choices=("json","markdown"),default="json")
    a=p.parse_args(argv)
    try:
        l=compile_ledger(load_json(a.input)) if a.command=="compile" else verify_ledger(load_json(a.input)) if a.command=="verify" else merge_event_streams(load_json(x) for x in a.input)
    except WorkRewardLedgerError as e: p.error(str(e))
    print(json.dumps(l,indent=2,sort_keys=True,ensure_ascii=False) if a.format=="json" else render_markdown(l),end="\n" if a.format=="json" else "")
    return 0

if __name__ == "__main__": raise SystemExit(main())
