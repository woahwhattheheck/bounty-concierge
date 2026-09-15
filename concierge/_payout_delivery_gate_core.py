# SPDX-License-Identifier: MIT
"""Single-writer gate for one bounty payout-claim/follow-up provider mutation.

The gate is deliberately offline. It does not send email, comments, DMs, or
forms. It only compiles exact request + arbitration + complete prior-mutation
history into a bounded owner-control receipt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from urllib.parse import urlsplit

REQUEST_SCHEMA = "bounty-concierge-payout-delivery-request/v1"
ARBITRATION_SCHEMA = "bounty-concierge-payout-delivery-arbitration/v1"
HISTORY_SCHEMA = "bounty-concierge-payout-delivery-history/v1"
RECEIPT_SCHEMA = "bounty-concierge-payout-delivery-receipt/v1"
AUTHORITY_CEILING = "READY_FOR_ONE_PROVIDER_MUTATION"
HISTORY_MAX_AGE_SECONDS = 600
MAX_JSON_BYTES = 1_048_576
MAX_HISTORY_ITEMS = 1000

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TARGET_KINDS = frozenset({"GITHUB_COMMENT", "EMAIL", "DM", "FORM"})
_DECISIONS = frozenset({"SELECTED", "NOT_SELECTED", "REVOKED"})
_MUTATION_OUTCOMES = frozenset({"CONFIRMED_SENT", "AMBIGUOUS", "CONFIRMED_NOT_SENT"})
_TERMINAL_STATES = frozenset({"SETTLED_PAID", "SPONSOR_DNR", "OPPORTUNITY_CLOSED"})

_REQUEST_KEYS = frozenset({
    "schema", "request_id", "opportunity_ref", "target_kind",
    "target_route_sha256", "work_url", "work_sha", "reward_reference",
    "claimant_label", "source_authority_ref", "source_authority_sha256",
    "prepared_at", "message_sha256",
})
_ARBITRATION_KEYS = frozenset({
    "schema", "request_sha256", "target_route_sha256", "decision",
    "owner_seat", "arbiter", "arbitration_ref", "issued_at", "expires_at",
})
_HISTORY_KEYS = frozenset({
    "schema", "request_sha256", "target_route_sha256", "complete",
    "captured_at", "items", "terminal",
})
_MUTATION_KEYS = frozenset({
    "mutation_id", "request_sha256", "target_route_sha256", "opportunity_ref",
    "message_sha256", "owner_seat", "attempted_at", "outcome",
    "provider_receipt_sha256",
})
_TERMINAL_KEYS = frozenset({"state", "observed_at", "evidence_sha256"})


class PayoutDeliveryInputError(ValueError):
    """Malformed or unsupported local input."""


class PayoutDeliveryEvidenceError(RuntimeError):
    """Evidence is internally contradictory or transplanted."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PayoutDeliveryInputError("value is not canonical JSON") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise PayoutDeliveryInputError("canonical JSON value exceeds 1 MiB")
    return encoded


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_keys(value: Any, keys: frozenset[str], field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PayoutDeliveryInputError(f"{field} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise PayoutDeliveryInputError(
            f"{field} field set mismatch; missing={missing} extra={extra}"
        )
    return value


def _text(value: Any, field: str, *, max_len: int = 512) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise PayoutDeliveryInputError(f"{field} must be non-empty trimmed text")
    if len(value) > max_len or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise PayoutDeliveryInputError(f"{field} contains unsupported text")
    return value


def _safe_id(value: Any, field: str) -> str:
    value = _text(value, field, max_len=256)
    if not _SAFE_ID.fullmatch(value):
        raise PayoutDeliveryInputError(f"{field} must be a safe identifier")
    return value


def _role_label(value: Any, field: str) -> str:
    value = _text(value, field, max_len=128)
    lowered = value.casefold()
    forbidden = ("http://", "https://", "@", "api_key", "apikey", "password", "secret", "token=")
    if any(marker in lowered for marker in forbidden):
        raise PayoutDeliveryInputError(
            f"{field} must be an opaque role/seat label, not contact or secret material"
        )
    return value


def _hex64(value: Any, field: str) -> str:
    if type(value) is not str or not _HEX64.fullmatch(value):
        raise PayoutDeliveryInputError(f"{field} must be lowercase SHA-256")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if type(value) is not str or not _UTC.fullmatch(value):
        raise PayoutDeliveryInputError(f"{field} must be canonical UTC seconds")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PayoutDeliveryInputError(f"{field} must be a valid UTC timestamp") from exc
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _https_url(value: Any, field: str) -> str:
    value = _text(value, field, max_len=1024)
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise PayoutDeliveryInputError(f"{field} must be credential-free HTTPS")
    if parsed.fragment:
        raise PayoutDeliveryInputError(f"{field} must not contain a fragment")
    return value


def _normalize_request(raw: Any) -> dict[str, Any]:
    raw = _exact_keys(raw, _REQUEST_KEYS, "request")
    if raw["schema"] != REQUEST_SCHEMA:
        raise PayoutDeliveryInputError(f"request.schema must be {REQUEST_SCHEMA}")
    target_kind = raw["target_kind"]
    if target_kind not in _TARGET_KINDS:
        raise PayoutDeliveryInputError("request.target_kind is unsupported")
    work_sha = raw["work_sha"]
    if type(work_sha) is not str or not _HEX40.fullmatch(work_sha):
        raise PayoutDeliveryInputError("request.work_sha must be lowercase 40-hex")
    return {
        "schema": REQUEST_SCHEMA,
        "request_id": _safe_id(raw["request_id"], "request.request_id"),
        "opportunity_ref": _safe_id(raw["opportunity_ref"], "request.opportunity_ref"),
        "target_kind": target_kind,
        "target_route_sha256": _hex64(raw["target_route_sha256"], "request.target_route_sha256"),
        "work_url": _https_url(raw["work_url"], "request.work_url"),
        "work_sha": work_sha,
        "reward_reference": _text(raw["reward_reference"], "request.reward_reference", max_len=256),
        "claimant_label": _role_label(raw["claimant_label"], "request.claimant_label"),
        "source_authority_ref": _https_url(raw["source_authority_ref"], "request.source_authority_ref"),
        "source_authority_sha256": _hex64(raw["source_authority_sha256"], "request.source_authority_sha256"),
        "prepared_at": _iso(_timestamp(raw["prepared_at"], "request.prepared_at")),
        "message_sha256": _hex64(raw["message_sha256"], "request.message_sha256"),
    }


def request_sha256(request: Any) -> str:
    return sha256_json(_normalize_request(request))


def _normalize_arbitration(raw: Any, *, request_digest: str, route_digest: str) -> dict[str, Any]:
    raw = _exact_keys(raw, _ARBITRATION_KEYS, "arbitration")
    if raw["schema"] != ARBITRATION_SCHEMA:
        raise PayoutDeliveryInputError(f"arbitration.schema must be {ARBITRATION_SCHEMA}")
    if raw["request_sha256"] != request_digest:
        raise PayoutDeliveryEvidenceError("arbitration request digest does not match request")
    if raw["target_route_sha256"] != route_digest:
        raise PayoutDeliveryEvidenceError("arbitration route digest does not match request")
    decision = raw["decision"]
    if decision not in _DECISIONS:
        raise PayoutDeliveryInputError("arbitration.decision is unsupported")
    issued = _timestamp(raw["issued_at"], "arbitration.issued_at")
    expires = _timestamp(raw["expires_at"], "arbitration.expires_at")
    if expires <= issued:
        raise PayoutDeliveryInputError("arbitration expiry must be after issuance")
    return {
        "schema": ARBITRATION_SCHEMA,
        "request_sha256": request_digest,
        "target_route_sha256": route_digest,
        "decision": decision,
        "owner_seat": _role_label(raw["owner_seat"], "arbitration.owner_seat"),
        "arbiter": _role_label(raw["arbiter"], "arbitration.arbiter"),
        "arbitration_ref": _https_url(raw["arbitration_ref"], "arbitration.arbitration_ref"),
        "issued_at": _iso(issued),
        "expires_at": _iso(expires),
    }


def _normalize_mutation(raw: Any) -> dict[str, Any]:
    raw = _exact_keys(raw, _MUTATION_KEYS, "history mutation")
    outcome = raw["outcome"]
    if outcome not in _MUTATION_OUTCOMES:
        raise PayoutDeliveryInputError("history mutation.outcome is unsupported")
    return {
        "mutation_id": _safe_id(raw["mutation_id"], "history mutation.mutation_id"),
        "request_sha256": _hex64(raw["request_sha256"], "history mutation.request_sha256"),
        "target_route_sha256": _hex64(raw["target_route_sha256"], "history mutation.target_route_sha256"),
        "opportunity_ref": _safe_id(raw["opportunity_ref"], "history mutation.opportunity_ref"),
        "message_sha256": _hex64(raw["message_sha256"], "history mutation.message_sha256"),
        "owner_seat": _role_label(raw["owner_seat"], "history mutation.owner_seat"),
        "attempted_at": _iso(_timestamp(raw["attempted_at"], "history mutation.attempted_at")),
        "outcome": outcome,
        "provider_receipt_sha256": _hex64(
            raw["provider_receipt_sha256"], "history mutation.provider_receipt_sha256"
        ),
    }


def _normalize_terminal(raw: Any, *, captured_at: datetime) -> dict[str, Any] | None:
    if raw is None:
        return None
    raw = _exact_keys(raw, _TERMINAL_KEYS, "history.terminal")
    state = raw["state"]
    if state not in _TERMINAL_STATES:
        raise PayoutDeliveryInputError("history.terminal.state is unsupported")
    observed = _timestamp(raw["observed_at"], "history.terminal.observed_at")
    if observed > captured_at:
        raise PayoutDeliveryEvidenceError("terminal evidence postdates history capture")
    return {
        "state": state,
        "observed_at": _iso(observed),
        "evidence_sha256": _hex64(raw["evidence_sha256"], "history.terminal.evidence_sha256"),
    }


def _normalize_history(raw: Any, *, request_digest: str, route_digest: str) -> dict[str, Any]:
    raw = _exact_keys(raw, _HISTORY_KEYS, "history")
    if raw["schema"] != HISTORY_SCHEMA:
        raise PayoutDeliveryInputError(f"history.schema must be {HISTORY_SCHEMA}")
    if raw["request_sha256"] != request_digest:
        raise PayoutDeliveryEvidenceError("history request digest does not match request")
    if raw["target_route_sha256"] != route_digest:
        raise PayoutDeliveryEvidenceError("history route digest does not match request")
    if type(raw["complete"]) is not bool:
        raise PayoutDeliveryInputError("history.complete must be a boolean")
    captured = _timestamp(raw["captured_at"], "history.captured_at")
    items = raw["items"]
    if type(items) is not list or len(items) > MAX_HISTORY_ITEMS:
        raise PayoutDeliveryInputError("history.items must be a bounded list")
    normalized = [_normalize_mutation(item) for item in items]
    ids = [row["mutation_id"] for row in normalized]
    if len(set(ids)) != len(ids):
        raise PayoutDeliveryEvidenceError("history repeats a mutation_id")
    fingerprints = [sha256_json(row) for row in normalized]
    if len(set(fingerprints)) != len(fingerprints):
        raise PayoutDeliveryEvidenceError("history contains duplicate indistinguishable mutations")
    normalized.sort(key=lambda row: (row["attempted_at"], row["mutation_id"]))
    for row in normalized:
        if row["target_route_sha256"] != route_digest:
            raise PayoutDeliveryEvidenceError(
                "route-scoped history contains a mutation from another provider route"
            )
        if _timestamp(row["attempted_at"], "history mutation.attempted_at") > captured:
            raise PayoutDeliveryEvidenceError("history mutation postdates history capture")
    return {
        "schema": HISTORY_SCHEMA,
        "request_sha256": request_digest,
        "target_route_sha256": route_digest,
        "complete": raw["complete"],
        "captured_at": _iso(captured),
        "items": normalized,
        "terminal": _normalize_terminal(raw["terminal"], captured_at=captured),
    }


def compile_payout_delivery(
    request: Any,
    arbitration: Any,
    history: Any,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Compile one deterministic single-writer provider-mutation decision."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise PayoutDeliveryInputError("as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    req = _normalize_request(request)
    prepared = _timestamp(req["prepared_at"], "request.prepared_at")
    if prepared > as_of:
        raise PayoutDeliveryEvidenceError("request was prepared in the future")
    req_digest = sha256_json(req)
    arb = _normalize_arbitration(
        arbitration,
        request_digest=req_digest,
        route_digest=req["target_route_sha256"],
    )
    issued = _timestamp(arb["issued_at"], "arbitration.issued_at")
    expires = _timestamp(arb["expires_at"], "arbitration.expires_at")
    if issued < prepared:
        raise PayoutDeliveryEvidenceError("arbitration predates the request it authorizes")
    hist = _normalize_history(
        history,
        request_digest=req_digest,
        route_digest=req["target_route_sha256"],
    )
    captured = _timestamp(hist["captured_at"], "history.captured_at")
    if captured > as_of:
        raise PayoutDeliveryEvidenceError("history capture is in the future")

    relevant = [
        row for row in hist["items"]
        if row["request_sha256"] == req_digest
        or row["opportunity_ref"] == req["opportunity_ref"]
    ]
    blocking = [
        row for row in relevant
        if row["outcome"] in {"CONFIRMED_SENT", "AMBIGUOUS"}
    ]

    state: str
    reason: str
    one_mutation_authorized = False
    if hist["terminal"] is not None:
        state = "SETTLED_NO_SEND"
        reason = f"terminal_{hist['terminal']['state'].lower()}"
    elif blocking:
        state = "DUPLICATE_SEND_BLOCKED"
        reason = "equivalent_prior_provider_mutation_exists"
    elif not hist["complete"]:
        state = "HISTORY_INCOMPLETE_HOLD"
        reason = "complete_prior_mutation_history_required"
    elif arb["decision"] == "REVOKED":
        state = "ARBITRATION_REVOKED"
        reason = "arbiter_revoked_single_writer_selection"
    elif arb["decision"] == "NOT_SELECTED":
        state = "ARBITRATION_REQUIRED"
        reason = "this_owner_was_not_selected"
    elif as_of < issued:
        state = "LEASE_NOT_YET_ACTIVE"
        reason = "arbitration_selection_not_yet_active"
    elif as_of >= expires:
        state = "LEASE_EXPIRED"
        reason = "single_writer_selection_expired"
    elif captured < issued:
        state = "HISTORY_PREDATES_LEASE_HOLD"
        reason = "prior_send_history_must_be_captured_after_selection"
    elif (as_of - captured).total_seconds() > HISTORY_MAX_AGE_SECONDS:
        state = "HISTORY_STALE_HOLD"
        reason = "prior_send_history_is_too_old_for_safe_mutation"
    else:
        state = AUTHORITY_CEILING
        reason = "selected_owner_with_fresh_complete_history_and_no_equivalent_prior_send"
        one_mutation_authorized = True

    body = {
        "schema": RECEIPT_SCHEMA,
        "as_of": _iso(as_of),
        "request_sha256": req_digest,
        "arbitration_sha256": sha256_json(arb),
        "history_sha256": sha256_json(hist),
        "request_id": req["request_id"],
        "opportunity_ref": req["opportunity_ref"],
        "target_kind": req["target_kind"],
        "target_route_sha256": req["target_route_sha256"],
        "message_sha256": req["message_sha256"],
        "work_url": req["work_url"],
        "work_sha": req["work_sha"],
        "reward_reference": req["reward_reference"],
        "claimant_label": req["claimant_label"],
        "owner_seat": arb["owner_seat"],
        "arbiter": arb["arbiter"],
        "arbitration_ref": arb["arbitration_ref"],
        "lease_issued_at": arb["issued_at"],
        "lease_expires_at": arb["expires_at"],
        "history_captured_at": hist["captured_at"],
        "prior_relevant_mutation_count": len(relevant),
        "blocking_prior_mutation_count": len(blocking),
        "state": state,
        "reason": reason,
        "one_provider_mutation_authorized": one_mutation_authorized,
        "external_action_performed": False,
        "sponsor_receipt_inferred": False,
        "sponsor_acceptance_inferred": False,
        "merge_inferred": False,
        "settlement_inferred": False,
        "payment_inferred": False,
        "cash_inferred": False,
        "revenue_inferred": False,
        "normalized_request": req,
        "normalized_arbitration": arb,
        "normalized_history": hist,
    }
    body["receipt_sha256"] = sha256_json(body)
    return body


def verify_payout_delivery_receipt(
    request: Any,
    arbitration: Any,
    history: Any,
    receipt: Any,
) -> dict[str, Any]:
    if type(receipt) is not dict:
        raise PayoutDeliveryInputError("receipt must be an object")
    as_of = _timestamp(receipt.get("as_of"), "receipt.as_of")
    expected = compile_payout_delivery(request, arbitration, history, as_of=as_of)
    if canonical_json_bytes(expected) != canonical_json_bytes(receipt):
        raise PayoutDeliveryEvidenceError("receipt differs from deterministic recompilation")
    return json.loads(canonical_json_bytes(expected))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PayoutDeliveryInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PayoutDeliveryInputError(f"cannot open regular input: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PayoutDeliveryInputError("input must be a regular file")
        if info.st_size > MAX_JSON_BYTES:
            raise PayoutDeliveryInputError("input JSON exceeds 1 MiB")
        chunks: list[bytes] = []
        remaining = MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_JSON_BYTES:
            raise PayoutDeliveryInputError("input JSON exceeds 1 MiB")
    finally:
        os.close(fd)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayoutDeliveryInputError("input JSON must be UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise PayoutDeliveryInputError("input is not valid JSON") from exc


def _write_new(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PayoutDeliveryInputError(f"refusing to overwrite or follow output path: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PayoutDeliveryInputError("output must be a regular file")
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise PayoutDeliveryInputError("short output write")
            view = view[written:]
        os.fsync(fd)
    except Exception:
        try:
            os.close(fd)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    else:
        os.close(fd)


def _emit(receipt: dict[str, Any], output: Path | None) -> None:
    payload = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    if output is None:
        sys.stdout.buffer.write(payload)
    else:
        _write_new(output, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.payout_delivery_gate",
        description=(
            "Compile/verify an offline single-writer payout follow-up gate. "
            "This command never performs a provider mutation."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    compile_p = sub.add_parser("compile")
    compile_p.add_argument("request", type=Path)
    compile_p.add_argument("arbitration", type=Path)
    compile_p.add_argument("history", type=Path)
    compile_p.add_argument("--output", type=Path)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("request", type=Path)
    verify_p.add_argument("arbitration", type=Path)
    verify_p.add_argument("history", type=Path)
    verify_p.add_argument("receipt", type=Path)
    verify_p.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        request = _read_json(args.request)
        arbitration = _read_json(args.arbitration)
        history = _read_json(args.history)
        if args.command == "compile":
            receipt = compile_payout_delivery(
                request, arbitration, history, as_of=datetime.now(timezone.utc)
            )
        else:
            receipt = verify_payout_delivery_receipt(
                request, arbitration, history, _read_json(args.receipt)
            )
        _emit(receipt, args.output)
    except (PayoutDeliveryInputError, PayoutDeliveryEvidenceError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
