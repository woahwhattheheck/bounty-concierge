# SPDX-License-Identifier: MIT
"""Canonical evidence-bound cash-close board for paid external work.

This module composes *facts* from heterogeneous work/payment surfaces into one
owner queue.  It deliberately grants no authority to contact a sponsor, mutate
a provider or wallet, initiate payment, or recognize accounting revenue.

Most importantly, delivery/merge, issue closure, payment links, sent requests,
and sponsor assertions are never payment-rail settlement evidence.  ``SETTLED``
is reachable only from explicit ``PAYMENT_RAIL_RECEIVED`` evidence whose amount
fully covers a known obligation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = "bounty-cash-close-board/v1"
POLICY_SCHEMA = "bounty-cash-close-policy/v1"
_MAX_CLAIMS = 10_000
_MAX_REFS = 100
_MAX_EVENTS = 1_000
_MAX_TEXT = 512
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_HOURS = 24 * 365 * 10
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9._-]{1,11}$")
_AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$")

_REFERENCE_KINDS = frozenset({
    "github_issue", "github_pr", "email_thread", "slack_thread",
    "payment_rail", "sponsor_message", "support_ticket", "other",
})
_EVENT_KINDS = frozenset({
    "WORK_DELIVERED", "WORK_MERGED", "WORK_ACCEPTED",
    "EXTERNAL_REVIEW_REQUIRED", "PAYMENT_REQUEST_SENT",
    "PAYMENT_REQUEST_FAILED", "SPONSOR_PAID_ACK", "PAYMENT_RAIL_RECEIVED",
})
_AMOUNT_REQUIRED = frozenset({
    "PAYMENT_REQUEST_SENT", "PAYMENT_REQUEST_FAILED", "PAYMENT_RAIL_RECEIVED",
})
_AMOUNT_OPTIONAL = frozenset({"WORK_ACCEPTED"})
_AMOUNT_FORBIDDEN = _EVENT_KINDS - _AMOUNT_REQUIRED - _AMOUNT_OPTIONAL

# Lower is more urgent. Settled rows remain visible but sort last.
_STAGE_PRIORITY = {
    "PAID_ACKNOWLEDGED": 0,
    "PARTIALLY_SETTLED": 1,
    "PAYMENT_RECEIVED_UNPRICED": 2,
    "ACCEPTED": 3,
    "PAYMENT_ROUTE_BROKEN": 4,
    "PAYMENT_REQUESTED": 5,
    "BLOCKED_EXTERNAL_REVIEW": 6,
    "DELIVERED": 7,
    "IN_PROGRESS": 8,
    "SETTLED": 99,
}

_AUTHORITY = {
    "outbound_contact": False,
    "wallet_mutation": False,
    "provider_mutation": False,
    "payment_initiation": False,
    "accounting_revenue_recognition": False,
    "settled_requires_independent_payment_rail": True,
}


class CashCloseInputError(ValueError):
    """Input is malformed, ambiguous, or internally contradictory."""


class CashCloseEvidenceError(RuntimeError):
    """Evidence cannot safely support the claimed cash-close state."""


def _reject_constant(value: str) -> None:
    raise CashCloseInputError("JSON contains non-finite number: " + value)


def _strict_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CashCloseInputError("JSON contains duplicate object key: " + key)
        result[key] = value
    return result


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise CashCloseInputError("JSON numbers must not be floating point")
    if isinstance(value, list):
        for child in value:
            _reject_floats(child)
    elif isinstance(value, dict):
        for child in value.values():
            _reject_floats(child)


def strict_json_loads(text: str) -> Any:
    if type(text) is not str or len(text.encode("utf-8")) > _MAX_JSON_BYTES:
        raise CashCloseInputError("JSON source must be bounded UTF-8 text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except CashCloseInputError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CashCloseInputError("JSON source is malformed") from exc
    _reject_floats(value)
    return value


def _exact_keys(value: Any, expected: Iterable[str], label: str) -> Dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        raise CashCloseInputError(label + " fields do not match schema")
    return value


def _clean_text(value: Any, label: str, *, maximum: int = _MAX_TEXT) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(not char.isprintable() for char in value)
    ):
        raise CashCloseInputError(label + " is malformed")
    return value


def _utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not _UTC_RE.fullmatch(value):
        raise CashCloseInputError(label + " must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CashCloseInputError(label + " must be canonical UTC") from exc
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="auto").replace("+00:00", "Z")


def _positive_hours(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0 or value > _MAX_HOURS:
        raise CashCloseInputError(label + " must be a bounded positive integer")
    return value


def _money(raw: Any, label: str) -> Dict[str, str]:
    obj = _exact_keys(raw, {"amount", "currency"}, label)
    amount_raw = obj["amount"]
    currency = obj["currency"]
    if type(amount_raw) is not str or not _AMOUNT_RE.fullmatch(amount_raw):
        raise CashCloseInputError(label + ".amount must be canonical positive decimal text")
    try:
        amount = Decimal(amount_raw)
    except InvalidOperation as exc:
        raise CashCloseInputError(label + ".amount is invalid") from exc
    if not amount.is_finite() or amount <= 0:
        raise CashCloseInputError(label + ".amount must be positive")
    if type(currency) is not str or not _CURRENCY_RE.fullmatch(currency):
        raise CashCloseInputError(label + ".currency is invalid")
    return {"amount": _amount_text(amount), "currency": currency}


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _decimal(money: Optional[Dict[str, str]]) -> Optional[Decimal]:
    if money is None:
        return None
    return Decimal(money["amount"])


def _sha256(value: Any) -> str:
    data = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _normalize_policy(raw: Any) -> Dict[str, Any]:
    obj = _exact_keys(
        raw,
        {"schema", "followup_after_hours", "escalate_after_hours", "ack_verify_after_hours"},
        "policy",
    )
    if obj["schema"] != POLICY_SCHEMA:
        raise CashCloseInputError("policy schema is unsupported")
    followup = _positive_hours(obj["followup_after_hours"], "followup_after_hours")
    escalate = _positive_hours(obj["escalate_after_hours"], "escalate_after_hours")
    ack = _positive_hours(obj["ack_verify_after_hours"], "ack_verify_after_hours")
    if escalate <= followup:
        raise CashCloseInputError("escalate_after_hours must exceed followup_after_hours")
    normalized = {
        "schema": POLICY_SCHEMA,
        "followup_after_hours": followup,
        "escalate_after_hours": escalate,
        "ack_verify_after_hours": ack,
    }
    normalized["policy_sha256"] = _sha256(normalized)
    return normalized


def _normalize_references(raw: Any, claim_key: str) -> Tuple[List[Dict[str, str]], set]:
    if type(raw) is not list or not raw or len(raw) > _MAX_REFS:
        raise CashCloseInputError(claim_key + ".references must be a non-empty bounded list")
    result: List[Dict[str, str]] = []
    refs = set()
    for index, item in enumerate(raw):
        obj = _exact_keys(item, {"kind", "ref"}, f"{claim_key}.references[{index}]")
        kind = obj["kind"]
        ref = _clean_text(obj["ref"], f"{claim_key}.references[{index}].ref")
        if type(kind) is not str or kind not in _REFERENCE_KINDS:
            raise CashCloseInputError(f"{claim_key}.references[{index}].kind is unsupported")
        identity = (kind, ref)
        if identity in refs:
            raise CashCloseInputError(claim_key + " contains duplicate reference")
        refs.add(identity)
        result.append({"kind": kind, "ref": ref})
    result.sort(key=lambda item: (item["kind"], item["ref"]))
    return result, {item["ref"] for item in result}


def _normalize_event(
    raw: Any,
    *,
    claim_key: str,
    index: int,
    known_refs: set,
    evaluated_at: datetime,
) -> Dict[str, Any]:
    label = f"{claim_key}.events[{index}]"
    obj = _exact_keys(raw, {"event_id", "kind", "at", "source_ref", "sha256", "amount"}, label)
    event_id = _clean_text(obj["event_id"], label + ".event_id", maximum=200)
    if not _KEY_RE.fullmatch(event_id):
        raise CashCloseInputError(label + ".event_id is not canonical")
    kind = obj["kind"]
    if type(kind) is not str or kind not in _EVENT_KINDS:
        raise CashCloseInputError(label + ".kind is unsupported")
    at = _utc(obj["at"], label + ".at")
    if at > evaluated_at:
        raise CashCloseEvidenceError(label + " is in the future")
    source_ref = _clean_text(obj["source_ref"], label + ".source_ref")
    if source_ref not in known_refs:
        raise CashCloseEvidenceError(label + ".source_ref is absent from claim references")
    digest = obj["sha256"]
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        raise CashCloseInputError(label + ".sha256 must be lowercase SHA-256")
    amount_raw = obj["amount"]
    if kind in _AMOUNT_REQUIRED:
        if amount_raw is None:
            raise CashCloseInputError(label + ".amount is required for " + kind)
        amount = _money(amount_raw, label + ".amount")
    elif kind in _AMOUNT_FORBIDDEN:
        if amount_raw is not None:
            raise CashCloseInputError(label + ".amount is forbidden for " + kind)
        amount = None
    else:
        amount = None if amount_raw is None else _money(amount_raw, label + ".amount")
    return {
        "event_id": event_id,
        "kind": kind,
        "at": _iso(at),
        "source_ref": source_ref,
        "sha256": digest,
        "amount": amount,
        "_at": at,
    }


def _same_currency(monies: Iterable[Optional[Dict[str, str]]], claim_key: str) -> Optional[str]:
    values = {money["currency"] for money in monies if money is not None}
    if len(values) > 1:
        raise CashCloseEvidenceError(claim_key + " contains conflicting currencies")
    return next(iter(values)) if values else None


def _sum_money(events: List[Dict[str, Any]], kind: str) -> Optional[Dict[str, str]]:
    selected = [event["amount"] for event in events if event["kind"] == kind]
    if not selected:
        return None
    currency = selected[0]["currency"]
    total = sum((Decimal(item["amount"]) for item in selected), Decimal("0"))
    return {"amount": _amount_text(total), "currency": currency}


def _latest(events: List[Dict[str, Any]], kind: str) -> Optional[Dict[str, Any]]:
    rows = [event for event in events if event["kind"] == kind]
    return rows[-1] if rows else None


def _latest_any(events: List[Dict[str, Any]], kinds: Iterable[str]) -> Optional[Dict[str, Any]]:
    allowed = set(kinds)
    rows = [event for event in events if event["kind"] in allowed]
    return rows[-1] if rows else None


def _due(now: datetime, due_at: Optional[datetime]) -> Tuple[Optional[str], str]:
    if due_at is None:
        return None, "NONE"
    if due_at <= now:
        return _iso(due_at), "OVERDUE" if due_at < now else "DUE_NOW"
    return _iso(due_at), "PENDING"


def _compile_claim(
    raw: Any,
    *,
    evaluated_at: datetime,
    policy: Dict[str, Any],
    global_event_ids: set,
    global_evidence: set,
) -> Dict[str, Any]:
    obj = _exact_keys(raw, {"claim_key", "owner", "advertised", "references", "events"}, "claim")
    claim_key = _clean_text(obj["claim_key"], "claim.claim_key", maximum=200)
    if not _KEY_RE.fullmatch(claim_key):
        raise CashCloseInputError("claim_key is not canonical")
    owner = _clean_text(obj["owner"], claim_key + ".owner", maximum=200)
    advertised = None if obj["advertised"] is None else _money(obj["advertised"], claim_key + ".advertised")
    references, known_refs = _normalize_references(obj["references"], claim_key)
    raw_events = obj["events"]
    if type(raw_events) is not list or len(raw_events) > _MAX_EVENTS:
        raise CashCloseInputError(claim_key + ".events must be a bounded list")
    events = [
        _normalize_event(item, claim_key=claim_key, index=index, known_refs=known_refs, evaluated_at=evaluated_at)
        for index, item in enumerate(raw_events)
    ]
    events.sort(key=lambda item: (item["_at"], item["event_id"]))
    local_ids = set()
    local_evidence = set()
    for event in events:
        event_id = event["event_id"]
        digest = event["sha256"]
        if event_id in local_ids or event_id in global_event_ids:
            raise CashCloseEvidenceError("event_id is duplicated across the board: " + event_id)
        if digest in local_evidence or digest in global_evidence:
            raise CashCloseEvidenceError("evidence digest is reused across the board: " + digest)
        local_ids.add(event_id)
        local_evidence.add(digest)
    global_event_ids.update(local_ids)
    global_evidence.update(local_evidence)

    accepted_event = _latest(events, "WORK_ACCEPTED")
    accepted = accepted_event["amount"] if accepted_event is not None else None
    request_event = _latest(events, "PAYMENT_REQUEST_SENT")
    requested = request_event["amount"] if request_event is not None else None
    failed_event = _latest(events, "PAYMENT_REQUEST_FAILED")
    failed_requested = failed_event["amount"] if failed_event is not None else None
    received = _sum_money(events, "PAYMENT_RAIL_RECEIVED")
    currency = _same_currency([advertised, accepted, requested, failed_requested, received], claim_key)

    target = accepted if accepted is not None else advertised
    target_decimal = _decimal(target)
    received_decimal = _decimal(received) or Decimal("0")
    if target_decimal is not None and received_decimal > target_decimal:
        raise CashCloseEvidenceError(claim_key + " payment-rail receipts exceed known obligation")
    if requested is not None and target_decimal is not None and Decimal(requested["amount"]) > target_decimal:
        raise CashCloseEvidenceError(claim_key + " payment request exceeds known obligation")
    if failed_requested is not None and target_decimal is not None and Decimal(failed_requested["amount"]) > target_decimal:
        raise CashCloseEvidenceError(claim_key + " failed payment request exceeds known obligation")

    paid_ack = _latest(events, "SPONSOR_PAID_ACK")
    accepted_present = accepted_event is not None
    delivered = _latest_any(events, {"WORK_DELIVERED", "WORK_MERGED"})
    external_gate = _latest(events, "EXTERNAL_REVIEW_REQUIRED")
    route_broken = (
        failed_event is not None
        and (request_event is None or failed_event["_at"] > request_event["_at"])
        and received_decimal == 0
    )

    settled = target_decimal is not None and received_decimal == target_decimal and received_decimal > 0
    partial = target_decimal is not None and Decimal("0") < received_decimal < target_decimal
    unpriced_received = target_decimal is None and received_decimal > 0

    due_at: Optional[datetime]
    if settled:
        stage = "SETTLED"
        next_action = "NONE"
        due_at = None
    elif paid_ack is not None:
        stage = "PAID_ACKNOWLEDGED"
        next_action = "VERIFY_PAYMENT_RAIL"
        due_at = paid_ack["_at"] + timedelta(hours=policy["ack_verify_after_hours"])
    elif partial:
        stage = "PARTIALLY_SETTLED"
        next_action = "COLLECT_REMAINING_BALANCE"
        due_at = evaluated_at
    elif unpriced_received:
        stage = "PAYMENT_RECEIVED_UNPRICED"
        next_action = "VERIFY_OBLIGATION_AMOUNT"
        due_at = evaluated_at
    elif accepted_present and request_event is None and not route_broken:
        stage = "ACCEPTED"
        next_action = "REQUEST_PAYMENT"
        due_at = evaluated_at
    elif route_broken:
        stage = "PAYMENT_ROUTE_BROKEN"
        next_action = "REPAIR_PAYMENT_ROUTE"
        due_at = evaluated_at
    elif request_event is not None:
        stage = "PAYMENT_REQUESTED"
        followup_due = request_event["_at"] + timedelta(hours=policy["followup_after_hours"])
        escalate_due = request_event["_at"] + timedelta(hours=policy["escalate_after_hours"])
        if evaluated_at >= escalate_due:
            next_action = "ESCALATE_PAYMENT_FOLLOWUP"
            due_at = escalate_due
        elif evaluated_at >= followup_due:
            next_action = "FOLLOW_UP_PAYMENT"
            due_at = followup_due
        else:
            next_action = "MONITOR_PAYMENT"
            due_at = followup_due
    elif external_gate is not None:
        stage = "BLOCKED_EXTERNAL_REVIEW"
        next_action = "UNBLOCK_EXTERNAL_REVIEW"
        due_at = evaluated_at
    elif delivered is not None:
        stage = "DELIVERED"
        next_action = "REQUEST_ACCEPTANCE_OR_PAYOUT_TERMS"
        due_at = evaluated_at
    else:
        stage = "IN_PROGRESS"
        next_action = "ADVANCE_DELIVERY"
        due_at = evaluated_at

    sla_due_at, sla_status = _due(evaluated_at, due_at)
    cleaned_events = [
        {key: value for key, value in event.items() if key != "_at"}
        for event in events
    ]
    latest_at = cleaned_events[-1]["at"] if cleaned_events else None
    remaining = None
    if target_decimal is not None:
        remaining = {"amount": _amount_text(target_decimal - received_decimal), "currency": currency}
    return {
        "claim_key": claim_key,
        "owner": owner,
        "stage": stage,
        "priority": _STAGE_PRIORITY[stage],
        "next_action": next_action,
        "sla_due_at": sla_due_at,
        "sla_status": sla_status,
        "advertised": advertised,
        "accepted": accepted,
        "requested": requested,
        "received": received,
        "remaining": remaining,
        "currency": currency,
        "acceptance_verified": accepted_present,
        "independent_payment_rail_verified": received_decimal > 0,
        "settled": settled,
        "latest_event_at": latest_at,
        "references": references,
        "events": cleaned_events,
    }


def compile_cash_close_board(payload: Any, *, evaluated_at: Optional[datetime] = None) -> Dict[str, Any]:
    if evaluated_at is None:
        evaluated_at = datetime.now(timezone.utc)
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise CashCloseInputError("evaluated_at must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(timezone.utc)
    root = _exact_keys(payload, {"schema_version", "policy", "claims"}, "board")
    if root["schema_version"] != SCHEMA_VERSION:
        raise CashCloseInputError("board schema_version is unsupported")
    policy = _normalize_policy(root["policy"])
    claims_raw = root["claims"]
    if type(claims_raw) is not list or len(claims_raw) > _MAX_CLAIMS:
        raise CashCloseInputError("claims must be a bounded list")

    seen_claims = set()
    global_event_ids = set()
    global_evidence = set()
    claims: List[Dict[str, Any]] = []
    for raw in claims_raw:
        if type(raw) is not dict:
            raise CashCloseInputError("claim must be an object")
        key = raw.get("claim_key")
        if type(key) is not str:
            raise CashCloseInputError("claim_key must be text")
        normalized_key = key.casefold()
        if normalized_key in seen_claims:
            raise CashCloseEvidenceError("claim_key is duplicated case-insensitively: " + key)
        seen_claims.add(normalized_key)
        claims.append(
            _compile_claim(
                raw,
                evaluated_at=evaluated_at,
                policy=policy,
                global_event_ids=global_event_ids,
                global_evidence=global_evidence,
            )
        )

    claims.sort(key=lambda row: (row["priority"], row["claim_key"].casefold(), row["claim_key"]))
    counts = Counter(row["stage"] for row in claims)
    advertised_open: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    received_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    settled_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    accepted_unsettled = 0
    overdue = 0
    for row in claims:
        if row["accepted"] is not None and not row["settled"]:
            accepted_unsettled += 1
        if row["sla_status"] in {"OVERDUE", "DUE_NOW"} and not row["settled"]:
            overdue += 1
        advertised = row["advertised"]
        if advertised is not None and not row["settled"]:
            advertised_open[advertised["currency"]] += Decimal(advertised["amount"])
        received = row["received"]
        if received is not None:
            received_totals[received["currency"]] += Decimal(received["amount"])
            if row["settled"]:
                settled_totals[received["currency"]] += Decimal(received["amount"])

    def money_map(values: Dict[str, Decimal]) -> Dict[str, str]:
        return {currency: _amount_text(values[currency]) for currency in sorted(values)}

    body = {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": _iso(evaluated_at),
        "policy": policy,
        "authority": dict(_AUTHORITY),
        "summary": {
            "claim_count": len(claims),
            "unsettled_claim_count": sum(1 for row in claims if not row["settled"]),
            "accepted_unsettled_claim_count": accepted_unsettled,
            "due_or_overdue_claim_count": overdue,
            "counts_by_stage": {key: counts[key] for key in sorted(counts)},
            "advertised_unsettled_by_currency": money_map(advertised_open),
            "payment_rail_received_by_currency": money_map(received_totals),
            "settled_by_currency": money_map(settled_totals),
        },
        "claims": claims,
    }
    body["receipt_sha256"] = _sha256(body)
    return body


def _md(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r", " ").replace("\n", " ")


def _money_text(value: Optional[Dict[str, str]]) -> str:
    if value is None:
        return "—"
    return value["amount"] + " " + value["currency"]


def render_markdown(board: Dict[str, Any]) -> str:
    if type(board) is not dict or board.get("schema_version") != SCHEMA_VERSION:
        raise CashCloseInputError("render_markdown requires a compiled cash-close board")
    lines = [
        "# Bounty Cash Close Board",
        "",
        "Evaluated: `" + _md(board.get("evaluated_at")) + "`  ",
        "Receipt: `" + _md(board.get("receipt_sha256")) + "`",
        "",
        "**Settlement law:** merge, issue closure, payment links, sent requests, and sponsor assertions are not settlement. `SETTLED` requires independent payment-rail evidence covering a known obligation.",
        "",
        "| Priority | Claim | Owner | Stage | Advertised | Accepted | Requested | Received | Next action | SLA |",
        "|---:|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in board.get("claims", []):
        lines.append(
            "| {priority} | {claim} | {owner} | {stage} | {advertised} | {accepted} | {requested} | {received} | {action} | {sla} |".format(
                priority=row["priority"],
                claim=_md(row["claim_key"]),
                owner=_md(row["owner"]),
                stage=_md(row["stage"]),
                advertised=_md(_money_text(row["advertised"])),
                accepted=_md(_money_text(row["accepted"])),
                requested=_md(_money_text(row["requested"])),
                received=_md(_money_text(row["received"])),
                action=_md(row["next_action"]),
                sla=_md(row["sla_status"] + (" @ " + row["sla_due_at"] if row["sla_due_at"] else "")),
            )
        )
    lines.append("")
    lines.append("Authority ceiling: read/derive only; no outreach, payment initiation, provider/wallet mutation, or accounting revenue recognition.")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.cash_close_board",
        description="Compile one evidence-bound owner queue from paid-work closeout facts.",
    )
    parser.add_argument("manifest", help="cash-close manifest JSON")
    parser.add_argument("--json", action="store_true", help="emit canonical board JSON instead of Markdown")
    args = parser.parse_args(argv)
    try:
        with open(args.manifest, "r", encoding="utf-8") as handle:
            payload = strict_json_loads(handle.read())
        board = compile_cash_close_board(payload)
    except (OSError, CashCloseInputError, CashCloseEvidenceError) as exc:
        parser.error(str(exc))
    if args.json:
        json.dump(board, sys.stdout, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_markdown(board))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
