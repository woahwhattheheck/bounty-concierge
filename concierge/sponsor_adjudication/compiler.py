from .common import *
from .schema import _validate_manifest

def _new_unit(unit_id: str) -> Dict[str, Any]:
    return {
        "claim_unit_id": unit_id,
        "status": "submitted",
        "finding_ids": [],
        "submission_ids": [],
        "event_ids": [],
        "sponsor_received": False,
        "sponsor_verified": False,
        "reward_amount": None,
        "reward_currency": None,
        "collapsed_into": None,
        "first_sponsor_event_at": None,
        "last_sponsor_event_at": None,
        "last_event_dt": None,
    }


def _assign_members(unit: MutableMapping[str, Any], event: Mapping[str, Any], finding_to_unit: MutableMapping[str, str]) -> None:
    event_findings = list(event["finding_ids"])
    event_submissions = list(event["submission_ids"])
    if not event_findings and not unit["finding_ids"]:
        raise AdjudicationError(f"sponsor event {event['event_id']}: first unit event must bind finding_ids")
    if event_findings:
        for fid in event_findings:
            existing = finding_to_unit.get(fid)
            if existing is not None and existing != unit["claim_unit_id"]:
                raise AdjudicationError(
                    f"sponsor event {event['event_id']}: finding {fid} already bound to active unit {existing}; explicit collapse required"
                )
        combined = sorted(set(unit["finding_ids"]) | set(event_findings))
        unit["finding_ids"] = combined
        for fid in combined:
            finding_to_unit[fid] = unit["claim_unit_id"]
    if event_submissions:
        unit["submission_ids"] = sorted(set(unit["submission_ids"]) | set(event_submissions))


def _require_chronology(event: Mapping[str, Any], unit: Mapping[str, Any], submissions: Mapping[str, Mapping[str, Any]]) -> None:
    if unit["last_event_dt"] is not None and event["_event_dt"] <= unit["last_event_dt"]:
        raise AdjudicationError(
            f"sponsor event {event['event_id']}: claim-unit sponsor events require strictly increasing timestamps"
        )
    referenced = set(event["submission_ids"]) | set(unit["submission_ids"])
    if not referenced and event["finding_ids"]:
        for sid, sub in submissions.items():
            if set(sub["finding_ids"]) & set(event["finding_ids"]):
                referenced.add(sid)
    if referenced:
        earliest = min(submissions[sid]["_submitted_dt"] for sid in referenced)
        if event["_event_dt"] < earliest:
            raise AdjudicationError(f"sponsor event {event['event_id']}: predates referenced submission evidence")


def compile_manifest(manifest: Any) -> Dict[str, Any]:
    normalized = _validate_manifest(manifest)
    findings = normalized["findings"]
    submissions = normalized["submissions"]
    events = normalized["events"]

    units: Dict[str, Dict[str, Any]] = {}
    finding_to_unit: Dict[str, str] = {}

    for event in events:
        unit_id = event["claim_unit_id"]
        if event["event_type"] == "DUPLICATE_COLLAPSED":
            target = units.get(unit_id)
            if target is None:
                raise AdjudicationError(f"collapse {event['event_id']}: target unit {unit_id} does not exist")
            if target["reward_amount"] is not None:
                raise AdjudicationError(f"collapse {event['event_id']}: target unit {unit_id} already has reward authority")
            _require_chronology(event, target, submissions)
            for source_id in event["collapsed_claim_unit_ids"]:
                if source_id == unit_id:
                    raise AdjudicationError(f"collapse {event['event_id']}: unit cannot collapse into itself")
                source = units.get(source_id)
                if source is None:
                    raise AdjudicationError(f"collapse {event['event_id']}: source unit {source_id} does not exist")
                if source["status"] in {"duplicate", "declined", "paid_evidence_pending"}:
                    raise AdjudicationError(f"collapse {event['event_id']}: source unit {source_id} is terminal")
                if source["reward_amount"] is not None:
                    raise AdjudicationError(f"collapse {event['event_id']}: source unit {source_id} already has reward authority")
                if source["last_event_dt"] is not None and event["_event_dt"] <= source["last_event_dt"]:
                    raise AdjudicationError(f"collapse {event['event_id']}: collapse must be later than source history")
                for fid in source["finding_ids"]:
                    finding_to_unit[fid] = unit_id
                target["finding_ids"] = sorted(set(target["finding_ids"]) | set(source["finding_ids"]))
                target["submission_ids"] = sorted(set(target["submission_ids"]) | set(source["submission_ids"]))
                target["sponsor_received"] = bool(target["sponsor_received"] or source["sponsor_received"])
                target["sponsor_verified"] = bool(target["sponsor_verified"] or source["sponsor_verified"])
                if source["first_sponsor_event_at"] is not None:
                    if target["first_sponsor_event_at"] is None or source["first_sponsor_event_at"] < target["first_sponsor_event_at"]:
                        target["first_sponsor_event_at"] = source["first_sponsor_event_at"]
                pre_reward_rank = {"submitted": 0, "sponsor_verified": 1, "adjudicating": 2}
                if target["status"] in pre_reward_rank and source["status"] in pre_reward_rank:
                    if pre_reward_rank[source["status"]] > pre_reward_rank[target["status"]]:
                        target["status"] = source["status"]
                source["status"] = "duplicate"
                source["collapsed_into"] = unit_id
                source["event_ids"].append(event["event_id"])
                source["last_sponsor_event_at"] = event["event_at"]
                source["last_event_dt"] = event["_event_dt"]
            target["event_ids"].append(event["event_id"])
            target["last_sponsor_event_at"] = event["event_at"]
            target["last_event_dt"] = event["_event_dt"]
            continue

        unit = units.get(unit_id)
        if unit is None:
            if event["event_type"] not in {"SPONSOR_RECEIVED", "SPONSOR_VERIFIED", "ADJUDICATION_STARTED"}:
                raise AdjudicationError(f"sponsor event {event['event_id']}: cannot create unit with {event['event_type']}")
            unit = _new_unit(unit_id)
            units[unit_id] = unit
        if unit["status"] in {"duplicate", "declined", "paid_evidence_pending"}:
            raise AdjudicationError(f"sponsor event {event['event_id']}: terminal unit {unit_id} cannot advance")
        _require_chronology(event, unit, submissions)
        _assign_members(unit, event, finding_to_unit)

        if event["event_type"] == "SPONSOR_RECEIVED":
            unit["sponsor_received"] = True
        elif event["event_type"] == "SPONSOR_VERIFIED":
            unit["sponsor_received"] = True
            unit["sponsor_verified"] = True
            if unit["status"] == "submitted":
                unit["status"] = "sponsor_verified"
        elif event["event_type"] == "ADJUDICATION_STARTED":
            unit["sponsor_received"] = True
            if unit["status"] == "reward_offered":
                raise AdjudicationError(f"sponsor event {event['event_id']}: adjudication cannot follow a reward offer")
            unit["status"] = "adjudicating"
        elif event["event_type"] == "REWARD_OFFERED":
            unit["sponsor_received"] = True
            if unit["reward_amount"] is not None:
                if unit["reward_amount"] != event["amount"] or unit["reward_currency"] != event["currency"]:
                    raise AdjudicationError(f"sponsor event {event['event_id']}: conflicting reward offer")
            unit["reward_amount"] = event["amount"]
            unit["reward_currency"] = event["currency"]
            unit["status"] = "reward_offered"
        elif event["event_type"] == "DECLINED":
            if unit["reward_amount"] is not None:
                raise AdjudicationError(f"sponsor event {event['event_id']}: cannot decline after reward offer")
            unit["status"] = "declined"
        elif event["event_type"] == "PAYMENT_REPORTED":
            if unit["reward_amount"] is None:
                raise AdjudicationError(f"sponsor event {event['event_id']}: payment report requires prior reward offer")
            if unit["reward_amount"] != event["amount"] or unit["reward_currency"] != event["currency"]:
                raise AdjudicationError(f"sponsor event {event['event_id']}: payment report must equal prior reward offer")
            unit["status"] = "paid_evidence_pending"
        else:  # pragma: no cover - validated above
            raise AdjudicationError(f"unhandled event type {event['event_type']}")

        unit["event_ids"].append(event["event_id"])
        if unit["first_sponsor_event_at"] is None:
            unit["first_sponsor_event_at"] = event["event_at"]
        unit["last_sponsor_event_at"] = event["event_at"]
        unit["last_event_dt"] = event["_event_dt"]

    # Sponsor events are optional: unsponsored findings remain submitted and owner-review only.
    unit_rows: List[Dict[str, Any]] = []
    for unit_id in sorted(units):
        unit = units[unit_id]
        if not unit["finding_ids"]:
            raise AdjudicationError(f"claim unit {unit_id}: no findings")
        status = unit["status"]
        if status not in UNIT_STATUSES:
            raise AdjudicationError(f"claim unit {unit_id}: invalid compiled status")
        if status == "duplicate":
            action = "DO_NOT_RESUBMIT"
        elif status in {"sponsor_verified", "adjudicating"} or unit["sponsor_received"]:
            action = "WAIT_SPONSOR"
        else:
            action = "OWNER_REVIEW"
        if status in {"reward_offered", "paid_evidence_pending", "declined"}:
            action = "OWNER_REVIEW" if status in {"reward_offered", "paid_evidence_pending"} else "DO_NOT_RESUBMIT"
        unit_rows.append({
            "claim_unit_id": unit_id,
            "status": status,
            "action": action,
            "finding_ids": sorted(unit["finding_ids"]),
            "submission_ids": sorted(unit["submission_ids"]),
            "event_ids": list(unit["event_ids"]),
            "sponsor_received": bool(unit["sponsor_received"]),
            "sponsor_verified": bool(unit["sponsor_verified"]),
            "reward_amount": unit["reward_amount"],
            "reward_currency": unit["reward_currency"],
            "collapsed_into": unit["collapsed_into"],
            "first_sponsor_event_at": unit["first_sponsor_event_at"],
            "last_sponsor_event_at": unit["last_sponsor_event_at"],
            "cash_status": "not_inferred",
        })

    finding_rows: List[Dict[str, Any]] = []
    for fid in sorted(findings):
        sid_list = sorted(normalized["finding_submissions"][fid])
        unit_id = finding_to_unit.get(fid)
        unit = units.get(unit_id) if unit_id else None
        if unit is None:
            finding_status = "submitted"
            action = "OWNER_REVIEW"
            canonical_unit = None
        else:
            if unit["status"] == "duplicate" and unit["collapsed_into"]:
                canonical_unit = unit["collapsed_into"]
                canonical = units[canonical_unit]
                finding_status = canonical["status"]
            else:
                canonical_unit = unit_id
                finding_status = unit["status"]
            action = "DO_NOT_RESUBMIT" if unit["sponsor_received"] or unit["status"] in {"duplicate", "declined", "reward_offered", "paid_evidence_pending"} else "OWNER_REVIEW"
        finding_rows.append({
            "finding_id": fid,
            "fingerprint_sha256": findings[fid]["fingerprint_sha256"],
            "title": findings[fid]["title"],
            "submitted_by": findings[fid]["submitted_by"],
            "evidence_ref": findings[fid]["evidence_ref"],
            "submission_ids": sid_list,
            "submission_count": len(sid_list),
            "duplicate_submission_count": max(0, len(sid_list) - 1),
            "canonical_claim_unit_id": canonical_unit,
            "status": finding_status,
            "action": action,
            "cash_status": "not_inferred",
        })

    event_rows: List[Dict[str, Any]] = []
    for event in events:
        event_rows.append({
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "event_at": event["event_at"],
            "source_ref": event["source_ref"],
            "source_sha256": event["source_sha256"],
            "claim_unit_id": event["claim_unit_id"],
            "finding_ids": event["finding_ids"],
            "submission_ids": event["submission_ids"],
            "collapsed_claim_unit_ids": event["collapsed_claim_unit_ids"],
            "amount": event["amount"],
            "currency": event["currency"],
            "note": event["note"],
        })

    summary = {
        "finding_count": len(finding_rows),
        "submission_count": len(submissions),
        "sponsor_event_count": len(event_rows),
        "claim_unit_count": len(unit_rows),
        "sponsor_verified_finding_count": sum(1 for row in finding_rows if row["canonical_claim_unit_id"] and units[row["canonical_claim_unit_id"]]["sponsor_verified"]),
        "duplicate_submission_count": sum(row["duplicate_submission_count"] for row in finding_rows),
        "cash_recognized": False,
    }

    report_core = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "program": deepcopy(normalized["program"]),
        "summary": summary,
        "claim_units": unit_rows,
        "findings": finding_rows,
        "sponsor_events": event_rows,
        "authority_ceiling": deepcopy(AUTHORITY_CEILING),
    }
    report = deepcopy(report_core)
    report["report_sha256"] = _sha256_obj(report_core)
    return report


