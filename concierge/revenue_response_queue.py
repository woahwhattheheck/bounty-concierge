# SPDX-License-Identifier: MIT
"""Host-attested, provider-grounded revenue response queue.

Authoritative queue states require both a host-signed provider-read batch and a
host-configured authorization binding for the exact provider/principal. Thread
membership alone is not commercial-engagement authority: later outbound and
inbound events advance or satisfy an engagement only when provider-normalized
``related_message_id`` links them to the current generation.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any


class RevenueResponseQueueError(ValueError):
    """Response-queue evidence cannot safely support a decision."""


_SCHEMA = "bounty-concierge.revenue-response-queue/v3"
_POLICY_SCHEMA = "bounty-concierge.revenue-response-policy/v1"
_BATCH_SCHEMA = "bounty-concierge.provider-thread-read-batch/v1"
_ATTESTATION_KEY_ENV = "BOUNTY_RESPONSE_QUEUE_ATTESTATION_KEY_B64"
_ATTESTATION_KEY_ID_ENV = "BOUNTY_RESPONSE_QUEUE_ATTESTATION_KEY_ID"
_AUTHORIZED_PROVIDER_ENV = "BOUNTY_RESPONSE_QUEUE_AUTHORIZED_PROVIDER"
_AUTHORIZED_PRINCIPAL_SHA256_ENV = "BOUNTY_RESPONSE_QUEUE_AUTHORIZED_PRINCIPAL_SHA256"
_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+=@-]{0,255}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
_ENGAGEMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTACT_POLICIES = frozenset({"follow_up_allowed", "wait_for_buyer_event"})
_MESSAGE_KINDS = frozenset({"outbound", "human_inbound", "automated_inbound", "bounce"})
_MANIFEST_KEYS = {
    "engagement_id", "provider_thread_id", "sent_message_id", "buyer_route",
    "authorized_reply_routes", "offer_key", "contact_policy", "follow_up_after_hours",
}
_POLICY_KEYS = {"schema", "max_snapshot_age_seconds", "auto_ack_grace_hours"}
_BATCH_KEYS = {
    "schema", "key_id", "provider", "authenticated_principal", "scope_sha256",
    "policy_sha256", "captured_at", "snapshots", "hmac_sha256",
}
_RECEIPT_KEYS = {
    "schema", "captured_at", "scope_sha256", "policy", "policy_sha256",
    "provider_authority", "authority", "summary", "items", "evidence_sha256",
    "host_attestation_hmac_sha256",
}
_PROVIDER_AUTHORITY_KEYS = {
    "provider_sha256", "authenticated_principal_sha256", "key_id",
    "batch_captured_at", "oldest_snapshot_fetched_at",
}
_SNAPSHOT_KEYS = {"thread_id", "fetched_at", "complete", "messages"}
_MESSAGE_KEYS = {
    "id", "kind", "occurred_at", "from_route", "to_routes", "related_message_id", "sequence",
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
_MAX_FUTURE_SKEW_SECONDS = 5


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RevenueResponseQueueError("value is not canonical JSON") from exc


def _detached_canonical_generation(value: Any) -> Any:
    """Return built-in JSON containers reconstructed from one retained serialization.

    HMAC verification and every later semantic read consume this generation, never
    caller-owned nested containers. If a caller mutates the original object while
    it is serialized, the HMAC can validate only the exact retained generation.
    """
    canonical = _canonical_json(value)
    try:
        detached = json.loads(canonical)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RevenueResponseQueueError("value cannot be detached as canonical JSON") from exc
    if _canonical_json(detached) != canonical:
        raise RevenueResponseQueueError("canonical JSON detachment is unstable")
    return detached


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_json(value: Any) -> str:
    return _hash_text(_canonical_json(value))


def _exact_dict(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise RevenueResponseQueueError(f"{context} must be an object")
    if set(value) != keys:
        raise RevenueResponseQueueError(
            f"{context} fields are not exact (missing={sorted(keys-set(value))}, extra={sorted(set(value)-keys)})"
        )
    return value


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or type(value) is not int or not minimum <= value <= maximum:
        raise RevenueResponseQueueError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _compact_id(value: Any, *, field: str) -> str:
    if type(value) is not str or value != value.strip() or not _ID_RE.fullmatch(value):
        raise RevenueResponseQueueError(f"{field} must be a compact opaque id")
    return value


def _canonical_email(value: Any, *, field: str) -> str:
    if type(value) is not str or value != value.strip() or not _EMAIL_RE.fullmatch(value):
        raise RevenueResponseQueueError(f"{field} must be a canonical addr-spec email")
    local, domain = value.rsplit("@", 1)
    if not local or not domain or local.startswith(".") or local.endswith(".") or ".." in local:
        raise RevenueResponseQueueError(f"{field} must be a valid addr-spec email")
    try:
        domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise RevenueResponseQueueError(f"{field} domain is not valid IDNA") from exc
    if not domain or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        raise RevenueResponseQueueError(f"{field} domain is invalid")
    return f"{local.casefold()}@{domain}"


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


def _current_utc() -> datetime:
    return datetime.now(timezone.utc)


def _validate_policy(policy: Any) -> dict[str, Any]:
    obj = _exact_dict(policy, _POLICY_KEYS, "policy")
    if obj["schema"] != _POLICY_SCHEMA:
        raise RevenueResponseQueueError("policy schema mismatch")
    return {
        "schema": _POLICY_SCHEMA,
        "max_snapshot_age_seconds": _bounded_int(
            obj["max_snapshot_age_seconds"], field="max_snapshot_age_seconds", minimum=30, maximum=3600
        ),
        "auto_ack_grace_hours": _bounded_int(
            obj["auto_ack_grace_hours"], field="auto_ack_grace_hours", minimum=1, maximum=24 * 7
        ),
    }


def _manifest_rows(manifest: Any) -> list[dict[str, Any]]:
    if type(manifest) is not list or not manifest or len(manifest) > _MAX_ENGAGEMENTS:
        raise RevenueResponseQueueError(f"manifest must be a nonempty list with at most {_MAX_ENGAGEMENTS} engagements")
    rows: list[dict[str, Any]] = []
    seen_engagements: set[str] = set()
    seen_threads: set[str] = set()
    seen_sent: set[str] = set()
    seen_buyer_offer: set[tuple[str, str]] = set()
    for index, raw in enumerate(manifest):
        row = _exact_dict(raw, _MANIFEST_KEYS, f"manifest[{index}]")
        engagement = row["engagement_id"]
        if type(engagement) is not str or engagement != engagement.strip() or not _ENGAGEMENT_RE.fullmatch(engagement):
            raise RevenueResponseQueueError("engagement_id must be a compact stable id")
        thread_id = _compact_id(row["provider_thread_id"], field="provider_thread_id")
        sent_id = _compact_id(row["sent_message_id"], field="sent_message_id")
        buyer = _canonical_email(row["buyer_route"], field="buyer_route")
        routes_raw = row["authorized_reply_routes"]
        if type(routes_raw) is not list or not routes_raw or len(routes_raw) > 16:
            raise RevenueResponseQueueError("authorized_reply_routes must be a nonempty bounded list")
        routes = tuple(sorted(_canonical_email(route, field="authorized_reply_routes[]") for route in routes_raw))
        if len(routes) != len(set(routes)):
            raise RevenueResponseQueueError("authorized_reply_routes must not contain duplicates")
        if buyer not in routes:
            raise RevenueResponseQueueError("authorized_reply_routes must include buyer_route")
        offer = row["offer_key"]
        if type(offer) is not str or offer != offer.strip() or not _KEY_RE.fullmatch(offer):
            raise RevenueResponseQueueError("offer_key is malformed")
        contact_policy = row["contact_policy"]
        if type(contact_policy) is not str or contact_policy not in _CONTACT_POLICIES:
            raise RevenueResponseQueueError(f"contact_policy must be one of {sorted(_CONTACT_POLICIES)}")
        follow_up = row["follow_up_after_hours"]
        if contact_policy == "follow_up_allowed":
            follow_up = _bounded_int(follow_up, field="follow_up_after_hours", minimum=1, maximum=24 * 30)
        elif follow_up is not None:
            raise RevenueResponseQueueError("follow_up_after_hours must be null for wait_for_buyer_event")
        if engagement in seen_engagements or thread_id in seen_threads or sent_id in seen_sent:
            raise RevenueResponseQueueError("engagement/thread/sent bindings must be unique")
        buyer_offer = (buyer, offer)
        if buyer_offer in seen_buyer_offer:
            raise RevenueResponseQueueError("buyer_route + offer_key must be unique across active engagements")
        seen_engagements.add(engagement); seen_threads.add(thread_id); seen_sent.add(sent_id); seen_buyer_offer.add(buyer_offer)
        normalized = {
            "engagement_id": engagement, "provider_thread_id": thread_id, "sent_message_id": sent_id,
            "buyer_route": buyer, "authorized_reply_routes": list(routes), "offer_key": offer,
            "contact_policy": contact_policy, "follow_up_after_hours": follow_up,
        }
        normalized["manifest_row_sha256"] = _sha_json(normalized)
        rows.append(normalized)
    return sorted(rows, key=lambda row: row["engagement_id"])


def _scope_material(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in row.items() if k != "manifest_row_sha256"} for row in rows]


def _host_attestation_context() -> tuple[str, bytes, str, str]:
    key_id = os.environ.get(_ATTESTATION_KEY_ID_ENV)
    encoded = os.environ.get(_ATTESTATION_KEY_ENV)
    provider = os.environ.get(_AUTHORIZED_PROVIDER_ENV)
    principal_sha = os.environ.get(_AUTHORIZED_PRINCIPAL_SHA256_ENV)
    if type(key_id) is not str or not _KEY_RE.fullmatch(key_id):
        raise RevenueResponseQueueError("host provider-attestation key id is unavailable")
    if type(encoded) is not str or not encoded:
        raise RevenueResponseQueueError("host provider-attestation key is unavailable")
    provider = _compact_id(provider, field="host authorized provider")
    if type(principal_sha) is not str or not _SHA_RE.fullmatch(principal_sha):
        raise RevenueResponseQueueError("host authorized principal digest is unavailable")
    try:
        key = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise RevenueResponseQueueError("host provider-attestation key is malformed") from exc
    if len(key) < 32:
        raise RevenueResponseQueueError("host provider-attestation key is too short")
    return key_id, key, provider, principal_sha


def _verified_batch(raw: Any, *, expected_scope_sha256: str, expected_policy_sha256: str) -> dict[str, Any]:
    _exact_dict(raw, _BATCH_KEYS, "provider_read_batch")
    batch = _exact_dict(_detached_canonical_generation(raw), _BATCH_KEYS, "provider_read_batch")
    if batch["schema"] != _BATCH_SCHEMA:
        raise RevenueResponseQueueError("provider_read_batch schema mismatch")
    signature = batch["hmac_sha256"]
    if type(signature) is not str or not _SHA_RE.fullmatch(signature):
        raise RevenueResponseQueueError("provider_read_batch attestation is malformed")
    key_id, key, authorized_provider, authorized_principal_sha = _host_attestation_context()
    if batch["key_id"] != key_id:
        raise RevenueResponseQueueError("provider_read_batch key id is not trusted")
    unsigned = dict(batch); unsigned.pop("hmac_sha256")
    expected_hmac = hmac.new(key, _canonical_json(unsigned).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_hmac):
        raise RevenueResponseQueueError("provider_read_batch attestation is invalid")
    if batch["scope_sha256"] != expected_scope_sha256:
        raise RevenueResponseQueueError("provider_read_batch scope binding mismatch")
    if batch["policy_sha256"] != expected_policy_sha256:
        raise RevenueResponseQueueError("provider_read_batch policy binding mismatch")
    provider = _compact_id(batch["provider"], field="provider")
    principal = _compact_id(batch["authenticated_principal"], field="authenticated_principal")
    if provider != authorized_provider:
        raise RevenueResponseQueueError("provider_read_batch provider is not authorized by host")
    if not hmac.compare_digest(_hash_text(principal), authorized_principal_sha):
        raise RevenueResponseQueueError("provider_read_batch principal is not authorized by host")
    captured_at = _timestamp(batch["captured_at"], field="provider_read_batch.captured_at")
    if type(batch["snapshots"]) is not list:
        raise RevenueResponseQueueError("provider_read_batch.snapshots must be a list")
    return {
        "provider": provider, "authenticated_principal": principal, "captured_at": captured_at,
        "snapshots": batch["snapshots"], "key_id": key_id, "_attestation_key": key,
    }


def _message(raw: Any, *, index: int, fetched_at: datetime) -> dict[str, Any]:
    item = _exact_dict(raw, _MESSAGE_KEYS, f"messages[{index}]")
    message_id = _compact_id(item["id"], field="message.id")
    kind = item["kind"]
    if type(kind) is not str or kind not in _MESSAGE_KINDS:
        raise RevenueResponseQueueError(f"message.kind must be one of {sorted(_MESSAGE_KINDS)}")
    occurred_at = _timestamp(item["occurred_at"], field="message.occurred_at")
    if occurred_at > fetched_at:
        raise RevenueResponseQueueError("message.occurred_at must not be after snapshot fetched_at")
    from_route = _canonical_email(item["from_route"], field="message.from_route")
    if type(item["to_routes"]) is not list or len(item["to_routes"]) > 32:
        raise RevenueResponseQueueError("message.to_routes must be a bounded list")
    to_routes = tuple(_canonical_email(route, field="message.to_routes[]") for route in item["to_routes"])
    if len(to_routes) != len(set(to_routes)):
        raise RevenueResponseQueueError("message.to_routes must not contain duplicates")
    related = item["related_message_id"]
    if related is not None:
        related = _compact_id(related, field="message.related_message_id")
    sequence = _bounded_int(item["sequence"], field="message.sequence", minimum=0, maximum=2**63 - 1)
    if kind == "outbound" and not to_routes:
        raise RevenueResponseQueueError("outbound message must have a recipient")
    if kind == "bounce" and related is None:
        raise RevenueResponseQueueError("bounce must bind related_message_id to the failed outbound")
    return {
        "id": message_id, "kind": kind, "occurred_at": occurred_at, "from_route": from_route,
        "to_routes": to_routes, "related_message_id": related, "sequence": sequence,
    }


def _snapshot(raw: Any, *, expected_thread_id: str, batch_captured_at: datetime, now: datetime, max_snapshot_age_seconds: int) -> dict[str, Any]:
    snap = _exact_dict(raw, _SNAPSHOT_KEYS, "provider snapshot")
    thread_id = _compact_id(snap["thread_id"], field="snapshot.thread_id")
    if thread_id != expected_thread_id:
        raise RevenueResponseQueueError("provider snapshot thread_id does not match binding")
    if type(snap["complete"]) is not bool or not snap["complete"]:
        raise RevenueResponseQueueError("provider snapshot must be explicitly complete")
    fetched_at = _timestamp(snap["fetched_at"], field="snapshot.fetched_at")
    if fetched_at > batch_captured_at:
        raise RevenueResponseQueueError("snapshot.fetched_at must not be after attested batch captured_at")
    if fetched_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        raise RevenueResponseQueueError("provider snapshot is future-dated")
    if (now - fetched_at).total_seconds() > max_snapshot_age_seconds:
        raise RevenueResponseQueueError("provider snapshot is stale")
    raw_messages = snap["messages"]
    if type(raw_messages) is not list or len(raw_messages) > _MAX_MESSAGES_PER_THREAD:
        raise RevenueResponseQueueError("snapshot.messages must be a bounded list")
    by_id: dict[str, dict[str, Any]] = {}; signatures: dict[str, str] = {}; sequences: dict[int, str] = {}
    for index, raw_message in enumerate(raw_messages):
        message = _message(raw_message, index=index, fetched_at=fetched_at)
        semantic = {
            "id": message["id"], "kind": message["kind"], "occurred_at": _utc_text(message["occurred_at"]),
            "from_route": message["from_route"], "to_routes": list(message["to_routes"]),
            "related_message_id": message["related_message_id"], "sequence": message["sequence"],
        }
        sig = _canonical_json(semantic)
        if message["id"] in signatures and signatures[message["id"]] != sig:
            raise RevenueResponseQueueError("conflicting provider rows reuse the same message id")
        prior = sequences.get(message["sequence"])
        if prior is not None and prior != message["id"]:
            raise RevenueResponseQueueError("distinct provider messages must not share sequence")
        signatures[message["id"]] = sig; sequences[message["sequence"]] = message["id"]; by_id[message["id"]] = message
    return {
        "thread_id": thread_id, "fetched_at": fetched_at,
        "messages": sorted(by_id.values(), key=lambda m: (m["occurred_at"], m["sequence"], m["id"])),
    }


def _safe_snapshot_digest(snapshot: dict[str, Any]) -> str:
    rows = [{
        "id_sha256": _hash_text(m["id"]), "kind": m["kind"], "occurred_at": _utc_text(m["occurred_at"]),
        "from_route_sha256": _hash_text(m["from_route"]),
        "to_route_sha256": sorted(_hash_text(route) for route in m["to_routes"]),
        "related_message_id_sha256": _hash_text(m["related_message_id"]) if m["related_message_id"] else None,
        "sequence": m["sequence"],
    } for m in snapshot["messages"]]
    return _sha_json({"thread_id_sha256": _hash_text(snapshot["thread_id"]), "fetched_at": _utc_text(snapshot["fetched_at"]), "messages": rows})


def _order_key(message: dict[str, Any]) -> tuple[datetime, int, str]:
    return message["occurred_at"], message["sequence"], message["id"]


def _classify(row: dict[str, Any], snapshot: dict[str, Any], *, now: datetime, auto_ack_grace_hours: int) -> dict[str, Any]:
    messages = snapshot["messages"]
    anchor_rows = [m for m in messages if m["id"] == row["sent_message_id"]]
    if len(anchor_rows) != 1:
        raise RevenueResponseQueueError("bound sent_message_id must appear exactly once in provider snapshot")
    anchor = anchor_rows[0]
    if anchor["kind"] != "outbound":
        raise RevenueResponseQueueError("bound sent_message_id must be provider-classified outbound")
    if row["buyer_route"] not in anchor["to_routes"]:
        raise RevenueResponseQueueError("bound outbound does not target buyer_route")
    authorized = set(row["authorized_reply_routes"])

    baseline = anchor
    branch_ambiguity: list[dict[str, Any]] = []
    while True:
        linked = [
            m for m in messages
            if m["kind"] == "outbound"
            and _order_key(m) > _order_key(baseline)
            and m["related_message_id"] == baseline["id"]
            and authorized.intersection(m["to_routes"])
        ]
        if not linked:
            break
        if len(linked) != 1:
            branch_ambiguity = linked
            break
        baseline = linked[0]

    baseline_id = baseline["id"]
    after = [m for m in messages if _order_key(m) > _order_key(baseline)]
    linked_human = [m for m in after if m["kind"] == "human_inbound" and m["related_message_id"] == baseline_id and m["from_route"] in authorized]
    linked_unbound_human = [m for m in after if m["kind"] == "human_inbound" and m["related_message_id"] == baseline_id and m["from_route"] not in authorized]
    bounces = [m for m in after if m["kind"] == "bounce" and m["related_message_id"] == baseline_id]
    linked_auto = [m for m in after if m["kind"] == "automated_inbound" and m["related_message_id"] == baseline_id and m["from_route"] in authorized]
    unlinked_relevant = [
        m for m in after
        if (
            (m["kind"] == "outbound" and authorized.intersection(m["to_routes"]) and m["related_message_id"] != baseline_id)
            or (m["kind"] in {"human_inbound", "automated_inbound"} and m["from_route"] in authorized and m["related_message_id"] != baseline_id)
        )
    ]
    ambiguous = branch_ambiguity + unlinked_relevant

    event: dict[str, Any] | None = None; reasons: list[str] = []; follow_up_due_at: datetime | None = None
    decisive: list[tuple[dict[str, Any], str, list[str]]] = []
    decisive.extend((m, "HUMAN_REPLY", ["AUTHORIZED_LINKED_HUMAN_REPLY"]) for m in linked_human)
    decisive.extend((m, "HUMAN_REVIEW_REQUIRED", ["LINKED_HUMAN_REPLY_FROM_UNBOUND_ROUTE"]) for m in linked_unbound_human)
    decisive.extend((m, "ROUTE_REPAIR", ["LATEST_OUTBOUND_BOUNCED"]) for m in bounces)
    decisive.extend((m, "HUMAN_REVIEW_REQUIRED", ["UNLINKED_OR_AMBIGUOUS_SAME_THREAD_ACTIVITY"]) for m in ambiguous)
    if decisive:
        event, state, reasons = max(decisive, key=lambda candidate: _order_key(candidate[0]))
    elif row["contact_policy"] == "wait_for_buyer_event":
        if linked_auto:
            event = max(linked_auto, key=_order_key); reasons.append("LINKED_AUTOMATED_ACK_PRESENT")
        state = "WAIT_BUYER_EVENT"; reasons.append("OPERATOR_POLICY_DNR_UNTIL_BUYER_EVENT")
    else:
        follow_up_due_at = baseline["occurred_at"] + timedelta(hours=row["follow_up_after_hours"])
        if linked_auto:
            event = max(linked_auto, key=_order_key)
            follow_up_due_at = max(follow_up_due_at, event["occurred_at"] + timedelta(hours=auto_ack_grace_hours))
            if now < follow_up_due_at:
                state = "WAIT_AUTO_ACK"; reasons = ["LINKED_AUTOMATED_ACK_GRACE_ACTIVE"]
            else:
                state = "FOLLOW_UP_DUE"; reasons = ["FOLLOW_UP_THRESHOLD_REACHED", "LINKED_AUTOMATED_ACK_GRACE_EXPIRED"]
        elif now >= follow_up_due_at:
            state = "FOLLOW_UP_DUE"; reasons = ["FOLLOW_UP_THRESHOLD_REACHED"]
        else:
            state = "WAIT"; reasons = ["FOLLOW_UP_THRESHOLD_NOT_REACHED"]

    latest = max(messages, key=_order_key, default=baseline)
    route_hashes = sorted(_hash_text(route) for route in row["authorized_reply_routes"])
    return {
        "engagement_id": row["engagement_id"], "offer_key": row["offer_key"], "state": state,
        "priority": _PRIORITY[state], "reason_codes": reasons, "contact_policy": row["contact_policy"],
        "manifest_row_sha256": row["manifest_row_sha256"], "buyer_route_sha256": _hash_text(row["buyer_route"]),
        "authorized_reply_routes_sha256": _sha_json(route_hashes), "latest_outbound_at": _utc_text(baseline["occurred_at"]),
        "latest_provider_event_at": _utc_text(latest["occurred_at"]),
        "follow_up_due_at": _utc_text(follow_up_due_at) if follow_up_due_at else None,
        "thread_id_sha256": _hash_text(row["provider_thread_id"]), "anchor_message_id_sha256": _hash_text(row["sent_message_id"]),
        "latest_outbound_message_id_sha256": _hash_text(baseline_id),
        "evidence_message_id_sha256": _hash_text(event["id"]) if event else None,
        "snapshot_evidence_sha256": _safe_snapshot_digest(snapshot),
    }


def compile_revenue_response_queue(manifest: Any, provider_read_batch: Any, *, policy: Any) -> dict[str, Any]:
    rows = _manifest_rows(manifest)
    normalized_policy = _validate_policy(policy)
    scope_sha256 = _sha_json(_scope_material(rows)); policy_sha256 = _sha_json(normalized_policy)
    batch = _verified_batch(provider_read_batch, expected_scope_sha256=scope_sha256, expected_policy_sha256=policy_sha256)
    current = _current_utc()
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise RevenueResponseQueueError("verifier clock must return an aware datetime")
    current = current.astimezone(timezone.utc)
    if batch["captured_at"] > current + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        raise RevenueResponseQueueError("provider_read_batch captured_at is future-dated")
    if (current - batch["captured_at"]).total_seconds() > normalized_policy["max_snapshot_age_seconds"]:
        raise RevenueResponseQueueError("provider_read_batch is stale")
    if len(batch["snapshots"]) != len(rows):
        raise RevenueResponseQueueError("provider_read_batch must contain exactly one snapshot per engagement")
    raw_by_thread: dict[str, Any] = {}
    for index, raw in enumerate(batch["snapshots"]):
        if type(raw) is not dict:
            raise RevenueResponseQueueError(f"snapshots[{index}] must be an object")
        thread_id = _compact_id(raw.get("thread_id"), field=f"snapshots[{index}].thread_id")
        if thread_id in raw_by_thread:
            raise RevenueResponseQueueError("provider_read_batch contains duplicate thread snapshots")
        raw_by_thread[thread_id] = raw
    if set(raw_by_thread) != {row["provider_thread_id"] for row in rows}:
        raise RevenueResponseQueueError("provider_read_batch thread set does not exactly match manifest")
    items = []
    normalized_snapshots: list[dict[str, Any]] = []
    for row in rows:
        snapshot = _snapshot(
            raw_by_thread[row["provider_thread_id"]], expected_thread_id=row["provider_thread_id"],
            batch_captured_at=batch["captured_at"], now=current,
            max_snapshot_age_seconds=normalized_policy["max_snapshot_age_seconds"],
        )
        normalized_snapshots.append(snapshot)
        items.append(_classify(row, snapshot, now=current, auto_ack_grace_hours=normalized_policy["auto_ack_grace_hours"]))
    items.sort(key=lambda item: (item["priority"], item["follow_up_due_at"] or "", item["latest_provider_event_at"], item["engagement_id"]))
    summary = {state: sum(item["state"] == state for item in items) for state in _PRIORITY}
    authority = {
        "provider_read_attested": True, "provider_identity_authorized": True, "scope_attested": True,
        "policy_attested": True, "receipt_host_attested": True, "send_message": False,
        "reply_to_buyer": False, "mutate_provider": False, "change_contact_policy": False,
        "recognize_revenue": False, "recognize_payment": False,
    }
    oldest_snapshot_fetched_at = min(snapshot["fetched_at"] for snapshot in normalized_snapshots)
    provider_authority = {
        "provider_sha256": _hash_text(batch["provider"]),
        "authenticated_principal_sha256": _hash_text(batch["authenticated_principal"]),
        "key_id": batch["key_id"], "batch_captured_at": _utc_text(batch["captured_at"]),
        "oldest_snapshot_fetched_at": _utc_text(oldest_snapshot_fetched_at),
    }
    semantic = {
        "schema": _SCHEMA, "captured_at": _utc_text(current), "scope_sha256": scope_sha256,
        "policy": normalized_policy, "policy_sha256": policy_sha256, "provider_authority": provider_authority,
        "authority": authority, "summary": summary, "items": items,
    }
    signed = {**semantic, "evidence_sha256": _sha_json(semantic)}
    receipt_hmac = hmac.new(batch["_attestation_key"], _canonical_json(signed).encode(), hashlib.sha256).hexdigest()
    return {**signed, "host_attestation_hmac_sha256": receipt_hmac}


def verify_revenue_response_queue_receipt(receipt: Any) -> bool:
    try:
        obj = _exact_dict(receipt, _RECEIPT_KEYS, "receipt")
        if obj["schema"] != _SCHEMA:
            return False
        signature = obj["host_attestation_hmac_sha256"]
        if type(signature) is not str or not _SHA_RE.fullmatch(signature):
            return False
        key_id, key, authorized_provider, authorized_principal_sha = _host_attestation_context()
        pa = _exact_dict(obj["provider_authority"], _PROVIDER_AUTHORITY_KEYS, "provider_authority")
        if pa["key_id"] != key_id:
            return False
        if pa["provider_sha256"] != _hash_text(authorized_provider):
            return False
        if pa["authenticated_principal_sha256"] != authorized_principal_sha:
            return False
        signed = dict(obj); signed.pop("host_attestation_hmac_sha256")
        expected_hmac = hmac.new(key, _canonical_json(signed).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_hmac):
            return False
        evidence = signed.get("evidence_sha256")
        if type(evidence) is not str or not _SHA_RE.fullmatch(evidence):
            return False
        semantic = dict(signed); semantic.pop("evidence_sha256")
        if not hmac.compare_digest(evidence, _sha_json(semantic)):
            return False
        normalized_policy = _validate_policy(obj["policy"])
        if _sha_json(normalized_policy) != obj["policy_sha256"]:
            return False
        captured = _timestamp(obj["captured_at"], field="receipt.captured_at")
        batch_captured = _timestamp(pa["batch_captured_at"], field="provider_authority.batch_captured_at")
        oldest_snapshot = _timestamp(pa["oldest_snapshot_fetched_at"], field="provider_authority.oldest_snapshot_fetched_at")
        if oldest_snapshot > batch_captured:
            return False
        if batch_captured > captured + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
            return False
        now = _current_utc()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return False
        now = now.astimezone(timezone.utc)
        for evidence_time in (captured, batch_captured, oldest_snapshot):
            if evidence_time > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
                return False
            if (now - evidence_time).total_seconds() > normalized_policy["max_snapshot_age_seconds"]:
                return False
        return True
    except (RevenueResponseQueueError, TypeError, ValueError):
        return False
