# SPDX-License-Identifier: MIT
"""Evidence-bound owner reinvestment review from realized unit economics.

This module closes a deliberate loop between Bounty Concierge's realized cash
proof and the owner's next-cycle capacity review.  It never treats advertised
rewards as cash, predicts future winnings, claims work, submits work, spends,
or contacts an external party.  The strongest output is an owner-review
recommendation over work *families* that have enough verified realized history.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


_SOURCE_SCHEMA_VERSION = 1
_TAXONOMY_SCHEMA = "realized-reinvestment-taxonomy/v1"
_POLICY_SCHEMA = "realized-reinvestment-policy/v1"
_RECEIPT_SCHEMA = "realized-reinvestment-review/v1"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_FAMILIES = 1_000
_MAX_CAPACITY_MINUTES = 525_600
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FAMILY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_STATES = frozenset({"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"})
_ALLOWED_CASH_STATUS = frozenset({"verified_paid", "partially_verified", "not_inferred"})
_SOURCE_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "wallet",
        "history_source",
        "scope_sha256",
        "summary",
        "ranking",
        "items",
        "receipt_sha256",
    }
)
_SOURCE_SUMMARY_KEYS = frozenset(
    {
        "currency",
        "verified_cash_total",
        "active_minutes_total",
        "realized_rtc_per_hour_estimate",
        "fully_paid_items",
        "partially_paid_items",
        "zero_verified_cash_items",
        "item_count",
        "scope_complete",
        "cash_basis",
        "effort_basis",
        "fx_conversion",
        "accounting_revenue_claim",
        "tax_claim",
        "payout_or_transfer_authority",
    }
)
_SOURCE_ITEM_KEYS = frozenset(
    {
        "repo",
        "pr",
        "state",
        "cash_status",
        "verified_cash_rtc",
        "active_minutes",
        "realized_rtc_per_hour_estimate",
        "payment_evidence_sha256s",
    }
)
_SOURCE_RANKING_KEYS = frozenset(
    {"rank", "repo", "pr", "realized_rtc_per_hour_estimate"}
)
_TAXONOMY_KEYS = frozenset({"schema", "version", "mappings", "taxonomy_sha256"})
_MAPPING_KEYS = frozenset({"repo", "pr", "family"})
_POLICY_KEYS = frozenset(
    {
        "schema",
        "version",
        "minimum_samples",
        "minimum_nonzero_cash_samples",
        "minimum_median_rtc_per_hour",
        "maximum_family_capacity_bps",
        "policy_sha256",
    }
)


class ReinvestmentInputError(ValueError):
    """Raised when source evidence or owner policy cannot be trusted."""


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReinvestmentInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise ReinvestmentInputError("canonical JSON value is too large")
    return payload


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_keys(value: Any, expected: Iterable[str], name: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise ReinvestmentInputError(f"{name} must be an object")
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise ReinvestmentInputError(
            f"{name} must have exact keys" + (": " + "; ".join(detail) if detail else "")
        )
    return value


def _positive_int(value: Any, field: str, *, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ReinvestmentInputError(f"{field} must be an integer in 1..{maximum}")
    return value


def _nonnegative_int(value: Any, field: str, *, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise ReinvestmentInputError(f"{field} must be an integer in 0..{maximum}")
    return value


def _repo_pr(repo: Any, pr: Any, *, field: str) -> Tuple[str, int]:
    if type(repo) is not str or not _REPO_RE.fullmatch(repo):
        raise ReinvestmentInputError(f"{field}.repo must be in owner/name form")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ReinvestmentInputError(f"{field}.repo contains a forbidden path segment")
    number = _positive_int(pr, f"{field}.pr", maximum=2_147_483_647)
    return repo, number


def _decimal(value: Any, field: str, *, nonnegative: bool = True) -> Decimal:
    if type(value) is not str or not value or value != value.strip() or len(value) > 96:
        raise ReinvestmentInputError(f"{field} must be a canonical decimal string")
    if "e" in value.lower() or value.startswith("+"):
        raise ReinvestmentInputError(f"{field} must use non-exponent fixed-point form")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ReinvestmentInputError(f"{field} must be a finite decimal") from exc
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or len(result.as_tuple().digits) > 60
        or not isinstance(exponent, int)
        or abs(exponent) > 36
        or (nonnegative and result < 0)
    ):
        raise ReinvestmentInputError(f"{field} is outside bounded decimal form")
    if _amount_text(result) != value:
        raise ReinvestmentInputError(f"{field} must use canonical fixed-point form")
    return result


def _amount_text(amount: Decimal) -> str:
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _fraction_decimal_text(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 36
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _rate(cash: Decimal, minutes: int) -> Fraction:
    return Fraction(cash) * 60 / minutes


def _rate_text(cash: Decimal, minutes: int) -> str:
    return _fraction_decimal_text(_rate(cash, minutes))


def _identity_key(repo: str, pr: int) -> Tuple[str, int]:
    return repo.casefold(), pr


def _verify_digest_envelope(value: Dict[str, Any], digest_field: str, name: str) -> str:
    digest = value.get(digest_field)
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        raise ReinvestmentInputError(f"{name}.{digest_field} must be lowercase SHA-256")
    body = dict(value)
    body.pop(digest_field, None)
    if _sha256(body) != digest:
        raise ReinvestmentInputError(f"{name} digest does not match canonical content")
    return digest


def _normalize_source(receipt: Any) -> List[Dict[str, Any]]:
    source = _strict_keys(receipt, _SOURCE_ROOT_KEYS, "realized receipt")
    if source["schema_version"] != _SOURCE_SCHEMA_VERSION or type(source["schema_version"]) is not int:
        raise ReinvestmentInputError("realized receipt schema_version must be integer 1")
    wallet = source["wallet"]
    if (
        type(wallet) is not str
        or not wallet
        or wallet != wallet.strip()
        or any(char.isspace() or not char.isprintable() for char in wallet)
        or len(wallet) > 256
    ):
        raise ReinvestmentInputError("realized receipt wallet is invalid")
    if source["history_source"] not in {"queried_wallet", "captured_wallet"}:
        raise ReinvestmentInputError("realized receipt history_source is invalid")
    if type(source["scope_sha256"]) is not str or not _SHA256_RE.fullmatch(source["scope_sha256"]):
        raise ReinvestmentInputError("realized receipt scope_sha256 is invalid")
    _verify_digest_envelope(source, "receipt_sha256", "realized receipt")

    summary = _strict_keys(source["summary"], _SOURCE_SUMMARY_KEYS, "realized receipt summary")
    expected_constants = {
        "currency": "RTC",
        "scope_complete": True,
        "cash_basis": "revenue_settlement_wallet_evidence",
        "effort_basis": "operator_active_minutes",
        "fx_conversion": False,
        "accounting_revenue_claim": False,
        "tax_claim": False,
        "payout_or_transfer_authority": False,
    }
    for key, expected in expected_constants.items():
        if type(summary.get(key)) is not type(expected) or summary.get(key) != expected:
            raise ReinvestmentInputError(f"realized receipt summary.{key} is invalid")

    items = source["items"]
    if type(items) is not list or not items or len(items) > _MAX_ITEMS:
        raise ReinvestmentInputError("realized receipt items must be a non-empty bounded list")

    normalized: List[Dict[str, Any]] = []
    seen: set = set()
    evidence_seen: set = set()
    for index, raw in enumerate(items):
        item = _strict_keys(raw, _SOURCE_ITEM_KEYS, f"realized receipt items[{index}]")
        repo, pr = _repo_pr(item["repo"], item["pr"], field=f"realized receipt items[{index}]")
        identity = _identity_key(repo, pr)
        if identity in seen:
            raise ReinvestmentInputError(f"duplicate realized identity: {repo}#{pr}")
        seen.add(identity)
        state = item["state"]
        status = item["cash_status"]
        if type(state) is not str or state not in _ALLOWED_STATES:
            raise ReinvestmentInputError(f"{repo}#{pr} has invalid closeout state")
        if type(status) is not str or status not in _ALLOWED_CASH_STATUS:
            raise ReinvestmentInputError(f"{repo}#{pr} has invalid cash_status")
        cash = _decimal(item["verified_cash_rtc"], f"{repo}#{pr}.verified_cash_rtc")
        minutes = _positive_int(item["active_minutes"], f"{repo}#{pr}.active_minutes", maximum=525_600)
        expected_rate = _rate_text(cash, minutes)
        if type(item["realized_rtc_per_hour_estimate"]) is not str or item["realized_rtc_per_hour_estimate"] != expected_rate:
            raise ReinvestmentInputError(f"{repo}#{pr} realized rate disagrees with cash/minutes")
        evidence = item["payment_evidence_sha256s"]
        if type(evidence) is not list or len(evidence) > 1_000:
            raise ReinvestmentInputError(f"{repo}#{pr} payment evidence must be a bounded list")
        local_evidence = set()
        for digest in evidence:
            if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
                raise ReinvestmentInputError(f"{repo}#{pr} contains invalid payment evidence digest")
            if digest in local_evidence or digest in evidence_seen:
                raise ReinvestmentInputError("payment evidence digest is duplicated or cross-used")
            local_evidence.add(digest)
            evidence_seen.add(digest)
        if status == "not_inferred":
            if cash != 0 or evidence:
                raise ReinvestmentInputError(f"{repo}#{pr} not_inferred must have zero cash and no evidence")
        else:
            if state != "MERGED" or cash <= 0 or not evidence:
                raise ReinvestmentInputError(f"{repo}#{pr} paid/partial evidence is internally inconsistent")
        normalized.append(
            {
                "repo": repo,
                "pr": pr,
                "state": state,
                "cash_status": status,
                "verified_cash_rtc": _amount_text(cash),
                "active_minutes": minutes,
                "realized_rtc_per_hour_estimate": expected_rate,
                "payment_evidence_sha256s": list(evidence),
                "_cash": cash,
                "_rate": _rate(cash, minutes),
            }
        )

    expected_order = sorted(normalized, key=lambda row: _identity_key(row["repo"], row["pr"]))
    if [(_identity_key(row["repo"], row["pr"])) for row in normalized] != [
        _identity_key(row["repo"], row["pr"]) for row in expected_order
    ]:
        raise ReinvestmentInputError("realized receipt items are not in canonical identity order")

    total_cash = sum((row["_cash"] for row in normalized), Decimal("0"))
    total_minutes = sum(row["active_minutes"] for row in normalized)
    paid = sum(row["cash_status"] == "verified_paid" for row in normalized)
    partial = sum(row["cash_status"] == "partially_verified" for row in normalized)
    zero = sum(row["cash_status"] == "not_inferred" for row in normalized)
    expected_summary = {
        "verified_cash_total": _amount_text(total_cash),
        "active_minutes_total": total_minutes,
        "realized_rtc_per_hour_estimate": _rate_text(total_cash, total_minutes),
        "fully_paid_items": paid,
        "partially_paid_items": partial,
        "zero_verified_cash_items": zero,
        "item_count": len(normalized),
    }
    for key, expected in expected_summary.items():
        if type(summary.get(key)) is not type(expected) or summary.get(key) != expected:
            raise ReinvestmentInputError(f"realized receipt summary.{key} disagrees with items")

    cash_scope = [
        {
            "repo": row["repo"],
            "pr": row["pr"],
            "state": row["state"],
            "cash_status": row["cash_status"],
            "verified_cash_rtc": row["verified_cash_rtc"],
            "payment_evidence_sha256s": row["payment_evidence_sha256s"],
        }
        for row in normalized
    ]
    effort_scope = [
        {"repo": row["repo"], "pr": row["pr"], "active_minutes": row["active_minutes"]}
        for row in normalized
    ]
    if _sha256({"cash": cash_scope, "effort": effort_scope}) != source["scope_sha256"]:
        raise ReinvestmentInputError("realized receipt scope_sha256 disagrees with items")

    ranking = source["ranking"]
    if type(ranking) is not list or len(ranking) != len(normalized):
        raise ReinvestmentInputError("realized receipt ranking must cover every item exactly once")
    ranked = sorted(
        normalized,
        key=lambda row: (
            -row["_rate"],
            row["repo"].casefold(),
            row["pr"],
        ),
    )
    for position, raw in enumerate(ranking, 1):
        rank_row = _strict_keys(raw, _SOURCE_RANKING_KEYS, f"realized receipt ranking[{position - 1}]")
        expected = ranked[position - 1]
        if (
            type(rank_row["rank"]) is not int
            or rank_row["rank"] != position
            or rank_row["repo"] != expected["repo"]
            or rank_row["pr"] != expected["pr"]
            or rank_row["realized_rtc_per_hour_estimate"] != expected["realized_rtc_per_hour_estimate"]
        ):
            raise ReinvestmentInputError("realized receipt ranking disagrees with item economics")

    return normalized


def _normalize_taxonomy(taxonomy: Any, source_items: List[Dict[str, Any]]) -> Tuple[Dict[Tuple[str, int], str], str]:
    payload = _strict_keys(taxonomy, _TAXONOMY_KEYS, "taxonomy")
    if payload["schema"] != _TAXONOMY_SCHEMA:
        raise ReinvestmentInputError("taxonomy schema is invalid")
    _positive_int(payload["version"], "taxonomy.version", maximum=2_147_483_647)
    digest = _verify_digest_envelope(payload, "taxonomy_sha256", "taxonomy")
    mappings = payload["mappings"]
    if type(mappings) is not list or not mappings or len(mappings) > _MAX_ITEMS:
        raise ReinvestmentInputError("taxonomy.mappings must be a non-empty bounded list")
    result: Dict[Tuple[str, int], str] = {}
    families = set()
    for index, raw in enumerate(mappings):
        mapping = _strict_keys(raw, _MAPPING_KEYS, f"taxonomy.mappings[{index}]")
        repo, pr = _repo_pr(mapping["repo"], mapping["pr"], field=f"taxonomy.mappings[{index}]")
        family = mapping["family"]
        if type(family) is not str or not _FAMILY_RE.fullmatch(family):
            raise ReinvestmentInputError("taxonomy family must be a bounded lowercase slug")
        identity = _identity_key(repo, pr)
        if identity in result:
            raise ReinvestmentInputError(f"duplicate taxonomy mapping: {repo}#{pr}")
        result[identity] = family
        families.add(family)
    if len(families) > _MAX_FAMILIES:
        raise ReinvestmentInputError("taxonomy contains too many work families")
    expected = {_identity_key(row["repo"], row["pr"]) for row in source_items}
    if set(result) != expected:
        missing = sorted(expected - set(result))
        extra = sorted(set(result) - expected)
        detail = ""
        if missing:
            detail += f" missing={missing[0][0]}#{missing[0][1]}"
        if extra:
            detail += f" extra={extra[0][0]}#{extra[0][1]}"
        raise ReinvestmentInputError("taxonomy must exactly cover realized source scope" + detail)
    canonical_mappings = sorted(
        mappings,
        key=lambda row: (_identity_key(row["repo"], row["pr"]), row["family"]),
    )
    if mappings != canonical_mappings:
        raise ReinvestmentInputError("taxonomy mappings are not in canonical identity order")
    return result, digest


def _normalize_policy(policy: Any) -> Tuple[Dict[str, Any], str]:
    payload = _strict_keys(policy, _POLICY_KEYS, "policy")
    if payload["schema"] != _POLICY_SCHEMA:
        raise ReinvestmentInputError("policy schema is invalid")
    _positive_int(payload["version"], "policy.version", maximum=2_147_483_647)
    minimum_samples = _positive_int(payload["minimum_samples"], "policy.minimum_samples", maximum=_MAX_ITEMS)
    minimum_nonzero = _nonnegative_int(
        payload["minimum_nonzero_cash_samples"],
        "policy.minimum_nonzero_cash_samples",
        maximum=_MAX_ITEMS,
    )
    if minimum_nonzero > minimum_samples:
        raise ReinvestmentInputError("minimum_nonzero_cash_samples cannot exceed minimum_samples")
    threshold = _decimal(
        payload["minimum_median_rtc_per_hour"],
        "policy.minimum_median_rtc_per_hour",
    )
    cap_bps = _positive_int(
        payload["maximum_family_capacity_bps"],
        "policy.maximum_family_capacity_bps",
        maximum=10_000,
    )
    digest = _verify_digest_envelope(payload, "policy_sha256", "policy")
    return {
        "minimum_samples": minimum_samples,
        "minimum_nonzero_cash_samples": minimum_nonzero,
        "minimum_median_rtc_per_hour": threshold,
        "maximum_family_capacity_bps": cap_bps,
    }, digest


def _median(values: List[Fraction]) -> Fraction:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _family_rows(
    source_items: List[Dict[str, Any]],
    mapping: Dict[Tuple[str, int], str],
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in source_items:
        family = mapping[_identity_key(row["repo"], row["pr"])]
        buckets.setdefault(family, []).append(row)

    rows: List[Dict[str, Any]] = []
    threshold = Fraction(policy["minimum_median_rtc_per_hour"])
    for family in sorted(buckets):
        items = buckets[family]
        total_cash = sum((row["_cash"] for row in items), Decimal("0"))
        total_minutes = sum(row["active_minutes"] for row in items)
        rates = [row["_rate"] for row in items]
        median_rate = _median(rates)
        worst_rate = min(rates)
        aggregate_rate = _rate(total_cash, total_minutes)
        nonzero = sum(row["_cash"] > 0 for row in items)
        paid = sum(row["cash_status"] == "verified_paid" for row in items)
        partial = sum(row["cash_status"] == "partially_verified" for row in items)
        zero = sum(row["cash_status"] == "not_inferred" for row in items)
        reasons: List[str] = []
        if len(items) < policy["minimum_samples"]:
            reasons.append("sample_count_below_minimum")
        if nonzero < policy["minimum_nonzero_cash_samples"]:
            reasons.append("nonzero_cash_samples_below_minimum")
        if reasons:
            state = "LEARN_MORE"
        elif total_cash == 0 or median_rate < threshold:
            state = "DEPRIORITIZE_REVIEW"
            if total_cash == 0:
                reasons.append("zero_total_verified_cash")
            if median_rate < threshold:
                reasons.append("median_realized_yield_below_minimum")
        else:
            state = "SCALE_REVIEW_ELIGIBLE"

        weight = aggregate_rate * len(items) if state == "SCALE_REVIEW_ELIGIBLE" else Fraction(0)
        rows.append(
            {
                "family": family,
                "state": state,
                "reasons": reasons,
                "sample_count": len(items),
                "verified_paid_items": paid,
                "partially_paid_items": partial,
                "zero_verified_cash_items": zero,
                "nonzero_cash_samples": nonzero,
                "nonzero_cash_share": f"{nonzero}/{len(items)}",
                "verified_cash_rtc": _amount_text(total_cash),
                "active_minutes": total_minutes,
                "aggregate_realized_rtc_per_hour_estimate": _fraction_decimal_text(aggregate_rate),
                "median_realized_rtc_per_hour_estimate": _fraction_decimal_text(median_rate),
                "worst_realized_rtc_per_hour_estimate": _fraction_decimal_text(worst_rate),
                "allocation_weight_fraction": f"{weight.numerator}/{weight.denominator}",
                "source_items": [
                    {
                        "repo": row["repo"],
                        "pr": row["pr"],
                        "cash_status": row["cash_status"],
                        "verified_cash_rtc": row["verified_cash_rtc"],
                        "active_minutes": row["active_minutes"],
                    }
                    for row in items
                ],
                "_weight": weight,
            }
        )
    return rows


def _capped_apportion(
    family_rows: List[Dict[str, Any]],
    capacity_minutes: int,
    maximum_family_capacity_bps: int,
) -> Tuple[Dict[str, int], int]:
    eligible = [row for row in family_rows if row["state"] == "SCALE_REVIEW_ELIGIBLE" and row["_weight"] > 0]
    assignments = {row["family"]: 0 for row in eligible}
    if not eligible:
        return assignments, capacity_minutes

    cap = (capacity_minutes * maximum_family_capacity_bps) // 10_000
    if cap <= 0:
        return assignments, capacity_minutes
    caps = {row["family"]: cap for row in eligible}
    weights = {row["family"]: row["_weight"] for row in eligible}
    active = set(weights)
    remaining = capacity_minutes

    while active and remaining > 0:
        total_weight = sum((weights[name] for name in active), Fraction(0))
        if total_weight <= 0:
            break
        raw = {name: Fraction(remaining) * weights[name] / total_weight for name in active}
        saturated = sorted(
            name
            for name in active
            if raw[name] >= caps[name] - assignments[name]
        )
        if saturated:
            for name in saturated:
                room = caps[name] - assignments[name]
                if room > 0:
                    assignments[name] += room
                    remaining -= room
                active.remove(name)
            continue

        floors: Dict[str, int] = {}
        for name in active:
            amount = raw[name].numerator // raw[name].denominator
            room = caps[name] - assignments[name]
            floors[name] = min(amount, room)
        for name, amount in floors.items():
            assignments[name] += amount
            remaining -= amount

        if remaining <= 0:
            break
        remainder_order = sorted(
            active,
            key=lambda name: (
                -(raw[name] - (raw[name].numerator // raw[name].denominator)),
                name,
            ),
        )
        progressed = False
        for name in remainder_order:
            if remaining <= 0:
                break
            if assignments[name] < caps[name]:
                assignments[name] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
        if all(assignments[name] >= caps[name] for name in active):
            break

    return assignments, remaining


def compile_reinvestment_review(
    realized_receipt: Any,
    taxonomy: Any,
    policy: Any,
    capacity_minutes: Any,
) -> Dict[str, Any]:
    """Compile a deterministic owner-review reinvestment receipt."""
    capacity = _positive_int(
        capacity_minutes,
        "capacity_minutes",
        maximum=_MAX_CAPACITY_MINUTES,
    )
    source_items = _normalize_source(realized_receipt)
    mapping, taxonomy_digest = _normalize_taxonomy(taxonomy, source_items)
    normalized_policy, policy_digest = _normalize_policy(policy)
    families = _family_rows(source_items, mapping, normalized_policy)
    assignments, unallocated = _capped_apportion(
        families,
        capacity,
        normalized_policy["maximum_family_capacity_bps"],
    )

    public_families: List[Dict[str, Any]] = []
    for row in families:
        public = {key: value for key, value in row.items() if key != "_weight"}
        public["recommended_review_capacity_minutes"] = assignments.get(row["family"], 0)
        public_families.append(public)

    eligible_count = sum(row["state"] == "SCALE_REVIEW_ELIGIBLE" for row in families)
    recommended = capacity - unallocated
    payload: Dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        "source_receipt_sha256": realized_receipt["receipt_sha256"],
        "source_scope_sha256": realized_receipt["scope_sha256"],
        "taxonomy_sha256": taxonomy_digest,
        "policy_sha256": policy_digest,
        "capacity_minutes": capacity,
        "summary": {
            "family_count": len(families),
            "scale_review_eligible_families": eligible_count,
            "recommended_review_capacity_minutes": recommended,
            "unallocated_capacity_minutes": unallocated,
            "review_state": (
                "READY_FOR_OWNER_REINVESTMENT_REVIEW"
                if eligible_count > 0
                else "NO_SCALE_REVIEW_ELIGIBLE"
            ),
            "evidence_basis": "verified_realized_rtc_settlement_plus_operator_active_minutes",
            "allocation_basis": "capped_realized_yield_times_sample_count",
            "advertised_reward_used_as_cash": False,
            "future_revenue_claimed": False,
            "guaranteed_return_claimed": False,
            "autonomous_claim_authorized": False,
            "submission_authorized": False,
            "spend_authorized": False,
            "external_contact_authorized": False,
        },
        "families": public_families,
    }
    payload["receipt_sha256"] = _sha256(payload)
    return payload


def verify_reinvestment_receipt(
    receipt: Any,
    realized_receipt: Any,
    taxonomy: Any,
    policy: Any,
    capacity_minutes: Any,
) -> bool:
    """Recompile exact inputs and reject any receipt or input drift."""
    if type(receipt) is not dict:
        return False
    try:
        expected = compile_reinvestment_review(
            realized_receipt,
            taxonomy,
            policy,
            capacity_minutes,
        )
        return _canonical_json(receipt) == _canonical_json(expected)
    except ReinvestmentInputError:
        return False


def _strict_json(path: str) -> Any:
    source = Path(path)
    try:
        if not source.is_file() or source.stat().st_size > _MAX_JSON_BYTES:
            raise ReinvestmentInputError(f"{path} must be a bounded ordinary JSON file")

        def unique_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ReinvestmentInputError(f"{path} contains duplicate JSON key {key!r}")
                result[key] = value
            return result

        return json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except ReinvestmentInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReinvestmentInputError(f"{path} is not valid bounded UTF-8 JSON") from exc


def _write_output(path: Optional[str], payload: Dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(text, end="")
        return
    destination = Path(path)
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ReinvestmentInputError(f"refusing to overwrite existing output: {path}") from exc
    except OSError as exc:
        raise ReinvestmentInputError(f"cannot create output {path}: {exc}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.reinvestment_allocator",
        description="Compile or verify evidence-bound realized-outcome reinvestment review.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile")
    compile_parser.add_argument("source")
    compile_parser.add_argument("taxonomy")
    compile_parser.add_argument("policy")
    compile_parser.add_argument("--capacity-minutes", required=True, type=int)
    compile_parser.add_argument("--output")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("receipt")
    verify_parser.add_argument("source")
    verify_parser.add_argument("taxonomy")
    verify_parser.add_argument("policy")
    verify_parser.add_argument("--capacity-minutes", required=True, type=int)

    args = parser.parse_args(argv)
    try:
        source = _strict_json(args.source)
        taxonomy = _strict_json(args.taxonomy)
        policy = _strict_json(args.policy)
        if args.command == "compile":
            payload = compile_reinvestment_review(
                source, taxonomy, policy, args.capacity_minutes
            )
            _write_output(args.output, payload)
            return 0
        receipt = _strict_json(args.receipt)
        if not verify_reinvestment_receipt(
            receipt, source, taxonomy, policy, args.capacity_minutes
        ):
            raise ReinvestmentInputError("receipt verification failed")
        print(receipt["receipt_sha256"])
        return 0
    except ReinvestmentInputError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
