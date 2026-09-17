# SPDX-License-Identifier: MIT
"""Strict parsing primitives for the reward settlement ledger."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

SCHEMA = "bounty-concierge/reward-settlement-ledger/v1"
PACKET_SCHEMA = "bounty-concierge/reward-settlement-ledger-packet/v1"
MAX_BYTES = 512 * 1024
MAX_EVENTS = 128
EVENT_KINDS = frozenset({
    "ADVERTISED", "AWARDED", "ELIGIBILITY_UNKNOWN", "TICKET_OPENED",
    "RAIL_SUPPLIED", "TRANSFER_EVIDENCED", "PAID", "CLOSED_WITHOUT_REWARD",
    "WORK_MERGED", "WORK_ACCEPTED",
})
AMOUNT_KINDS = frozenset({"ADVERTISED", "AWARDED", "TRANSFER_EVIDENCED", "PAID"})
EVIDENCE_MODES = frozenset({"PROVIDER", "REFERENCE", "SYNTHETIC"})
UNIT_KINDS = frozenset({"FIAT", "NONCASH"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Z][A-Z0-9._-]{0,31}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#?=&@+\-]{0,239}$")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
AMOUNT = re.compile(r"^(0|[1-9]\d*)(\.\d{1,12})?$")


class RewardLedgerError(ValueError):
    """Input or semantic validation failure."""


def _pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise RewardLedgerError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def loads_strict(text: str) -> Any:
    if type(text) is not str:
        raise RewardLedgerError("JSON input must be text")
    def reject(value: str) -> None:
        raise RewardLedgerError(f"non-finite JSON number: {value}")
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=reject)
    except RewardLedgerError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise RewardLedgerError("invalid JSON") from exc


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RewardLedgerError("value is not canonical JSON") from exc


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def obj(value: Any, *, name: str, keys: set[str], required: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise RewardLedgerError(f"{name} must be an object")
    unknown, missing = set(value) - keys, required - set(value)
    if unknown:
        raise RewardLedgerError(f"{name} unknown keys: {sorted(unknown)}")
    if missing:
        raise RewardLedgerError(f"{name} missing keys: {sorted(missing)}")
    return value


def text(value: Any, *, name: str, pattern: re.Pattern[str] | None = None, max_len: int = 240) -> str:
    if type(value) is not str or not value or len(value) > max_len:
        raise RewardLedgerError(f"{name} must be bounded non-empty text")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise RewardLedgerError(f"{name} has invalid syntax")
    return value


def timestamp(value: Any, *, name: str) -> str:
    value = text(value, name=name, pattern=TS, max_len=20)
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise RewardLedgerError(f"{name} is not a real UTC timestamp") from exc
    return value


def decimal_text(value: Any, *, name: str) -> str:
    value = text(value, name=name, max_len=80)
    if AMOUNT.fullmatch(value) is None:
        raise RewardLedgerError(f"{name} must be a non-negative canonical decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise RewardLedgerError(f"{name} is invalid") from exc
    if not number.is_finite() or number < 0:
        raise RewardLedgerError(f"{name} is invalid")
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    normalized = normalized or "0"
    if value != normalized:
        raise RewardLedgerError(f"{name} must be canonical ({normalized})")
    return value


def evidence(value: Any, *, captured_at: str, name: str) -> dict[str, Any]:
    value = obj(value, name=name,
        keys={"mode", "source_ref", "sha256", "observed_at", "source_class"},
        required={"mode", "source_ref", "sha256", "observed_at", "source_class"})
    mode = text(value["mode"], name=f"{name}.mode", pattern=TOKEN, max_len=16)
    if mode not in EVIDENCE_MODES:
        raise RewardLedgerError(f"{name}.mode unsupported")
    result = {
        "mode": mode,
        "source_ref": text(value["source_ref"], name=f"{name}.source_ref", pattern=REF),
        "sha256": text(value["sha256"], name=f"{name}.sha256", pattern=HEX64, max_len=64),
        "observed_at": timestamp(value["observed_at"], name=f"{name}.observed_at"),
        "source_class": text(value["source_class"], name=f"{name}.source_class", pattern=TOKEN, max_len=32),
    }
    if result["observed_at"] > captured_at:
        raise RewardLedgerError(f"{name}.observed_at is after captured_at")
    if mode != "PROVIDER" and result["source_class"] == "PROVIDER":
        raise RewardLedgerError(f"{name} non-provider evidence cannot use PROVIDER source_class")
    return result


def event(value: Any, *, captured_at: str, index: int) -> dict[str, Any]:
    name = f"events[{index}]"
    value = obj(value, name=name,
        keys={"kind", "event_id", "amount", "unit", "unit_kind", "evidence", "note"},
        required={"kind", "event_id", "evidence"})
    kind = text(value["kind"], name=f"{name}.kind", pattern=TOKEN, max_len=32)
    if kind not in EVENT_KINDS:
        raise RewardLedgerError(f"{name}.kind unsupported")
    result: dict[str, Any] = {
        "kind": kind,
        "event_id": text(value["event_id"], name=f"{name}.event_id", pattern=ID, max_len=160),
        "evidence": evidence(value["evidence"], captured_at=captured_at, name=f"{name}.evidence"),
    }
    if kind in AMOUNT_KINDS:
        if not all(k in value for k in ("amount", "unit", "unit_kind")):
            raise RewardLedgerError(f"{name} amount event requires amount/unit/unit_kind")
        result["amount"] = decimal_text(value["amount"], name=f"{name}.amount")
        result["unit"] = text(value["unit"], name=f"{name}.unit", pattern=TOKEN, max_len=32)
        result["unit_kind"] = text(value["unit_kind"], name=f"{name}.unit_kind", pattern=TOKEN, max_len=16)
        if result["unit_kind"] not in UNIT_KINDS:
            raise RewardLedgerError(f"{name}.unit_kind unsupported")
        if result["unit_kind"] == "FIAT" and len(result["unit"]) != 3:
            raise RewardLedgerError(f"{name}.unit must be an ISO-style 3-letter fiat token")
    elif any(k in value for k in ("amount", "unit", "unit_kind")):
        raise RewardLedgerError(f"{name} non-amount event cannot carry amount")
    if "note" in value:
        result["note"] = text(value["note"], name=f"{name}.note", max_len=240)
    return result


def conversion(value: Any, *, captured_at: str, paid_unit: str) -> dict[str, Any]:
    value = obj(value, name="conversion",
        keys={"kind", "evidence_id", "from_unit", "to_unit", "usd_amount", "evidence"},
        required={"kind", "evidence_id", "from_unit", "to_unit", "usd_amount", "evidence"})
    if value["kind"] != "SETTLEMENT_CONVERSION":
        raise RewardLedgerError("conversion.kind must be SETTLEMENT_CONVERSION")
    result = {
        "kind": "SETTLEMENT_CONVERSION",
        "evidence_id": text(value["evidence_id"], name="conversion.evidence_id", pattern=ID, max_len=160),
        "from_unit": text(value["from_unit"], name="conversion.from_unit", pattern=TOKEN, max_len=32),
        "to_unit": text(value["to_unit"], name="conversion.to_unit", pattern=TOKEN, max_len=32),
        "usd_amount": decimal_text(value["usd_amount"], name="conversion.usd_amount"),
        "evidence": evidence(value["evidence"], captured_at=captured_at, name="conversion.evidence"),
    }
    if result["from_unit"] != paid_unit or result["to_unit"] != "USD":
        raise RewardLedgerError("conversion units do not bind the paid unit to USD")
    if result["evidence"]["mode"] != "PROVIDER":
        raise RewardLedgerError("conversion requires PROVIDER settlement evidence")
    return result


def trusted_binding(ev: dict[str, Any]) -> dict[str, str]:
    return {k: ev[k] for k in ("source_ref", "sha256", "observed_at", "source_class")}


def validate_provider_authority(provider: list[dict[str, Any]], conv: dict[str, Any] | None, registry: Any) -> None:
    required = {row["event_id"]: trusted_binding(row["evidence"]) for row in provider}
    if conv is not None:
        required[conv["evidence_id"]] = trusted_binding(conv["evidence"])
    if not required:
        if registry not in (None, {}):
            raise RewardLedgerError("trusted provider registry supplied with no PROVIDER evidence")
        return
    if type(registry) is not dict or set(registry) != set(required):
        raise RewardLedgerError("PROVIDER evidence requires exact trusted out-of-band registry ids")
    for evidence_id, expected in required.items():
        actual = registry[evidence_id]
        if type(actual) is not dict or set(actual) != set(expected) or actual != expected:
            raise RewardLedgerError(f"trusted provider registry entry {evidence_id} does not bind exact evidence")
