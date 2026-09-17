# SPDX-License-Identifier: MIT
"""Deterministic owner-review queue for certified reward settlement cases.

This module composes the declared-evidence ledger with the independent trust
certifier. It never sends outreach, requests payout, mutates providers, or
recognizes accounting revenue. Its only job is to turn certified settlement
truth into explicit owner-review work states with evidence/currentness binding.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .reward_settlement_certifier import canonical, certify, loads_strict
from .reward_settlement_ledger import compile_document

QUEUE_SCHEMA = "bounty-concierge/certified-settlement-collection-queue/v1"
MAX_FRESHNESS_SECONDS = 366 * 86400

QUEUE_STATES = frozenset({
    "SETTLED",
    "CLOSED_NO_REWARD",
    "NEEDS_TRUST_EVIDENCE",
    "AWARD_FOLLOWUP_CANDIDATE",
    "PAYOUT_TICKET_FOLLOWUP_CANDIDATE",
    "PAYOUT_RAIL_FOLLOWUP_CANDIDATE",
    "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
    "HOLD_CONTRADICTION",
})

_STATE_PRIORITY = {
    "HOLD_CONTRADICTION": 0,
    "NEEDS_TRUST_EVIDENCE": 1,
    "TRANSFER_PENDING_FOLLOWUP_CANDIDATE": 2,
    "PAYOUT_RAIL_FOLLOWUP_CANDIDATE": 3,
    "PAYOUT_TICKET_FOLLOWUP_CANDIDATE": 4,
    "AWARD_FOLLOWUP_CANDIDATE": 5,
    "SETTLED": 6,
    "CLOSED_NO_REWARD": 7,
}

_LEDGER_RANK = {
    "MERGED_UNSETTLED": 0,
    "SPONSOR_AWARDED": 1,
    "PAYOUT_TICKET_OPENED": 2,
    "PAYOUT_RAIL_SUPPLIED": 3,
    "TRANSFER_EVIDENCED": 4,
    "PAID_CONFIRMED": 5,
    "CLOSED_WITHOUT_REWARD": 5,
}

_CERT_RANK = {
    "UNTRUSTED_WORK_SOURCE": -1,
    "MERGE_ONLY_CERTIFIED": 0,
    "ADVERTISED_CERTIFIED": 0,
    "SPONSOR_AWARD_CERTIFIED": 1,
    "PAYOUT_TICKET_CERTIFIED": 2,
    "PAYOUT_RAIL_CERTIFIED": 3,
    "TRANSFER_EVIDENCE_CERTIFIED": 4,
    "PAID_CERTIFIED": 5,
    "CLOSED_WITHOUT_REWARD_CERTIFIED": 5,
}

_STAGE_KINDS = {
    "MERGED_UNSETTLED": frozenset(),
    "SPONSOR_AWARDED": frozenset({"SPONSOR_AWARD"}),
    "PAYOUT_TICKET_OPENED": frozenset({"PAYOUT_TICKET"}),
    "PAYOUT_RAIL_SUPPLIED": frozenset({"PAYOUT_RAIL"}),
    "TRANSFER_EVIDENCED": frozenset({"TRANSFER"}),
    "PAID_CONFIRMED": frozenset({"TRANSFER"}),
    "CLOSED_WITHOUT_REWARD": frozenset({"CLOSURE"}),
}


class CollectionQueueError(ValueError):
    pass


def _timestamp(value: Any, name: str) -> tuple[str, datetime]:
    if type(value) is not str:
        raise CollectionQueueError(f"{name} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise CollectionQueueError(f"{name} must be a real canonical UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CollectionQueueError(f"{name} must be canonical UTC seconds")
    return value, parsed


def _age_seconds(observed_at: str, as_of_dt: datetime, name: str) -> int:
    _, observed_dt = _timestamp(observed_at, name)
    seconds = int((as_of_dt - observed_dt).total_seconds())
    if seconds < 0:
        raise CollectionQueueError(f"{name} is after as_of")
    return seconds


def _source_view(source: dict[str, Any], as_of_dt: datetime, freshness_seconds: int, certified: bool) -> dict[str, Any]:
    age = _age_seconds(source["observed_at"], as_of_dt, "source.observed_at")
    return {
        "source_id": source["source_id"],
        "source_ref": source["source_ref"],
        "source_sha256": source["source_sha256"],
        "observed_at": source["observed_at"],
        "authority": source["authority"],
        "age_seconds": age,
        "current": age <= freshness_seconds,
        "certified": bool(certified),
    }


def _money_basis(case: dict[str, Any], certified_source_ids: set[str]) -> dict[str, Any]:
    """Expose declared money without disguising whether its source is certified."""
    for kind, basis in (("SPONSOR_AWARD", "SPONSOR_AWARD"), ("ADVERTISED_BOUNTY", "ADVERTISED_BOUNTY")):
        events = [e for e in case["events"] if e["kind"] == kind]
        if events:
            latest = max(events, key=lambda e: (e["source"]["observed_at"], e["event_id"]))
            source_id = latest["source"]["source_id"]
            return {
                "basis": basis,
                "amount_minor": latest["amount_minor"],
                "currency": latest["currency"],
                "unit": "minor",
                "source_id": source_id,
                "certified": source_id in certified_source_ids,
            }
    transfers = [e for e in case["events"] if e["kind"] == "TRANSFER"]
    if transfers:
        latest = max(transfers, key=lambda e: (e["source"]["observed_at"], e["event_id"]))
        source_id = latest["source"]["source_id"]
        return {
            "basis": "LATEST_TRANSFER",
            "amount_minor": latest["amount_minor"],
            "currency": latest["currency"],
            "unit": "minor",
            "source_id": source_id,
            "certified": source_id in certified_source_ids,
        }
    return {
        "basis": "NONE",
        "amount_minor": None,
        "currency": None,
        "unit": "minor",
        "source_id": None,
        "certified": False,
    }


def _route_view(case: dict[str, Any], certified_source_ids: set[str], as_of_dt: datetime, freshness_seconds: int) -> dict[str, Any]:
    tickets: list[dict[str, Any]] = []
    rails: list[dict[str, Any]] = []
    transfers: list[dict[str, Any]] = []
    for event in case["events"]:
        kind = event["kind"]
        if kind not in {"PAYOUT_TICKET", "PAYOUT_RAIL", "TRANSFER"}:
            continue
        sid = event["source"]["source_id"]
        source = _source_view(event["source"], as_of_dt, freshness_seconds, sid in certified_source_ids)
        if kind == "PAYOUT_TICKET":
            tickets.append({"event_id": event["event_id"], "ticket_id": event["ticket_id"], "source": source})
        elif kind == "PAYOUT_RAIL":
            rails.append({"event_id": event["event_id"], "rail_ref": event["rail_ref"], "source": source})
        else:
            transfers.append({
                "event_id": event["event_id"],
                "transfer_id": event["transfer_id"],
                "status": event["status"],
                "direction": event["direction"],
                "amount_minor": event["amount_minor"],
                "currency": event["currency"],
                "unit": "minor",
                "source": source,
            })
    tickets.sort(key=lambda x: (x["source"]["observed_at"], x["event_id"], x["ticket_id"]))
    rails.sort(key=lambda x: (x["source"]["observed_at"], x["event_id"], x["rail_ref"]))
    transfers.sort(key=lambda x: (x["source"]["observed_at"], x["event_id"], x["transfer_id"]))
    return {"payout_tickets": tickets, "payout_rails": rails, "transfers": transfers}


def _driver_sources(case: dict[str, Any], ledger_state: str) -> list[dict[str, Any]]:
    kinds = _STAGE_KINDS[ledger_state]
    if not kinds:
        return [case["work"]["source"]]
    events = [e for e in case["events"] if e["kind"] in kinds]
    if ledger_state in {"TRANSFER_EVIDENCED", "PAID_CONFIRMED"}:
        latest: dict[str, dict[str, Any]] = {}
        for event in events:
            prior = latest.get(event["transfer_id"])
            if prior is None or (event["source"]["observed_at"], event["event_id"]) > (prior["source"]["observed_at"], prior["event_id"]):
                latest[event["transfer_id"]] = event
        events = list(latest.values())
        if ledger_state == "PAID_CONFIRMED":
            events = [e for e in events if e["status"] == "CONFIRMED" and e["direction"] == "INCOMING"]
    if not events:
        return [case["work"]["source"]]
    return [e["source"] for e in events]


def _latest_transfer_statuses(case: dict[str, Any], certified_source_ids: set[str]) -> list[str]:
    latest: dict[str, dict[str, Any]] = {}
    for event in case["events"]:
        if event["kind"] != "TRANSFER" or event["source"]["source_id"] not in certified_source_ids:
            continue
        prior = latest.get(event["transfer_id"])
        if prior is None or (event["source"]["observed_at"], event["event_id"]) > (prior["source"]["observed_at"], prior["event_id"]):
            latest[event["transfer_id"]] = event
    return sorted(event["status"] for event in latest.values())


def _certified_truth_chain(case: dict[str, Any], certified_source_ids: set[str]) -> tuple[bool, set[str]]:
    """Return whether a sponsor award is certified and all certified eligibility decisions."""
    certified_award = False
    eligibility: set[str] = set()
    for event in case["events"]:
        if event["source"]["source_id"] not in certified_source_ids:
            continue
        if event["kind"] == "SPONSOR_AWARD":
            certified_award = True
        elif event["kind"] == "ELIGIBILITY":
            eligibility.add(event["decision"])
    return certified_award, eligibility


def _derive_state(
    case: dict[str, Any],
    ledger_record: dict[str, Any],
    cert_record: dict[str, Any],
    driver_views: list[dict[str, Any]],
    registry_current: bool,
) -> tuple[str, list[str]]:
    declared = ledger_record["settlement_state"]
    certified = cert_record["certified_settlement_state"]
    reasons: list[str] = []

    if declared not in _LEDGER_RANK or certified not in _CERT_RANK:
        return "HOLD_CONTRADICTION", ["UNKNOWN_SIBLING_STATE"]
    if certified == "PAID_CERTIFIED":
        if declared != "PAID_CONFIRMED":
            return "HOLD_CONTRADICTION", ["CERTIFIED_PAID_WITHOUT_DECLARED_PAID"]
        if cert_record["certified_paid_by_currency"] != ledger_record["paid_confirmed_by_currency"]:
            return "HOLD_CONTRADICTION", ["CERTIFIED_PAID_AMOUNT_MISMATCH"]
        return "SETTLED", ["CERTIFIED_CONFIRMED_TRANSFER"]
    if certified == "CLOSED_WITHOUT_REWARD_CERTIFIED":
        if declared != "CLOSED_WITHOUT_REWARD":
            return "HOLD_CONTRADICTION", ["CERTIFIED_CLOSURE_WITHOUT_DECLARED_CLOSURE"]
        return "CLOSED_NO_REWARD", ["CERTIFIED_NO_REWARD_CLOSURE"]
    if declared in {"PAID_CONFIRMED", "CLOSED_WITHOUT_REWARD"}:
        return "NEEDS_TRUST_EVIDENCE", ["TERMINAL_DECLARED_STATE_NOT_CERTIFIED"]
    if certified == "UNTRUSTED_WORK_SOURCE":
        return "NEEDS_TRUST_EVIDENCE", ["WORK_SOURCE_NOT_CERTIFIED"]
    if _CERT_RANK[certified] > _LEDGER_RANK[declared]:
        return "HOLD_CONTRADICTION", ["CERTIFIED_STAGE_EXCEEDS_DECLARED_STAGE"]
    if _CERT_RANK[certified] < _LEDGER_RANK[declared]:
        return "NEEDS_TRUST_EVIDENCE", ["LATEST_DECLARED_STAGE_NOT_CERTIFIED"]
    if any(not view["certified"] for view in driver_views):
        return "NEEDS_TRUST_EVIDENCE", ["LATEST_DECLARED_STAGE_SOURCE_NOT_CERTIFIED"]
    if not registry_current:
        return "NEEDS_TRUST_EVIDENCE", ["TRUST_REGISTRY_STALE_AT_AS_OF"]
    if any(not view["current"] for view in driver_views):
        return "NEEDS_TRUST_EVIDENCE", ["LATEST_CERTIFIED_STAGE_EVIDENCE_STALE_AT_AS_OF"]

    certified_ids = set(cert_record["certified_source_ids"])
    certified_award, eligibility = _certified_truth_chain(case, certified_ids)
    if len(eligibility) > 1:
        return "HOLD_CONTRADICTION", ["CERTIFIED_ELIGIBILITY_CONTRADICTION"]
    if eligibility == {"INELIGIBLE"}:
        return "HOLD_CONTRADICTION", ["CERTIFIED_INELIGIBLE"]
    if certified in {
        "SPONSOR_AWARD_CERTIFIED",
        "PAYOUT_TICKET_CERTIFIED",
        "PAYOUT_RAIL_CERTIFIED",
        "TRANSFER_EVIDENCE_CERTIFIED",
    } and not certified_award:
        return "NEEDS_TRUST_EVIDENCE", ["NO_CERTIFIED_SPONSOR_AWARD"]

    if certified == "TRANSFER_EVIDENCE_CERTIFIED":
        statuses = _latest_transfer_statuses(case, certified_ids)
        if "FAILED" in statuses:
            return "HOLD_CONTRADICTION", ["CERTIFIED_TRANSFER_FAILED_TERMINAL"]
        if any(status in {"PENDING", "CONFIRMING"} for status in statuses):
            return "TRANSFER_PENDING_FOLLOWUP_CANDIDATE", ["CERTIFIED_TRANSFER_NOT_TERMINAL"]
        return "HOLD_CONTRADICTION", ["TRANSFER_EVIDENCE_WITHOUT_PENDING_OR_CONFIRMED_STATE"]
    if certified == "PAYOUT_RAIL_CERTIFIED":
        return "PAYOUT_RAIL_FOLLOWUP_CANDIDATE", ["CERTIFIED_RAIL_WITHOUT_TRANSFER"]
    if certified == "PAYOUT_TICKET_CERTIFIED":
        return "PAYOUT_TICKET_FOLLOWUP_CANDIDATE", ["CERTIFIED_TICKET_WITHOUT_RAIL"]
    if certified == "SPONSOR_AWARD_CERTIFIED":
        return "AWARD_FOLLOWUP_CANDIDATE", ["CERTIFIED_AWARD_WITHOUT_PAYOUT_TICKET"]
    if certified in {"ADVERTISED_CERTIFIED", "MERGE_ONLY_CERTIFIED"}:
        reasons.append("NO_CERTIFIED_SPONSOR_AWARD")
        return "NEEDS_TRUST_EVIDENCE", reasons
    return "HOLD_CONTRADICTION", ["UNMAPPED_CERTIFIED_STATE"]


def build_queue(
    document: dict[str, Any],
    registry: dict[str, Any],
    key: bytes,
    *,
    as_of: str,
    freshness_seconds: int,
) -> dict[str, Any]:
    """Compile a deterministic owner-review queue from declared + certified truth.

    ``as_of`` and ``freshness_seconds`` are explicit policy inputs so repeated
    runs over identical evidence are byte-stable. Terminal certified payment and
    certified no-reward closure do not expire; freshness gates only nonterminal
    follow-up candidates.
    """
    if type(freshness_seconds) is not int or type(freshness_seconds) is bool or not (1 <= freshness_seconds <= MAX_FRESHNESS_SECONDS):
        raise CollectionQueueError("freshness_seconds must be an integer in [1, 31622400]")
    as_of_text, as_of_dt = _timestamp(as_of, "as_of")
    ledger = compile_document(document)
    certificate = certify(document, registry, key)
    _, input_dt = _timestamp(document["generated_at"], "document.generated_at")
    _, registry_dt = _timestamp(registry["generated_at"], "registry.generated_at")
    if as_of_dt < input_dt or as_of_dt < registry_dt:
        raise CollectionQueueError("as_of cannot precede input or trusted registry generation")
    registry_age_seconds = int((as_of_dt - registry_dt).total_seconds())
    registry_current = registry_age_seconds <= freshness_seconds

    ledger_by_case = {record["case_id"]: record for record in ledger["records"]}
    cert_by_case = {record["case_id"]: record for record in certificate["records"]}
    cases_by_id = {case["case_id"]: case for case in document["cases"]}
    if not (set(ledger_by_case) == set(cert_by_case) == set(cases_by_id)):
        raise CollectionQueueError("sibling record sets disagree")

    rows: list[dict[str, Any]] = []
    for case_id, case in cases_by_id.items():
        ledger_record = ledger_by_case[case_id]
        cert_record = cert_by_case[case_id]
        certified_ids = set(cert_record["certified_source_ids"])
        driver_sources = _driver_sources(case, ledger_record["settlement_state"])
        driver_views = [
            _source_view(src, as_of_dt, freshness_seconds, src["source_id"] in certified_ids)
            for src in driver_sources
        ]
        event_age_seconds = max(view["age_seconds"] for view in driver_views)
        queue_state, reasons = _derive_state(case, ledger_record, cert_record, driver_views, registry_current)
        if queue_state not in QUEUE_STATES:
            raise CollectionQueueError("internal queue state invalid")
        rows.append({
            "case_id": case_id,
            "work": {
                "repo": case["work"]["repo"],
                "pr": case["work"]["pr"],
                "merge_commit_sha": case["work"]["merge_commit_sha"],
                "merged_at": case["work"]["merged_at"],
            },
            "queue_state": queue_state,
            "reason_codes": reasons,
            "declared_settlement_state": ledger_record["settlement_state"],
            "certified_settlement_state": cert_record["certified_settlement_state"],
            "work_source_certified": cert_record["work_source_certified"],
            "certificate_receipt_sha256": certificate["receipt_sha256"],
            "trusted_registry_body_sha256": certificate["trusted_registry_body_sha256"],
            "trusted_registry_age_seconds": registry_age_seconds,
            "trusted_registry_current": registry_current,
            "event_age_seconds": event_age_seconds,
            "driver_sources": sorted(driver_views, key=lambda x: (x["observed_at"], x["source_id"])),
            "money": _money_basis(case, certified_ids),
            "certified_paid_by_currency": cert_record["certified_paid_by_currency"],
            "route_evidence": _route_view(case, certified_ids, as_of_dt, freshness_seconds),
            "owner_review_only": True,
        })

    rows.sort(key=lambda row: (
        _STATE_PRIORITY[row["queue_state"]],
        -row["event_age_seconds"],
        row["work"]["repo"],
        row["work"]["pr"],
        row["case_id"],
    ))
    counts = {state: 0 for state in sorted(QUEUE_STATES)}
    for row in rows:
        counts[row["queue_state"]] += 1
    body = {
        "schema": QUEUE_SCHEMA,
        "input_generated_at": document["generated_at"],
        "trusted_registry_generated_at": registry["generated_at"],
        "as_of": as_of_text,
        "freshness_seconds": freshness_seconds,
        "certificate_receipt_sha256": certificate["receipt_sha256"],
        "trusted_registry_body_sha256": certificate["trusted_registry_body_sha256"],
        "records": rows,
        "aggregates": {"record_count": len(rows), "queue_state_counts": counts},
        "authority": {
            "send_outbound": False,
            "request_payout": False,
            "mutate_provider": False,
            "mutate_wallet_or_bank": False,
            "recognize_accounting_revenue": False,
        },
    }
    return {**body, "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest()}


def queue_bytes(
    document: dict[str, Any],
    registry: dict[str, Any],
    key: bytes,
    *,
    as_of: str,
    freshness_seconds: int,
) -> bytes:
    return canonical(build_queue(document, registry, key, as_of=as_of, freshness_seconds=freshness_seconds))


def compile_bytes(
    document_raw: bytes,
    registry_raw: bytes,
    key: bytes,
    *,
    as_of: str,
    freshness_seconds: int,
) -> bytes:
    """Strict-byte entry point for pipeline callers; output is canonical JSON."""
    document = loads_strict(document_raw)
    registry = loads_strict(registry_raw)
    return queue_bytes(document, registry, key, as_of=as_of, freshness_seconds=freshness_seconds)
