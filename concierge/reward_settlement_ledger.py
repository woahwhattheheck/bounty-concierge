# SPDX-License-Identifier: MIT
"""Evidence-first cross-program reward settlement ledger.

This module records what a provider/source actually proves about a paid-work
opportunity. A merge is intentionally outside the settlement state machine:
merged work can be useful evidence of delivery, but it is not evidence that a
sponsor awarded a bounty or paid it.

The mature ``revenue_settlement`` wallet-history reconciler remains the cash
authority for RTC. ``paid_event_from_reconciliation`` can lift only its
``verified_paid`` result into this higher-level ledger; partial or inferred cash
states are rejected.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable


SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_RECORDS = 10_000
_MAX_EVENTS = 1_000
_MAX_TEXT = 2_048
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_.:-]{0,31}$")

STATE_ELIGIBILITY_UNKNOWN = "ELIGIBILITY_UNKNOWN"
STATE_ADVERTISED = "ADVERTISED"
STATE_AWARDED = "AWARDED"
STATE_TICKET_OPENED = "TICKET_OPENED"
STATE_RAIL_SUPPLIED = "RAIL_SUPPLIED"
STATE_TRANSFER_EVIDENCED = "TRANSFER_EVIDENCED"
STATE_PAID = "PAID"
STATE_CLOSED_WITHOUT_REWARD = "CLOSED_WITHOUT_REWARD"

_STATES = (
    STATE_ELIGIBILITY_UNKNOWN,
    STATE_ADVERTISED,
    STATE_AWARDED,
    STATE_TICKET_OPENED,
    STATE_RAIL_SUPPLIED,
    STATE_TRANSFER_EVIDENCED,
    STATE_PAID,
    STATE_CLOSED_WITHOUT_REWARD,
)
_PROGRESS = (
    STATE_ELIGIBILITY_UNKNOWN,
    STATE_ADVERTISED,
    STATE_AWARDED,
    STATE_TICKET_OPENED,
    STATE_RAIL_SUPPLIED,
    STATE_TRANSFER_EVIDENCED,
    STATE_PAID,
)
_SOURCE_BY_KIND = {
    STATE_ELIGIBILITY_UNKNOWN: frozenset({"status_evidence"}),
    STATE_ADVERTISED: frozenset({"sponsor_publication"}),
    STATE_AWARDED: frozenset({"sponsor_decision"}),
    STATE_TICKET_OPENED: frozenset({"payout_ticket"}),
    STATE_RAIL_SUPPLIED: frozenset({"payment_rail"}),
    STATE_TRANSFER_EVIDENCED: frozenset({"transfer_evidence"}),
    STATE_PAID: frozenset({"verified_cash"}),
    STATE_CLOSED_WITHOUT_REWARD: frozenset({"sponsor_closeout"}),
}
_AMOUNT_KINDS = frozenset({STATE_ADVERTISED, STATE_AWARDED, STATE_TRANSFER_EVIDENCED, STATE_PAID})


class RewardSettlementInputError(ValueError):
    """Malformed or ambiguous ledger input."""


class RewardSettlementEvidenceError(RuntimeError):
    """Evidence cannot support the requested settlement fact."""


def _canonical_json(value: Any) -> bytes:
    try:
        data = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RewardSettlementInputError("value is not canonical JSON") from exc
    if len(data) > _MAX_JSON_BYTES:
        raise RewardSettlementInputError("canonical JSON exceeds size bound")
    return data


def _text(value: Any, *, field: str, max_len: int = _MAX_TEXT) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > max_len:
        raise RewardSettlementInputError(f"{field} must be one bounded non-empty string")
    if any(not char.isprintable() for char in value):
        raise RewardSettlementInputError(f"{field} contains non-printable text")
    return value


def _sha256(value: Any, *, field: str = "source_sha256") -> str:
    value = _text(value, field=field, max_len=64)
    if not _SHA256_RE.fullmatch(value):
        raise RewardSettlementInputError(f"{field} must be lowercase SHA-256")
    return value


def _timestamp(value: Any) -> str:
    value = _text(value, field="observed_at", max_len=64)
    if not value.endswith("Z"):
        raise RewardSettlementInputError("observed_at must be UTC and end in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RewardSettlementInputError("observed_at must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RewardSettlementInputError("observed_at must be UTC")
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_text(value: Any, *, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise RewardSettlementInputError(f"{field} must be an exact decimal string or integer")
    source = str(value)
    if len(source) > 80:
        raise RewardSettlementInputError(f"{field} representation is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise RewardSettlementInputError(f"{field} must be a decimal") from exc
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or amount < 0
        or len(amount.as_tuple().digits) > 40
        or not isinstance(exponent, int)
        or abs(exponent) > 18
    ):
        raise RewardSettlementInputError(f"{field} must be a bounded non-negative decimal")
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _denomination(raw: Any, *, required: bool) -> dict[str, str] | None:
    if raw is None and not required:
        return None
    if not isinstance(raw, dict):
        raise RewardSettlementInputError("denomination must be an object")
    kind = raw.get("kind")
    if kind not in {"currency", "unit"}:
        raise RewardSettlementInputError("denomination.kind must be currency or unit")
    code = _text(raw.get("code"), field="denomination.code", max_len=32)
    if not _CODE_RE.fullmatch(code):
        raise RewardSettlementInputError("denomination.code must be an uppercase bounded code")
    amount = _decimal_text(raw.get("amount"), field="denomination.amount")
    return {"kind": kind, "code": code, "amount": amount}


def _record_identity(raw: Any) -> tuple[str, str | None, int | None]:
    if not isinstance(raw, dict):
        raise RewardSettlementInputError("record must be an object")
    work_id = _text(raw.get("work_id"), field="work_id", max_len=256)
    repo = raw.get("repo")
    pr = raw.get("pr")
    if repo is None and pr is not None:
        raise RewardSettlementInputError("pr requires repo")
    if repo is not None:
        repo = _text(repo, field="repo", max_len=200)
        if not _REPO_RE.fullmatch(repo):
            raise RewardSettlementInputError("repo must be owner/name")
        owner, name = repo.split("/", 1)
        if owner in {".", ".."} or name in {".", ".."}:
            raise RewardSettlementInputError("repo contains an invalid path segment")
    if pr is not None and (isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0):
        raise RewardSettlementInputError("pr must be a positive integer")
    return work_id, repo, pr


def _normalize_event(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RewardSettlementInputError("event must be an object")
    kind = raw.get("kind")
    if kind not in _STATES:
        raise RewardSettlementInputError("event.kind is unsupported")
    source_type = raw.get("source_type")
    if source_type not in _SOURCE_BY_KIND[kind]:
        raise RewardSettlementEvidenceError(f"{kind} requires an authoritative {sorted(_SOURCE_BY_KIND[kind])[0]} source")
    event: dict[str, Any] = {
        "kind": kind,
        "source_type": source_type,
        "source_ref": _text(raw.get("source_ref"), field="source_ref"),
        "source_sha256": _sha256(raw.get("source_sha256")),
        "observed_at": _timestamp(raw.get("observed_at")),
    }
    event["denomination"] = _denomination(raw.get("denomination"), required=kind in _AMOUNT_KINDS)
    if event["denomination"] is None:
        del event["denomination"]

    if kind == STATE_TRANSFER_EVIDENCED:
        if raw.get("transfer_status") != "confirmed":
            raise RewardSettlementEvidenceError("TRANSFER_EVIDENCED requires transfer_status=confirmed")
        event["transfer_status"] = "confirmed"
    elif "transfer_status" in raw:
        raise RewardSettlementInputError("transfer_status is only valid for TRANSFER_EVIDENCED")

    if kind == STATE_PAID:
        if raw.get("cash_status") != "verified_paid":
            raise RewardSettlementEvidenceError("PAID requires cash_status=verified_paid")
        event["cash_status"] = "verified_paid"
    elif "cash_status" in raw:
        raise RewardSettlementInputError("cash_status is only valid for PAID")

    note = raw.get("note")
    if note is not None:
        event["note"] = _text(note, field="note", max_len=512)
    return event


def _event_identity(event: dict[str, Any]) -> tuple[str, str, str, str]:
    return (event["kind"], event["source_type"], event["source_ref"], event["source_sha256"])


def _single_amount(events: list[dict[str, Any]], kind: str) -> dict[str, str] | None:
    values = [event["denomination"] for event in events if event["kind"] == kind]
    if not values:
        return None
    first = values[0]
    if any(value != first for value in values[1:]):
        raise RewardSettlementEvidenceError(f"conflicting {kind} amounts require a new work record")
    return dict(first)


def _derive_state(events: list[dict[str, Any]]) -> str:
    kinds = {event["kind"] for event in events}
    if STATE_CLOSED_WITHOUT_REWARD in kinds and STATE_PAID in kinds:
        raise RewardSettlementEvidenceError("one work record cannot be PAID and CLOSED_WITHOUT_REWARD")
    if STATE_CLOSED_WITHOUT_REWARD in kinds:
        return STATE_CLOSED_WITHOUT_REWARD
    for state in reversed(_PROGRESS):
        if state in kinds:
            return state
    return STATE_ELIGIBILITY_UNKNOWN


def _evidence_gaps(events: list[dict[str, Any]], state: str) -> list[str]:
    kinds = {event["kind"] for event in events}
    if state in {STATE_ELIGIBILITY_UNKNOWN, STATE_CLOSED_WITHOUT_REWARD}:
        return []
    index = _PROGRESS.index(state)
    required = _PROGRESS[1:index]
    return [item for item in required if item not in kinds]


def compile_record(raw: Any) -> dict[str, Any]:
    """Compile one evidence record into deterministic settlement truth."""
    work_id, repo, pr = _record_identity(raw)
    events_raw = raw.get("events")
    if not isinstance(events_raw, list) or len(events_raw) > _MAX_EVENTS:
        raise RewardSettlementInputError("events must be a bounded list")
    events = [_normalize_event(item) for item in events_raw]
    seen: set[tuple[str, str, str, str]] = set()
    for event in events:
        identity = _event_identity(event)
        if identity in seen:
            raise RewardSettlementInputError("duplicate indistinguishable evidence event")
        seen.add(identity)
    events.sort(key=lambda event: (event["observed_at"], event["kind"], event["source_type"], event["source_ref"], event["source_sha256"]))

    state = _derive_state(events)
    advertised = _single_amount(events, STATE_ADVERTISED)
    awarded = _single_amount(events, STATE_AWARDED)
    paid = _single_amount(events, STATE_PAID)
    if advertised and awarded:
        if (advertised["kind"], advertised["code"]) != (awarded["kind"], awarded["code"]):
            raise RewardSettlementEvidenceError(
                "advertised and awarded denominations differ; explicit conversion evidence is required"
            )
        if Decimal(awarded["amount"]) > Decimal(advertised["amount"]):
            raise RewardSettlementEvidenceError("awarded amount exceeds advertised amount")
    if awarded and paid:
        if (awarded["kind"], awarded["code"]) != (paid["kind"], paid["code"]):
            raise RewardSettlementEvidenceError(
                "awarded and paid denominations differ; explicit conversion evidence is required"
            )
        if Decimal(paid["amount"]) > Decimal(awarded["amount"]):
            raise RewardSettlementEvidenceError("verified paid amount exceeds sponsor-awarded amount")

    result: dict[str, Any] = {
        "work_id": work_id,
        "state": state,
        "evidence_gaps": _evidence_gaps(events, state),
        "events": events,
        "claims": {
            "advertised": advertised,
            "awarded": awarded,
            "paid": paid,
            "revenue_recognition": "not_computed",
        },
    }
    if repo is not None:
        result["repo"] = repo
    if pr is not None:
        result["pr"] = pr
    return result


def compile_document(payload: Any) -> dict[str, Any]:
    """Compile a source document and attach a canonical semantic receipt."""
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise RewardSettlementInputError("schema_version must be 1")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > _MAX_RECORDS:
        raise RewardSettlementInputError("items must be a bounded list")
    records = [compile_record(item) for item in items]
    keys: set[tuple[str, str | None, int | None]] = set()
    for record in records:
        key = (record["work_id"].casefold(), record.get("repo", "").casefold() or None, record.get("pr"))
        if key in keys:
            raise RewardSettlementInputError("duplicate work record")
        keys.add(key)
    records.sort(key=lambda row: (row["work_id"].casefold(), (row.get("repo") or "").casefold(), row.get("pr") or 0))
    body = {"schema_version": SCHEMA_VERSION, "records": records}
    receipt = hashlib.sha256(_canonical_json(body)).hexdigest()
    return {**body, "receipt_sha256": receipt}


def verify_document(compiled: Any) -> dict[str, Any]:
    """Recompile retained events and verify the semantic receipt byte-for-byte."""
    if not isinstance(compiled, dict) or compiled.get("schema_version") != SCHEMA_VERSION:
        raise RewardSettlementInputError("compiled schema_version must be 1")
    receipt = _sha256(compiled.get("receipt_sha256"), field="receipt_sha256")
    records = compiled.get("records")
    if not isinstance(records, list) or len(records) > _MAX_RECORDS:
        raise RewardSettlementInputError("compiled records must be a bounded list")
    source_items = []
    for record in records:
        if not isinstance(record, dict):
            raise RewardSettlementInputError("compiled record must be an object")
        raw: dict[str, Any] = {"work_id": record.get("work_id"), "events": record.get("events")}
        if "repo" in record:
            raw["repo"] = record["repo"]
        if "pr" in record:
            raw["pr"] = record["pr"]
        source_items.append(raw)
    recomputed = compile_document({"schema_version": SCHEMA_VERSION, "items": source_items})
    if recomputed != compiled:
        if recomputed["receipt_sha256"] != receipt:
            raise RewardSettlementEvidenceError("receipt does not match retained evidence")
        raise RewardSettlementEvidenceError("compiled semantics differ from retained evidence")
    return {"verified": True, "receipt_sha256": receipt, "records": len(records)}


def paid_event_from_reconciliation(
    row: Any,
    *,
    source_ref: str,
    source_sha256: str,
    observed_at: str,
) -> dict[str, Any]:
    """Lift only verified cash output from the existing cash reconciler.

    ``partially_verified`` and ``not_inferred`` rows are intentionally rejected.
    This function performs no currency conversion and no revenue recognition.
    """
    if not isinstance(row, dict):
        raise RewardSettlementInputError("reconciliation row must be an object")
    if row.get("cash_status") != "verified_paid":
        raise RewardSettlementEvidenceError("cash reconciliation is not verified_paid")
    currency = _text(row.get("currency"), field="currency", max_len=32)
    if not _CODE_RE.fullmatch(currency):
        raise RewardSettlementInputError("currency must be an uppercase bounded code")
    amount = _decimal_text(row.get("verified_amount"), field="verified_amount")
    if Decimal(amount) <= 0:
        raise RewardSettlementEvidenceError("verified_paid amount must be positive")
    return {
        "kind": STATE_PAID,
        "source_type": "verified_cash",
        "source_ref": _text(source_ref, field="source_ref"),
        "source_sha256": _sha256(source_sha256),
        "observed_at": _timestamp(observed_at),
        "denomination": {"kind": "currency", "code": currency, "amount": amount},
        "cash_status": "verified_paid",
    }


def _strict_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RewardSettlementInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise RewardSettlementInputError(f"non-finite JSON constant is not allowed: {value}")


def _load_json(path: str) -> Any:
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_JSON_BYTES:
            raise RewardSettlementInputError("input file exceeds size bound")
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RewardSettlementInputError(f"cannot read {path}") from exc
    try:
        return json.loads(text, object_pairs_hook=_strict_object, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise RewardSettlementInputError(f"{path} is not valid JSON") from exc


def _write_json(path: str | None, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path is None:
        print(text, end="")
        return
    target = Path(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise RewardSettlementInputError(f"cannot write {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile or verify evidence-first reward settlement ledgers")
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile", help="compile source evidence")
    compile_parser.add_argument("input")
    compile_parser.add_argument("-o", "--output")
    verify_parser = sub.add_parser("verify", help="verify compiled evidence")
    verify_parser.add_argument("input")
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            result = compile_document(_load_json(args.input))
            _write_json(args.output, result)
        else:
            result = verify_document(_load_json(args.input))
            _write_json(None, result)
    except (RewardSettlementInputError, RewardSettlementEvidenceError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
