# SPDX-License-Identifier: MIT
"""Collision-safe pre-send guard for sponsor collection requests.

``collection_request`` proves what may be asked. ``outbound_dedupe`` proves that
all trusted send-capable providers have fresh target-bound evidence with no
matching prior send. ``outbound_singlewriter`` elects exactly one cloud writer
and supplies the durable local lease state machine. This module composes those
three authorities into one fail-closed decision for collection outreach.

The guard is intentionally offline. It never sends a message, mutates a mail
provider, submits a claim, charges a payment route, changes a wallet, or treats
an advertised reward as debt/cash/revenue. A CLEAR receipt is only pre-send
authority for the elected writer, and the caller must still acquire the local
single-writer lease before touching an external provider.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Optional, Sequence

from concierge.collection_request import (
    CollectionRequestInputError,
    verify_collection_request,
)
from concierge.outbound_dedupe import (
    OutboundDedupeInputError,
    evaluate_outbound_dedupe,
)
from concierge.outbound_singlewriter import (
    OutboundGuardError,
    OutboundSingleWriter,
    SharedClaimBlocked,
    SharedClaimError,
    evaluate_shared_claim,
    normalize_identity,
)

SCHEMA = "collection-dispatch-guard/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_BYTES = 2 * 1024 * 1024


class CollectionDispatchGuardError(ValueError):
    """Malformed, inconsistent, or authority-expanding guard input."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> None:
    raise CollectionDispatchGuardError(code, message)


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
        raise CollectionDispatchGuardError("NONCANONICAL_JSON") from exc
    if len(raw) > _MAX_JSON_BYTES:
        _fail("JSON_TOO_LARGE")
    return raw


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        _fail("SHA256_INVALID", field)
    return value


def _text(value: Any, field: str, *, limit: int = 512) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > limit
        or any(ord(ch) < 32 for ch in value)
    ):
        _fail("TEXT_INVALID", field)
    return value


def _packet_binding(packet: Any) -> dict[str, Any]:
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

    packet_sha256 = _sha256(packet.get("receipt_sha256"), "packet.receipt_sha256")
    work = source.get("work")
    if type(work) is not dict:
        _fail("PACKET_WORK_INVALID")
    repo = _text(work.get("repo"), "work.repo", limit=200)
    sponsor_name = _text(source.get("sponsor_name"), "sponsor_name", limit=160)
    sponsor_key = unicodedata.normalize("NFKC", sponsor_name).casefold()
    if not sponsor_key:
        _fail("SPONSOR_IDENTITY_INVALID")
    sponsor_id = _digest({"sponsor": sponsor_key})[:16]
    pr = work.get("pr")
    if type(pr) is not int or pr <= 0:
        _fail("PACKET_PR_INVALID")

    disposition = packet.get("disposition")
    if disposition == "READY_TO_REQUEST_ASSESSMENT":
        phase = "assessment"
    elif disposition == "READY_TO_REQUEST_PAYMENT":
        phase = "payment"
    else:  # verify_collection_request should already make this unreachable.
        _fail("PACKET_DISPOSITION_INVALID")

    # GitHub repository identity is case-insensitive. Sponsor identity is
    # normalized with NFKC + casefold so presentation casing/width cannot mint
    # another writer, while distinct sponsors remain independently collectible.
    # Crucially, identity does NOT include recipient, outbound provider, packet
    # receipt, or payout route. Regenerating a packet or choosing another
    # provider therefore cannot mint a second writer for the same sponsor/work
    # collection operation.
    work_identity = {"repo": repo.casefold(), "pr": pr}
    work_id = _digest(work_identity)[:24]
    offer_key = f"collection:{phase}:{work_id}:{sponsor_id}"
    identity = normalize_identity(
        provider="collection-federation",
        destination=f"work:{work_id}:sponsor:{sponsor_id}",
        thread=f"collection:{phase}",
        operation="dispatch",
    )
    return {
        "packet_sha256": packet_sha256,
        "repo": repo,
        "pr": pr,
        "phase": phase,
        "work_id": work_id,
        "sponsor_id": sponsor_id,
        "offer_key": offer_key,
        "singlewriter_identity": identity.canonical,
        "operation_key": identity.key,
    }


def derive_collection_dispatch_identity(packet: Any) -> dict[str, Any]:
    """Return the stable sponsor/work/phase identity used by every collision fence."""
    binding = _packet_binding(packet)
    return {
        "schema": SCHEMA,
        "packet_sha256": binding["packet_sha256"],
        "repo": binding["repo"],
        "pr": binding["pr"],
        "phase": binding["phase"],
        "work_id": binding["work_id"],
        "sponsor_id": binding["sponsor_id"],
        "offer_key": binding["offer_key"],
        "operation_key": binding["operation_key"],
        "singlewriter_identity": dict(binding["singlewriter_identity"]),
    }


def _decision_receipt(core: dict[str, Any]) -> dict[str, Any]:
    result = dict(core)
    result["receipt_sha256"] = _digest(core)
    return result


def _shared_hold(
    *,
    core: dict[str, Any],
    reason_code: str,
    disposition: str = "HOLD",
    decision: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    core = dict(core)
    core["disposition"] = disposition
    core["dispatch"] = False
    core["reason_codes"] = [reason_code]
    core["shared_claim"] = decision or {
        "checked": False,
        "authorized": False,
        "disposition": reason_code,
    }
    return _decision_receipt(core)


def authorize_collection_dispatch(
    packet: Any,
    *,
    recipient: str,
    provider_queries: list[dict[str, Any]],
    required_providers: Iterable[str],
    owner: str,
    claim_event_id: str,
    shared_events: Iterable[Mapping[str, Any]],
    snapshot_complete: bool,
    now: datetime | None = None,
    max_query_age_seconds: int = 300,
) -> dict[str, Any]:
    """Compose packet, provider-truth, and shared-writer evidence.

    A result authorizes no external side effect by itself. ``dispatch=True``
    means only that the exact packet is valid, the all-provider duplicate census
    is fresh and clear, and this owner won the complete shared claim election.
    The caller must next acquire the local lease with
    :func:`acquire_guarded_local_lease` before sending.
    """
    binding = _packet_binding(packet)
    owner_text = _text(owner, "owner", limit=256)
    claim_id = _text(claim_event_id, "claim_event_id", limit=256)
    if type(provider_queries) is not list:
        _fail("PROVIDER_QUERIES_LIST_REQUIRED")

    try:
        dedupe = evaluate_outbound_dedupe(
            {
                "recipient": recipient,
                "offer_key": binding["offer_key"],
                "provider_queries": provider_queries,
            },
            required_providers=required_providers,
            now=now,
            max_query_age_seconds=max_query_age_seconds,
        )
    except OutboundDedupeInputError as exc:
        raise CollectionDispatchGuardError("PROVIDER_EVIDENCE_INVALID", str(exc)) from exc

    core: dict[str, Any] = {
        "schema": SCHEMA,
        "packet_sha256": binding["packet_sha256"],
        "work": {
            "repo": binding["repo"],
            "pr": binding["pr"],
            "phase": binding["phase"],
            "work_id": binding["work_id"],
            "sponsor_id": binding["sponsor_id"],
        },
        "offer_key": binding["offer_key"],
        "operation_key": binding["operation_key"],
        "singlewriter": {
            "identity": dict(binding["singlewriter_identity"]),
            "operation_key": binding["operation_key"],
            "local_lease_required": True,
        },
        "provider_gate": dedupe,
        "owner": owner_text,
        "claim_event_id": claim_id,
        "authority": {
            "effect": "pre_send_collection_dispatch_only",
            "collection_packet_verified": True,
            "provider_census_required": True,
            "shared_claim_required": True,
            "local_singlewriter_lease_required": True,
            "recipient_independent_sponsor_work_phase_identity": True,
            "distinct_sponsors_remain_collectible": True,
            "external_send_performed": False,
            "provider_mutation_performed": False,
            "claim_submission_performed": False,
            "payment_mutation_performed": False,
            "wallet_mutation_performed": False,
            "advertised_reward_is_debt": False,
            "advertised_reward_is_cash": False,
            "advertised_reward_is_revenue": False,
        },
    }

    if dedupe.get("dispatch") is not True:
        provider_disposition = dedupe.get("disposition")
        if provider_disposition == "DNR":
            return _shared_hold(
                core=core,
                reason_code="PROVIDER_GATE:DNR",
                disposition="DNR",
            )
        return _shared_hold(core=core, reason_code="PROVIDER_GATE:HOLD")

    try:
        shared = evaluate_shared_claim(
            operation_key=binding["operation_key"],
            owner=owner_text,
            claim_event_id=claim_id,
            events=shared_events,
            snapshot_complete=snapshot_complete,
        )
    except SharedClaimBlocked as exc:
        core["shared_claim_error"] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
        return _shared_hold(core=core, reason_code="SHARED_CLAIM:BLOCKED")
    except SharedClaimError as exc:
        core["shared_claim_error"] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
        return _shared_hold(core=core, reason_code="SHARED_CLAIM:INVALID")

    shared_dict = shared.as_dict()
    core["shared_claim"] = shared_dict
    if not shared.authorized:
        if shared.disposition == "ALREADY_SENT":
            core["disposition"] = "DNR"
            core["dispatch"] = False
            core["reason_codes"] = ["SHARED_CLAIM:ALREADY_SENT"]
        else:
            core["disposition"] = "HOLD"
            core["dispatch"] = False
            core["reason_codes"] = [f"SHARED_CLAIM:{shared.disposition}"]
        return _decision_receipt(core)

    core["disposition"] = "CLEAR"
    core["dispatch"] = True
    core["reason_codes"] = ["PROVIDER_CLEAR", "SHARED_CLAIM_AUTHORIZED"]
    return _decision_receipt(core)


def _verify_guard_receipt(result: Any) -> dict[str, Any]:
    if type(result) is not dict:
        _fail("GUARD_RECEIPT_OBJECT_REQUIRED")
    digest = _sha256(result.get("receipt_sha256"), "guard.receipt_sha256")
    unsigned = {key: value for key, value in result.items() if key != "receipt_sha256"}
    if _digest(unsigned) != digest:
        _fail("GUARD_RECEIPT_TAMPERED")
    if result.get("schema") != SCHEMA:
        _fail("GUARD_SCHEMA_INVALID")
    return result


def acquire_guarded_local_lease(
    result: Any,
    *,
    root: str | Path,
    owner: str,
    ttl_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Acquire the durable local lease only from an intact CLEAR guard receipt."""
    guard = _verify_guard_receipt(result)
    owner_text = _text(owner, "owner", limit=256)
    if guard.get("disposition") != "CLEAR" or guard.get("dispatch") is not True:
        _fail("GUARD_NOT_CLEAR")
    if guard.get("owner") != owner_text:
        _fail("GUARD_OWNER_MISMATCH")
    shared = guard.get("shared_claim")
    if type(shared) is not dict or shared.get("authorized") is not True:
        _fail("SHARED_CLAIM_NOT_AUTHORIZED")
    if shared.get("winner_owner") != owner_text:
        _fail("SHARED_CLAIM_OWNER_MISMATCH")

    singlewriter = guard.get("singlewriter")
    if type(singlewriter) is not dict or singlewriter.get("local_lease_required") is not True:
        _fail("SINGLEWRITER_PLAN_INVALID")
    identity_raw = singlewriter.get("identity")
    if type(identity_raw) is not dict:
        _fail("SINGLEWRITER_IDENTITY_INVALID")
    try:
        identity = normalize_identity(**identity_raw)
    except (OutboundGuardError, TypeError) as exc:
        raise CollectionDispatchGuardError("SINGLEWRITER_IDENTITY_INVALID") from exc
    if identity.key != guard.get("operation_key") or identity.key != singlewriter.get("operation_key"):
        _fail("SINGLEWRITER_OPERATION_KEY_MISMATCH")

    writer = OutboundSingleWriter(root)
    return writer.acquire(
        **identity.canonical,
        owner=owner_text,
        ttl_seconds=ttl_seconds,
        now=now,
    )


def _strict_json_loads(text: str) -> Any:
    if type(text) is not str:
        _fail("INPUT_JSON_TEXT_REQUIRED")

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                _fail("DUPLICATE_JSON_KEY", key)
            obj[key] = value
        return obj

    try:
        return json.loads(text, object_pairs_hook=pairs_hook)
    except CollectionDispatchGuardError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CollectionDispatchGuardError("INPUT_JSON_INVALID") from exc


def _load_json(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    value = _strict_json_loads(raw)
    if type(value) is not dict:
        _fail("INPUT_OBJECT_REQUIRED")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.collection_dispatch_guard",
        description="Authorize one collision-safe collection dispatch without sending it.",
    )
    parser.add_argument("input", help="JSON object containing packet/evidence/election fields")
    parser.add_argument(
        "--required-provider",
        action="append",
        dest="required_providers",
        required=True,
        help="trusted send-capable provider id; repeat for the complete inventory",
    )
    parser.add_argument("--max-query-age-seconds", type=int, default=300)
    args = parser.parse_args(argv)

    try:
        payload = _load_json(args.input)
        expected = {
            "packet",
            "recipient",
            "provider_queries",
            "owner",
            "claim_event_id",
            "shared_events",
            "snapshot_complete",
        }
        if set(payload) != expected:
            _fail("INPUT_FIELDS_INVALID")
        result = authorize_collection_dispatch(
            payload["packet"],
            recipient=payload["recipient"],
            provider_queries=payload["provider_queries"],
            required_providers=args.required_providers,
            owner=payload["owner"],
            claim_event_id=payload["claim_event_id"],
            shared_events=payload["shared_events"],
            snapshot_complete=payload["snapshot_complete"],
            max_query_age_seconds=args.max_query_age_seconds,
        )
    except (OSError, CollectionDispatchGuardError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, sort_keys=True))
    if result["dispatch"]:
        return 0
    return 3 if result["disposition"] == "DNR" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
