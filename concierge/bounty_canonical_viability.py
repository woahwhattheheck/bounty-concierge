# SPDX-License-Identifier: MIT
"""Fail-closed canonical-state gate for externally listed $50+ bounties."""
from __future__ import annotations

import argparse, hashlib, json, re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "bounty-canonical-viability/v2"
RECEIPT = "bounty-canonical-viability-receipt/v2"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
ISSUE = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$")
PULL = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?$")
AUTHORITY = {"advisory_only": True, "claim_authority": False,
             "implementation_authority": False, "submission_authority": False,
             "payment_or_wallet_authority": False}


class BountyCanonicalViabilityInputError(ValueError):
    pass


def _obj(v: Any, name: str) -> dict[str, Any]:
    if type(v) is not dict: raise BountyCanonicalViabilityInputError(f"{name} must be an object")
    return v


def _txt(v: Any, name: str, n: int = 2048) -> str:
    if type(v) is not str or not v or v != v.strip() or len(v) > n:
        raise BountyCanonicalViabilityInputError(f"{name} must be a non-empty trimmed string")
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise BountyCanonicalViabilityInputError(f"{name} contains control characters")
    return v


def _login(v: Any, name: str) -> str:
    s = _txt(v, name, 39)
    if not LOGIN.fullmatch(s): raise BountyCanonicalViabilityInputError(f"{name} is not a GitHub login")
    return s.casefold()


def _time(v: Any, name: str) -> datetime:
    s = _txt(v, name, 64)
    if not s.endswith("Z"): raise BountyCanonicalViabilityInputError(f"{name} must end in Z")
    try: d = datetime.fromisoformat(s[:-1] + "+00:00")
    except ValueError as e: raise BountyCanonicalViabilityInputError(f"{name} is not RFC3339") from e
    return d


def _age(v: Any, name: str, now: datetime, max_age: int) -> tuple[str, int, bool]:
    s = _txt(v, name, 64); d = _time(s, name)
    if d > now: raise BountyCanonicalViabilityInputError(f"{name} is in the future")
    age = int((now - d).total_seconds()); return s, age, age <= max_age


def _url(v: Any, name: str) -> tuple[str, str, str]:
    s = _txt(v, name)
    if any(c.isspace() for c in s) or "\\" in s or "%" in s:
        raise BountyCanonicalViabilityInputError(f"{name} must be an unencoded HTTPS URL")
    try: p = urlsplit(s); port = p.port
    except ValueError as e: raise BountyCanonicalViabilityInputError(f"{name} is invalid") from e
    if p.scheme.lower() != "https" or not p.hostname or p.username or p.password or port is not None or p.query or p.fragment:
        raise BountyCanonicalViabilityInputError(f"{name} must be canonical HTTPS")
    return s, p.hostname.casefold(), p.path


def _gh(v: Any, name: str, pattern: re.Pattern[str]) -> tuple[str, str, int, str]:
    s, host, path = _url(v, name)
    if host not in {"github.com", "www.github.com"}: raise BountyCanonicalViabilityInputError(f"{name} must use github.com")
    m = pattern.fullmatch(path)
    if not m: raise BountyCanonicalViabilityInputError(f"{name} has wrong GitHub resource shape")
    a, b, n = m.groups(); return a.casefold(), b.casefold(), int(n), s


def _exact(o: dict[str, Any], keys: set[str], name: str) -> None:
    if set(o) != keys: raise BountyCanonicalViabilityInputError(f"{name} fields are invalid")


def compile_bounty_canonical_viability(request: dict[str, Any]) -> dict[str, Any]:
    r = _obj(request, "request")
    keys = {"schema","value_gate","actor_login","canonical_issue_url","listing","repository",
            "issue","reward","collisions","evaluated_at","max_snapshot_age_seconds","claim_pressure_threshold"}
    _exact(r, keys, "request")
    if r["schema"] != SCHEMA: raise BountyCanonicalViabilityInputError(f"schema must equal {SCHEMA}")
    now_s = _txt(r["evaluated_at"], "evaluated_at", 64); now = _time(now_s, "evaluated_at")
    max_age = r["max_snapshot_age_seconds"]; pressure = r["claim_pressure_threshold"]
    if isinstance(max_age, bool) or not isinstance(max_age, int) or not 60 <= max_age <= 86400:
        raise BountyCanonicalViabilityInputError("max_snapshot_age_seconds must be 60..86400")
    if isinstance(pressure, bool) or not isinstance(pressure, int) or not 1 <= pressure <= 100:
        raise BountyCanonicalViabilityInputError("claim_pressure_threshold must be 1..100")
    actor = _login(r["actor_login"], "actor_login")
    owner, repo, number, issue_url = _gh(r["canonical_issue_url"], "canonical_issue_url", ISSUE)

    vg = _obj(r["value_gate"], "value_gate"); _exact(vg, {"work_id","canonical_source_url","disposition","receipt_sha256"}, "value_gate")
    work_id = _txt(vg["work_id"], "value_gate.work_id", 200)
    value_owner, value_repo, value_number, value_url = _gh(
        vg["canonical_source_url"], "value_gate.canonical_source_url", ISSUE
    )
    if (value_owner, value_repo, value_number) != (owner, repo, number):
        raise BountyCanonicalViabilityInputError(
            "value_gate canonical_source_url must match canonical issue"
        )
    if vg["disposition"] != "VALUE_50_PLUS" or not HEX64.fullmatch(_txt(vg["receipt_sha256"], "value_gate.receipt_sha256", 64)):
        raise BountyCanonicalViabilityInputError("value_gate must bind a VALUE_50_PLUS receipt")

    listing = _obj(r["listing"], "listing"); _exact(listing,{"url","state","observed_at"},"listing")
    listing_url, _, _ = _url(listing["url"], "listing.url")
    ls = _txt(listing["state"], "listing.state", 16).upper()
    if ls not in {"OPEN","CLOSED","UNKNOWN"}: raise BountyCanonicalViabilityInputError("listing.state is invalid")
    lo, la, lf = _age(listing["observed_at"], "listing.observed_at", now, max_age)

    repository = _obj(r["repository"], "repository"); _exact(repository,{"full_name","archived","observed_at"},"repository")
    full = _txt(repository["full_name"], "repository.full_name", 200)
    if full.casefold() != f"{owner}/{repo}" or type(repository["archived"]) is not bool:
        raise BountyCanonicalViabilityInputError("repository does not match canonical issue")
    ro, ra, rf = _age(repository["observed_at"], "repository.observed_at", now, max_age)

    issue = _obj(r["issue"], "issue"); _exact(issue,{"state","state_reason","acceptance","assignees","observed_at"},"issue")
    state = _txt(issue["state"], "issue.state", 16).upper(); acceptance = _txt(issue["acceptance"], "issue.acceptance", 32).upper()
    if state not in {"OPEN","CLOSED"} or acceptance not in {"ACCEPTED","UNACCEPTED_PROPOSAL","UNKNOWN"}:
        raise BountyCanonicalViabilityInputError("issue state/acceptance is invalid")
    reason = issue["state_reason"]
    if reason is not None: reason = _txt(reason,"issue.state_reason",32).upper()
    if type(issue["assignees"]) is not list or len(issue["assignees"]) > 100: raise BountyCanonicalViabilityInputError("issue.assignees is invalid")
    assignees = [_login(x,f"issue.assignees[{i}]") for i,x in enumerate(issue["assignees"])]
    if len(set(assignees)) != len(assignees): raise BountyCanonicalViabilityInputError("duplicate assignee")
    io, ia, inf = _age(issue["observed_at"], "issue.observed_at", now, max_age); actor_assigned = actor in assignees

    reward = _obj(r["reward"], "reward"); _exact(reward,{"payment_path","assignment_required","actor_applied","observed_at"},"reward")
    pay = _txt(reward["payment_path"],"reward.payment_path",32).upper()
    if pay not in {"VERIFIED","UNVERIFIED","SELECTION_GATED","REPORT_ONLY"}: raise BountyCanonicalViabilityInputError("reward.payment_path is invalid")
    if type(reward["assignment_required"]) is not bool or type(reward["actor_applied"]) is not bool: raise BountyCanonicalViabilityInputError("reward booleans are invalid")
    wo, wa, wf = _age(reward["observed_at"], "reward.observed_at", now, max_age)

    col = _obj(r["collisions"],"collisions"); _exact(col,{"open_prs","active_claim_count","maintainer_confirmed_residual","observed_at"},"collisions")
    co, ca, cf = _age(col["observed_at"], "collisions.observed_at", now, max_age)
    if type(col["open_prs"]) is not list or len(col["open_prs"]) > 100: raise BountyCanonicalViabilityInputError("collisions.open_prs is invalid")
    if isinstance(col["active_claim_count"],bool) or not isinstance(col["active_claim_count"],int) or not 0 <= col["active_claim_count"] <= 500: raise BountyCanonicalViabilityInputError("active_claim_count is invalid")
    if type(col["maintainer_confirmed_residual"]) is not bool: raise BountyCanonicalViabilityInputError("maintainer_confirmed_residual must be bool")
    prs=[]; seen=set(); stale_pr=False
    for i,praw in enumerate(col["open_prs"]):
        p=_obj(praw,f"open_prs[{i}]"); _exact(p,{"url","author_login","overlap","observed_at"},f"open_prs[{i}]")
        po,pr,pn,pu=_gh(p["url"],f"open_prs[{i}].url",PULL)
        if (po,pr)!=(owner,repo) or (po,pr,pn) in seen: raise BountyCanonicalViabilityInputError("open PR identity is invalid")
        seen.add((po,pr,pn)); author=_login(p["author_login"],f"open_prs[{i}].author_login")
        overlap=_txt(p["overlap"],f"open_prs[{i}].overlap",16).upper()
        if overlap not in {"FULL","PARTIAL","UNKNOWN"}: raise BountyCanonicalViabilityInputError("open PR overlap is invalid")
        obs,age,fresh=_age(p["observed_at"],f"open_prs[{i}].observed_at",now,max_age); stale_pr |= not fresh
        prs.append({"url":pu,"author_login":author,"overlap":overlap,"observed_at":obs,"actor_owned":author==actor})

    reasons=[]
    if not all((lf,rf,inf,wf,cf)): reasons.append("SNAPSHOT_STALE")
    if stale_pr: reasons.append("COLLISION_SNAPSHOT_STALE")
    if ls=="CLOSED": reasons.append("LISTING_NOT_OPEN")
    elif ls=="UNKNOWN": reasons.append("LISTING_STATE_UNKNOWN")
    if ls=="OPEN" and state=="CLOSED": reasons.append("LISTING_CANONICAL_STATE_MISMATCH")
    if repository["archived"]: reasons.append("CANONICAL_REPOSITORY_ARCHIVED")
    if state!="OPEN": reasons.append("CANONICAL_ISSUE_NOT_OPEN")
    if acceptance=="UNACCEPTED_PROPOSAL": reasons.append("PROPOSAL_NOT_ACCEPTED")
    elif acceptance=="UNKNOWN": reasons.append("CANONICAL_ACCEPTANCE_UNKNOWN")
    if any(a!=actor for a in assignees) and not actor_assigned: reasons.append("ASSIGNED_TO_OTHER")
    blocking=[p for p in prs if not p["actor_owned"] and p["overlap"] in {"FULL","UNKNOWN"}]
    residual=col["maintainer_confirmed_residual"]
    if blocking and not residual: reasons.append("OVERLAPPING_OPEN_PR_PRESENT")
    if col["active_claim_count"]>=pressure and not residual and not actor_assigned: reasons.append("CLAIM_PRESSURE_HIGH")
    if pay=="UNVERIFIED": reasons.append("PAYMENT_PATH_UNVERIFIED")
    elif pay=="SELECTION_GATED" and not actor_assigned: reasons.append("SELECTION_GATE_NOT_SATISFIED")
    elif pay=="REPORT_ONLY": reasons.append("REPORT_ONLY_NOT_IMPLEMENTATION_BOUNTY")

    terminal={"LISTING_NOT_OPEN","LISTING_CANONICAL_STATE_MISMATCH","CANONICAL_REPOSITORY_ARCHIVED","CANONICAL_ISSUE_NOT_OPEN","REPORT_ONLY_NOT_IMPLEMENTATION_BOUNTY"}
    if terminal.intersection(reasons): disp,nexta="PRUNE","REMOVE_FROM_ACTIVE_BUILD_QUEUE"
    elif reasons: disp,nexta="HOLD","REFRESH_OR_RESOLVE_CANONICAL_BLOCKERS"
    elif reward["assignment_required"] and not actor_assigned:
        disp,nexta=("WAIT_ASSIGNMENT","WAIT_FOR_ASSIGNMENT_WITHOUT_IMPLEMENTATION") if reward["actor_applied"] else ("READY_FOR_CLAIM_REVIEW","REVIEW_CLAIM_OR_APPLICATION_ROUTE")
    elif actor_assigned: disp,nexta="READY_FOR_IMPLEMENTATION_REVIEW","REVIEW_ASSIGNED_SCOPE_BEFORE_IMPLEMENTATION"
    else: disp,nexta="READY_FOR_CLAIM_REVIEW","REVIEW_CLAIM_OR_APPLICATION_ROUTE"

    normalized={"schema":SCHEMA,"value_gate":{"work_id":work_id,"canonical_source_url":value_url,"disposition":"VALUE_50_PLUS","receipt_sha256":vg["receipt_sha256"]},"actor_login":actor,"canonical_issue_url":issue_url,
      "listing":{"url":listing_url,"state":ls,"observed_at":lo},"repository":{"full_name":full,"archived":repository["archived"],"observed_at":ro},
      "issue":{"state":state,"state_reason":reason,"acceptance":acceptance,"assignees":assignees,"observed_at":io},
      "reward":{"payment_path":pay,"assignment_required":reward["assignment_required"],"actor_applied":reward["actor_applied"],"observed_at":wo},
      "collisions":{"open_prs":[{k:p[k] for k in ("url","author_login","overlap","observed_at")} for p in prs],"active_claim_count":col["active_claim_count"],"maintainer_confirmed_residual":residual,"observed_at":co},
      "evaluated_at":now_s,"max_snapshot_age_seconds":max_age,"claim_pressure_threshold":pressure}
    body={"schema":RECEIPT,"input":normalized,"identity":{"owner":owner,"repo":repo,"issue_number":number,"work_id":work_id},"disposition":disp,"advisory_next_action":nexta,"reason_codes":reasons,
          "state":{"canonical_state_mismatch":ls=="OPEN" and state=="CLOSED","actor_assigned":actor_assigned,"blocking_pr_count":len(blocking),"active_claim_count":col["active_claim_count"],"payment_path":pay},
          "freshness":{"listing_age_seconds":la,"repository_age_seconds":ra,"issue_age_seconds":ia,"reward_age_seconds":wa,"collisions_age_seconds":ca,"max_snapshot_age_seconds":max_age},"authority":AUTHORITY,
          "composition_rule":"verify bounty_value_router receipt first; this receipt binds its work_id/canonical_source_url/disposition/digest to the same canonical issue"}
    return {**body,"receipt_sha256":hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()}


def verify_receipt(receipt: dict[str, Any], *, semantic: bool = True) -> bool:
    if type(receipt) is not dict or receipt.get("schema")!=RECEIPT or receipt.get("authority")!=AUTHORITY: return False
    digest=receipt.get("receipt_sha256")
    if type(digest) is not str or not HEX64.fullmatch(digest): return False
    body=dict(receipt); body.pop("receipt_sha256",None)
    if hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()!=digest: return False
    if not semantic: return True
    try: return compile_bounty_canonical_viability(receipt["input"])==receipt
    except (BountyCanonicalViabilityInputError,KeyError,TypeError,ValueError): return False


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(); p.add_argument("snapshot"); p.add_argument("--json",action="store_true"); a=p.parse_args(argv)
    try:
        payload=json.load(__import__("sys").stdin) if a.snapshot=="-" else json.loads(Path(a.snapshot).read_text())
        receipt=compile_bounty_canonical_viability(payload)
    except (OSError,json.JSONDecodeError,BountyCanonicalViabilityInputError) as e: p.error(str(e))
    print(json.dumps(receipt,indent=2,sort_keys=True) if a.json else f"{receipt['disposition']} {receipt['identity']['owner']}/{receipt['identity']['repo']}#{receipt['identity']['issue_number']} {','.join(receipt['reason_codes']) or 'none'}")
    return 0 if receipt["disposition"].startswith("READY_") else 2


if __name__ == "__main__": raise SystemExit(main())
