# SPDX-License-Identifier: MIT
"""Read-only, evidence-bound lifecycle ledger for merged-work rewards.

The compiler keeps commercial facts deliberately separate: an advertised bounty
is not an award; an award is not eligibility; a payout ticket or rail is not a
transfer; and a transfer is not paid until terminal provider/wallet/bank
evidence says so. Repository merge state never proves payment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Optional

INPUT_SCHEMA = "bounty-concierge/reward-settlement-input/v1"
LEDGER_SCHEMA = "bounty-concierge/reward-settlement-ledger/v1"
RECEIPT_SCHEMA = "bounty-concierge/reward-settlement-receipt/v1"
TRUTH_BOUNDARY = "READ_ONLY_EVIDENCE_RECONCILIATION"

_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_CASES = 10_000
_MAX_EVENTS_PER_CASE = 1_000
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,199}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_EVENT_KINDS = frozenset(
    {
        "ADVERTISED_BOUNTY",
        "SPONSOR_AWARD",
        "ELIGIBILITY",
        "PAYOUT_TICKET",
        "PAYOUT_RAIL",
        "TRANSFER",
        "CLOSURE",
    }
)
_SOURCE_AUTHORITIES = frozenset(
    {
        "REPOSITORY",
        "OFFICIAL_OFFER",
        "SPONSOR",
        "PROVIDER",
        "WALLET",
        "BANK",
        "OPERATOR_CAPTURE",
    }
)
_ALLOWED_AUTHORITY = {
    "ADVERTISED_BOUNTY": frozenset({"OFFICIAL_OFFER", "SPONSOR"}),
    "SPONSOR_AWARD": frozenset({"SPONSOR", "PROVIDER"}),
    "ELIGIBILITY": frozenset({"SPONSOR", "PROVIDER"}),
    "PAYOUT_TICKET": frozenset({"SPONSOR", "PROVIDER"}),
    "PAYOUT_RAIL": frozenset({"SPONSOR", "PROVIDER", "OPERATOR_CAPTURE"}),
    "TRANSFER": frozenset({"PROVIDER", "WALLET", "BANK"}),
    "CLOSURE": frozenset({"SPONSOR", "PROVIDER"}),
}
_TRANSFER_RANK = {"PENDING": 0, "CONFIRMING": 1, "CONFIRMED": 2, "FAILED": 2}
_TERMINAL_TRANSFER = frozenset({"CONFIRMED", "FAILED"})


class LedgerInputError(ValueError):
    """Malformed, ambiguous, or authority-violating ledger input."""


class LedgerVerificationError(RuntimeError):
    """Published ledger bytes do not verify against their bound source."""


def _reject_float(value: str) -> Any:
    raise LedgerInputError(f"floating-point JSON number is forbidden: {value}")


def _reject_constant(value: str) -> Any:
    raise LedgerInputError(f"non-finite JSON value is forbidden: {value}")


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise LedgerInputError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def load_json_bytes(raw: bytes) -> dict[str, Any]:
    if len(raw) > _MAX_JSON_BYTES:
        raise LedgerInputError("input exceeds maximum JSON size")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise LedgerInputError("UTF-8 BOM is forbidden")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise LedgerInputError("input must be strict UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except LedgerInputError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise LedgerInputError("input must be strict JSON") from exc
    if type(value) is not dict:
        raise LedgerInputError("root must be a JSON object")
    return value


def _json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LedgerInputError("value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact_keys(value: Any, expected: Iterable[str], *, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise LedgerInputError(f"{field} must be an object")
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        raise LedgerInputError(f"{field} keys mismatch; missing={missing} extra={extra}")
    return value


def _token(value: Any, *, field: str) -> str:
    if type(value) is not str or not _TOKEN_RE.fullmatch(value):
        raise LedgerInputError(f"{field} must be a bounded printable token")
    return value


def _repo(value: Any) -> str:
    if type(value) is not str or not _REPO_RE.fullmatch(value):
        raise LedgerInputError("work.repo must be owner/name")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise LedgerInputError("work.repo may not contain dot path segments")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise LedgerInputError(f"{field} must be a positive integer")
    return value


def _currency(value: Any, *, field: str) -> str:
    if type(value) is not str or not _CURRENCY_RE.fullmatch(value):
        raise LedgerInputError(f"{field} must be an ISO-like three-letter uppercase currency")
    return value


def _timestamp(value: Any, *, field: str) -> tuple[str, datetime]:
    if type(value) is not str or not _TS_RE.fullmatch(value):
        raise LedgerInputError(f"{field} must be canonical UTC seconds (YYYY-MM-DDTHH:MM:SSZ)")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise LedgerInputError(f"{field} is not a real UTC timestamp") from exc
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise LedgerInputError(f"{field} must be canonical UTC seconds")
    return value, parsed


def _source(
    raw: Any,
    *,
    field: str,
    generated_dt: datetime,
    allowed_authorities: frozenset[str],
    source_ids: dict[str, dict[str, Any]],
    fingerprints: dict[tuple[str, str, str, str], str],
) -> dict[str, Any]:
    value = _exact_keys(
        raw,
        ["source_id", "source_ref", "source_sha256", "observed_at", "authority"],
        field=field,
    )
    source_id = _token(value["source_id"], field=f"{field}.source_id")
    source_ref = _token(value["source_ref"], field=f"{field}.source_ref")
    source_sha = value["source_sha256"]
    if type(source_sha) is not str or not _SHA256_RE.fullmatch(source_sha):
        raise LedgerInputError(f"{field}.source_sha256 must be lowercase sha256")
    observed_at, observed_dt = _timestamp(value["observed_at"], field=f"{field}.observed_at")
    if observed_dt > generated_dt:
        raise LedgerInputError(f"{field}.observed_at may not be after generated_at")
    authority = value["authority"]
    if type(authority) is not str or authority not in _SOURCE_AUTHORITIES:
        raise LedgerInputError(f"{field}.authority is unsupported")
    if authority not in allowed_authorities:
        raise LedgerInputError(f"{field}.authority cannot support this fact")
    normalized = {
        "source_id": source_id,
        "source_ref": source_ref,
        "source_sha256": source_sha,
        "observed_at": observed_at,
        "authority": authority,
    }
    prior = source_ids.get(source_id)
    if prior is not None and prior != normalized:
        raise LedgerInputError(f"source_id {source_id} was rebound to different evidence")
    source_ids[source_id] = normalized
    fp = (source_ref, source_sha, observed_at, authority)
    prior_id = fingerprints.get(fp)
    if prior_id is not None and prior_id != source_id:
        raise LedgerInputError(
            f"identical evidence was reminted as source_id {source_id}; canonical id is {prior_id}"
        )
    fingerprints[fp] = source_id
    return normalized


def _money_fact(events: list[dict[str, Any]], *, label: str) -> Optional[dict[str, Any]]:
    if not events:
        return None
    facts = {(e["amount_minor"], e["currency"]) for e in events}
    if len(facts) != 1:
        raise LedgerInputError(f"conflicting {label} money facts require explicit reconciliation")
    amount, currency = next(iter(facts))
    return {
        "amount_minor": amount,
        "currency": currency,
        "source_ids": sorted({e["source"]["source_id"] for e in events}),
    }


def _eligibility(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"status": "ELIGIBILITY_UNKNOWN", "source_ids": []}
    decisions = {e["decision"] for e in events}
    if len(decisions) != 1:
        raise LedgerInputError("conflicting eligibility decisions require explicit reconciliation")
    decision = next(iter(decisions))
    return {
        "status": "ELIGIBLE_CONFIRMED" if decision == "ELIGIBLE" else "INELIGIBLE",
        "source_ids": sorted({e["source"]["source_id"] for e in events}),
    }


def _normalize_event(
    raw: Any,
    *,
    generated_dt: datetime,
    source_ids: dict[str, dict[str, Any]],
    fingerprints: dict[tuple[str, str, str, str], str],
) -> dict[str, Any]:
    if type(raw) is not dict:
        raise LedgerInputError("event must be an object")
    if set(raw) < {"event_id", "kind", "source"}:
        raise LedgerInputError("event must contain event_id, kind, and source")
    event_id = _token(raw["event_id"], field="event.event_id")
    kind = raw["kind"]
    if type(kind) is not str or kind not in _EVENT_KINDS:
        raise LedgerInputError("event.kind is unsupported")
    source = _source(
        raw["source"],
        field=f"event[{event_id}].source",
        generated_dt=generated_dt,
        allowed_authorities=_ALLOWED_AUTHORITY[kind],
        source_ids=source_ids,
        fingerprints=fingerprints,
    )
    if kind in {"ADVERTISED_BOUNTY", "SPONSOR_AWARD"}:
        _exact_keys(raw, ["event_id", "kind", "source", "amount_minor", "currency"], field=f"event[{event_id}]")
        amount = _positive_int(raw["amount_minor"], field=f"event[{event_id}].amount_minor")
        currency = _currency(raw["currency"], field=f"event[{event_id}].currency")
        return {"event_id": event_id, "kind": kind, "source": source, "amount_minor": amount, "currency": currency}
    if kind == "ELIGIBILITY":
        _exact_keys(raw, ["event_id", "kind", "source", "decision"], field=f"event[{event_id}]")
        decision = raw["decision"]
        if type(decision) is not str or decision not in {"ELIGIBLE", "INELIGIBLE"}:
            raise LedgerInputError("eligibility decision must be ELIGIBLE or INELIGIBLE")
        return {"event_id": event_id, "kind": kind, "source": source, "decision": decision}
    if kind == "PAYOUT_TICKET":
        _exact_keys(raw, ["event_id", "kind", "source", "ticket_id"], field=f"event[{event_id}]")
        return {"event_id": event_id, "kind": kind, "source": source, "ticket_id": _token(raw["ticket_id"], field=f"event[{event_id}].ticket_id")}
    if kind == "PAYOUT_RAIL":
        _exact_keys(raw, ["event_id", "kind", "source", "rail_ref"], field=f"event[{event_id}]")
        return {"event_id": event_id, "kind": kind, "source": source, "rail_ref": _token(raw["rail_ref"], field=f"event[{event_id}].rail_ref")}
    if kind == "TRANSFER":
        _exact_keys(
            raw,
            ["event_id", "kind", "source", "transfer_id", "status", "direction", "amount_minor", "currency"],
            field=f"event[{event_id}]",
        )
        transfer_id = _token(raw["transfer_id"], field=f"event[{event_id}].transfer_id")
        status = raw["status"]
        if type(status) is not str or status not in _TRANSFER_RANK:
            raise LedgerInputError("transfer status must be PENDING, CONFIRMING, CONFIRMED, or FAILED")
        if raw["direction"] != "INCOMING":
            raise LedgerInputError("only incoming transfer evidence may enter the reward ledger")
        return {
            "event_id": event_id,
            "kind": kind,
            "source": source,
            "transfer_id": transfer_id,
            "status": status,
            "direction": "INCOMING",
            "amount_minor": _positive_int(raw["amount_minor"], field=f"event[{event_id}].amount_minor"),
            "currency": _currency(raw["currency"], field=f"event[{event_id}].currency"),
        }
    if kind == "CLOSURE":
        _exact_keys(raw, ["event_id", "kind", "source", "reason"], field=f"event[{event_id}]")
        reason = raw["reason"]
        if type(reason) is not str or reason not in {"NO_REWARD", "DECLINED", "EXPIRED"}:
            raise LedgerInputError("closure reason must be NO_REWARD, DECLINED, or EXPIRED")
        return {"event_id": event_id, "kind": kind, "source": source, "reason": reason}
    raise LedgerInputError("unreachable event kind")


def _transfer_summaries(
    events: list[dict[str, Any]],
    *,
    case_key: tuple[str, int],
    global_transfer_owner: dict[str, tuple[str, int]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        transfer_id = event["transfer_id"]
        owner = global_transfer_owner.get(transfer_id)
        if owner is not None and owner != case_key:
            raise LedgerInputError(f"transfer_id {transfer_id} was reused across work items")
        global_transfer_owner[transfer_id] = case_key
        grouped.setdefault(transfer_id, []).append(event)

    summaries: list[dict[str, Any]] = []
    paid: dict[str, int] = {}
    for transfer_id, rows in sorted(grouped.items()):
        money = {(row["amount_minor"], row["currency"]) for row in rows}
        if len(money) != 1:
            raise LedgerInputError(f"transfer {transfer_id} changed amount or currency")
        amount_minor, currency = next(iter(money))
        ordered = sorted(rows, key=lambda r: (r["source"]["observed_at"], r["event_id"]))
        last_status: Optional[str] = None
        last_time: Optional[str] = None
        seen_same_time: dict[str, str] = {}
        for row in ordered:
            when = row["source"]["observed_at"]
            status = row["status"]
            prior_at_time = seen_same_time.get(when)
            if prior_at_time is not None and prior_at_time != status:
                raise LedgerInputError(f"transfer {transfer_id} has conflicting states at {when}")
            seen_same_time[when] = status
            if last_status is not None:
                if last_status in _TERMINAL_TRANSFER and status != last_status:
                    raise LedgerInputError(f"transfer {transfer_id} changed after terminal state {last_status}")
                if _TRANSFER_RANK[status] < _TRANSFER_RANK[last_status]:
                    raise LedgerInputError(f"transfer {transfer_id} regressed from {last_status} to {status}")
                if last_status in _TERMINAL_TRANSFER and status == last_status and when < (last_time or when):
                    raise LedgerInputError(f"transfer {transfer_id} terminal evidence is out of order")
            last_status = status
            last_time = when
        if last_status is None:
            raise LedgerInputError(f"transfer {transfer_id} had no evidence rows")
        summary = {
            "transfer_id": transfer_id,
            "status": last_status,
            "amount_minor": amount_minor,
            "currency": currency,
            "source_ids": sorted({r["source"]["source_id"] for r in rows}),
        }
        summaries.append(summary)
        if last_status == "CONFIRMED":
            paid[currency] = paid.get(currency, 0) + amount_minor
    return summaries, paid


def _money_aggregate(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        fact = row[key]
        if fact is None:
            continue
        currency = fact["currency"]
        out[currency] = out.get(currency, 0) + fact["amount_minor"]
    return dict(sorted(out.items()))


def _paid_aggregate(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        for currency, amount in row["paid_confirmed_by_currency"].items():
            out[currency] = out.get(currency, 0) + amount
    return dict(sorted(out.items()))


def compile_document(document: dict[str, Any]) -> dict[str, Any]:
    root = _exact_keys(document, ["schema", "generated_at", "cases"], field="root")
    if root["schema"] != INPUT_SCHEMA:
        raise LedgerInputError(f"schema must be {INPUT_SCHEMA}")
    generated_at, generated_dt = _timestamp(root["generated_at"], field="generated_at")
    cases = root["cases"]
    if type(cases) is not list or len(cases) > _MAX_CASES:
        raise LedgerInputError("cases must be a bounded list")

    source_ids: dict[str, dict[str, Any]] = {}
    fingerprints: dict[tuple[str, str, str, str], str] = {}
    global_event_ids: set[str] = set()
    global_ticket_owner: dict[str, tuple[str, int]] = {}
    global_transfer_owner: dict[str, tuple[str, int]] = {}
    case_ids: set[str] = set()
    work_ids: set[tuple[str, int]] = set()
    records: list[dict[str, Any]] = []

    for index, raw_case in enumerate(cases):
        case = _exact_keys(raw_case, ["case_id", "work", "events"], field=f"cases[{index}]")
        case_id = _token(case["case_id"], field=f"cases[{index}].case_id")
        if case_id in case_ids:
            raise LedgerInputError(f"duplicate case_id: {case_id}")
        case_ids.add(case_id)

        work = _exact_keys(
            case["work"],
            ["repo", "pr", "merge_commit_sha", "merged_at", "source"],
            field=f"cases[{index}].work",
        )
        repo = _repo(work["repo"])
        pr = _positive_int(work["pr"], field=f"cases[{index}].work.pr")
        work_key = (repo, pr)
        if work_key in work_ids:
            raise LedgerInputError(f"duplicate merged work item: {repo}#{pr}")
        work_ids.add(work_key)
        merge_sha = work["merge_commit_sha"]
        if type(merge_sha) is not str or not _SHA1_RE.fullmatch(merge_sha):
            raise LedgerInputError("work.merge_commit_sha must be lowercase 40-hex Git SHA")
        merged_at, merged_dt = _timestamp(work["merged_at"], field=f"cases[{index}].work.merged_at")
        if merged_dt > generated_dt:
            raise LedgerInputError("work.merged_at may not be after generated_at")
        merge_source = _source(
            work["source"],
            field=f"cases[{index}].work.source",
            generated_dt=generated_dt,
            allowed_authorities=frozenset({"REPOSITORY"}),
            source_ids=source_ids,
            fingerprints=fingerprints,
        )
        if merge_source["observed_at"] < merged_at:
            raise LedgerInputError("repository merge evidence may not predate merged_at")

        raw_events = case["events"]
        if type(raw_events) is not list or len(raw_events) > _MAX_EVENTS_PER_CASE:
            raise LedgerInputError("events must be a bounded list")
        events: list[dict[str, Any]] = []
        for raw_event in raw_events:
            event = _normalize_event(
                raw_event,
                generated_dt=generated_dt,
                source_ids=source_ids,
                fingerprints=fingerprints,
            )
            event_id = event["event_id"]
            if event_id in global_event_ids:
                raise LedgerInputError(f"duplicate event_id: {event_id}")
            global_event_ids.add(event_id)
            events.append(event)

        by_kind = {kind: [e for e in events if e["kind"] == kind] for kind in _EVENT_KINDS}
        advertised = _money_fact(by_kind["ADVERTISED_BOUNTY"], label="advertised bounty")
        award = _money_fact(by_kind["SPONSOR_AWARD"], label="sponsor award")
        if advertised is not None and award is not None and advertised["currency"] != award["currency"]:
            raise LedgerInputError("advertised bounty and sponsor award currencies conflict")
        eligibility = _eligibility(by_kind["ELIGIBILITY"])

        tickets = sorted({e["ticket_id"] for e in by_kind["PAYOUT_TICKET"]})
        for ticket in tickets:
            owner = global_ticket_owner.get(ticket)
            if owner is not None and owner != work_key:
                raise LedgerInputError(f"payout ticket {ticket} was reused across work items")
            global_ticket_owner[ticket] = work_key
        rails = sorted({e["rail_ref"] for e in by_kind["PAYOUT_RAIL"]})
        transfers, paid = _transfer_summaries(
            by_kind["TRANSFER"],
            case_key=work_key,
            global_transfer_owner=global_transfer_owner,
        )
        closure_reasons = sorted({e["reason"] for e in by_kind["CLOSURE"]})
        closure_sources = sorted({e["source"]["source_id"] for e in by_kind["CLOSURE"]})
        if closure_reasons and paid:
            raise LedgerInputError("CLOSED_WITHOUT_REWARD evidence conflicts with confirmed incoming payment")

        if closure_reasons:
            settlement_state = "CLOSED_WITHOUT_REWARD"
        elif paid:
            settlement_state = "PAID_CONFIRMED"
        elif transfers:
            settlement_state = "TRANSFER_EVIDENCED"
        elif rails:
            settlement_state = "PAYOUT_RAIL_SUPPLIED"
        elif tickets:
            settlement_state = "PAYOUT_TICKET_OPENED"
        elif award is not None:
            settlement_state = "SPONSOR_AWARDED"
        else:
            settlement_state = "MERGED_UNSETTLED"

        record = {
            "case_id": case_id,
            "work": {
                "repo": repo,
                "pr": pr,
                "merge_commit_sha": merge_sha,
                "merged_at": merged_at,
                "source_id": merge_source["source_id"],
            },
            "advertised": advertised,
            "advertised_status": "ADVERTISED_BOUNTY" if advertised is not None else "ADVERTISED_BOUNTY_NOT_EVIDENCED",
            "award": award,
            "award_status": "SPONSOR_AWARDED" if award is not None else "AWARD_NOT_EVIDENCED",
            "eligibility": eligibility,
            "payout_ticket": {"status": "PAYOUT_TICKET_OPENED" if tickets else "PAYOUT_TICKET_NOT_EVIDENCED", "ticket_ids": tickets},
            "payout_rail": {"status": "PAYOUT_RAIL_SUPPLIED" if rails else "PAYOUT_RAIL_NOT_EVIDENCED", "rail_refs": rails},
            "transfers": transfers,
            "paid_confirmed_by_currency": dict(sorted(paid.items())),
            "closure": {"status": "CLOSED_WITHOUT_REWARD" if closure_reasons else "CLOSURE_NOT_EVIDENCED", "reasons": closure_reasons, "source_ids": closure_sources},
            "settlement_state": settlement_state,
            "truth": {
                "merge_proves_payment": False,
                "advertised_bounty_proves_award": False,
                "ticket_or_rail_proves_transfer": False,
                "nonterminal_transfer_proves_payment": False,
                "provider_payment_evidenced": bool(paid),
                "recognized_revenue": False,
            },
        }
        records.append(record)

    records.sort(key=lambda r: (r["work"]["repo"], r["work"]["pr"], r["case_id"]))
    state_counts: dict[str, int] = {}
    for record in records:
        state = record["settlement_state"]
        state_counts[state] = state_counts.get(state, 0) + 1

    return {
        "schema": LEDGER_SCHEMA,
        "generated_at": generated_at,
        "truth_boundary": TRUTH_BOUNDARY,
        "records": records,
        "aggregates": {
            "case_count": len(records),
            "state_counts": dict(sorted(state_counts.items())),
            "advertised_reference_by_currency": _money_aggregate(records, "advertised"),
            "sponsor_awarded_by_currency": _money_aggregate(records, "award"),
            "paid_confirmed_by_currency": _paid_aggregate(records),
            "recognized_revenue_by_currency": {},
        },
        "authority": {
            "send_outbound": False,
            "request_payout": False,
            "mutate_provider": False,
            "mutate_wallet_or_bank": False,
            "recognize_accounting_revenue": False,
        },
    }


def render_markdown(ledger: dict[str, Any]) -> str:
    lines = [
        "# Merged-work reward settlement ledger",
        "",
        f"Generated: `{ledger['generated_at']}`",
        f"Truth boundary: `{ledger['truth_boundary']}`",
        "",
        "> Merge is not payment. Advertised bounty is not award. A payout ticket or rail is not a transfer. Only terminal provider/wallet/bank evidence can support PAID_CONFIRMED.",
        "",
        "## Totals",
        "",
        f"- Cases: {ledger['aggregates']['case_count']}",
    ]
    for label, key in [
        ("Advertised reference", "advertised_reference_by_currency"),
        ("Sponsor awarded", "sponsor_awarded_by_currency"),
        ("Paid confirmed", "paid_confirmed_by_currency"),
    ]:
        values = ledger["aggregates"][key]
        rendered = ", ".join(f"{currency} {amount} minor units" for currency, amount in values.items()) or "none"
        lines.append(f"- {label}: {rendered}")
    lines.extend(["", "## Cases", ""])
    for record in ledger["records"]:
        work = record["work"]
        lines.append(f"### {record['case_id']} — {work['repo']}#{work['pr']}")
        lines.append("")
        lines.append(f"- Settlement: `{record['settlement_state']}`")
        lines.append(f"- Advertised: `{record['advertised_status']}`")
        lines.append(f"- Award: `{record['award_status']}`")
        lines.append(f"- Eligibility: `{record['eligibility']['status']}`")
        lines.append(f"- Ticket: `{record['payout_ticket']['status']}`")
        lines.append(f"- Rail: `{record['payout_rail']['status']}`")
        paid = record["paid_confirmed_by_currency"]
        paid_text = ", ".join(f"{c} {a} minor units" for c, a in paid.items()) or "none"
        lines.append(f"- Terminal payment evidence: {paid_text}")
        lines.append(f"- Closure: `{record['closure']['status']}`")
        lines.append("")
    lines.extend(
        [
            "## Authority ceiling",
            "",
            "This artifact is read-only evidence reconciliation. It does not contact sponsors, request payout, mutate providers/wallets/banks, or recognize accounting revenue.",
            "",
        ]
    )
    return "\n".join(lines)


def compile_bytes(raw: bytes) -> tuple[bytes, bytes, bytes]:
    document = load_json_bytes(raw)
    ledger = compile_document(document)
    ledger_bytes = _json_bytes(ledger)
    markdown_bytes = render_markdown(ledger).encode("utf-8")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_at": ledger["generated_at"],
        "truth_boundary": TRUTH_BOUNDARY,
        "input_sha256": _sha256(raw),
        "ledger_sha256": _sha256(ledger_bytes),
        "markdown_sha256": _sha256(markdown_bytes),
        "record_count": len(ledger["records"]),
        "provider_payment_case_count": sum(1 for r in ledger["records"] if r["truth"]["provider_payment_evidenced"]),
        "recognized_revenue": False,
    }
    return ledger_bytes, markdown_bytes, _json_bytes(receipt)


def verify_bytes(raw: bytes, ledger_bytes: bytes, markdown_bytes: bytes, receipt_bytes: bytes) -> None:
    expected_ledger, expected_markdown, expected_receipt = compile_bytes(raw)
    if ledger_bytes != expected_ledger:
        raise LedgerVerificationError("ledger bytes do not match deterministic recomputation")
    if markdown_bytes != expected_markdown:
        raise LedgerVerificationError("markdown bytes do not match deterministic recomputation")
    if receipt_bytes != expected_receipt:
        raise LedgerVerificationError("receipt bytes do not match deterministic recomputation")


def _write_exclusive(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()


def _compile_cli(input_path: Path, out_dir: Path) -> int:
    raw = input_path.read_bytes()
    ledger, markdown, receipt = compile_bytes(raw)
    out_dir.mkdir(parents=True, exist_ok=False)
    _write_exclusive(out_dir / "ledger.json", ledger)
    _write_exclusive(out_dir / "ledger.md", markdown)
    _write_exclusive(out_dir / "receipt.json", receipt)
    return 0


def _verify_cli(input_path: Path, ledger_path: Path, markdown_path: Path, receipt_path: Path) -> int:
    verify_bytes(
        input_path.read_bytes(),
        ledger_path.read_bytes(),
        markdown_path.read_bytes(),
        receipt_path.read_bytes(),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile", help="compile an evidence-bound settlement ledger")
    compile_parser.add_argument("--input", required=True, type=Path)
    compile_parser.add_argument("--out-dir", required=True, type=Path)
    verify_parser = sub.add_parser("verify", help="verify published ledger bytes exactly")
    verify_parser.add_argument("--input", required=True, type=Path)
    verify_parser.add_argument("--ledger", required=True, type=Path)
    verify_parser.add_argument("--markdown", required=True, type=Path)
    verify_parser.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "compile":
            return _compile_cli(args.input, args.out_dir)
        if args.command == "verify":
            return _verify_cli(args.input, args.ledger, args.markdown, args.receipt)
    except (OSError, LedgerInputError, LedgerVerificationError) as exc:
        raise SystemExit(str(exc)) from exc
    raise SystemExit("unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
