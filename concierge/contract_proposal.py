# SPDX-License-Identifier: MIT
"""Evidence-bound owner-review packets for qualified external paid contracts.

The module re-verifies ``contract_qualification`` at trusted current time and
assembles a proposal.  It never submits, contacts a customer, accepts terms or
a contract, spends, mutates marketplace/KYC state, or infers award/payment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from typing import Any, Dict, List, Set, Tuple

from .contract_qualification import (
    ContractQualificationInputError,
    verify_contract_qualification_receipt,
)

SCHEMA_VERSION = "bounty-concierge.external-contract-proposal/v1"
BRIEF_VERSION = "bounty-concierge.external-contract-proposal-brief/v1"
MAX_INPUT_BYTES = 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_AUTHORITY = {
    "marketplace_submission", "customer_contact", "terms_acceptance",
    "account_creation", "kyc_completion", "spend", "contract_acceptance",
    "work_awarded", "payment_received", "revenue_recognized",
}
_PACKET_KEYS = {
    "schema_version", "proposal_id", "canonical_source_url", "source_digest",
    "qualification_digest", "qualification_verified_as_of", "headline",
    "cover_note", "proposal_claims", "deliverables", "milestones", "bid",
    "submission_route", "status", "human_review_required", "submitted",
    "authority", "packet_sha256",
}


class ContractProposalInputError(ValueError):
    """Proposal structure or evidence is not trustworthy enough to assemble."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _obj(value: Any, name: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise ContractProposalInputError("%s must be an object" % name)
    return value


def _shape(value: Dict[str, Any], keys: Set[str], name: str) -> None:
    if set(value) != keys:
        raise ContractProposalInputError("%s shape mismatch" % name)


def _text(value: Any, name: str, limit: int = 4096) -> str:
    if type(value) is not str or not value.strip():
        raise ContractProposalInputError("%s must be a non-empty string" % name)
    value = value.strip()
    if len(value) > limit or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ContractProposalInputError("%s is unsafe or too long" % name)
    return value


def _identifier(value: Any, name: str) -> str:
    value = _text(value, name, 96)
    if not _ID.fullmatch(value):
        raise ContractProposalInputError("%s must be a log-safe identifier" % name)
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name, 64)
    if not _SHA.fullmatch(value):
        raise ContractProposalInputError("%s must be lowercase sha256" % name)
    return value


def _day(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ContractProposalInputError("%s must be a bounded positive integer" % name)
    return value


def _qualification_evidence(receipt: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    raw = receipt.get("evidence_bindings")
    if type(raw) is not list or len(raw) > 500:
        raise ContractProposalInputError("qualification evidence_bindings must be bounded")
    result: Dict[str, Dict[str, str]] = {}
    keys = {"claim_id", "evidence_id", "evidence_type", "evidence_digest", "scope"}
    for index, item in enumerate(raw):
        row = _obj(item, "evidence_bindings[%d]" % index); _shape(row, keys, "evidence binding")
        evidence_id = _identifier(row["evidence_id"], "evidence_id")
        if evidence_id in result:
            raise ContractProposalInputError("duplicate qualification evidence_id")
        scope = _text(row["scope"], "evidence scope", 16).upper()
        if scope not in {"EXACT", "ADJACENT"}:
            raise ContractProposalInputError("unsupported qualification evidence scope")
        result[evidence_id] = {
            "claim_id": _identifier(row["claim_id"], "claim_id"),
            "evidence_id": evidence_id,
            "evidence_type": _identifier(row["evidence_type"], "evidence_type"),
            "evidence_digest": _digest(row["evidence_digest"], "evidence_digest"),
            "scope": scope,
        }
    return result


def _mandatory(snapshot: Dict[str, Any]) -> Set[str]:
    raw = snapshot.get("required_claims")
    if type(raw) is not list or len(raw) > 100:
        raise ContractProposalInputError("snapshot.required_claims must be bounded")
    result, seen = set(), set()
    for item in raw:
        row = _obj(item, "required claim")
        _shape(row, {"claim_id", "claim_type", "mandatory"}, "required claim")
        claim_id = _identifier(row["claim_id"], "claim_id")
        if claim_id in seen or type(row["mandatory"]) is not bool:
            raise ContractProposalInputError("required claim identity/mandatory flag is invalid")
        seen.add(claim_id)
        if row["mandatory"]:
            result.add(claim_id)
    return result


def _claims(raw: Any, evidence: Dict[str, Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Set[str]]:
    if type(raw) is not list or not raw or len(raw) > 100:
        raise ContractProposalInputError("proposal_claims must be non-empty and bounded")
    result, represented = [], set()
    for item in raw:
        row = _obj(item, "proposal claim"); _shape(row, {"claim_id", "statement", "evidence_ids"}, "proposal claim")
        claim_id = _identifier(row["claim_id"], "proposal claim_id")
        if claim_id in represented:
            raise ContractProposalInputError("proposal claim_id must appear exactly once")
        ids = row["evidence_ids"]
        if type(ids) is not list or not ids or len(ids) > 32:
            raise ContractProposalInputError("proposal claim evidence_ids must be non-empty and bounded")
        checked = []
        for raw_id in ids:
            evidence_id = _identifier(raw_id, "proposal evidence_id")
            bound = evidence.get(evidence_id)
            if bound is None:
                raise ContractProposalInputError("proposal references evidence absent from qualification")
            if bound["claim_id"] != claim_id:
                raise ContractProposalInputError("proposal evidence is bound to a different claim_id")
            if bound["scope"] != "EXACT":
                raise ContractProposalInputError("proposal claims may use only EXACT qualification evidence")
            if evidence_id in checked:
                raise ContractProposalInputError("duplicate evidence_id within proposal claim")
            checked.append(evidence_id)
        represented.add(claim_id)
        result.append({"claim_id": claim_id, "statement": _text(row["statement"], "claim statement", 1200), "evidence_ids": sorted(checked)})
    result.sort(key=lambda row: row["claim_id"])
    return result, represented


def _deliverables(raw: Any) -> Tuple[List[Dict[str, str]], Set[str]]:
    if type(raw) is not list or not raw or len(raw) > 64:
        raise ContractProposalInputError("deliverables must be non-empty and bounded")
    result, ids = [], set()
    for item in raw:
        row = _obj(item, "deliverable"); _shape(row, {"deliverable_id", "description", "acceptance_note"}, "deliverable")
        did = _identifier(row["deliverable_id"], "deliverable_id")
        if did in ids:
            raise ContractProposalInputError("duplicate deliverable_id")
        ids.add(did)
        result.append({"deliverable_id": did, "description": _text(row["description"], "description", 1600), "acceptance_note": _text(row["acceptance_note"], "acceptance_note", 1600)})
    result.sort(key=lambda row: row["deliverable_id"])
    return result, ids


def _milestones(raw: Any, delivery_days: int, deliverable_ids: Set[str]) -> List[Dict[str, Any]]:
    if type(raw) is not list or not raw or len(raw) > 64:
        raise ContractProposalInputError("milestones must be non-empty and bounded")
    result, mids, assigned, previous = [], set(), set(), 0
    for item in raw:
        row = _obj(item, "milestone"); _shape(row, {"milestone_id", "title", "due_day", "deliverable_ids"}, "milestone")
        mid = _identifier(row["milestone_id"], "milestone_id")
        if mid in mids:
            raise ContractProposalInputError("duplicate milestone_id")
        mids.add(mid); due = _day(row["due_day"], "milestone due_day", delivery_days)
        if due <= previous:
            raise ContractProposalInputError("milestone due_day values must be strictly increasing")
        previous = due
        raw_ids = row["deliverable_ids"]
        if type(raw_ids) is not list or not raw_ids or len(raw_ids) > 64:
            raise ContractProposalInputError("milestone deliverable_ids must be non-empty and bounded")
        checked = []
        for raw_id in raw_ids:
            did = _identifier(raw_id, "milestone deliverable_id")
            if did not in deliverable_ids:
                raise ContractProposalInputError("milestone references unknown deliverable_id")
            if did in assigned:
                raise ContractProposalInputError("a deliverable may be assigned to only one milestone")
            assigned.add(did); checked.append(did)
        result.append({"milestone_id": mid, "title": _text(row["title"], "milestone title", 300), "due_day": due, "deliverable_ids": sorted(checked)})
    if assigned != deliverable_ids:
        raise ContractProposalInputError("every deliverable must be assigned to exactly one milestone")
    return result


def _normalize_brief(raw: Any, snapshot: Dict[str, Any], receipt: Dict[str, Any]) -> Dict[str, Any]:
    brief = _obj(raw, "brief")
    keys = {"schema_version", "proposal_id", "headline", "cover_note", "proposal_claims", "deliverables", "milestones"}
    _shape(brief, keys, "brief")
    if brief["schema_version"] != BRIEF_VERSION:
        raise ContractProposalInputError("brief schema_version mismatch")
    evidence = _qualification_evidence(receipt)
    claims, represented = _claims(brief["proposal_claims"], evidence)
    missing = sorted(_mandatory(snapshot) - represented)
    if missing:
        raise ContractProposalInputError("proposal omits mandatory qualified claims: %s" % ",".join(missing))
    bid = _obj(receipt.get("bid"), "qualification.bid"); _shape(bid, {"currency", "amount", "delivery_days"}, "qualification.bid")
    days = _day(bid["delivery_days"], "qualification bid.delivery_days", 1000000)
    deliverables, ids = _deliverables(brief["deliverables"])
    return {
        "proposal_id": _identifier(brief["proposal_id"], "proposal_id"),
        "headline": _text(brief["headline"], "headline", 300),
        "cover_note": _text(brief["cover_note"], "cover_note", 3000),
        "proposal_claims": claims,
        "deliverables": deliverables,
        "milestones": _milestones(brief["milestones"], days, ids),
    }


def build_external_contract_proposal(snapshot: Dict[str, Any], qualification_receipt: Dict[str, Any], brief: Dict[str, Any], *, as_of: str) -> Dict[str, Any]:
    """Reverify qualification and assemble a deterministic owner-review packet."""
    snapshot = _obj(snapshot, "snapshot"); receipt = _obj(qualification_receipt, "qualification_receipt")
    try:
        verified = verify_contract_qualification_receipt(snapshot, receipt, as_of=as_of)
    except ContractQualificationInputError as exc:
        raise ContractProposalInputError("qualification verification failed: %s" % exc) from exc
    if receipt.get("disposition") != "ACTIONABLE" or receipt.get("bid_ready") is not True:
        raise ContractProposalInputError("qualification receipt is not ACTIONABLE and bid-ready")
    if receipt.get("submitted") is not False:
        raise ContractProposalInputError("qualification receipt must explicitly state submitted=false")
    if verified.get("valid") is not True or verified.get("current_disposition") != "ACTIONABLE":
        raise ContractProposalInputError("qualification is not current-actionable")
    qdigest = _digest(receipt.get("qualification_digest"), "qualification_digest")
    if verified.get("qualification_digest") != qdigest:
        raise ContractProposalInputError("verified qualification digest mismatch")
    route = _text(receipt.get("submission_route"), "submission_route", 32).upper()
    if route not in {"MANUAL_OWNER", "CONNECTED_ACTION"}:
        raise ContractProposalInputError("unsupported submission route")
    currency = _text(receipt.get("native_currency"), "native_currency", 8)
    bid = _obj(receipt.get("bid"), "qualification.bid"); _shape(bid, {"currency", "amount", "delivery_days"}, "qualification.bid")
    if bid["currency"] != currency:
        raise ContractProposalInputError("qualified bid currency does not match native currency")
    normalized = _normalize_brief(brief, snapshot, receipt)
    core = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": normalized["proposal_id"],
        "canonical_source_url": _text(receipt.get("canonical_source_url"), "canonical_source_url", 2048),
        "source_digest": _digest(receipt.get("source_digest"), "source_digest"),
        "qualification_digest": qdigest,
        "qualification_verified_as_of": _text(verified.get("verified_as_of"), "verified_as_of", 40),
        "headline": normalized["headline"], "cover_note": normalized["cover_note"],
        "proposal_claims": normalized["proposal_claims"], "deliverables": normalized["deliverables"], "milestones": normalized["milestones"],
        "bid": {"currency": _text(bid["currency"], "bid.currency", 8), "amount": _text(bid["amount"], "bid.amount", 64), "delivery_days": _day(bid["delivery_days"], "bid.delivery_days", 1000000)},
        "submission_route": route,
        "status": "READY_FOR_OWNER_REVIEW", "human_review_required": True, "submitted": False,
        "authority": {key: False for key in sorted(_AUTHORITY)},
    }
    result = dict(core); result["packet_sha256"] = _hash(core); return result


def verify_external_contract_proposal(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Verify self-integrity only; this does not recreate qualification authority."""
    packet = _obj(packet, "packet"); _shape(packet, _PACKET_KEYS, "packet")
    digest = _digest(packet.get("packet_sha256"), "packet_sha256")
    core = {key: value for key, value in packet.items() if key != "packet_sha256"}
    if _hash(core) != digest:
        raise ContractProposalInputError("proposal packet digest mismatch")
    if core.get("schema_version") != SCHEMA_VERSION:
        raise ContractProposalInputError("proposal packet schema mismatch")
    authority = _obj(core.get("authority"), "packet.authority"); _shape(authority, _AUTHORITY, "packet.authority")
    if any(value is not False for value in authority.values()) or core.get("submitted") is not False:
        raise ContractProposalInputError("proposal packet exceeds assembly authority")
    if core.get("human_review_required") is not True or core.get("status") != "READY_FOR_OWNER_REVIEW":
        raise ContractProposalInputError("proposal packet review boundary is invalid")
    return {"valid": True, "packet_sha256": digest, "proposal_id": core.get("proposal_id"), "human_review_required": True, "submitted": False}


def format_summary(result: Dict[str, Any]) -> str:
    bid = result["bid"]
    return "status=%s proposal=%s route=%s bid=%s %s delivery_days=%s human_review=true submitted=false packet=%s" % (
        result["status"], result["proposal_id"], result["submission_route"], bid["amount"], bid["currency"], bid["delivery_days"], result["packet_sha256"]
    )


def _load_json(path: str, name: str) -> Dict[str, Any]:
    if path == "-":
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        flags = os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0))
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT_BYTES:
                raise ContractProposalInputError("%s must be a bounded regular file" % name)
            chunks, remaining = [], MAX_INPUT_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk: break
                chunks.append(chunk); remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(fd)
    if len(data) > MAX_INPUT_BYTES:
        raise ContractProposalInputError("%s exceeds input size limit" % name)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractProposalInputError("%s is not valid UTF-8 JSON" % name) from exc
    return _obj(value, name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.contract_proposal")
    parser.add_argument("snapshot"); parser.add_argument("qualification_receipt"); parser.add_argument("brief")
    parser.add_argument("--as-of", required=True); parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if [args.snapshot, args.qualification_receipt, args.brief].count("-") > 1:
        parser.error("at most one input may be read from stdin")
    try:
        result = build_external_contract_proposal(
            _load_json(args.snapshot, "snapshot"), _load_json(args.qualification_receipt, "qualification_receipt"),
            _load_json(args.brief, "brief"), as_of=args.as_of,
        )
    except (OSError, ContractProposalInputError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else format_summary(result)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
