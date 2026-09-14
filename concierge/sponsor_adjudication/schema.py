from .common import *
from .authority import verify_manifest_authority


def _validate_manifest(manifest: Any) -> Dict[str, Any]:
    root = _require_dict(manifest, where="manifest")
    _require_exact_keys(
        root,
        ["schema_version", "program", "findings", "submissions", "sponsor_events"],
        ["sponsor_authority"],
        where="manifest",
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != SCHEMA_VERSION:
        raise AdjudicationError(f"manifest.schema_version must be integer {SCHEMA_VERSION}")

    program = _require_dict(root["program"], where="program")
    _require_exact_keys(program, ["program_id", "sponsor", "source_ref"], where="program")
    program_id = _require_id(program["program_id"], where="program.program_id")
    sponsor = _require_text(program["sponsor"], where="program.sponsor")
    source_ref = _require_text(program["source_ref"], where="program.source_ref")
    bare_program = {"program_id": program_id, "sponsor": sponsor, "source_ref": source_ref}

    findings: Dict[str, Dict[str, Any]] = {}
    finding_order: List[str] = []
    for idx, raw in enumerate(_require_list(root["findings"], where="findings")):
        obj = _require_dict(raw, where=f"findings[{idx}]")
        _require_exact_keys(obj, ["finding_id", "fingerprint_sha256", "title", "submitted_by", "evidence_ref"], where=f"findings[{idx}]")
        finding_id = _require_id(obj["finding_id"], where=f"findings[{idx}].finding_id")
        if finding_id in findings:
            raise AdjudicationError(f"duplicate finding_id: {finding_id}")
        findings[finding_id] = {
            "finding_id": finding_id,
            "fingerprint_sha256": _require_hex64(obj["fingerprint_sha256"], where=f"findings[{idx}].fingerprint_sha256"),
            "title": _require_text(obj["title"], where=f"findings[{idx}].title"),
            "submitted_by": _require_text(obj["submitted_by"], where=f"findings[{idx}].submitted_by"),
            "evidence_ref": _require_text(obj["evidence_ref"], where=f"findings[{idx}].evidence_ref"),
        }
        finding_order.append(finding_id)
    if not findings:
        raise AdjudicationError("findings must not be empty")
    fingerprints: Dict[str, str] = {}
    for item in findings.values():
        fp = item["fingerprint_sha256"]
        if fp in fingerprints and fingerprints[fp] != item["finding_id"]:
            raise AdjudicationError("two finding_ids cannot claim the same fingerprint")
        fingerprints[fp] = item["finding_id"]

    submissions: Dict[str, Dict[str, Any]] = {}
    finding_submissions: Dict[str, List[str]] = {fid: [] for fid in findings}
    for idx, raw in enumerate(_require_list(root["submissions"], where="submissions")):
        obj = _require_dict(raw, where=f"submissions[{idx}]")
        _require_exact_keys(
            obj,
            ["submission_id", "finding_ids", "submitted_at", "channel", "receipt_ref", "receipt_sha256"],
            where=f"submissions[{idx}]",
        )
        submission_id = _require_id(obj["submission_id"], where=f"submissions[{idx}].submission_id")
        if submission_id in submissions:
            raise AdjudicationError(f"duplicate submission_id: {submission_id}")
        finding_ids = _string_id_list(obj["finding_ids"], where=f"submissions[{idx}].finding_ids")
        missing = [fid for fid in finding_ids if fid not in findings]
        if missing:
            raise AdjudicationError(f"submission {submission_id}: unknown finding_ids {missing}")
        submitted_at = _require_text(obj["submitted_at"], where=f"submissions[{idx}].submitted_at")
        submitted_dt = _parse_utc(submitted_at, where=f"submissions[{idx}].submitted_at")
        submission = {
            "submission_id": submission_id,
            "finding_ids": sorted(finding_ids),
            "submitted_at": submitted_at,
            "_submitted_dt": submitted_dt,
            "channel": _require_text(obj["channel"], where=f"submissions[{idx}].channel"),
            "receipt_ref": _require_text(obj["receipt_ref"], where=f"submissions[{idx}].receipt_ref"),
            "receipt_sha256": _require_hex64(obj["receipt_sha256"], where=f"submissions[{idx}].receipt_sha256"),
        }
        submissions[submission_id] = submission
        for fid in finding_ids:
            finding_submissions[fid].append(submission_id)
    if not submissions:
        raise AdjudicationError("submissions must not be empty")
    unsubmitted = sorted(fid for fid, sids in finding_submissions.items() if not sids)
    if unsubmitted:
        raise AdjudicationError(f"findings missing submission evidence: {unsubmitted}")

    events: List[Dict[str, Any]] = []
    event_ids = set()
    for idx, raw in enumerate(_require_list(root["sponsor_events"], where="sponsor_events")):
        obj = _require_dict(raw, where=f"sponsor_events[{idx}]")
        required = ["event_id", "event_type", "event_at", "source_ref", "source_sha256", "claim_unit_id"]
        optional = ["finding_ids", "submission_ids", "amount", "currency", "collapsed_claim_unit_ids", "note"]
        _require_exact_keys(obj, required, optional, where=f"sponsor_events[{idx}]")
        event_id = _require_id(obj["event_id"], where=f"sponsor_events[{idx}].event_id")
        if event_id in event_ids:
            raise AdjudicationError(f"duplicate sponsor event_id: {event_id}")
        event_ids.add(event_id)
        event_type = _require_text(obj["event_type"], where=f"sponsor_events[{idx}].event_type")
        if event_type not in EVENT_TYPES:
            raise AdjudicationError(f"sponsor event {event_id}: unsupported type {event_type}")
        event_at = _require_text(obj["event_at"], where=f"sponsor_events[{idx}].event_at")
        event_dt = _parse_utc(event_at, where=f"sponsor_events[{idx}].event_at")
        claim_unit_id = _require_id(obj["claim_unit_id"], where=f"sponsor_events[{idx}].claim_unit_id")
        finding_ids = _string_id_list(obj.get("finding_ids", []), where=f"sponsor_events[{idx}].finding_ids", nonempty=False)
        submission_ids = _string_id_list(obj.get("submission_ids", []), where=f"sponsor_events[{idx}].submission_ids", nonempty=False)
        for fid in finding_ids:
            if fid not in findings:
                raise AdjudicationError(f"sponsor event {event_id}: unknown finding {fid}")
        for sid in submission_ids:
            if sid not in submissions:
                raise AdjudicationError(f"sponsor event {event_id}: unknown submission {sid}")
        if finding_ids:
            event_finding_set = set(finding_ids)
            for sid in submission_ids:
                if not (set(submissions[sid]["finding_ids"]) & event_finding_set):
                    raise AdjudicationError(f"sponsor event {event_id}: referenced submission has no covered finding")
        collapsed_ids = _string_id_list(obj.get("collapsed_claim_unit_ids", []), where=f"sponsor_events[{idx}].collapsed_claim_unit_ids", nonempty=False)
        amount_text = None
        amount_dec = None
        currency = None
        if "amount" in obj:
            amount_text, amount_dec = _amount(obj["amount"], where=f"sponsor_events[{idx}].amount", positive=True)
        if "currency" in obj:
            currency = _require_text(obj["currency"], where=f"sponsor_events[{idx}].currency")
            if not CURRENCY_RE.fullmatch(currency):
                raise AdjudicationError(f"sponsor_events[{idx}].currency: invalid currency code")
        if event_type in {"REWARD_OFFERED", "PAYMENT_REPORTED"}:
            if amount_text is None or currency is None:
                raise AdjudicationError(f"sponsor event {event_id}: {event_type} requires amount and currency")
        elif amount_text is not None or currency is not None:
            raise AdjudicationError(f"sponsor event {event_id}: amount/currency allowed only on reward/payment events")
        if event_type == "DUPLICATE_COLLAPSED":
            if not collapsed_ids:
                raise AdjudicationError(f"sponsor event {event_id}: collapse requires collapsed_claim_unit_ids")
            if finding_ids or submission_ids:
                raise AdjudicationError(f"sponsor event {event_id}: collapse derives membership from source units")
        elif collapsed_ids:
            raise AdjudicationError(f"sponsor event {event_id}: collapsed_claim_unit_ids only valid for collapse")
        note = _require_text(obj["note"], where=f"sponsor_events[{idx}].note", allow_empty=False) if "note" in obj else None
        events.append({
            "event_id": event_id,
            "event_type": event_type,
            "event_at": event_at,
            "_event_dt": event_dt,
            "source_ref": _require_text(obj["source_ref"], where=f"sponsor_events[{idx}].source_ref"),
            "source_sha256": _require_hex64(obj["source_sha256"], where=f"sponsor_events[{idx}].source_sha256"),
            "claim_unit_id": claim_unit_id,
            "finding_ids": sorted(finding_ids),
            "submission_ids": sorted(submission_ids),
            "collapsed_claim_unit_ids": sorted(collapsed_ids),
            "amount": amount_text,
            "_amount_dec": amount_dec,
            "currency": currency,
            "note": note,
        })

    events.sort(key=lambda e: (e["_event_dt"], e["event_id"]))
    authority = verify_manifest_authority(root, bare_program, events, root.get("sponsor_authority"))
    normalized_program: Dict[str, Any] = dict(bare_program)
    if authority is not None:
        normalized_program["sponsor_authority"] = authority
    return {
        "schema_version": SCHEMA_VERSION,
        "program": normalized_program,
        "findings": findings,
        "finding_order": finding_order,
        "submissions": submissions,
        "finding_submissions": finding_submissions,
        "events": events,
    }
