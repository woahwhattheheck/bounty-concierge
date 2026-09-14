"""Deterministic custody for sponsor adjudication evidence before cash settlement.

This module intentionally cannot contact sponsors, submit claims, move funds, or
recognize revenue.  It compiles operator-retained submission evidence and
explicit sponsor-origin events into bounded claim-unit state.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

SCHEMA_VERSION = 1
OUTPUT_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 2_000_000
MAX_ITEMS = 10_000
MAX_TEXT = 4_096
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
UTC_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z$")
CURRENCY_RE = re.compile(r"^[A-Z]{3,12}$")
AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$")

EVENT_TYPES = {
    "SPONSOR_RECEIVED",
    "SPONSOR_VERIFIED",
    "ADJUDICATION_STARTED",
    "REWARD_OFFERED",
    "DUPLICATE_COLLAPSED",
    "DECLINED",
    "PAYMENT_REPORTED",
}
UNIT_STATUSES = {
    "submitted",
    "sponsor_verified",
    "adjudicating",
    "reward_offered",
    "declined",
    "duplicate",
    "paid_evidence_pending",
}
ACTIONS = {"DO_NOT_RESUBMIT", "WAIT_SPONSOR", "OWNER_REVIEW"}
AUTHORITY_CEILING = {
    "sponsor_contact": False,
    "claim_submission": False,
    "provider_mutation": False,
    "wallet_mutation": False,
    "payout_initiation": False,
    "payment_recognition": False,
    "accounting_revenue_recognition": False,
}

class AdjudicationError(ValueError):
    """Manifest, state, or receipt violates the custody contract."""


def _no_float(text: str) -> Any:
    raise AdjudicationError("floating-point JSON numbers are forbidden; use canonical decimal strings")


def _no_constant(text: str) -> Any:
    raise AdjudicationError(f"non-finite JSON constant is forbidden: {text}")


def _object_pairs(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise AdjudicationError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def loads_strict(text: str) -> Any:
    try:
        return json.loads(
            text,
            parse_float=_no_float,
            parse_constant=_no_constant,
            object_pairs_hook=_object_pairs,
        )
    except AdjudicationError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise AdjudicationError(f"invalid JSON: {exc}") from exc


def load_strict(path: os.PathLike[str] | str) -> Any:
    data = Path(path).read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise AdjudicationError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AdjudicationError("input is not strict UTF-8") from exc
    return loads_strict(text)


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_obj(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _require_exact_keys(obj: Mapping[str, Any], required: Sequence[str], optional: Sequence[str] = (), *, where: str) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    got = set(obj)
    missing = sorted(required_set - got)
    extra = sorted(got - allowed)
    if missing or extra:
        raise AdjudicationError(f"{where}: schema mismatch missing={missing} extra={extra}")


def _require_dict(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise AdjudicationError(f"{where}: expected object")
    return value


def _require_list(value: Any, *, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise AdjudicationError(f"{where}: expected array")
    if len(value) > MAX_ITEMS:
        raise AdjudicationError(f"{where}: too many items")
    return value


def _require_text(value: Any, *, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise AdjudicationError(f"{where}: expected string")
    if len(value) > MAX_TEXT:
        raise AdjudicationError(f"{where}: text too long")
    if not allow_empty and (not value or value != value.strip()):
        raise AdjudicationError(f"{where}: must be non-empty and have no surrounding whitespace")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        raise AdjudicationError(f"{where}: control characters forbidden")
    return value


def _require_id(value: Any, *, where: str) -> str:
    text = _require_text(value, where=where)
    if not ID_RE.fullmatch(text):
        raise AdjudicationError(f"{where}: invalid identifier")
    return text


def _require_hex64(value: Any, *, where: str) -> str:
    text = _require_text(value, where=where)
    if not HEX64.fullmatch(text):
        raise AdjudicationError(f"{where}: expected lowercase 64-hex SHA-256")
    return text


def _parse_utc(value: Any, *, where: str) -> datetime:
    text = _require_text(value, where=where)
    match = UTC_RE.fullmatch(text)
    if not match:
        raise AdjudicationError(f"{where}: expected canonical UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        dt = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise AdjudicationError(f"{where}: invalid UTC timestamp") from exc
    if dt.strftime("%Y-%m-%dT%H:%M:%SZ") != text:
        raise AdjudicationError(f"{where}: non-canonical UTC timestamp")
    return dt


def _amount(value: Any, *, where: str, positive: bool = False) -> Tuple[str, Decimal]:
    text = _require_text(value, where=where)
    if not AMOUNT_RE.fullmatch(text):
        raise AdjudicationError(f"{where}: amount must be canonical unsigned decimal text")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise AdjudicationError(f"{where}: invalid decimal") from exc
    if positive and number <= 0:
        raise AdjudicationError(f"{where}: amount must be positive")
    return text, number


def _string_id_list(value: Any, *, where: str, nonempty: bool = True) -> List[str]:
    raw = _require_list(value, where=where)
    if nonempty and not raw:
        raise AdjudicationError(f"{where}: must not be empty")
    out: List[str] = []
    seen = set()
    for idx, item in enumerate(raw):
        ident = _require_id(item, where=f"{where}[{idx}]")
        if ident in seen:
            raise AdjudicationError(f"{where}: duplicate id {ident}")
        seen.add(ident)
        out.append(ident)
    return out



__all__ = [name for name in globals() if not name.startswith("__")]
