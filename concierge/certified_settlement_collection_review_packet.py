# SPDX-License-Identifier: MIT
"""Owner-review action packets layered on the certified settlement queue.

The landed collection queue decides settlement truth. This module never changes
that truth; it binds optional separately-observed contact-route evidence and
turns each queue row into a deterministic next-action packet. No packet is send
or payout authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any, Iterable

QUEUE_SCHEMA = "bounty-concierge/certified-settlement-collection-queue/v1"
ROUTES_SCHEMA = "bounty-concierge/certified-settlement-contact-routes/v1"
PACKET_SCHEMA = "bounty-concierge/certified-settlement-review-packet/v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,239}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_ROUTES = 20_000

FALSE_AUTHORITY = {
    "send_outbound": False,
    "request_payout": False,
    "mutate_provider": False,
    "mutate_wallet_or_bank": False,
    "recognize_accounting_revenue": False,
}
FOLLOWUP_STATES = frozenset({
    "AWARD_FOLLOWUP_CANDIDATE",
    "PAYOUT_TICKET_FOLLOWUP_CANDIDATE",
    "PAYOUT_RAIL_FOLLOWUP_CANDIDATE",
    "TRANSFER_PENDING_FOLLOWUP_CANDIDATE",
})


class ReviewPacketError(ValueError):
    pass


def _pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    out = {}
    for key, value in pairs:
        if key in out:
            raise ReviewPacketError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def loads_strict(raw: bytes) -> Any:
    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf") or len(raw) > 4 * 1024 * 1024:
        raise ReviewPacketError("input must be bounded BOM-free bytes")
    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_pairs,
            parse_float=lambda value: (_ for _ in ()).throw(ReviewPacketError(f"float forbidden: {value}")),
            parse_constant=lambda value: (_ for _ in ()).throw(ReviewPacketError(f"non-finite forbidden: {value}")),
        )
    except ReviewPacketError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewPacketError("invalid strict JSON") from exc


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ReviewPacketError("value is not canonical JSON") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _exact(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ReviewPacketError(f"{name} keys mismatch")
    return value


def _token(value: Any, name: str) -> str:
    if type(value) is not str or TOKEN.fullmatch(value) is None:
        raise ReviewPacketError(f"{name} invalid")
    return value


def _timestamp(value: Any, name: str) -> tuple[str, datetime]:
    if type(value) is not str or TS.fullmatch(value) is None:
        raise ReviewPacketError(f"{name} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ReviewPacketError(f"{name} invalid") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ReviewPacketError(f"{name} invalid")
    return value, parsed


def _verify_queue(queue: Any) -> dict[str, Any]:
    if type(queue) is not dict or queue.get("schema") != QUEUE_SCHEMA:
        raise ReviewPacketError("queue schema invalid")
    receipt = queue.get("receipt_sha256")
    if type(receipt) is not str or HEX64.fullmatch(receipt) is None:
        raise ReviewPacketError("queue receipt invalid")
    body = {key: value for key, value in queue.items() if key != "receipt_sha256"}
    if not hmac.compare_digest(receipt, _sha(body)):
        raise ReviewPacketError("queue receipt mismatch")
    authority = queue.get("authority")
    if authority != FALSE_AUTHORITY:
        raise ReviewPacketError("queue authority is not hard-false")
    rows = queue.get("records")
    if type(rows) is not list:
        raise ReviewPacketError("queue records invalid")
    seen = set()
    for index, row in enumerate(rows):
        if type(row) is not dict:
            raise ReviewPacketError(f"queue.records[{index}] invalid")
        case_id = _token(row.get("case_id"), f"queue.records[{index}].case_id")
        if case_id in seen:
            raise ReviewPacketError(f"duplicate queue case_id: {case_id}")
        seen.add(case_id)
    return queue


def _parse_routes(routes_doc: Any, *, queue_as_of: str) -> dict[str, list[dict[str, Any]]]:
    doc = _exact(routes_doc, {"schema", "as_of", "routes"}, "routes")
    if doc["schema"] != ROUTES_SCHEMA:
        raise ReviewPacketError("routes schema invalid")
    as_of, as_of_dt = _timestamp(doc["as_of"], "routes.as_of")
    if as_of != queue_as_of:
        raise ReviewPacketError("routes.as_of must equal queue.as_of")
    rows = doc["routes"]
    if type(rows) is not list or len(rows) > MAX_ROUTES:
        raise ReviewPacketError("routes must be a bounded list")
    out: dict[str, list[dict[str, Any]]] = {}
    identities = set()
    for index, raw in enumerate(rows):
        row = _exact(raw, {"case_id", "route_id", "kind", "route_ref", "disposition", "source_ref", "source_sha256", "observed_at"}, f"routes[{index}]")
        case_id = _token(row["case_id"], f"routes[{index}].case_id")
        route_id = _token(row["route_id"], f"routes[{index}].route_id")
        identity = (case_id, route_id)
        if identity in identities:
            raise ReviewPacketError(f"duplicate route: {case_id}/{route_id}")
        identities.add(identity)
        kind = row["kind"]
        if kind not in {"EMAIL", "ISSUE", "TICKET", "DM", "FORM", "OTHER"}:
            raise ReviewPacketError(f"routes[{index}].kind invalid")
        disposition = row["disposition"]
        if disposition not in {"AVAILABLE", "DNR", "UNKNOWN"}:
            raise ReviewPacketError(f"routes[{index}].disposition invalid")
        observed_at, observed_dt = _timestamp(row["observed_at"], f"routes[{index}].observed_at")
        if observed_dt > as_of_dt:
            raise ReviewPacketError("route observation may not be after as_of")
        digest = row["source_sha256"]
        if type(digest) is not str or HEX64.fullmatch(digest) is None:
            raise ReviewPacketError("route source_sha256 invalid")
        normalized = {
            "route_id": route_id,
            "kind": kind,
            "route_ref": _token(row["route_ref"], f"routes[{index}].route_ref"),
            "disposition": disposition,
            "source_ref": _token(row["source_ref"], f"routes[{index}].source_ref"),
            "source_sha256": digest,
            "observed_at": observed_at,
            "age_seconds": int((as_of_dt - observed_dt).total_seconds()),
        }
        out.setdefault(case_id, []).append(normalized)
    for rows_for_case in out.values():
        rows_for_case.sort(key=lambda row: (row["observed_at"], row["route_id"]))
    return out


def _route_status(routes: list[dict[str, Any]]) -> str:
    dispositions = {row["disposition"] for row in routes}
    if "DNR" in dispositions and "AVAILABLE" in dispositions:
        return "HOLD_CONFLICT"
    if "DNR" in dispositions:
        return "DNR"
    if "AVAILABLE" in dispositions:
        return "AVAILABLE"
    if routes:
        return "UNKNOWN"
    return "NONE"


def _next_action(state: str, route_status: str) -> tuple[str, str]:
    if route_status == "HOLD_CONFLICT":
        return "HOLD", "resolve contradictory contact-route evidence before any outreach"
    if state == "SETTLED":
        return "NO_FOLLOWUP", "retain certified payment evidence; no collection follow-up"
    if state == "CLOSED_NO_REWARD":
        return "NO_FOLLOWUP", "retain certified closure evidence; no payout follow-up"
    if state == "HOLD_CONTRADICTION":
        return "HOLD", "resolve settlement contradiction before any follow-up"
    if state == "NEEDS_TRUST_EVIDENCE":
        return "REFRESH_EVIDENCE", "refresh independently trusted settlement evidence before any follow-up"
    if state in FOLLOWUP_STATES and route_status == "DNR":
        return "HOLD_DNR", "owner review only; DNR route recorded, do not send or open a replacement route"
    if state in FOLLOWUP_STATES and route_status in {"NONE", "UNKNOWN"}:
        return "FIND_ROUTE", "owner may locate a separately authorized contact route; this packet cannot send"
    if state == "AWARD_FOLLOWUP_CANDIDATE":
        return "REVIEW_AWARD_STATUS", "owner may review award status using the separately authorized route"
    if state == "PAYOUT_TICKET_FOLLOWUP_CANDIDATE":
        return "REVIEW_PAYOUT_TICKET", "owner may review payout-ticket status using the separately authorized route"
    if state == "PAYOUT_RAIL_FOLLOWUP_CANDIDATE":
        return "REVIEW_PAYOUT_RAIL", "owner may review payout-rail status; provider mutation remains forbidden"
    if state == "TRANSFER_PENDING_FOLLOWUP_CANDIDATE":
        return "REVIEW_TRANSFER_STATUS", "owner may review transfer status; nonterminal transfer is not payment"
    return "HOLD", "unknown queue state; do not act"


def build_review_packet(queue: Any, routes_doc: Any) -> dict[str, Any]:
    queue = _verify_queue(queue)
    queue_as_of = queue.get("as_of")
    _timestamp(queue_as_of, "queue.as_of")
    routes_by_case = _parse_routes(routes_doc, queue_as_of=queue_as_of)
    case_ids = {row["case_id"] for row in queue["records"]}
    unknown = sorted(set(routes_by_case) - case_ids)
    if unknown:
        raise ReviewPacketError(f"routes reference unknown cases: {unknown}")
    records = []
    for row in queue["records"]:
        case_id = row["case_id"]
        routes = routes_by_case.get(case_id, [])
        route_status = _route_status(routes)
        action_code, action = _next_action(row.get("queue_state"), route_status)
        records.append({
            "case_id": case_id,
            "work": row.get("work"),
            "queue_state": row.get("queue_state"),
            "reason_codes": row.get("reason_codes", []),
            "money": row.get("money"),
            "event_age_seconds": row.get("event_age_seconds"),
            "route_status": route_status,
            "contact_routes": routes,
            "next_action_code": action_code,
            "next_action": action,
            "outbound_authorized": False,
        })
    body = {
        "schema": PACKET_SCHEMA,
        "as_of": queue_as_of,
        "queue_receipt_sha256": queue["receipt_sha256"],
        "routes_sha256": _sha(routes_doc),
        "records": records,
        "authority": dict(FALSE_AUTHORITY),
    }
    return {**body, "receipt_sha256": _sha(body)}


def compile_bytes(queue_raw: bytes, routes_raw: bytes) -> bytes:
    return canonical(build_review_packet(loads_strict(queue_raw), loads_strict(routes_raw))) + b"\n"
