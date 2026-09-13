# SPDX-License-Identifier: MIT
"""Prioritize currently-qualified external paid contracts without inventing FX.

This module is deliberately separate from the GitHub-bounty rankers.  Every
candidate must carry an exact external-contract qualification receipt and the
snapshot that produced it.  The receipt is re-verified at trusted current time
before any operator estimate can influence ranking.

The strongest output is a priority ordering *inside one native-currency
partition*.  There is no global winner, no cross-currency comparison, and no
authority to submit, accept terms/contracts, spend, claim an award/payment, or
recognize revenue.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Optional, Union

from concierge.contract_qualification import (
    ContractQualificationInputError,
    verify_contract_qualification_receipt,
)

SCHEMA_VERSION = "bounty-concierge.external-contract-partitioned-ranking/v1"
_MAX_CANDIDATES = 500
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_DECIMAL_TEXT = 64
_HEX64 = frozenset("0123456789abcdef")
_CANDIDATE_KEYS = frozenset(
    {
        "snapshot",
        "qualification_receipt",
        "estimated_effort_hours",
        "estimated_award_probability",
    }
)


class ExternalContractRankInputError(ValueError):
    """Input cannot safely participate in external-contract ranking."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExternalContractRankInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ExternalContractRankInputError(f"non-standard JSON numeric constant: {value}")


def loads_strict_json(payload: Union[bytes, str]) -> Any:
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExternalContractRankInputError("request JSON must be UTF-8") from exc
    elif type(payload) is str:
        text = payload
    else:
        raise ExternalContractRankInputError("request payload must be text or bytes")
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ExternalContractRankInputError("request JSON is invalid") from exc


def _exact_decimal(
    value: Any,
    name: str,
    *,
    positive: bool = False,
    minimum: Optional[Decimal] = None,
    maximum: Optional[Decimal] = None,
) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ExternalContractRankInputError(f"{name} must be an exact decimal string or integer")
    if isinstance(value, Decimal):
        raw = format(value, "f")
    elif type(value) is int:
        raw = str(value)
    elif type(value) is str:
        raw = value.strip()
        if raw != value or not raw:
            raise ExternalContractRankInputError(f"{name} must be canonical exact decimal text")
    else:
        raise ExternalContractRankInputError(f"{name} must be an exact decimal string or integer")
    if len(raw) > _MAX_DECIMAL_TEXT:
        raise ExternalContractRankInputError(f"{name} is too long")
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ExternalContractRankInputError(f"{name} is invalid") from exc
    if not parsed.is_finite():
        raise ExternalContractRankInputError(f"{name} must be finite")
    if parsed.as_tuple().exponent < -12 or parsed.adjusted() > 12:
        raise ExternalContractRankInputError(f"{name} is outside supported precision or magnitude")
    if positive and parsed <= 0:
        raise ExternalContractRankInputError(f"{name} must be greater than zero")
    if minimum is not None and parsed < minimum:
        raise ExternalContractRankInputError(f"{name} is below the supported minimum")
    if maximum is not None and parsed > maximum:
        raise ExternalContractRankInputError(f"{name} is above the supported maximum")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _fraction_decimal(value: Fraction, *, places: int = 12) -> str:
    """Render display-only metrics without using them for ordering authority."""
    scale = 10**places
    # Truncate toward zero deterministically. Ranking always uses Fraction.
    scaled = value.numerator * scale // value.denominator
    integer, fraction = divmod(abs(scaled), scale)
    sign = "-" if scaled < 0 else ""
    if fraction == 0:
        return f"{sign}{integer}"
    return f"{sign}{integer}.{fraction:0{places}d}".rstrip("0")


def _text(value: Any, name: str, *, limit: int) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise ExternalContractRankInputError(f"{name} must be a bounded non-empty string")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ExternalContractRankInputError(f"{name} contains control characters")
    return value


def _digest(value: Any, name: str) -> str:
    text = _text(value, name, limit=64)
    if len(text) != 64 or any(char not in _HEX64 for char in text):
        raise ExternalContractRankInputError(f"{name} must be a lowercase sha256")
    return text


def _verified_identity(
    candidate: dict[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    snapshot = candidate.get("snapshot")
    receipt = candidate.get("qualification_receipt")
    if type(snapshot) is not dict or type(receipt) is not dict:
        raise ExternalContractRankInputError("candidate snapshot and qualification_receipt must be objects")
    try:
        verified = verify_contract_qualification_receipt(snapshot, receipt, as_of=as_of)
    except (ContractQualificationInputError, ValueError, TypeError, KeyError) as exc:
        raise ExternalContractRankInputError("qualification receipt failed current verification") from exc
    if type(verified) is not dict or verified.get("valid") is not True:
        raise ExternalContractRankInputError("qualification verifier did not return valid=true")
    if receipt.get("disposition") != "ACTIONABLE" or verified.get("current_disposition") != "ACTIONABLE":
        raise ExternalContractRankInputError("qualification is not current-actionable")
    if receipt.get("bid_ready") is not True or receipt.get("submitted") is not False:
        raise ExternalContractRankInputError("qualification receipt is not an unsubmitted bid-ready receipt")

    source = _text(receipt.get("canonical_source_url"), "canonical_source_url", limit=2048)
    source_digest = _digest(receipt.get("source_digest"), "source_digest")
    qualification_digest = _digest(receipt.get("qualification_digest"), "qualification_digest")
    currency = _text(receipt.get("native_currency"), "native_currency", limit=3)
    if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
        raise ExternalContractRankInputError("native_currency must be a three-letter uppercase code")
    bid = receipt.get("bid")
    if type(bid) is not dict or set(bid) != {"currency", "amount", "delivery_days"}:
        raise ExternalContractRankInputError("qualification bid shape mismatch")
    if bid.get("currency") != currency:
        raise ExternalContractRankInputError("qualification bid currency mismatch")
    amount = _exact_decimal(bid.get("amount"), "qualification bid amount", positive=True)
    delivery_days = bid.get("delivery_days")
    if isinstance(delivery_days, bool) or type(delivery_days) is not int or delivery_days <= 0:
        raise ExternalContractRankInputError("qualification bid delivery_days must be a positive integer")
    return {
        "canonical_source_url": source,
        "source_digest": source_digest,
        "qualification_digest": qualification_digest,
        "currency": currency,
        "bid_amount": amount,
        "delivery_days": delivery_days,
    }


def _excluded(index: int, code: str, *, source: Optional[str] = None) -> dict[str, Any]:
    return {"input_index": index, "canonical_source_url": source, "reason_code": code}


def _score(index: int, identity: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    effort = _exact_decimal(candidate.get("estimated_effort_hours"), "estimated_effort_hours", positive=True)
    probability = _exact_decimal(
        candidate.get("estimated_award_probability"),
        "estimated_award_probability",
        minimum=Decimal("0"),
        maximum=Decimal("1"),
    )
    reward_fraction = Fraction(identity["bid_amount"])
    probability_fraction = Fraction(probability)
    effort_fraction = Fraction(effort)
    expected_value = reward_fraction * probability_fraction
    ev_per_hour = expected_value / effort_fraction
    return {
        "input_index": index,
        **identity,
        "_reward": reward_fraction,
        "_probability": probability_fraction,
        "_effort": effort_fraction,
        "_expected_value": expected_value,
        "_ev_per_hour": ev_per_hour,
        "_probability_text": _decimal_text(probability),
        "_effort_text": _decimal_text(effort),
    }


def _validate_request(candidates: Any, as_of: Any) -> tuple[list[Any], str]:
    if type(candidates) is not list:
        raise ExternalContractRankInputError("candidates must be a list")
    if len(candidates) > _MAX_CANDIDATES:
        raise ExternalContractRankInputError("candidate count exceeds supported boundary")
    as_of_text = _text(as_of, "as_of", limit=40)
    try:
        parsed = datetime.fromisoformat(as_of_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExternalContractRankInputError("as_of must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ExternalContractRankInputError("as_of must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if as_of_text != canonical:
        raise ExternalContractRankInputError("as_of must be canonical second-precision UTC")
    return candidates, canonical


def _rank_external_contracts_at(candidates: list[dict[str, Any]], *, as_of: str) -> dict[str, Any]:
    """Internal deterministic evaluator at an already-trusted UTC instant."""
    candidates, canonical_as_of = _validate_request(candidates, as_of)
    verified_identities: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    excluded: list[dict[str, Any]] = []
    source_occurrences: dict[str, list[int]] = {}

    for index, candidate in enumerate(candidates):
        if type(candidate) is not dict or set(candidate) != _CANDIDATE_KEYS:
            excluded.append(_excluded(index, "CANDIDATE_SHAPE_INVALID"))
            continue
        try:
            identity = _verified_identity(candidate, as_of=canonical_as_of)
        except ExternalContractRankInputError:
            excluded.append(_excluded(index, "QUALIFICATION_INVALID_OR_STALE"))
            continue
        source = identity["canonical_source_url"]
        # Record source before estimate validation so a malformed duplicate cannot
        # allow a valid sibling to survive into ranking.
        source_occurrences.setdefault(source, []).append(index)
        verified_identities.append((index, identity, candidate))

    duplicate_sources = {source for source, indices in source_occurrences.items() if len(indices) > 1}
    scored: list[dict[str, Any]] = []
    for index, identity, candidate in verified_identities:
        source = identity["canonical_source_url"]
        if source in duplicate_sources:
            excluded.append(_excluded(index, "DUPLICATE_CANONICAL_SOURCE", source=source))
            continue
        try:
            scored.append(_score(index, identity, candidate))
        except ExternalContractRankInputError:
            excluded.append(_excluded(index, "ESTIMATE_INVALID", source=source))

    by_currency: dict[str, list[dict[str, Any]]] = {}
    for row in scored:
        by_currency.setdefault(row["currency"], []).append(row)

    partitions: dict[str, dict[str, Any]] = {}
    total_ranked = 0
    for currency in sorted(by_currency):
        rows = by_currency[currency]
        rows.sort(
            key=lambda item: (
                -item["_ev_per_hour"],
                -item["_expected_value"],
                -item["_probability"],
                -item["_reward"],
                item["_effort"],
                item["canonical_source_url"],
                item["qualification_digest"],
                item["input_index"],
            )
        )
        ranked: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            public = {
                "rank": rank,
                "input_index": row["input_index"],
                "canonical_source_url": row["canonical_source_url"],
                "source_digest": row["source_digest"],
                "qualification_digest": row["qualification_digest"],
                "currency": currency,
                "proposed_bid_amount": _fraction_decimal(row["_reward"]),
                "delivery_days": row["delivery_days"],
                "estimated_award_probability": row["_probability_text"],
                "estimated_effort_hours": row["_effort_text"],
                "estimated_expected_value": _fraction_decimal(row["_expected_value"]),
                "estimated_ev_per_hour": _fraction_decimal(row["_ev_per_hour"]),
                "authority": {
                    "eligibility": "verified_external_contract_qualification",
                    "bid": "proposed_not_awarded",
                    "probability_and_effort": "operator_estimates",
                    "fx_conversion": False,
                    "marketplace_submission": False,
                    "contract_acceptance": False,
                    "work_awarded": False,
                    "payment_received": False,
                    "revenue_recognized": False,
                    "spend": False,
                },
            }
            ranked.append(public)
        total_ranked += len(ranked)
        partitions[currency] = {"currency": currency, "ranked_count": len(ranked), "ranked": ranked}

    excluded.sort(key=lambda row: (row["input_index"], row["reason_code"]))
    digest_projection = {
        "schema_version": SCHEMA_VERSION,
        "as_of": canonical_as_of,
        "partitions": partitions,
        "excluded": excluded,
        "candidate_count": len(candidates),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": canonical_as_of,
        "candidate_count": len(candidates),
        "ranked_count": total_ranked,
        "excluded_count": len(excluded),
        "partitions": partitions,
        "excluded": excluded,
        "global_winner": None,
        "ranking_basis_within_currency": [
            "estimated_ev_per_hour",
            "estimated_expected_value",
            "estimated_award_probability",
            "proposed_bid_amount",
            "estimated_effort_hours",
            "canonical_source_url",
        ],
        "ranking_digest": _sha(digest_projection),
        "authority": {
            "eligibility": "verified_external_contract_qualification",
            "bid": "proposed_not_awarded",
            "estimates": "operator_supplied",
            "fx_conversion": False,
            "cross_currency_ranking": False,
            "marketplace_submission": False,
            "terms_acceptance": False,
            "contract_acceptance": False,
            "work_awarded": False,
            "payment_received": False,
            "revenue_recognized": False,
            "spend": False,
        },
    }


def rank_external_contracts(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank contracts after verification at trusted current UTC.

    The public authority-producing API intentionally accepts no caller-supplied
    timestamp. Historical replay must not be able to resurrect stale or closed
    qualification as current prioritization authority.
    """
    return _rank_external_contracts_at(candidates, as_of=_current_utc())


def _read_bounded_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    remaining = _MAX_JSON_BYTES + 1
    while remaining > 0:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > _MAX_JSON_BYTES:
        raise ExternalContractRankInputError("request JSON exceeds size limit")
    return payload


def _read_request(path: str) -> dict[str, Any]:
    if path == "-":
        payload = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
        if len(payload) > _MAX_JSON_BYTES:
            raise ExternalContractRankInputError("request JSON exceeds size limit")
    else:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(Path(path), flags)
        except OSError as exc:
            raise ExternalContractRankInputError("request file cannot be opened safely") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ExternalContractRankInputError("request path must be a regular file")
            if info.st_size > _MAX_JSON_BYTES:
                raise ExternalContractRankInputError("request JSON exceeds size limit")
            payload = _read_bounded_fd(fd)
        finally:
            os.close(fd)
    value = loads_strict_json(payload)
    if type(value) is not dict or set(value) != {"candidates"}:
        raise ExternalContractRankInputError("request must contain exactly one candidates field")
    return value


def _current_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_summary(result: dict[str, Any]) -> str:
    parts = result.get("partitions")
    if type(parts) is not dict:
        raise ExternalContractRankInputError("ranking result partitions are missing")
    counts = ",".join(f"{currency}:{parts[currency]['ranked_count']}" for currency in sorted(parts)) or "none"
    return (
        f"ranked={result['ranked_count']} excluded={result['excluded_count']} "
        f"partitions={counts} global_winner=none fx_conversion=false"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.external_contract_ranker")
    parser.add_argument("request", help="strict JSON request file, or - for bounded stdin")
    parser.add_argument("--json", action="store_true", help="emit the complete ranking receipt")
    args = parser.parse_args(argv)
    try:
        request = _read_request(args.request)
        result = rank_external_contracts(request["candidates"])
    except ExternalContractRankInputError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
