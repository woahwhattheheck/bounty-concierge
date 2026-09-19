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

def _normalize_candidate(raw: Any, index: int) -> dict[str, Any]:
    value = _object(
        raw,
        f"candidates[{index}]",
        required=frozenset(
            {"work_id", "canonical_source_url", "reward_state", "reward_evidence_url", "observed_at"}
        ),
        optional=frozenset({"reward_usd", "native_reward_note"}),
    )
    work_id = _text(value["work_id"], f"candidates[{index}].work_id", maximum=256)
    source = _url(value["canonical_source_url"], f"candidates[{index}].canonical_source_url")
    evidence = _url(value["reward_evidence_url"], f"candidates[{index}].reward_evidence_url")
    observed_at = _timestamp(value["observed_at"], f"candidates[{index}].observed_at")
    state = value["reward_state"]
    if state not in _ALLOWED_REWARD_STATES:
        raise RewardFloorInputError(
            f"candidates[{index}].reward_state must be one of {sorted(_ALLOWED_REWARD_STATES)}"
        )
    native_note = value.get("native_reward_note")
    if native_note is not None:
        native_note = _text(native_note, f"candidates[{index}].native_reward_note")

    reward: Optional[Decimal] = None
    if state == "KNOWN_USD":
        if "reward_usd" not in value:
            raise RewardFloorInputError(
                f"candidates[{index}] KNOWN_USD requires reward_usd"
            )
        reward = _decimal(value["reward_usd"], f"candidates[{index}].reward_usd")
    elif "reward_usd" in value:
        raise RewardFloorInputError(
            f"candidates[{index}] UNKNOWN must not assert reward_usd"
        )

    return {
        "input_index": index,
        "work_id": work_id,
        "canonical_source_url": source,
        "reward_state": state,
        "reward_usd": None if reward is None else _amount_text(reward),
        "reward_evidence_url": evidence,
        "observed_at": observed_at,
        "native_reward_note": native_note,
        "_reward": reward,
    }


def compile_intake_reward_floor(request: dict[str, Any]) -> dict[str, Any]:
    """Compile a deterministic reward-floor routing receipt."""
    top = _object(
        request,
        "request",
        required=frozenset({"schema", "as_of", "policy", "candidates"}),
    )
    if top["schema"] != _REQUEST_SCHEMA:
        raise RewardFloorInputError(f"request.schema must be {_REQUEST_SCHEMA!r}")
    as_of = _timestamp(top["as_of"], "as_of")
    policy = _normalize_policy(top["policy"])
    raw_candidates = top["candidates"]
    if type(raw_candidates) is not list:
        raise RewardFloorInputError("candidates must be a list")
    if len(raw_candidates) > _MAX_CANDIDATES:
        raise RewardFloorInputError(f"candidates cannot exceed {_MAX_CANDIDATES} items")

    candidates = [_normalize_candidate(raw, i) for i, raw in enumerate(raw_candidates)]
    seen_work: set[str] = set()
    seen_source: set[str] = set()
    for item in candidates:
        if item["work_id"] in seen_work:
            raise RewardFloorInputError(f"duplicate work_id: {item['work_id']}")
        if item["canonical_source_url"] in seen_source:
            raise RewardFloorInputError(
                f"duplicate canonical_source_url: {item['canonical_source_url']}"
            )
        seen_work.add(item["work_id"])
        seen_source.add(item["canonical_source_url"])

    active_floor = Decimal(policy["active_floor_usd"])
    pile_floor = Decimal(policy["pile_floor_usd"])
    decisions: list[dict[str, Any]] = []
    counts = {name: 0 for name in sorted(_ALLOWED_DISPOSITIONS)}

    for item in candidates:
        reward = item["_reward"]
        if reward is None:
            disposition = "HOLD_UNCONFIRMED"
            reasons = ["EXACT_USD_REWARD_NOT_VERIFIED"]
        elif reward >= active_floor:
            disposition = "ACTIVE_FLOOR_MET"
            reasons = ["ACTIVE_USD_REWARD_FLOOR_MET"]
        elif reward >= pile_floor:
            disposition = "PILE_10_49"
            reasons = ["BELOW_ACTIVE_FLOOR", "PILE_USD_REWARD_FLOOR_MET"]
        else:
            disposition = "IGNORE_UNDER_10"
            reasons = ["BELOW_PILE_USD_REWARD_FLOOR"]
        counts[disposition] += 1
        decisions.append(
            {
                "input_index": item["input_index"],
                "work_id": item["work_id"],
                "canonical_source_url": item["canonical_source_url"],
                "reward_state": item["reward_state"],
                "reward_usd": item["reward_usd"],
                "reward_evidence_url": item["reward_evidence_url"],
                "observed_at": item["observed_at"],
                "native_reward_note": item["native_reward_note"],
                "disposition": disposition,
                "active_queue_eligible": disposition == "ACTIVE_FLOOR_MET",
                "reason_codes": reasons,
            }
        )

    body = {
        "schema": _RECEIPT_SCHEMA,
        "as_of": as_of,
        "policy": policy,
        "policy_sha256": _sha256_json(policy),
        "candidate_count": len(decisions),
        "counts": counts,
        "candidates": decisions,
        "authority": {
            "usd_reward_floor_router_only": True,
            "fx_conversion": False,
            "batch_promotion": False,
            "unpriced_reward_inference": False,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "acceptance_authority": False,
            "payment_cash_or_revenue_authority": False,
        },
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    if type(receipt) is not dict or receipt.get("schema") != _RECEIPT_SCHEMA:
        return False
