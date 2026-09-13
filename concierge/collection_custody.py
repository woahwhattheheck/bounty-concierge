# SPDX-License-Identifier: MIT
"""Evidence-bound custody for sponsor collection requests.

This module bridges ``collection_request`` packets to an append-only lifecycle
ledger.  It is deliberately offline: it records evidence that an operator says
was sent or observed, but never sends mail/comments, mutates a provider or
wallet, or turns merge/advertised amounts/silence into acceptance, debt, cash,
or revenue.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import urlsplit

from concierge.collection_request import (
    CollectionRequestInputError,
    verify_collection_request,
)

SCHEMA = "bounty-collection-custody/v1"
_ROUTE_CLASSES = frozenset({"github_comment", "email", "form", "other"})
_SPONSOR_STATUSES = frozenset(
    {
        "acknowledged",
        "needs_changes",
        "accepted",
        "awarded",
        "rejected",
        "payment_pending",
        "paid_evidence_ready",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9_.-]{1,11}$")
_GH_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_MAX_EVENTS = 10_000
_MAX_JSON_BYTES = 2 * 1024 * 1024


class CollectionCustodyError(ValueError):
    """Malformed, conflicting, stale, or authority-expanding custody evidence."""

    def __init__(self, code: str, message: Optional[str] = None):
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: Optional[str] = None) -> None:
    raise CollectionCustodyError(code, message)


def _canonical_json(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CollectionCustodyError("NONCANONICAL_JSON") from exc
    if len(raw) > _MAX_JSON_BYTES:
        _fail("JSON_TOO_LARGE")
    return raw


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _plain_dict(value: Any, code: str = "OBJECT_REQUIRED") -> dict[str, Any]:
    if type(value) is not dict:
        _fail(code)
    return value


def _exact_keys(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    obj = _plain_dict(value, code)
    if set(obj) != keys:
        _fail(code)
    return obj


def _text(value: Any, field: str, *, max_len: int = 512) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > max_len:
        _fail("TEXT_INVALID", field)
    if any(ord(ch) < 32 for ch in value):
        _fail("TEXT_CONTROL_CHARACTER", field)
    return value


def _opaque_id(value: Any, field: str) -> str:
    text = _text(value, field, max_len=128)
    if "@" in text or "/" in text or "\\" in text:
        _fail("ID_NOT_OPAQUE", field)
    if not _ID_RE.fullmatch(text):
        _fail("ID_INVALID", field)
    return text


def _github_pr_url(value: Any) -> str:
    text = _text(value, "pr_url", max_len=1024)
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError:
        _fail("PR_URL_INVALID")
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        _fail("PR_URL_INVALID")
    parts = parsed.path.split("/")
    if len(parts) != 5 or parts[0] != "" or parts[3] != "pull":
        _fail("PR_URL_INVALID")
    owner, repo, number = parts[1], parts[2], parts[4]
    if (
        not _GH_NAME_RE.fullmatch(owner)
        or not _GH_NAME_RE.fullmatch(repo)
        or owner in {".", ".."}
        or repo in {".", ".."}
        or not number.isdigit()
        or int(number) <= 0
        or str(int(number)) != number
    ):
        _fail("PR_URL_INVALID")
    canonical = f"https://github.com/{owner}/{repo}/pull/{number}"
    if text != canonical:
        _fail("PR_URL_INVALID")
    return text


def _positive_amount(value: Any) -> str:
    text = _text(value, "advertised_amount", max_len=64)
    try:
        amount = Decimal(text)
    except InvalidOperation:
        _fail("ADVERTISED_AMOUNT_INVALID")
    parts = amount.as_tuple()
    if (
        not amount.is_finite()
        or amount <= 0
        or len(parts.digits) > 30
        or not isinstance(parts.exponent, int)
        or abs(parts.exponent) > 18
    ):
        _fail("ADVERTISED_AMOUNT_INVALID")
    canonical = format(amount, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if text != canonical:
        _fail("ADVERTISED_AMOUNT_NONCANONICAL")
    return text


def _currency(value: Any) -> str:
    text = _text(value, "currency", max_len=12)
    if not _CURRENCY_RE.fullmatch(text):
        _fail("CURRENCY_INVALID")
    return text


def _sha256(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        _fail("SHA256_INVALID", field)
    return value


def _timestamp(value: Any, field: str) -> tuple[str, datetime]:
    if type(value) is not str or not value.endswith("Z"):
        _fail("TIMESTAMP_INVALID", field)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("TIMESTAMP_INVALID", field)
    if parsed.tzinfo != timezone.utc:
        _fail("TIMESTAMP_INVALID", field)
    canonical = parsed.isoformat(
        timespec="microseconds" if parsed.microsecond else "seconds"
    ).replace("+00:00", "Z")
    if canonical != value:
        _fail("TIMESTAMP_NONCANONICAL", field)
    return canonical, parsed


def _assert_as_of(as_of: Any) -> tuple[str, datetime]:
    return _timestamp(as_of, "as_of")


def _ensure_not_future(occurred: datetime, as_of: datetime) -> None:
    if occurred > as_of:
        _fail("FUTURE_EVENT")


def _route_class(value: Any) -> str:
    if value not in _ROUTE_CLASSES:
        _fail("ROUTE_CLASS_INVALID")
    return value


def _request_id(value: Any) -> str:
    return _opaque_id(value, "request_id")


def _event_id(value: Any) -> str:
    return _opaque_id(value, "event_id")


def _verify_packet(packet: Any) -> dict[str, str]:
    if type(packet) is not dict:
        _fail("PACKET_OBJECT_REQUIRED")
    source = packet.get("source")
    if type(source) is not dict:
        _fail("PACKET_SOURCE_INVALID")
    try:
        valid = verify_collection_request(source, packet)
    except (CollectionRequestInputError, TypeError, ValueError):
        valid = False
    if not valid:
        _fail("PACKET_VERIFICATION_FAILED")

    receipt = packet.get("receipt_sha256")
    _sha256(receipt, "packet.receipt_sha256")
    work = source["work"]
    acceptance = source["acceptance"]
    payout_route = source["payout_route"]
    pr_url = work["canonical_url"]
    head_sha = work["head_sha"]
    if type(head_sha) is not str or not _SHA40_RE.fullmatch(head_sha):
        _fail("PACKET_HEAD_SHA_INVALID")
    disposition = packet.get("disposition")
    expected = (
        "READY_TO_REQUEST_ASSESSMENT"
        if acceptance["kind"] == "NONE"
        else "READY_TO_REQUEST_PAYMENT"
    )
    if disposition != expected:
        _fail("PACKET_DISPOSITION_INVALID")
    acceptance_digest = acceptance["evidence_sha256"] or ""
    _sha256(acceptance_digest, "acceptance.evidence_sha256", allow_empty=True)
    return {
        "packet_sha256": receipt,
        "pr_url": pr_url,
        "head_sha": head_sha,
        "disposition": disposition,
        "advertised_amount": work["advertised_amount"],
        "currency": work["currency"],
        "acceptance_kind": acceptance["kind"],
        "acceptance_evidence_sha256": acceptance_digest,
        "payout_route_sha256": _digest(payout_route),
    }


def _request_core(
    binding: dict[str, str], route_class: str, route_key: str
) -> dict[str, str]:
    return {
        **binding,
        "route_class": route_class,
        "route_key": route_key,
    }


def _make_register_event(
    *,
    event_id: Any,
    occurred_at: Any,
    request_id: Any,
    packet: Any,
    route_class: Any,
    route_key: Any,
    as_of: datetime,
) -> dict[str, Any]:
    binding = _verify_packet(packet)
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    route = _route_class(route_class)
    opaque_route = _opaque_id(route_key, "route_key")
    core = _request_core(binding, route, opaque_route)
    return {
        "event_id": _event_id(event_id),
        "event_type": "request_registered",
        "occurred_at": event_time,
        "request_id": _request_id(request_id),
        **core,
        "request_digest": _digest(core),
    }


def _make_dispatch_event(
    *,
    event_id: Any,
    event_type: str,
    occurred_at: Any,
    request_id: Any,
    request_digest: Any,
    route_class: Any,
    provider_receipt_key: Any,
    evidence_sha256: Any,
    as_of: datetime,
) -> dict[str, Any]:
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    return {
        "event_id": _event_id(event_id),
        "event_type": event_type,
        "occurred_at": event_time,
        "request_id": _request_id(request_id),
        "request_digest": _sha256(request_digest, "request_digest"),
        "route_class": _route_class(route_class),
        "provider_receipt_key": _opaque_id(
            provider_receipt_key, "provider_receipt_key"
        ),
        "evidence_sha256": _sha256(evidence_sha256, "evidence_sha256"),
    }


def _make_sponsor_event(
    *,
    event_id: Any,
    occurred_at: Any,
    request_id: Any,
    request_digest: Any,
    status: Any,
    provider_event_key: Any,
    evidence_sha256: Any,
    as_of: datetime,
) -> dict[str, Any]:
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    if status not in _SPONSOR_STATUSES:
        _fail("SPONSOR_STATUS_INVALID")
    return {
        "event_id": _event_id(event_id),
        "event_type": "sponsor_event",
        "occurred_at": event_time,
        "request_id": _request_id(request_id),
        "request_digest": _sha256(request_digest, "request_digest"),
        "status": status,
        "provider_event_key": _opaque_id(provider_event_key, "provider_event_key"),
        "evidence_sha256": _sha256(evidence_sha256, "evidence_sha256"),
    }


def _make_supersede_event(
    *,
    event_id: Any,
    occurred_at: Any,
    old_request_id: Any,
    old_request_digest: Any,
    new_request_id: Any,
    new_request_digest: Any,
    evidence_sha256: Any,
    as_of: datetime,
) -> dict[str, Any]:
    event_time, event_dt = _timestamp(occurred_at, "occurred_at")
    _ensure_not_future(event_dt, as_of)
    return {
        "event_id": _event_id(event_id),
        "event_type": "request_superseded",
        "occurred_at": event_time,
        "old_request_id": _request_id(old_request_id),
        "old_request_digest": _sha256(old_request_digest, "old_request_digest"),
        "new_request_id": _request_id(new_request_id),
        "new_request_digest": _sha256(new_request_digest, "new_request_digest"),
        "evidence_sha256": _sha256(evidence_sha256, "evidence_sha256"),
    }


_REGISTER_KEYS = {
    "event_id", "event_type", "occurred_at", "request_id", "packet_sha256",
    "pr_url", "head_sha", "disposition", "advertised_amount", "currency",
    "acceptance_kind", "acceptance_evidence_sha256", "payout_route_sha256",
    "route_class", "route_key", "request_digest",
}
_DISPATCH_KEYS = {
    "event_id", "event_type", "occurred_at", "request_id", "request_digest",
    "route_class", "provider_receipt_key", "evidence_sha256",
}
_SPONSOR_KEYS = {
    "event_id", "event_type", "occurred_at", "request_id", "request_digest",
    "status", "provider_event_key", "evidence_sha256",
}
_SUPERSEDE_KEYS = {
    "event_id", "event_type", "occurred_at", "old_request_id",
    "old_request_digest", "new_request_id", "new_request_digest", "evidence_sha256",
}


def _validate_stored_event(event: Any, as_of: datetime) -> dict[str, Any]:
    obj = _plain_dict(event, "EVENT_OBJECT_REQUIRED")
    event_type = obj.get("event_type")
    if event_type == "request_registered":
        _exact_keys(obj, _REGISTER_KEYS, "REGISTER_EVENT_SHAPE_MISMATCH")
        normalized = deepcopy(obj)
        normalized["event_id"] = _event_id(obj["event_id"])
        normalized["request_id"] = _request_id(obj["request_id"])
        normalized["occurred_at"] = _timestamp(obj["occurred_at"], "occurred_at")[0]
        normalized["packet_sha256"] = _sha256(obj["packet_sha256"], "packet_sha256")
        normalized["payout_route_sha256"] = _sha256(
            obj["payout_route_sha256"], "payout_route_sha256"
        )
        normalized["acceptance_evidence_sha256"] = _sha256(
            obj["acceptance_evidence_sha256"],
            "acceptance_evidence_sha256",
            allow_empty=True,
        )
        normalized["request_digest"] = _sha256(
            obj["request_digest"], "request_digest"
        )
        normalized["pr_url"] = _github_pr_url(obj["pr_url"])
        normalized["advertised_amount"] = _positive_amount(obj["advertised_amount"])
        normalized["currency"] = _currency(obj["currency"])
        if type(obj["head_sha"]) is not str or not _SHA40_RE.fullmatch(obj["head_sha"]):
            _fail("PACKET_HEAD_SHA_INVALID")
        normalized["route_class"] = _route_class(obj["route_class"])
        normalized["route_key"] = _opaque_id(obj["route_key"], "route_key")
        if obj["disposition"] not in {
            "READY_TO_REQUEST_ASSESSMENT",
            "READY_TO_REQUEST_PAYMENT",
        }:
            _fail("PACKET_DISPOSITION_INVALID")
        if obj["acceptance_kind"] not in {"NONE", "SPONSOR_ACCEPTED", "AWARDED"}:
            _fail("PACKET_ACCEPTANCE_KIND_INVALID")
        if obj["acceptance_kind"] == "NONE" and obj["acceptance_evidence_sha256"] != "":
            _fail("PACKET_ACCEPTANCE_EVIDENCE_INVALID")
        if obj["acceptance_kind"] != "NONE" and obj["acceptance_evidence_sha256"] == "":
            _fail("PACKET_ACCEPTANCE_EVIDENCE_INVALID")
        expected_disposition = (
            "READY_TO_REQUEST_ASSESSMENT"
            if obj["acceptance_kind"] == "NONE"
            else "READY_TO_REQUEST_PAYMENT"
        )
        if obj["disposition"] != expected_disposition:
            _fail("PACKET_DISPOSITION_INVALID")
        core = {
            key: normalized[key]
            for key in (
                "packet_sha256", "pr_url", "head_sha", "disposition",
                "advertised_amount", "currency", "acceptance_kind",
                "acceptance_evidence_sha256", "payout_route_sha256",
                "route_class", "route_key",
            )
        }
        if _digest(core) != normalized["request_digest"]:
            _fail("REQUEST_DIGEST_MISMATCH")
    elif event_type in {"dispatched", "follow_up_recorded"}:
        _exact_keys(obj, _DISPATCH_KEYS, "DISPATCH_EVENT_SHAPE_MISMATCH")
        normalized = _make_dispatch_event(
            event_id=obj["event_id"], event_type=event_type,
            occurred_at=obj["occurred_at"], request_id=obj["request_id"],
            request_digest=obj["request_digest"], route_class=obj["route_class"],
            provider_receipt_key=obj["provider_receipt_key"],
            evidence_sha256=obj["evidence_sha256"], as_of=as_of,
        )
    elif event_type == "sponsor_event":
        _exact_keys(obj, _SPONSOR_KEYS, "SPONSOR_EVENT_SHAPE_MISMATCH")
        normalized = _make_sponsor_event(
            event_id=obj["event_id"], occurred_at=obj["occurred_at"],
            request_id=obj["request_id"], request_digest=obj["request_digest"],
            status=obj["status"], provider_event_key=obj["provider_event_key"],
            evidence_sha256=obj["evidence_sha256"], as_of=as_of,
        )
    elif event_type == "request_superseded":
        _exact_keys(obj, _SUPERSEDE_KEYS, "SUPERSEDE_EVENT_SHAPE_MISMATCH")
        normalized = _make_supersede_event(
            event_id=obj["event_id"], occurred_at=obj["occurred_at"],
            old_request_id=obj["old_request_id"],
            old_request_digest=obj["old_request_digest"],
            new_request_id=obj["new_request_id"],
            new_request_digest=obj["new_request_digest"],
            evidence_sha256=obj["evidence_sha256"], as_of=as_of,
        )
    else:
        _fail("EVENT_TYPE_INVALID")
    occurred_dt = _timestamp(normalized["occurred_at"], "occurred_at")[1]
    _ensure_not_future(occurred_dt, as_of)
    return normalized


def _work_key(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        candidate["pr_url"], candidate["head_sha"],
        candidate["advertised_amount"], candidate["currency"],
    )


def _latest_status(sponsor_events: list[dict[str, Any]]) -> Optional[str]:
    return sponsor_events[-1]["status"] if sponsor_events else None


def _terminal_for_follow_up(candidate: dict[str, Any], status: Optional[str]) -> bool:
    if status is None or status == "acknowledged":
        return False
    return True


def _replay(events: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    candidates: dict[str, dict[str, Any]] = {}
    event_ids: dict[str, str] = {}
    packet_ids: dict[str, str] = {}
    external_refs: dict[str, str] = {}
    dispatches: dict[str, list[dict[str, Any]]] = {}
    sponsor_events: dict[str, list[dict[str, Any]]] = {}
    superseded: set[str] = set()
    timeline: dict[str, datetime] = {}
    normalized_events: list[dict[str, Any]] = []

    for raw in events:
        event = _validate_stored_event(raw, as_of)
        event_digest = _digest(event)
        prior_digest = event_ids.get(event["event_id"])
        if prior_digest is not None:
            if prior_digest != event_digest:
                _fail("EVENT_ID_CONFLICT")
            _fail("EVENT_ID_DUPLICATE")
        event_ids[event["event_id"]] = event_digest
        event_dt = _timestamp(event["occurred_at"], "occurred_at")[1]

        if event["event_type"] == "request_registered":
            rid = event["request_id"]
            if rid in candidates:
                _fail("REQUEST_ID_DUPLICATE")
            packet_sha = event["packet_sha256"]
            if packet_sha in packet_ids:
                _fail("PACKET_ALREADY_REGISTERED")
            packet_ids[packet_sha] = rid
            candidates[rid] = event
            dispatches[rid] = []
            sponsor_events[rid] = []
            timeline[rid] = event_dt

        elif event["event_type"] in {"dispatched", "follow_up_recorded"}:
            rid = event["request_id"]
            candidate = candidates.get(rid)
            if candidate is None:
                _fail("UNKNOWN_REQUEST")
            if rid in superseded:
                _fail("SUPERSEDED_REQUEST_MUTATION")
            if event["request_digest"] != candidate["request_digest"]:
                _fail("REQUEST_DIGEST_MISMATCH")
            if event["route_class"] != candidate["route_class"]:
                _fail("ROUTE_CLASS_MISMATCH")
            if event_dt < timeline[rid]:
                _fail("TIMESTAMP_INVERSION")
            ext = event["provider_receipt_key"]
            if ext in external_refs:
                _fail("EXTERNAL_REFERENCE_REUSE")
            external_refs[ext] = event["event_id"]

            primary_exists = any(item["event_type"] == "dispatched" for item in dispatches[rid])
            if event["event_type"] == "dispatched":
                if primary_exists:
                    _fail("MULTIPLE_PRIMARY_DISPATCH")
                for other_id, other in candidates.items():
                    if other_id == rid or other_id in superseded:
                        continue
                    if _work_key(other) != _work_key(candidate):
                        continue
                    if any(item["event_type"] == "dispatched" for item in dispatches.get(other_id, [])):
                        _fail("PARALLEL_ACTIVE_REQUEST")
            else:
                if not primary_exists:
                    _fail("FOLLOW_UP_BEFORE_DISPATCH")
                status = _latest_status(sponsor_events[rid])
                if _terminal_for_follow_up(candidate, status):
                    _fail("FOLLOW_UP_AFTER_SPONSOR_DECISION")
            dispatches[rid].append(event)
            timeline[rid] = event_dt

        elif event["event_type"] == "sponsor_event":
            rid = event["request_id"]
            candidate = candidates.get(rid)
            if candidate is None:
                _fail("UNKNOWN_REQUEST")
            if rid in superseded:
                _fail("SUPERSEDED_REQUEST_MUTATION")
            if event["request_digest"] != candidate["request_digest"]:
                _fail("REQUEST_DIGEST_MISMATCH")
            if not any(item["event_type"] == "dispatched" for item in dispatches[rid]):
                _fail("SPONSOR_EVENT_BEFORE_DISPATCH")
            if event_dt < timeline[rid]:
                _fail("TIMESTAMP_INVERSION")
            ext = event["provider_event_key"]
            if ext in external_refs:
                _fail("EXTERNAL_REFERENCE_REUSE")
            external_refs[ext] = event["event_id"]

            previous = _latest_status(sponsor_events[rid])
            status = event["status"]
            if previous not in {None, "acknowledged"}:
                _fail("SPONSOR_DECISION_ALREADY_RECORDED")
            if previous == "acknowledged" and status == "acknowledged":
                _fail("DUPLICATE_ACKNOWLEDGEMENT")
            if candidate["disposition"] == "READY_TO_REQUEST_ASSESSMENT":
                if status not in {"acknowledged", "needs_changes", "accepted", "awarded", "rejected"}:
                    _fail("ASSESSMENT_STATUS_AUTHORITY_VIOLATION")
            else:
                if status not in {"acknowledged", "needs_changes", "payment_pending", "paid_evidence_ready", "rejected"}:
                    _fail("PAYMENT_STATUS_AUTHORITY_VIOLATION")
            sponsor_events[rid].append(event)
            timeline[rid] = event_dt

        else:
            old_id = event["old_request_id"]
            new_id = event["new_request_id"]
            old = candidates.get(old_id)
            new = candidates.get(new_id)
            if old is None or new is None:
                _fail("SUPERSEDE_UNKNOWN_REQUEST")
            if old_id == new_id:
                _fail("SELF_SUPERSESSION")
            if old_id in superseded or new_id in superseded:
                _fail("SUPERSESSION_CHAIN_INVALID")
            if event["old_request_digest"] != old["request_digest"] or event["new_request_digest"] != new["request_digest"]:
                _fail("SUPERSESSION_DIGEST_MISMATCH")
            if _work_key(old) != _work_key(new):
                _fail("SUPERSESSION_WORK_MISMATCH")
            if old["payout_route_sha256"] != new["payout_route_sha256"]:
                _fail("SUPERSESSION_PAYOUT_ROUTE_MISMATCH")
            if old["disposition"] != "READY_TO_REQUEST_ASSESSMENT":
                _fail("SUPERSESSION_OLD_NOT_ASSESSMENT")
            if new["disposition"] != "READY_TO_REQUEST_PAYMENT":
                _fail("SUPERSESSION_NEW_NOT_PAYMENT")
            if dispatches[new_id] or sponsor_events[new_id]:
                _fail("SUPERSESSION_NEW_ALREADY_ACTIVE")
            latest = sponsor_events[old_id][-1] if sponsor_events[old_id] else None
            if latest is None or latest["status"] not in {"accepted", "awarded"}:
                _fail("SUPERSESSION_WITHOUT_ACCEPTANCE")
            expected_kind = "SPONSOR_ACCEPTED" if latest["status"] == "accepted" else "AWARDED"
            if new["acceptance_kind"] != expected_kind:
                _fail("SUPERSESSION_ACCEPTANCE_KIND_MISMATCH")
            if new["acceptance_evidence_sha256"] != latest["evidence_sha256"]:
                _fail("SUPERSESSION_ACCEPTANCE_EVIDENCE_MISMATCH")
            if event["evidence_sha256"] != latest["evidence_sha256"]:
                _fail("SUPERSESSION_EVENT_EVIDENCE_MISMATCH")
            if event_dt < max(timeline[old_id], timeline[new_id]):
                _fail("TIMESTAMP_INVERSION")
            superseded.add(old_id)
            timeline[old_id] = event_dt
            timeline[new_id] = event_dt

        normalized_events.append(event)

    return {
        "events": normalized_events,
        "candidates": candidates,
        "dispatches": dispatches,
        "sponsor_events": sponsor_events,
        "superseded": superseded,
        "timeline": timeline,
    }


def _ledger_digest(events: list[dict[str, Any]]) -> str:
    return _digest({"schema": SCHEMA, "events": events})


def empty_ledger() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    return {"schema": SCHEMA, "events": events, "ledger_sha256": _ledger_digest(events)}


def _normalize_ledger(ledger: Any) -> dict[str, Any]:
    obj = _exact_keys(ledger, {"schema", "events", "ledger_sha256"}, "LEDGER_SHAPE_MISMATCH")
    if obj["schema"] != SCHEMA:
        _fail("LEDGER_SCHEMA_MISMATCH")
    if type(obj["events"]) is not list or len(obj["events"]) > _MAX_EVENTS:
        _fail("LEDGER_EVENTS_INVALID")
    _sha256(obj["ledger_sha256"], "ledger_sha256")
    if _ledger_digest(obj["events"]) != obj["ledger_sha256"]:
        _fail("LEDGER_DIGEST_MISMATCH")
    return deepcopy(obj)


def verify_ledger(ledger: Any, *, as_of: Any) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    normalized = _normalize_ledger(ledger)
    replay = _replay(normalized["events"], as_of_dt)
    if replay["events"] != normalized["events"]:
        _fail("EVENT_NORMALIZATION_DRIFT")
    return {
        "valid": True,
        "event_count": len(replay["events"]),
        "request_count": len(replay["candidates"]),
        "ledger_sha256": normalized["ledger_sha256"],
    }


def _append_event(ledger: Any, event: dict[str, Any], *, as_of: Any) -> tuple[dict[str, Any], bool]:
    _, as_of_dt = _assert_as_of(as_of)
    normalized = _normalize_ledger(ledger)
    replay = _replay(normalized["events"], as_of_dt)
    digest = _digest(event)
    for existing in replay["events"]:
        if existing["event_id"] == event["event_id"]:
            if _digest(existing) == digest:
                return normalized, True
            _fail("EVENT_ID_CONFLICT")
    next_events = replay["events"] + [event]
    _replay(next_events, as_of_dt)
    return {
        "schema": SCHEMA,
        "events": next_events,
        "ledger_sha256": _ledger_digest(next_events),
    }, False


def register_request(
    ledger: Any, *, event_id: Any, occurred_at: Any, request_id: Any,
    packet: Any, route_class: Any, route_key: Any, as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_register_event(
        event_id=event_id, occurred_at=occurred_at, request_id=request_id,
        packet=packet, route_class=route_class, route_key=route_key, as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {
        "ledger": next_ledger,
        "replayed": replayed,
        "request_digest": event["request_digest"],
        "packet_sha256": event["packet_sha256"],
    }


def record_dispatch(
    ledger: Any, *, event_id: Any, occurred_at: Any, request_id: Any,
    request_digest: Any, route_class: Any, provider_receipt_key: Any,
    evidence_sha256: Any, as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_dispatch_event(
        event_id=event_id, event_type="dispatched", occurred_at=occurred_at,
        request_id=request_id, request_digest=request_digest, route_class=route_class,
        provider_receipt_key=provider_receipt_key, evidence_sha256=evidence_sha256,
        as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def record_follow_up(
    ledger: Any, *, event_id: Any, occurred_at: Any, request_id: Any,
    request_digest: Any, route_class: Any, provider_receipt_key: Any,
    evidence_sha256: Any, as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_dispatch_event(
        event_id=event_id, event_type="follow_up_recorded", occurred_at=occurred_at,
        request_id=request_id, request_digest=request_digest, route_class=route_class,
        provider_receipt_key=provider_receipt_key, evidence_sha256=evidence_sha256,
        as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def record_sponsor_event(
    ledger: Any, *, event_id: Any, occurred_at: Any, request_id: Any,
    request_digest: Any, status: Any, provider_event_key: Any,
    evidence_sha256: Any, as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_sponsor_event(
        event_id=event_id, occurred_at=occurred_at, request_id=request_id,
        request_digest=request_digest, status=status, provider_event_key=provider_event_key,
        evidence_sha256=evidence_sha256, as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def supersede_with_payment_request(
    ledger: Any, *, event_id: Any, occurred_at: Any, old_request_id: Any,
    old_request_digest: Any, new_request_id: Any, new_request_digest: Any,
    evidence_sha256: Any, as_of: Any,
) -> dict[str, Any]:
    _, as_of_dt = _assert_as_of(as_of)
    event = _make_supersede_event(
        event_id=event_id, occurred_at=occurred_at,
        old_request_id=old_request_id, old_request_digest=old_request_digest,
        new_request_id=new_request_id, new_request_digest=new_request_digest,
        evidence_sha256=evidence_sha256, as_of=as_of_dt,
    )
    next_ledger, replayed = _append_event(ledger, event, as_of=as_of)
    return {"ledger": next_ledger, "replayed": replayed}


def evaluate_request(
    ledger: Any, request_id: Any, *, as_of: Any, follow_up_after_seconds: Any,
) -> dict[str, Any]:
    as_of_text, as_of_dt = _assert_as_of(as_of)
    if (
        isinstance(follow_up_after_seconds, bool)
        or type(follow_up_after_seconds) is not int
        or not 3600 <= follow_up_after_seconds <= 90 * 24 * 3600
    ):
        _fail("FOLLOW_UP_POLICY_INVALID")
    normalized = _normalize_ledger(ledger)
    replay = _replay(normalized["events"], as_of_dt)
    rid = _request_id(request_id)
    candidate = replay["candidates"].get(rid)
    if candidate is None:
        _fail("UNKNOWN_REQUEST")

    outbound = replay["dispatches"][rid]
    sponsor = replay["sponsor_events"][rid]
    latest_status = _latest_status(sponsor)
    primary_sent = any(item["event_type"] == "dispatched" for item in outbound)

    if rid in replay["superseded"]:
        send_disposition, lifecycle = "HOLD", "SUPERSEDED"
    elif not primary_sent:
        same_work_active = [
            other_id
            for other_id, other in replay["candidates"].items()
            if other_id != rid
            and other_id not in replay["superseded"]
            and _work_key(other) == _work_key(candidate)
            and any(item["event_type"] == "dispatched" for item in replay["dispatches"].get(other_id, []))
        ]
        if same_work_active:
            send_disposition, lifecycle = "HOLD", "LINK_GENERATION_REQUIRED"
        else:
            send_disposition, lifecycle = "READY_TO_SEND", "READY_TO_SEND"
    else:
        send_disposition = "ALREADY_SENT"
        if candidate["disposition"] == "READY_TO_REQUEST_ASSESSMENT":
            if latest_status in {"accepted", "awarded"}:
                lifecycle = "PAYMENT_REQUEST_REQUIRED"
            elif latest_status == "needs_changes":
                send_disposition, lifecycle = "HOLD", "NEEDS_CHANGES"
            elif latest_status == "rejected":
                send_disposition, lifecycle = "HOLD", "CLOSED_REJECTED"
            else:
                baseline = replay["timeline"][rid]
                lifecycle = (
                    "FOLLOW_UP_DUE"
                    if (as_of_dt - baseline).total_seconds() >= follow_up_after_seconds
                    else "AWAITING_SPONSOR"
                )
        else:
            if latest_status == "paid_evidence_ready":
                lifecycle = "SETTLEMENT_EVIDENCE_READY"
            elif latest_status == "payment_pending":
                lifecycle = "PAYMENT_PENDING"
            elif latest_status == "needs_changes":
                send_disposition, lifecycle = "HOLD", "NEEDS_CHANGES"
            elif latest_status == "rejected":
                send_disposition, lifecycle = "HOLD", "CLOSED_REJECTED"
            else:
                baseline = replay["timeline"][rid]
                lifecycle = (
                    "FOLLOW_UP_DUE"
                    if (as_of_dt - baseline).total_seconds() >= follow_up_after_seconds
                    else "AWAITING_SPONSOR"
                )

    baseline_dt = replay["timeline"][rid]
    baseline_text = baseline_dt.isoformat(
        timespec="microseconds" if baseline_dt.microsecond else "seconds"
    ).replace("+00:00", "Z")
    core = {
        "schema": SCHEMA,
        "as_of": as_of_text,
        "request_id": rid,
        "request_digest": candidate["request_digest"],
        "packet_sha256": candidate["packet_sha256"],
        "pr_url": candidate["pr_url"],
        "head_sha": candidate["head_sha"],
        "disposition": candidate["disposition"],
        "advertised_amount": candidate["advertised_amount"],
        "currency": candidate["currency"],
        "acceptance_kind": candidate["acceptance_kind"],
        "payout_route_sha256": candidate["payout_route_sha256"],
        "send_disposition": send_disposition,
        "lifecycle_state": lifecycle,
        "follow_up_after_seconds": follow_up_after_seconds,
        "event_baseline": baseline_text,
        "outbound_event_count": len(outbound),
        "sponsor_event_count": len(sponsor),
        "dispatch_evidence_recorded": primary_sent,
        "ledger_sha256": normalized["ledger_sha256"],
        "authority": {
            "external_send_performed_by_module": False,
            "provider_receipt_authenticated_by_module": False,
            "sponsor_response_inferred": False,
            "acceptance_inferred_from_silence": False,
            "advertised_amount_is_debt": False,
            "payment_inferred": False,
            "cash_recognized": False,
            "revenue_recognized": False,
            "wallet_mutated": False,
        },
    }
    return {**core, "receipt_sha256": _digest(core)}


def strict_json_loads(text: str) -> Any:
    """Load bounded JSON while rejecting duplicate keys and non-finite constants."""
    if type(text) is not str or len(text.encode("utf-8")) > _MAX_JSON_BYTES:
        _fail("JSON_TEXT_INVALID")

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                _fail("DUPLICATE_JSON_KEY", key)
            out[key] = value
        return out

    try:
        return json.loads(
            text,
            object_pairs_hook=hook,
            parse_constant=lambda value: (_ for _ in ()).throw(
                CollectionCustodyError("NONFINITE_JSON_CONSTANT", value)
            ),
        )
    except CollectionCustodyError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError) as exc:
        raise CollectionCustodyError("INVALID_JSON") from exc
