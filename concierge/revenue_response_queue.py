# SPDX-License-Identifier: MIT
"""Provider-grounded revenue response queue.

This module turns an operator-owned outbound engagement manifest plus freshly
reacquired provider thread snapshots into a deterministic, body-free action
queue. It never sends mail, mutates a provider, contacts a buyer, or recognizes
revenue.

The provider callback is a trusted host capability. Production callers must
implement ``fetch_authenticated_thread`` in the credential-owning host and
reacquire the exact provider thread directly. Caller-built webhook/message
objects are not an authority substitute.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Callable


class RevenueResponseQueueError(ValueError):
    """Raised when response-queue evidence cannot safely support a decision."""


_SCHEMA = "bounty-concierge.revenue-response-queue/v1"
_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+=@-]{0,255}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
_ENGAGEMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_CONTACT_POLICIES = frozenset({"follow_up_allowed", "wait_for_buyer_event"})
_MESSAGE_KINDS = frozenset(
    {"outbound", "human_inbound", "automated_inbound", "bounce"}
)
_MANIFEST_KEYS = {
    "engagement_id",
    "provider_thread_id",
    "sent_message_id",
    "buyer_route",
    "authorized_reply_routes",
    "offer_key",
    "contact_policy",
    "follow_up_after_hours",
}
_SNAPSHOT_KEYS = {"thread_id", "fetched_at", "complete", "messages"}
_MESSAGE_KEYS = {
    "id",
    "kind",
    "occurred_at",
    "from_route",
    "to_routes",
    "related_message_id",
    "sequence",
}
_PRIORITY = {
    "HUMAN_REPLY": 0,
    "HUMAN_REVIEW_REQUIRED": 1,
    "ROUTE_REPAIR": 2,
    "FOLLOW_UP_DUE": 3,
    "WAIT_AUTO_ACK": 4,
    "WAIT_BUYER_EVENT": 5,
    "WAIT": 6,
}
_MAX_ENGAGEMENTS = 500
_MAX_MESSAGES_PER_THREAD = 2000


def _exact_dict(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise RevenueResponseQueueError(f"{context} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise RevenueResponseQueueError(
            f"{context} fields are not exact (missing={missing}, extra={extra})"
        )
    return value


def _compact_id(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise RevenueResponseQueueError(f"{field} must be a string")
    if value != value.strip() or not _ID_RE.fullmatch(value):
        raise RevenueResponseQueueError(f"{field} must be a compact opaque id")
    return value


def _engagement_id(value: Any) -> str:
    if type(value) is not str or value != value.strip() or not _ENGAGEMENT_RE.fullmatch(value):
        raise RevenueResponseQueueError("engagement_id must be a compact stable id")
    return value


def _offer_key(value: Any) -> str:
    if type(value) is not str or value != value.strip() or not _KEY_RE.fullmatch(value):
        raise RevenueResponseQueueError(
            "offer_key must be lowercase and use letters, digits, . _ : / -"
        )
    return value


def _canonical_email(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise RevenueResponseQueueError(f"{field} must be an email string")
    address = value.strip()
    if address != value or not address or not _EMAIL_RE.fullmatch(address):
        raise RevenueResponseQueueError(f"{field} must be a canonical addr-spec email")
    local, domain = address.rsplit("@", 1)
    if not local or not domain or local.startswith(".") or local.endswith(".") or ".." in local:
        raise RevenueResponseQueueError(f"{field} must be a valid addr-spec email")
    try:
        domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise RevenueResponseQueueError(f"{field} domain is not valid IDNA") from exc
    if not domain or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        raise RevenueResponseQueueError(f"{field} domain is invalid")
    return f"{local.casefold()}@{domain}"


def _current_utc() -> datetime:
    """Return verifier-owned current UTC time.

    Kept as a tiny private seam so tests can patch it without making production
    freshness or SLA decisions caller-selectable.
    """
    return datetime.now(timezone.utc)


def _timestamp(value: Any, *, field: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        raise RevenueResponseQueueError(f"{field} must be an ISO-8601 timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RevenueResponseQueueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RevenueResponseQueueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise RevenueResponseQueueError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise RevenueResponseQueueError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_routes(value: Any, *, field: str) -> tuple[str, ...]:
    if type(value) is not list or not value or len(value) > 16:
        raise RevenueResponseQueueError(
            f"{field} must be a nonempty list with at most 16 routes"
        )
    routes = tuple(_canonical_email(item, field=f"{field}[]") for item in value)
    if len(routes) != len(set(routes)):
        raise RevenueResponseQueueError(f"{field} must not contain duplicates")
    return tuple(sorted(routes))


def _manifest_rows(manifest: Any) -> list[dict[str, Any]]:
    if type(manifest) is not list or not manifest or len(manifest) > _MAX_ENGAGEMENTS:
        raise RevenueResponseQueueError(
            f"manifest must be a nonempty list with at most {_MAX_ENGAGEMENTS} engagements"
        )
    rows: list[dict[str, Any]] = []
    seen_engagements: set[str] = set()
    seen_threads: set[str] = set()
    seen_sent: set[str] = set()
    seen_buyer_offer: set[tuple[str, str]] = set()
    for index, raw in enumerate(manifest):
        row = _exact_dict(raw, _MANIFEST_KEYS, f"manifest[{index}]")
        engagement = _engagement_id(row["engagement_id"])
        thread_id = _compact_id(row["provider_thread_id"], field="provider_thread_id")
        sent_id = _compact_id(row["sent_message_id"], field="sent_message_id")
        buyer = _canonical_email(row["buyer_route"], field="buyer_route")
        routes = _canonical_routes(
            row["authorized_reply_routes"], field="authorized_reply_routes"
        )
        if buyer not in routes:
            raise RevenueResponseQueueError(
                "authorized_reply_routes must include buyer_route"
            )
        offer = _offer_key(row["offer_key"])
        policy = row["contact_policy"]
        if type(policy) is not str or policy not in _CONTACT_POLICIES:
            raise RevenueResponseQueueError(
                f"contact_policy must be one of {sorted(_CONTACT_POLICIES)}"
            )
        follow_up = row["follow_up_after_hours"]
        if policy == "follow_up_allowed":
            follow_up = _bounded_int(
                follow_up,
                field="follow_up_after_hours",
                minimum=1,
                maximum=24 * 30,
            )
        elif follow_up is not None:
            raise RevenueResponseQueueError(
                "follow_up_after_hours must be null for wait_for_buyer_event"
            )
        buyer_offer = (buyer, offer)
        if engagement in seen_engagements:
            raise RevenueResponseQueueError("engagement_id values must be unique")
        if thread_id in seen_threads:
            raise RevenueResponseQueueError("provider_thread_id values must be unique")
        if sent_id in seen_sent:
            raise RevenueResponseQueueError("sent_message_id values must be unique")
        if buyer_offer in seen_buyer_offer:
            raise RevenueResponseQueueError(
                "buyer_route + offer_key must be unique across active engagements"
            )
        seen_engagements.add(engagement)
        seen_threads.add(thread_id)
        seen_sent.add(sent_id)
        seen_buyer_offer.add(buyer_offer)
        rows.append(
            {
                "engagement_id": engagement,
                "provider_thread_id": thread_id,
                "sent_message_id": sent_id,
                "buyer_route": buyer,
                "authorized_reply_routes": routes,
                "offer_key": offer,
                "contact_policy": policy,
                "follow_up_after_hours": follow_up,
            }
        )
    return rows


def _message(
    raw: Any,
    *,
    index: int,
    fetched_at: datetime,
) -> dict[str, Any]:
    item = _exact_dict(raw, _MESSAGE_KEYS, f"messages[{index}]")
    message_id = _compact_id(item["id"], field="message.id")
    kind = item["kind"]
    if type(kind) is not str or kind not in _MESSAGE_KINDS:
        raise RevenueResponseQueueError(
            f"message.kind must be one of {sorted(_MESSAGE_KINDS)}"
        )
    occurred_at = _timestamp(item["occurred_at"], field="message.occurred_at")
    if occurred_at > fetched_at:
        raise RevenueResponseQueueError(
            "message.occurred_at must not be after snapshot fetched_at"
        )
    from_route = _canonical_email(item["from_route"], field="message.from_route")
    if type(item["to_routes"]) is not list or len(item["to_routes"]) > 32:
        raise RevenueResponseQueueError(
            "message.to_routes must be a list with at most 32 routes"
        )
    to_routes = tuple(
        _canonical_email(route, field="message.to_routes[]")
        for route in item["to_routes"]
    )
    if len(to_routes) != len(set(to_routes)):
        raise RevenueResponseQueueError("message.to_routes must not contain duplicates")
    related = item["related_message_id"]
    if related is not None:
        related = _compact_id(related, field="message.related_message_id")
    sequence = _bounded_int(
        item["sequence"],
        field="message.sequence",
        minimum=0,
        maximum=2**63 - 1,
    )
    if kind == "outbound" and not to_routes:
        raise RevenueResponseQueueError("outbound message must have a recipient")
    if kind == "bounce" and related is None:
        raise RevenueResponseQueueError(
            "bounce must bind related_message_id to the failed outbound"
        )
    return {
        "id": message_id,
        "kind": kind,
        "occurred_at": occurred_at,
        "from_route": from_route,
        "to_routes": to_routes,
        "related_message_id": related,
        "sequence": sequence,
    }


def _snapshot(
    raw: Any,
    *,
    expected_thread_id: str,
    now: datetime,
    max_snapshot_age_seconds: int,
) -> dict[str, Any]:
    snapshot = _exact_dict(raw, _SNAPSHOT_KEYS, "provider snapshot")
    thread_id = _compact_id(snapshot["thread_id"], field="snapshot.thread_id")
    if thread_id != expected_thread_id:
        raise RevenueResponseQueueError("provider snapshot thread_id does not match binding")
    if type(snapshot["complete"]) is not bool or not snapshot["complete"]:
        raise RevenueResponseQueueError("provider snapshot must be explicitly complete")
    fetched_at = _timestamp(snapshot["fetched_at"], field="snapshot.fetched_at")
    if fetched_at > now:
        raise RevenueResponseQueueError("snapshot.fetched_at must not be in the future")
    age = (now - fetched_at).total_seconds()
    if age > max_snapshot_age_seconds:
        raise RevenueResponseQueueError("provider snapshot is stale")
    raw_messages = snapshot["messages"]
    if type(raw_messages) is not list or len(raw_messages) > _MAX_MESSAGES_PER_THREAD:
        raise RevenueResponseQueueError(
            f"snapshot.messages must be a list with at most {_MAX_MESSAGES_PER_THREAD} messages"
        )
    messages_by_id: dict[str, dict[str, Any]] = {}
    signatures: dict[str, str] = {}
    sequences: dict[int, str] = {}
    for index, raw_message in enumerate(raw_messages):
        message = _message(raw_message, index=index, fetched_at=fetched_at)
        semantic = {
            "id": message["id"],
            "kind": message["kind"],
            "occurred_at": _utc_text(message["occurred_at"]),
            "from_route": message["from_route"],
            "to_routes": list(message["to_routes"]),
            "related_message_id": message["related_message_id"],
            "sequence": message["sequence"],
        }
        signature = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
        prior = signatures.get(message["id"])
        if prior is not None and prior != signature:
            raise RevenueResponseQueueError(
                "conflicting provider rows reuse the same message id"
            )
        prior_sequence_id = sequences.get(message["sequence"])
        if prior_sequence_id is not None and prior_sequence_id != message["id"]:
            raise RevenueResponseQueueError(
                "distinct provider messages must not share sequence"
            )
        sequences[message["sequence"]] = message["id"]
        signatures[message["id"]] = signature
        messages_by_id[message["id"]] = message
    messages = sorted(
        messages_by_id.values(),
        key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
    )
    return {
        "thread_id": thread_id,
        "fetched_at": fetched_at,
        "messages": messages,
    }


def _safe_snapshot_digest(snapshot: dict[str, Any]) -> str:
    rows = []
    for item in snapshot["messages"]:
        rows.append(
            {
                "id_sha256": _hash(item["id"]),
                "kind": item["kind"],
                "occurred_at": _utc_text(item["occurred_at"]),
                "from_route_sha256": _hash(item["from_route"]),
                "to_route_sha256": sorted(_hash(route) for route in item["to_routes"]),
                "related_message_id_sha256": (
                    _hash(item["related_message_id"])
                    if item["related_message_id"] is not None
                    else None
                ),
                "sequence": item["sequence"],
            }
        )
    material = {
        "thread_id_sha256": _hash(snapshot["thread_id"]),
        "fetched_at": _utc_text(snapshot["fetched_at"]),
        "messages": rows,
    }
    return _hash(json.dumps(material, sort_keys=True, separators=(",", ":")))


def _classify(
    row: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    now: datetime,
    auto_ack_grace_hours: int,
) -> dict[str, Any]:
    messages = snapshot["messages"]
    anchor = [m for m in messages if m["id"] == row["sent_message_id"]]
    if len(anchor) != 1:
        raise RevenueResponseQueueError(
            "bound sent_message_id must appear exactly once in provider snapshot"
        )
    anchor_message = anchor[0]
    if anchor_message["kind"] != "outbound":
        raise RevenueResponseQueueError(
            "bound sent_message_id must be provider-classified outbound"
        )
    if row["buyer_route"] not in anchor_message["to_routes"]:
        raise RevenueResponseQueueError(
            "bound outbound does not target buyer_route"
        )

    authorized = set(row["authorized_reply_routes"])
    qualifying_outbounds = [
        message
        for message in messages
        if message["kind"] == "outbound"
        and message["occurred_at"] >= anchor_message["occurred_at"]
        and authorized.intersection(message["to_routes"])
    ]
    baseline = max(
        qualifying_outbounds,
        key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
    )
    baseline_id = baseline["id"]
    baseline_order = (baseline["occurred_at"], baseline["sequence"], baseline_id)
    after_baseline = [
        message
        for message in messages
        if (message["occurred_at"], message["sequence"], message["id"])
        > baseline_order
    ]

    exact_human = [
        message
        for message in after_baseline
        if message["kind"] == "human_inbound"
        and message["from_route"] in authorized
    ]
    unbound_human = [
        message
        for message in after_baseline
        if message["kind"] == "human_inbound"
        and message["from_route"] not in authorized
    ]
    bounces = [
        message
        for message in after_baseline
        if message["kind"] == "bounce"
        and message["related_message_id"] == baseline_id
    ]
    auto_acks = [
        message
        for message in after_baseline
        if message["kind"] == "automated_inbound"
        and message["from_route"] in authorized
    ]

    event: dict[str, Any] | None = None
    reasons: list[str] = []
    follow_up_due_at: datetime | None = None

    if exact_human:
        event = max(
            exact_human,
            key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
        )
        state = "HUMAN_REPLY"
        reasons = ["AUTHORIZED_HUMAN_REPLY_AFTER_LATEST_OUTBOUND"]
    elif unbound_human:
        event = max(
            unbound_human,
            key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
        )
        state = "HUMAN_REVIEW_REQUIRED"
        reasons = ["HUMAN_REPLY_FROM_UNBOUND_ROUTE"]
    elif bounces:
        event = max(
            bounces,
            key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
        )
        state = "ROUTE_REPAIR"
        reasons = ["LATEST_OUTBOUND_BOUNCED"]
    elif row["contact_policy"] == "wait_for_buyer_event":
        if auto_acks:
            event = max(
                auto_acks,
                key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
            )
            reasons.append("AUTOMATED_ACK_PRESENT")
        state = "WAIT_BUYER_EVENT"
        reasons.append("OPERATOR_POLICY_DNR_UNTIL_BUYER_EVENT")
    else:
        follow_up_due_at = baseline["occurred_at"] + timedelta(
            hours=row["follow_up_after_hours"]
        )
        if auto_acks:
            event = max(
                auto_acks,
                key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
            )
            auto_due = event["occurred_at"] + timedelta(hours=auto_ack_grace_hours)
            if auto_due > follow_up_due_at:
                follow_up_due_at = auto_due
            if now < follow_up_due_at:
                state = "WAIT_AUTO_ACK"
                reasons = ["AUTOMATED_ACK_GRACE_ACTIVE"]
            else:
                state = "FOLLOW_UP_DUE"
                reasons = ["FOLLOW_UP_THRESHOLD_REACHED", "AUTOMATED_ACK_GRACE_EXPIRED"]
        elif now >= follow_up_due_at:
            state = "FOLLOW_UP_DUE"
            reasons = ["FOLLOW_UP_THRESHOLD_REACHED"]
        else:
            state = "WAIT"
            reasons = ["FOLLOW_UP_THRESHOLD_NOT_REACHED"]

    latest_provider_event = max(
        messages,
        key=lambda item: (item["occurred_at"], item["sequence"], item["id"]),
        default=baseline,
    )
    return {
        "engagement_id": row["engagement_id"],
        "offer_key": row["offer_key"],
        "state": state,
        "priority": _PRIORITY[state],
        "reason_codes": reasons,
        "contact_policy": row["contact_policy"],
        "latest_outbound_at": _utc_text(baseline["occurred_at"]),
        "latest_provider_event_at": _utc_text(latest_provider_event["occurred_at"]),
        "follow_up_due_at": _utc_text(follow_up_due_at) if follow_up_due_at else None,
        "thread_id_sha256": _hash(row["provider_thread_id"]),
        "anchor_message_id_sha256": _hash(row["sent_message_id"]),
        "latest_outbound_message_id_sha256": _hash(baseline_id),
        "evidence_message_id_sha256": _hash(event["id"]) if event else None,
        "snapshot_evidence_sha256": _safe_snapshot_digest(snapshot),
    }


def compile_revenue_response_queue(
    manifest: Any,
    fetch_authenticated_thread: Callable[[str], Any],
    *,
    max_snapshot_age_seconds: int = 300,
    auto_ack_grace_hours: int = 24,
) -> dict[str, Any]:
    """Compile a fail-closed, provider-grounded response action queue.

    ``manifest`` is operator-owned scope. ``fetch_authenticated_thread`` is a
    trusted host capability and is invoked exactly once for each bound provider
    thread id. The callback must reacquire provider truth directly; passing a
    lambda over caller-supplied/webhook data defeats the authority model and is
    explicitly outside the production contract.
    """
    if not callable(fetch_authenticated_thread):
        raise RevenueResponseQueueError("fetch_authenticated_thread must be callable")
    current = _current_utc()
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise RevenueResponseQueueError("verifier clock must return an aware datetime")
    current = current.astimezone(timezone.utc)
    max_age = _bounded_int(
        max_snapshot_age_seconds,
        field="max_snapshot_age_seconds",
        minimum=30,
        maximum=3600,
    )
    ack_grace = _bounded_int(
        auto_ack_grace_hours,
        field="auto_ack_grace_hours",
        minimum=1,
        maximum=24 * 7,
    )
    rows = _manifest_rows(manifest)
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            raw_snapshot = fetch_authenticated_thread(row["provider_thread_id"])
        except Exception as exc:
            raise RevenueResponseQueueError(
                f"provider reacquisition failed for engagement {row['engagement_id']}"
            ) from exc
        snapshot = _snapshot(
            raw_snapshot,
            expected_thread_id=row["provider_thread_id"],
            now=current,
            max_snapshot_age_seconds=max_age,
        )
        items.append(
            _classify(row, snapshot, now=current, auto_ack_grace_hours=ack_grace)
        )

    items.sort(
        key=lambda item: (
            item["priority"],
            item["follow_up_due_at"] or "",
            item["latest_provider_event_at"],
            item["engagement_id"],
        )
    )
    summary = {
        state: sum(item["state"] == state for item in items)
        for state in _PRIORITY
    }
    authority = {
        "send_message": False,
        "reply_to_buyer": False,
        "mutate_provider": False,
        "change_contact_policy": False,
        "recognize_revenue": False,
        "recognize_payment": False,
    }
    semantic = {
        "schema": _SCHEMA,
        "captured_at": _utc_text(current),
        "max_snapshot_age_seconds": max_age,
        "auto_ack_grace_hours": ack_grace,
        "authority": authority,
        "summary": summary,
        "items": items,
    }
    evidence_sha256 = _hash(
        json.dumps(semantic, sort_keys=True, separators=(",", ":"))
    )
    return {**semantic, "evidence_sha256": evidence_sha256}
