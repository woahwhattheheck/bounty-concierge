# SPDX-License-Identifier: MIT
"""Fail-closed prefilter for the owner's paid-work reward floor.

This router answers one narrow question before deeper paid-work qualification:
where should a candidate go based on *verified USD reward evidence*?

It never performs FX, never promotes a batch of small items into executable
work, and never treats discretionary labels such as ``Maybe Rewarded`` as a
known reward.  Candidates are routed to one of four dispositions:

* ``ACTIVE_FLOOR_MET`` — verified USD reward >= the active floor (default $50)
* ``PILE_10_49`` — verified USD reward >= pile floor and < active floor
* ``IGNORE_UNDER_10`` — verified USD reward below the pile floor
* ``HOLD_UNCONFIRMED`` — no exact verified USD reward is available

The output is decision support only.  ACTIVE means "may proceed to the normal
paid-work/account/claim gates", not external claim authority, acceptance,
payment, cash, or revenue authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit


_REQUEST_SCHEMA = "intake-reward-floor/v1"
_RECEIPT_SCHEMA = "intake-reward-floor-receipt/v1"
_POLICY_SCHEMA = "intake-reward-floor-policy/v1"
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_CANDIDATES = 10000
_MAX_TEXT = 1024
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_REWARD_STATES = frozenset({"KNOWN_USD", "UNKNOWN"})
_ALLOWED_DISPOSITIONS = frozenset(
    {"ACTIVE_FLOOR_MET", "PILE_10_49", "IGNORE_UNDER_10", "HOLD_UNCONFIRMED"}
)


class RewardFloorInputError(ValueError):
    """Malformed or internally inconsistent reward-floor input."""


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RewardFloorInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise RewardFloorInputError("canonical JSON value is too large")
    return payload


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _object(
    value: Any,
    field: str,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if type(value) is not dict:
        raise RewardFloorInputError(f"{field} must be an object")
    keys = frozenset(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise RewardFloorInputError(f"{field} is missing required field {missing[0]!r}")
    if unknown:
        raise RewardFloorInputError(f"{field} contains unsupported field {unknown[0]!r}")
    return value


def _text(value: Any, field: str, *, maximum: int = _MAX_TEXT) -> str:
    if type(value) is not str or value != value.strip() or not value:
        raise RewardFloorInputError(f"{field} must be a non-empty trimmed string")
    if len(value) > maximum or any(not ch.isprintable() for ch in value):
        raise RewardFloorInputError(f"{field} exceeds the supported text boundary")
    return value


def _decimal(value: Any, field: str, *, nonnegative: bool = True) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise RewardFloorInputError(f"{field} must be an exact decimal string or integer")
    if not isinstance(value, (str, int, Decimal)):
        raise RewardFloorInputError(f"{field} must be an exact decimal string or integer")
    source = str(value)
    if len(source) > 64:
        raise RewardFloorInputError(f"{field} representation is too large")
    try:
        result = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise RewardFloorInputError(f"{field} must be a finite decimal") from exc
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or len(result.as_tuple().digits) > 30
        or not isinstance(exponent, int)
        or abs(exponent) > 18
        or (nonnegative and result < 0)
    ):
        raise RewardFloorInputError(f"{field} must be a bounded non-negative decimal")
    return result


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _timestamp(value: Any, field: str) -> str:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        raise RewardFloorInputError(f"{field} must be exact UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise RewardFloorInputError(f"{field} is invalid") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise RewardFloorInputError(f"{field} is not canonical UTC")
    return value


def _url(value: Any, field: str) -> str:
    raw = _text(value, field, maximum=2048)
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise RewardFloorInputError(f"{field} must be an https URL without credentials")
    if parsed.fragment:
        raise RewardFloorInputError(f"{field} must not contain a fragment")
    # Normalize scheme/host case and strip the default port/trailing slash only.
    host = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.port not in (None, 443):
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


def _normalize_policy(raw: Any) -> dict[str, str]:
    value = _object(
        raw,
        "policy",
        required=frozenset({"schema", "active_floor_usd", "pile_floor_usd"}),
    )
    if value["schema"] != _POLICY_SCHEMA:
        raise RewardFloorInputError(f"policy.schema must be {_POLICY_SCHEMA!r}")
    active = _decimal(value["active_floor_usd"], "policy.active_floor_usd")
    pile = _decimal(value["pile_floor_usd"], "policy.pile_floor_usd")
    if active <= 0:
        raise RewardFloorInputError("policy.active_floor_usd must be greater than zero")
    if pile <= 0:
        raise RewardFloorInputError("policy.pile_floor_usd must be greater than zero")
    if pile >= active:
        raise RewardFloorInputError("policy.pile_floor_usd must be below active_floor_usd")
    return {
        "schema": _POLICY_SCHEMA,
        "active_floor_usd": _amount_text(active),
        "pile_floor_usd": _amount_text(pile),
    }
