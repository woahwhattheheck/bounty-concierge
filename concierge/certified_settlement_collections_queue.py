# SPDX-License-Identifier: MIT
"""Build a read-only owner-review queue from certified reward-settlement evidence.

This layer composes the existing reward settlement ledger and trusted-source
certifier. It does not reinterpret repository merge state as a receivable and it
never authorizes contact, collection, payout, provider mutation, or accounting
revenue recognition.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from typing import Any, Optional

from .reward_settlement_certifier import (
    CertificationError,
    canonical,
    certify,
    loads_strict,
    verify_registry,
)
from .reward_settlement_ledger import LedgerInputError, compile_document

QUEUE_SCHEMA = "bounty-concierge/certified-settlement-collections-queue/v1"
CONTACT_SCHEMA = "bounty-concierge/settlement-contact-route-evidence/v1"
MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 7 * 24 * 60 * 60
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,239}$")

PARTITIONS = frozenset(
    {
        "SETTLED",
        "CLOSED_NO_REWARD",
        "NEEDS_TRUST_EVIDENCE",
        "AWARD_FOLLOWUP_CANDIDATE",
        "PAYOUT_TICKET_FOLLOWUP_CANDIDATE",
        "PAYOUT_RAIL_FOLLOWUP_CANDIDATE",
        "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
        "HOLD_CONTRADICTION",
    }
)
CONTACT_KINDS = frozenset(
    {
        "SPONSOR_ISSUE",
        "SPONSOR_EMAIL",
        "PROVIDER_TICKET",
        "PAYOUT_PORTAL",
        "OTHER_REFERENCE",
    }
)
TERMINAL_PARTITIONS = frozenset({"SETTLED", "CLOSED_NO_REWARD"})
CANDIDATE_PARTITIONS = frozenset(
    {
        "AWARD_FOLLOWUP_CANDIDATE",
        "PAYOUT_TICKET_FOLLOWUP_CANDIDATE",
        "PAYOUT_RAIL_FOLLOWUP_CANDIDATE",
        "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
    }
)


class QueueError(ValueError):
    """Malformed, contradictory, stale, or authority-amplifying queue input."""


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _exact(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise QueueError(f"{name} must have exact keys {sorted(keys)}")
    return value


def _timestamp(value: Any, name: str) -> tuple[str, datetime]:
    if type(value) is not str or TS.fullmatch(value) is None:
        raise QueueError(f"{name} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise QueueError(f"{name} is not a real UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise QueueError(f"{name} must be canonical UTC seconds")
    return value, parsed


def _token(value: Any, name: str) -> str:
    if type(value) is not str or TOKEN.fullmatch(value) is None:
        raise QueueError(f"{name} must be a bounded token")
    return value


def _event_is_trusted(event: dict[str, Any], trusted: dict[str, dict[str, str]]) -> bool:
    source = event.get("source")
    if type(source) is not dict:
        return False
    source_id = source.get("source_id")
    return type(source_id) is str and trusted.get(source_id) == source


def _trusted_events(case: dict[str, Any], trusted: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows = [event for event in case["events"] if _event_is_trusted(event, trusted)]
    return sorted(rows, key=lambda e: (e["source"]["observed_at"], e["event_id"]))


def _latest_transfer_finals(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("kind") == "TRANSFER":
            by_id.setdefault(event["transfer_id"], []).append(event)
    finals: list[dict[str, Any]] = []
    for rows in by_id.values():
        finals.append(sorted(rows, key=lambda e: (e["source"]["observed_at"], e["event_id"]))[-1])
    return sorted(finals, key=lambda e: e["transfer_id"])


def _state_contradictions(ledger: dict[str, Any], cert: dict[str, Any]) -> list[str]:
    state = cert["certified_settlement_state"]
    problems: list[str] = []
    if state == "PAID_CERTIFIED":
        if ledger["settlement_state"] != "PAID_CONFIRMED":
            problems.append("CERT_PAID_WITHOUT_LEDGER_PAID")
        if cert["certified_paid_by_currency"] != ledger["paid_confirmed_by_currency"]:
            problems.append("CERTIFIED_PAID_AMOUNT_MISMATCH")
    elif cert["certified_paid_by_currency"]:
        problems.append("NONPAID_CERT_HAS_PAID_AMOUNT")
    if state == "CLOSED_WITHOUT_REWARD_CERTIFIED" and ledger["closure"]["status"] != "CLOSED_WITHOUT_REWARD":
        problems.append("CERT_CLOSED_WITHOUT_LEDGER_CLOSURE")
    if state == "SPONSOR_AWARD_CERTIFIED" and ledger["award_status"] != "SPONSOR_AWARDED":
        problems.append("CERT_AWARD_WITHOUT_LEDGER_AWARD")
    if state == "PAYOUT_TICKET_CERTIFIED" and ledger["payout_ticket"]["status"] != "PAYOUT_TICKET_OPENED":
        problems.append("CERT_TICKET_WITHOUT_LEDGER_TICKET")
    if state == "PAYOUT_RAIL_CERTIFIED" and ledger["payout_rail"]["status"] != "PAYOUT_RAIL_SUPPLIED":
        problems.append("CERT_RAIL_WITHOUT_LEDGER_RAIL")
    if state == "TRANSFER_EVIDENCE_CERTIFIED" and not ledger["transfers"]:
        problems.append("CERT_TRANSFER_WITHOUT_LEDGER_TRANSFER")
    if state == "UNTRUSTED_WORK_SOURCE" and cert["work_source_certified"]:
        problems.append("UNTRUSTED_STATE_WITH_CERTIFIED_WORK")
    return sorted(set(problems))


def _contact_routes(
    contacts: Optional[dict[str, Any]],
    *,
    case_ids: set[str],
    trusted: dict[str, dict[str, str]],
    as_of_dt: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]], Optional[str]]:
    if contacts is None:
        return {}, {}, None
    contacts = _exact(contacts, {"schema", "generated_at", "routes"}, "contacts")
    if contacts["schema"] != CONTACT_SCHEMA:
        raise QueueError(f"contacts.schema must be {CONTACT_SCHEMA}")
    _, generated_dt = _timestamp(contacts["generated_at"], "contacts.generated_at")
    if generated_dt > as_of_dt:
        raise QueueError("contacts.generated_at may not be after as_of")
    routes = contacts["routes"]
    if type(routes) is not list or len(routes) > 10_000:
        raise QueueError("contacts.routes must be a bounded list")
    by_case: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, list[str]] = {}
    seen_ids: dict[str, str] = {}
    for index, raw in enumerate(routes):
        row = _exact(
            raw,
            {"case_id", "route_id", "route_kind", "source_id", "source_ref", "observed_at"},
            f"contacts.routes[{index}]",
        )
        case_id = _token(row["case_id"], f"contacts.routes[{index}].case_id")
        route_id = _token(row["route_id"], f"contacts.routes[{index}].route_id")
        if case_id not in case_ids:
            raise QueueError(f"contact route references unknown case_id: {case_id}")
        route_kind = row["route_kind"]
        if type(route_kind) is not str or route_kind not in CONTACT_KINDS:
            raise QueueError(f"contacts.routes[{index}].route_kind unsupported")
        source_id = _token(row["source_id"], f"contacts.routes[{index}].source_id")
        source_ref = _token(row["source_ref"], f"contacts.routes[{index}].source_ref")
        observed_at, observed_dt = _timestamp(row["observed_at"], f"contacts.routes[{index}].observed_at")
        if observed_dt > as_of_dt:
            errors.setdefault(case_id, []).append("CONTACT_ROUTE_FROM_FUTURE")
        prior_case = seen_ids.get(route_id)
        if prior_case is not None:
            errors.setdefault(case_id, []).append("DUPLICATE_CONTACT_ROUTE_ID")
            errors.setdefault(prior_case, []).append("DUPLICATE_CONTACT_ROUTE_ID")
        else:
            seen_ids[route_id] = case_id
        trusted_source = trusted.get(source_id)
        route_valid = observed_dt <= as_of_dt
        if trusted_source is None:
            errors.setdefault(case_id, []).append("CONTACT_ROUTE_SOURCE_UNTRUSTED")
            route_valid = False
        elif (
            trusted_source["source_ref"] != source_ref
            or trusted_source["observed_at"] != observed_at
            or trusted_source["authority"] == "REPOSITORY"
        ):
            errors.setdefault(case_id, []).append("CONTACT_ROUTE_SOURCE_MISMATCH")
            route_valid = False
        if prior_case is not None:
            route_valid = False
        if route_valid:
            by_case.setdefault(case_id, []).append(
                {
                    "route_id": route_id,
                    "route_kind": route_kind,
                    "source_id": source_id,
                    "source_ref": source_ref,
                    "observed_at": observed_at,
                    "reference_only": True,
                }
            )
    for rows in by_case.values():
        rows.sort(key=lambda r: (r["route_kind"], r["route_id"]))
    return by_case, {k: sorted(set(v)) for k, v in errors.items()}, _sha(contacts)


def _ledger_progress(state: str) -> int:
    return {
        "MERGED_UNSETTLED": 0,
        "SPONSOR_AWARDED": 1,
        "PAYOUT_TICKET_OPENED": 2,
        "PAYOUT_RAIL_SUPPLIED": 3,
        "TRANSFER_EVIDENCED": 4,
        "PAID_CONFIRMED": 5,
        "CLOSED_WITHOUT_REWARD": 5,
    }.get(state, -1)


def _cert_progress(state: str) -> int:
    return {
        "UNTRUSTED_WORK_SOURCE": -1,
        "MERGE_ONLY_CERTIFIED": 0,
        "ADVERTISED_CERTIFIED": 0,
        "SPONSOR_AWARD_CERTIFIED": 1,
        "PAYOUT_TICKET_CERTIFIED": 2,
        "PAYOUT_RAIL_CERTIFIED": 3,
        "TRANSFER_EVIDENCE_CERTIFIED": 4,
        "PAID_CERTIFIED": 5,
        "CLOSED_WITHOUT_REWARD_CERTIFIED": 5,
    }.get(state, -1)


def _partition(
    cert_state: str,
    ledger: dict[str, Any],
    trusted_events: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if _ledger_progress(ledger["settlement_state"]) > _cert_progress(cert_state):
        return "NEEDS_TRUST_EVIDENCE", ["DECLARED_LATER_SETTLEMENT_STATE_NOT_TRUST_BOUND"]
    if cert_state == "PAID_CERTIFIED":
        return "SETTLED", ["TERMINAL_PAYMENT_CERTIFIED"]
    if cert_state == "CLOSED_WITHOUT_REWARD_CERTIFIED":
        return "CLOSED_NO_REWARD", ["TERMINAL_NO_REWARD_CLOSURE_CERTIFIED"]
    if cert_state == "UNTRUSTED_WORK_SOURCE":
        return "NEEDS_TRUST_EVIDENCE", ["WORK_SOURCE_NOT_CERTIFIED"]
    if cert_state == "SPONSOR_AWARD_CERTIFIED":
        return "AWARD_FOLLOWUP_CANDIDATE", ["AWARD_CERTIFIED_PAYOUT_NOT_EVIDENCED"]
    if cert_state == "PAYOUT_TICKET_CERTIFIED":
        return "PAYOUT_TICKET_FOLLOWUP_CANDIDATE", ["PAYOUT_TICKET_CERTIFIED_NO_LATER_STATE"]
    if cert_state == "PAYOUT_RAIL_CERTIFIED":
        return "PAYOUT_RAIL_FOLLOWUP_CANDIDATE", ["PAYOUT_RAIL_CERTIFIED_NO_LATER_TRANSFER"]
    if cert_state == "TRANSFER_EVIDENCE_CERTIFIED":
        finals = _latest_transfer_finals(trusted_events)
        if finals and any(row.get("status") == "FAILED" for row in finals):
            return "PAYOUT_RAIL_FOLLOWUP_CANDIDATE", ["CERTIFIED_TRANSFER_FAILED_RECHECK_RAIL"]
        return "TRANSFER_PENDING_FOLLOWUP_CANDIDATE", ["TRANSFER_CERTIFIED_NOT_PAID_TERMINAL"]
    if cert_state == "ADVERTISED_CERTIFIED":
        return "NEEDS_TRUST_EVIDENCE", ["ADVERTISED_ONLY_NO_CERTIFIED_AWARD"]
    if cert_state == "MERGE_ONLY_CERTIFIED":
        if ledger["award_status"] == "SPONSOR_AWARDED":
            reasons.append("DECLARED_AWARD_NOT_TRUST_BOUND")
        elif ledger["payout_ticket"]["status"] == "PAYOUT_TICKET_OPENED":
            reasons.append("DECLARED_PAYOUT_TICKET_NOT_TRUST_BOUND")
        elif ledger["payout_rail"]["status"] == "PAYOUT_RAIL_SUPPLIED":
            reasons.append("DECLARED_PAYOUT_RAIL_NOT_TRUST_BOUND")
        elif ledger["transfers"]:
            reasons.append("DECLARED_TRANSFER_NOT_TRUST_BOUND")
        else:
            reasons.append("NO_CERTIFIED_REWARD_STATE")
        return "NEEDS_TRUST_EVIDENCE", reasons
    raise QueueError(f"unsupported certified settlement state: {cert_state}")


def _next_action(partition: str) -> str:
    return {
        "SETTLED": "ARCHIVE_SETTLEMENT_EVIDENCE",
        "CLOSED_NO_REWARD": "ARCHIVE_NO_REWARD_CLOSURE",
        "NEEDS_TRUST_EVIDENCE": "REFRESH_OR_BIND_TRUST_EVIDENCE",
        "AWARD_FOLLOWUP_CANDIDATE": "OWNER_REVIEW_AWARD_FOLLOWUP",
        "PAYOUT_TICKET_FOLLOWUP_CANDIDATE": "OWNER_REVIEW_PAYOUT_TICKET_STATUS",
        "PAYOUT_RAIL_FOLLOWUP_CANDIDATE": "OWNER_REVIEW_PAYOUT_RAIL_STATUS",
        "TRANSFER_PENDING_FOLLOWUP_CANDIDATE": "OWNER_REVIEW_TRANSFER_STATUS",
        "HOLD_CONTRADICTION": "RESOLVE_EVIDENCE_CONTRADICTION",
    }[partition]


def build_queue(
    document: dict[str, Any],
    registry: dict[str, Any],
    key: bytes,
    *,
    as_of: str,
    contacts: Optional[dict[str, Any]] = None,
    max_evidence_age_seconds: int = DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
) -> dict[str, Any]:
    if type(max_evidence_age_seconds) is not int or isinstance(max_evidence_age_seconds, bool) or max_evidence_age_seconds < 0:
        raise QueueError("max_evidence_age_seconds must be a non-negative integer")
    as_of, as_of_dt = _timestamp(as_of, "as_of")
    try:
        ledger = compile_document(document)
        certificate = certify(document, registry, key)
        trusted, registry_body_sha = verify_registry(registry, key)
    except (LedgerInputError, CertificationError) as exc:
        raise QueueError(f"predecessor evidence verification failed: {exc}") from exc
    _, input_generated_dt = _timestamp(certificate["input_generated_at"], "certificate.input_generated_at")
    _, registry_generated_dt = _timestamp(certificate["trusted_registry_generated_at"], "certificate.trusted_registry_generated_at")
    if input_generated_dt > as_of_dt or registry_generated_dt > as_of_dt:
        raise QueueError("as_of may not predate input or trusted registry generation")
    if certificate["trusted_registry_body_sha256"] != registry_body_sha:
        raise QueueError("certificate registry digest disagrees with verified registry")

    ledger_by_id = {row["case_id"]: row for row in ledger["records"]}
    cert_by_id = {row["case_id"]: row for row in certificate["records"]}
    document_by_id = {row["case_id"]: row for row in document["cases"]}
    case_ids = set(document_by_id)
    if case_ids != set(ledger_by_id) or case_ids != set(cert_by_id):
        raise QueueError("ledger/certificate/document case identities disagree")

    routes_by_case, route_errors, contacts_sha = _contact_routes(
        contacts,
        case_ids=case_ids,
        trusted=trusted,
        as_of_dt=as_of_dt,
    )

    rows: list[dict[str, Any]] = []
    for case_id in sorted(case_ids):
        ledger_row = ledger_by_id[case_id]
        cert_row = cert_by_id[case_id]
        case = document_by_id[case_id]
        events = _trusted_events(case, trusted)
        latest_at: Optional[str] = events[-1]["source"]["observed_at"] if events else None
        age_seconds: Optional[int] = None
        if latest_at is not None:
            _, latest_dt = _timestamp(latest_at, f"{case_id}.latest_certified_event_at")
            age_seconds = int((as_of_dt - latest_dt).total_seconds())
            if age_seconds < 0:
                raise QueueError(f"{case_id}: certified event is after as_of")

        contradictions = _state_contradictions(ledger_row, cert_row)
        contradictions.extend(route_errors.get(case_id, []))
        partition, reasons = _partition(cert_row["certified_settlement_state"], ledger_row, events)
        if contradictions:
            partition = "HOLD_CONTRADICTION"
            reasons = sorted(set(contradictions))
        elif partition in CANDIDATE_PARTITIONS and (
            age_seconds is None or age_seconds > max_evidence_age_seconds
        ):
            partition = "NEEDS_TRUST_EVIDENCE"
            reasons = ["CERTIFIED_EVENT_MISSING_OR_STALE"]
        if partition not in PARTITIONS:
            raise QueueError(f"{case_id}: invalid partition")

        missing: list[str] = []
        if partition == "NEEDS_TRUST_EVIDENCE":
            missing.append("CURRENT_TRUST_BOUND_SETTLEMENT_EVIDENCE")
        if partition in CANDIDATE_PARTITIONS and not routes_by_case.get(case_id):
            missing.append("TRUST_BOUND_CONTACT_ROUTE_OPTIONAL")
        if partition == "HOLD_CONTRADICTION":
            missing.append("CONTRADICTION_RESOLUTION")

        rows.append(
            {
                "case_id": case_id,
                "work": dict(ledger_row["work"]),
                "partition": partition,
                "certified_settlement_state": cert_row["certified_settlement_state"],
                "latest_certified_event_at": latest_at,
                "certified_event_age_seconds": age_seconds,
                "reason_codes": sorted(set(reasons)),
                "missing_evidence": sorted(set(missing)),
                "amount_evidence": {
                    "advertised": ledger_row["advertised"],
                    "awarded": ledger_row["award"],
                    "paid_certified_by_currency": dict(cert_row["certified_paid_by_currency"]),
                    "silent_conversion_performed": False,
                },
                "certified_source_ids": list(cert_row["certified_source_ids"]),
                "contact_routes": routes_by_case.get(case_id, []),
                "next_action": {
                    "kind": _next_action(partition),
                    "owner_review_required": partition not in TERMINAL_PARTITIONS,
                    "send_authorized": False,
                    "request_payout_authorized": False,
                    "collection_authorized": False,
                    "mutate_provider_authorized": False,
                    "recognize_accounting_revenue_authorized": False,
                },
            }
        )

    counts = {partition: 0 for partition in sorted(PARTITIONS)}
    for row in rows:
        counts[row["partition"]] += 1
    body = {
        "schema": QUEUE_SCHEMA,
        "as_of": as_of,
        "max_evidence_age_seconds": max_evidence_age_seconds,
        "source_binding": {
            "settlement_input_content_sha256": _sha(document),
            "settlement_ledger_projection_sha256": _sha(ledger),
            "trusted_registry_body_sha256": registry_body_sha,
            "certification_receipt_sha256": certificate["receipt_sha256"],
            "contact_routes_content_sha256": contacts_sha,
        },
        "records": rows,
        "aggregates": {
            "case_count": len(rows),
            "partition_counts": counts,
            "certified_paid_by_currency": dict(certificate["aggregates"]["paid_certified_by_currency"]),
            "recognized_revenue_by_currency": {},
        },
        "truth": {
            "merge_creates_receivable": False,
            "advertised_creates_award": False,
            "contact_route_authorizes_send": False,
            "noncash_is_silently_converted_to_usd": False,
        },
        "authority": {
            "send_outbound": False,
            "collect": False,
            "request_payout": False,
            "mutate_provider": False,
            "mutate_wallet_or_bank": False,
            "mutate_accounting": False,
            "recognize_accounting_revenue": False,
        },
    }
    return {**body, "receipt_sha256": _sha(body)}


def verify_queue(
    document: dict[str, Any],
    registry: dict[str, Any],
    key: bytes,
    queue: dict[str, Any],
    *,
    as_of: str,
    contacts: Optional[dict[str, Any]] = None,
    max_evidence_age_seconds: int = DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
) -> bool:
    try:
        rebuilt = build_queue(
            document,
            registry,
            key,
            as_of=as_of,
            contacts=contacts,
            max_evidence_age_seconds=max_evidence_age_seconds,
        )
    except QueueError:
        return False
    return hmac.compare_digest(canonical(rebuilt), canonical(queue))


def render_markdown(queue: dict[str, Any]) -> str:
    lines = [
        "# Certified settlement collections queue",
        "",
        f"As of: `{queue['as_of']}`",
        "",
        "> Owner-review only. This queue cannot send messages, request payout, collect funds, mutate providers, or recognize accounting revenue.",
        "",
        "## Cases",
        "",
    ]
    for row in queue["records"]:
        work = row["work"]
        lines.extend(
            [
                f"### {row['case_id']} — {work['repo']}#{work['pr']}",
                "",
                f"- Queue state: `{row['partition']}`",
                f"- Certified settlement: `{row['certified_settlement_state']}`",
                f"- Latest certified settlement event: `{row['latest_certified_event_at'] or 'none'}`",
                f"- Age seconds: `{row['certified_event_age_seconds'] if row['certified_event_age_seconds'] is not None else 'unknown'}`",
                f"- Next action: `{row['next_action']['kind']}`",
                f"- Reasons: {', '.join(row['reason_codes']) or 'none'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Authority ceiling",
            "",
            "No outbound, collection, payout request, provider/wallet/bank/accounting mutation, or revenue-recognition authority is granted.",
            "",
        ]
    )
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_size > MAX_BYTES:
        raise QueueError("input must be a bounded regular non-symlink file")
    value = loads_strict(path.read_bytes())
    if type(value) is not dict:
        raise QueueError("input root must be an object")
    return value


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise QueueError("output must not already exist and must not be a symlink") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--contacts", type=Path)
    parser.add_argument("--max-evidence-age-seconds", type=int, default=DEFAULT_MAX_EVIDENCE_AGE_SECONDS)
    parser.add_argument("--key-env", default="REWARD_SETTLEMENT_TRUST_KEY")
    args = parser.parse_args(argv)
    try:
        key_text = os.environ.get(args.key_env)
        if not key_text:
            raise QueueError(f"missing trust key environment variable: {args.key_env}")
        result = build_queue(
            _read_json(args.input),
            _read_json(args.registry),
            key_text.encode("utf-8"),
            as_of=args.as_of,
            contacts=_read_json(args.contacts) if args.contacts else None,
            max_evidence_age_seconds=args.max_evidence_age_seconds,
        )
        _write_exclusive(args.queue, canonical(result) + b"\n")
        if args.markdown:
            try:
                _write_exclusive(args.markdown, render_markdown(result).encode("utf-8"))
            except Exception:
                try:
                    args.queue.unlink()
                except OSError:
                    pass
                raise
        print(result["receipt_sha256"])
        return 0
    except (OSError, QueueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
