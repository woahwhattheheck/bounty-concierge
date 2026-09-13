# SPDX-License-Identifier: MIT
"""Live-provider-grounded owner reinvestment review.

The public authority path never accepts a standalone realized-economics receipt or
captured wallet history. It freezes caller-owned scope/planning inputs, reacquires
current GitHub closeout state and canonical wallet history in-process, compiles
realized unit economics through the landed settlement boundary, and only then
derives an advisory next-cycle capacity review.

No output authorizes a claim, submission, contact, spend, payment mutation,
accounting/tax conclusion, future-revenue claim, or guaranteed return.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import json
import os
import re
import stat
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

from concierge import realized_unit_economics as rue
from concierge import revenue_closeout as closeout
from concierge import revenue_settlement as settlement


_SOURCE_SCHEMA_VERSION = 1
_TAXONOMY_SCHEMA = "realized-reinvestment-taxonomy/v1"
_POLICY_SCHEMA = "realized-reinvestment-policy/v1"
_RECEIPT_SCHEMA = "realized-reinvestment-review/v2"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_FAMILIES = 1_000
_MAX_CAPACITY_MINUTES = 525_600
_MAX_CLOSEOUT_PAGES = 100
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FAMILY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_STATES = frozenset({"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"})
_ALLOWED_CASH_STATUS = frozenset({"verified_paid", "partially_verified", "not_inferred"})
_SOURCE_ROOT_KEYS = frozenset({"schema_version", "wallet", "history_source", "scope_sha256", "summary", "ranking", "items", "receipt_sha256"})
_SOURCE_SUMMARY_KEYS = frozenset({"currency", "verified_cash_total", "active_minutes_total", "realized_rtc_per_hour_estimate", "fully_paid_items", "partially_paid_items", "zero_verified_cash_items", "item_count", "scope_complete", "cash_basis", "effort_basis", "fx_conversion", "accounting_revenue_claim", "tax_claim", "payout_or_transfer_authority"})
_SOURCE_ITEM_KEYS = frozenset({"repo", "pr", "state", "cash_status", "verified_cash_rtc", "active_minutes", "realized_rtc_per_hour_estimate", "payment_evidence_sha256s"})
_SOURCE_RANKING_KEYS = frozenset({"rank", "repo", "pr", "realized_rtc_per_hour_estimate"})
_TAXONOMY_KEYS = frozenset({"schema", "version", "mappings", "taxonomy_sha256"})
_MAPPING_KEYS = frozenset({"repo", "pr", "family"})
_POLICY_KEYS = frozenset({"schema", "version", "minimum_samples", "minimum_nonzero_cash_samples", "minimum_median_rtc_per_hour", "maximum_family_capacity_bps", "policy_sha256"})


class ReinvestmentInputError(ValueError):
    """Malformed, inconsistent, or insufficiently authoritative review input."""


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ReinvestmentInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise ReinvestmentInputError("canonical JSON value is too large")
    return payload


def _snapshot_json(value: Any, label: str) -> Any:
    payload = _canonical_json(value)
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReinvestmentInputError(f"{label} could not be frozen") from exc


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
        suffix = ": " + "; ".join(detail) if detail else ""
        raise ReinvestmentInputError(f"{name} must have exact keys{suffix}")
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
    return repo, _positive_int(pr, f"{field}.pr", maximum=2_147_483_647)


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
    if not result.is_finite() or len(result.as_tuple().digits) > 60 or not isinstance(exponent, int) or abs(exponent) > 36 or (nonnegative and result < 0):
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


def _normalize_live_economics(receipt: Any, *, expected_wallet: str) -> List[Dict[str, Any]]:
    source = _strict_keys(receipt, _SOURCE_ROOT_KEYS, "realized economics")
    if type(source["schema_version"]) is not int or source["schema_version"] != _SOURCE_SCHEMA_VERSION:
        raise ReinvestmentInputError("realized economics schema_version must be integer 1")
    if source["wallet"] != expected_wallet or type(source["wallet"]) is not str:
        raise ReinvestmentInputError("realized economics wallet disagrees with requested wallet")
    if source["history_source"] != "queried_wallet":
        raise ReinvestmentInputError("production reinvestment review requires queried_wallet economics")
    if type(source["scope_sha256"]) is not str or not _SHA256_RE.fullmatch(source["scope_sha256"]):
        raise ReinvestmentInputError("realized economics scope_sha256 is invalid")
    _verify_digest_envelope(source, "receipt_sha256", "realized economics")
    summary = _strict_keys(source["summary"], _SOURCE_SUMMARY_KEYS, "realized economics summary")
    expected_constants = {"currency": "RTC", "scope_complete": True, "cash_basis": "revenue_settlement_wallet_evidence", "effort_basis": "operator_active_minutes", "fx_conversion": False, "accounting_revenue_claim": False, "tax_claim": False, "payout_or_transfer_authority": False}
    for key, expected in expected_constants.items():
        if type(summary.get(key)) is not type(expected) or summary.get(key) != expected:
            raise ReinvestmentInputError(f"realized economics summary.{key} is invalid")
    items = source["items"]
    if type(items) is not list or not items or len(items) > _MAX_ITEMS:
        raise ReinvestmentInputError("realized economics items must be a non-empty bounded list")
    normalized: List[Dict[str, Any]] = []
    seen = set()
    evidence_seen = set()
    for index, raw in enumerate(items):
        item = _strict_keys(raw, _SOURCE_ITEM_KEYS, f"realized economics items[{index}]")
        repo, pr = _repo_pr(item["repo"], item["pr"], field=f"realized economics items[{index}]")
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
        elif state != "MERGED" or cash <= 0 or not evidence:
            raise ReinvestmentInputError(f"{repo}#{pr} paid/partial evidence is internally inconsistent")
        normalized.append({"repo": repo, "pr": pr, "state": state, "cash_status": status, "verified_cash_rtc": _amount_text(cash), "active_minutes": minutes, "realized_rtc_per_hour_estimate": expected_rate, "payment_evidence_sha256s": list(evidence), "_cash": cash, "_rate": _rate(cash, minutes)})
    expected_order = sorted(normalized, key=lambda row: _identity_key(row["repo"], row["pr"]))
    if [_identity_key(row["repo"], row["pr"]) for row in normalized] != [_identity_key(row["repo"], row["pr"]) for row in expected_order]:
        raise ReinvestmentInputError("realized economics items are not in canonical identity order")
    total_cash = sum((row["_cash"] for row in normalized), Decimal("0"))
    total_minutes = sum(row["active_minutes"] for row in normalized)
    expected_summary = {"verified_cash_total": _amount_text(total_cash), "active_minutes_total": total_minutes, "realized_rtc_per_hour_estimate": _rate_text(total_cash, total_minutes), "fully_paid_items": sum(row["cash_status"] == "verified_paid" for row in normalized), "partially_paid_items": sum(row["cash_status"] == "partially_verified" for row in normalized), "zero_verified_cash_items": sum(row["cash_status"] == "not_inferred" for row in normalized), "item_count": len(normalized)}
    for key, expected in expected_summary.items():
        if type(summary.get(key)) is not type(expected) or summary.get(key) != expected:
            raise ReinvestmentInputError(f"realized economics summary.{key} disagrees with items")
    cash_scope = [{"repo": row["repo"], "pr": row["pr"], "state": row["state"], "cash_status": row["cash_status"], "verified_cash_rtc": row["verified_cash_rtc"], "payment_evidence_sha256s": row["payment_evidence_sha256s"]} for row in normalized]
    effort_scope = [{"repo": row["repo"], "pr": row["pr"], "active_minutes": row["active_minutes"]} for row in normalized]
    if _sha256({"cash": cash_scope, "effort": effort_scope}) != source["scope_sha256"]:
        raise ReinvestmentInputError("realized economics scope_sha256 disagrees with items")
    ranking = source["ranking"]
    if type(ranking) is not list or len(ranking) != len(normalized):
        raise ReinvestmentInputError("realized economics ranking must cover every item")
    ranked = sorted(normalized, key=lambda row: (-row["_rate"], row["repo"].casefold(), row["pr"]))
    for position, raw in enumerate(ranking, 1):
        rank_row = _strict_keys(raw, _SOURCE_RANKING_KEYS, f"realized economics ranking[{position - 1}]")
        expected = ranked[position - 1]
        if type(rank_row["rank"]) is not int or rank_row["rank"] != position or rank_row["repo"] != expected["repo"] or rank_row["pr"] != expected["pr"] or rank_row["realized_rtc_per_hour_estimate"] != expected["realized_rtc_per_hour_estimate"]:
            raise ReinvestmentInputError("realized economics ranking disagrees with item economics")
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
        raise ReinvestmentInputError("taxonomy must exactly cover live realized scope")
    canonical = sorted(mappings, key=lambda row: (_identity_key(row["repo"], row["pr"]), row["family"]))
    if mappings != canonical:
        raise ReinvestmentInputError("taxonomy mappings are not in canonical identity order")
    return result, digest


def _normalize_policy(policy: Any) -> Tuple[Dict[str, Any], str]:
    payload = _strict_keys(policy, _POLICY_KEYS, "policy")
    if payload["schema"] != _POLICY_SCHEMA:
        raise ReinvestmentInputError("policy schema is invalid")
    _positive_int(payload["version"], "policy.version", maximum=2_147_483_647)
    minimum_samples = _positive_int(payload["minimum_samples"], "policy.minimum_samples", maximum=_MAX_ITEMS)
    minimum_nonzero = _nonnegative_int(payload["minimum_nonzero_cash_samples"], "policy.minimum_nonzero_cash_samples", maximum=_MAX_ITEMS)
    if minimum_nonzero > minimum_samples:
        raise ReinvestmentInputError("minimum_nonzero_cash_samples cannot exceed minimum_samples")
    threshold = _decimal(payload["minimum_median_rtc_per_hour"], "policy.minimum_median_rtc_per_hour")
    cap_bps = _positive_int(payload["maximum_family_capacity_bps"], "policy.maximum_family_capacity_bps", maximum=10_000)
    digest = _verify_digest_envelope(payload, "policy_sha256", "policy")
    return {"minimum_samples": minimum_samples, "minimum_nonzero_cash_samples": minimum_nonzero, "minimum_median_rtc_per_hour": threshold, "maximum_family_capacity_bps": cap_bps}, digest


def _median(values: List[Fraction]) -> Fraction:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _family_rows(source_items: List[Dict[str, Any]], mapping: Dict[Tuple[str, int], str], policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in source_items:
        buckets.setdefault(mapping[_identity_key(row["repo"], row["pr"])], []).append(row)
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
        rows.append({"family": family, "state": state, "reasons": reasons, "sample_count": len(items), "verified_paid_items": paid, "partially_paid_items": partial, "zero_verified_cash_items": zero, "nonzero_cash_samples": nonzero, "nonzero_cash_share": f"{nonzero}/{len(items)}", "verified_cash_rtc": _amount_text(total_cash), "active_minutes": total_minutes, "aggregate_realized_rtc_per_hour_estimate": _fraction_decimal_text(aggregate_rate), "median_realized_rtc_per_hour_estimate": _fraction_decimal_text(median_rate), "worst_realized_rtc_per_hour_estimate": _fraction_decimal_text(worst_rate), "allocation_weight_fraction": f"{weight.numerator}/{weight.denominator}", "source_items": [{"repo": row["repo"], "pr": row["pr"], "cash_status": row["cash_status"], "verified_cash_rtc": row["verified_cash_rtc"], "active_minutes": row["active_minutes"]} for row in items], "_weight": weight})
    return rows


def _capped_apportion(family_rows: List[Dict[str, Any]], capacity_minutes: int, maximum_family_capacity_bps: int) -> Tuple[Dict[str, int], int]:
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
        saturated = sorted(name for name in active if raw[name] >= caps[name] - assignments[name])
        if saturated:
            for name in saturated:
                room = caps[name] - assignments[name]
                if room > 0:
                    assignments[name] += room
                    remaining -= room
                active.remove(name)
            continue
        for name in sorted(active):
            floor_amount = raw[name].numerator // raw[name].denominator
            room = caps[name] - assignments[name]
            amount = min(floor_amount, room)
            assignments[name] += amount
            remaining -= amount
        if remaining <= 0:
            break
        remainder_order = sorted(active, key=lambda name: (-(raw[name] - (raw[name].numerator // raw[name].denominator)), name))
        progressed = False
        for name in remainder_order:
            if remaining <= 0:
                break
            if assignments[name] < caps[name]:
                assignments[name] += 1
                remaining -= 1
                progressed = True
        if not progressed or all(assignments[name] >= caps[name] for name in active):
            break
    return assignments, remaining


def compile_reinvestment_review(closeout_manifest_items: Any, payment_bindings: Any, effort_log: Any, taxonomy: Any, policy: Any, capacity_minutes: Any, *, wallet: str, max_closeout_pages: int = 10) -> Dict[str, Any]:
    """Reacquire live cash authority, then compile an advisory reinvestment review."""
    capacity = _positive_int(capacity_minutes, "capacity_minutes", maximum=_MAX_CAPACITY_MINUTES)
    pages = _positive_int(max_closeout_pages, "max_closeout_pages", maximum=_MAX_CLOSEOUT_PAGES)
    if type(wallet) is not str or not wallet or wallet != wallet.strip() or any(char.isspace() or not char.isprintable() for char in wallet) or len(wallet) > 256:
        raise ReinvestmentInputError("wallet must be one bounded printable identifier")
    manifest_snapshot = _snapshot_json(closeout_manifest_items, "closeout manifest")
    bindings_snapshot = _snapshot_json(payment_bindings, "payment bindings")
    effort_snapshot = _snapshot_json(effort_log, "effort log")
    taxonomy_snapshot = _snapshot_json(taxonomy, "taxonomy")
    policy_snapshot = _snapshot_json(policy, "policy")
    live_closeout = closeout.build_closeout_queue(manifest_snapshot, max_pages=pages)
    live_closeout_snapshot = _snapshot_json(live_closeout, "live closeout")
    live_history, history_wallet = settlement._query_canonical_history(wallet)
    live_history_snapshot = _snapshot_json(live_history, "canonical wallet history")
    if type(history_wallet) is not str or history_wallet != wallet:
        raise ReinvestmentInputError("canonical wallet history identity mismatch")
    economics = rue.compile_realized_unit_economics(live_closeout_snapshot, live_history_snapshot, bindings_snapshot, effort_snapshot, wallet=wallet, history_wallet=history_wallet, history_source="queried_wallet")
    economics_bytes = _canonical_json(economics)
    economics_snapshot = json.loads(economics_bytes.decode("utf-8"))
    verify_copy = json.loads(economics_bytes.decode("utf-8"))
    if not rue.verify_receipt(verify_copy):
        raise ReinvestmentInputError("in-process realized economics failed self-integrity")
    source_items = _normalize_live_economics(economics_snapshot, expected_wallet=wallet)
    mapping, taxonomy_digest = _normalize_taxonomy(taxonomy_snapshot, source_items)
    normalized_policy, policy_digest = _normalize_policy(policy_snapshot)
    families = _family_rows(source_items, mapping, normalized_policy)
    assignments, unallocated = _capped_apportion(families, capacity, normalized_policy["maximum_family_capacity_bps"])
    public_families: List[Dict[str, Any]] = []
    for row in families:
        public = {key: value for key, value in row.items() if key != "_weight"}
        public["recommended_review_capacity_minutes"] = assignments.get(row["family"], 0)
        public_families.append(public)
    eligible_count = sum(row["state"] == "SCALE_REVIEW_ELIGIBLE" for row in families)
    payload: Dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        "source_economics_receipt_sha256": economics_snapshot["receipt_sha256"],
        "source_economics_scope_sha256": economics_snapshot["scope_sha256"],
        "taxonomy_sha256": taxonomy_digest,
        "policy_sha256": policy_digest,
        "capacity_minutes": capacity,
        "summary": {"family_count": len(families), "scale_review_eligible_families": eligible_count, "recommended_review_capacity_minutes": capacity - unallocated, "unallocated_capacity_minutes": unallocated, "review_state": "READY_FOR_OWNER_REINVESTMENT_REVIEW" if eligible_count > 0 else "NO_SCALE_REVIEW_ELIGIBLE"},
        "authority": {"closeout_state": "live_github_reacquired_in_process", "wallet_history": "canonical_provider_reacquired_in_process", "economics": "compiled_in_process_from_live_provider_observations", "standalone_economics_receipt_accepted": False, "captured_wallet_history_accepted": False, "cash_evidence_authority": "reacquired_not_inherited", "taxonomy_policy_basis": "caller_supplied_planning_inputs", "taxonomy_policy_authenticated_as_owner": False, "taxonomy_policy_digests_are_integrity_only": True, "advertised_reward_used_as_realized_cash": False, "future_revenue_claimed": False, "guaranteed_return_claimed": False, "autonomous_claim_authorized": False, "submission_authorized": False, "spend_authorized": False, "external_contact_authorized": False, "payment_or_wallet_mutation_authorized": False, "accounting_or_tax_claim": False},
        "families": public_families,
    }
    payload["receipt_sha256"] = _sha256(payload)
    return payload


def verify_reinvestment_receipt_current(receipt: Any, closeout_manifest_items: Any, payment_bindings: Any, effort_log: Any, taxonomy: Any, policy: Any, capacity_minutes: Any, *, wallet: str, max_closeout_pages: int = 10) -> bool:
    if type(receipt) is not dict:
        return False
    try:
        expected = compile_reinvestment_review(closeout_manifest_items, payment_bindings, effort_log, taxonomy, policy, capacity_minutes, wallet=wallet, max_closeout_pages=max_closeout_pages)
        return _canonical_json(receipt) == _canonical_json(expected)
    except (OSError, ReinvestmentInputError, closeout.RevenueCloseoutError, closeout.RevenueCloseoutInputError, settlement.PayoutLookupError, settlement.RevenueSettlementInputError, settlement.RevenueSettlementEvidenceError, rue.RealizedUnitEconomicsInputError):
        return False


def verify_receipt_integrity_only(receipt: Any) -> bool:
    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        return False
    candidate = dict(receipt)
    candidate.pop("receipt_sha256", None)
    try:
        return _sha256(candidate) == digest
    except ReinvestmentInputError:
        return False


def _duplicate_safe_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReinvestmentInputError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_bounded(path: str) -> bytes:
    if path == "-":
        data = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            raise ReinvestmentInputError("stdin JSON is too large")
        return data
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ReinvestmentInputError(f"cannot open {path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ReinvestmentInputError(f"{path} must be a regular file")
        if info.st_size > _MAX_JSON_BYTES:
            raise ReinvestmentInputError(f"{path} is too large")
        chunks: List[bytes] = []
        remaining = _MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > _MAX_JSON_BYTES:
            raise ReinvestmentInputError(f"{path} grew beyond the size limit")
        return data
    finally:
        os.close(fd)


def _load_json(path: str) -> Any:
    raw = _read_bounded(path)
    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_duplicate_safe_object, parse_constant=lambda value: (_ for _ in ()).throw(ReinvestmentInputError(f"non-finite JSON value {value} is not allowed")))
    except ReinvestmentInputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReinvestmentInputError(f"{path} is not valid strict UTF-8 JSON") from exc


def _schema_items(payload: Any, label: str) -> List[Dict[str, Any]]:
    if type(payload) is not dict or payload.get("schema_version") != 1:
        raise ReinvestmentInputError(f"{label} must be a schema_version 1 object")
    items = payload.get("items")
    if type(items) is not list:
        raise ReinvestmentInputError(f"{label}.items must be a list")
    return items


def _write_output(path: Optional[str], payload: Dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(text, end="")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ReinvestmentInputError(f"refusing unsafe or existing output {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(text)
    finally:
        if fd >= 0:
            os.close(fd)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.reinvestment_allocator", description="Reacquire live GitHub closeout + canonical wallet history, compile realized economics in-process, then prepare an advisory family reinvestment review.")
    sub = parser.add_subparsers(dest="command", required=True)
    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("manifest", help="revenue-closeout manifest JSON")
        target.add_argument("bindings", help="payment bindings JSON")
        target.add_argument("effort", help="operator effort JSON")
        target.add_argument("taxonomy", help="caller planning taxonomy JSON")
        target.add_argument("policy", help="caller planning policy JSON")
        target.add_argument("--wallet", required=True, help="canonical recipient wallet")
        target.add_argument("--capacity-minutes", required=True, type=int)
        target.add_argument("--max-closeout-pages", type=int, default=10)
    compile_parser = sub.add_parser("compile")
    add_common(compile_parser)
    compile_parser.add_argument("--output")
    verify_parser = sub.add_parser("verify-current")
    verify_parser.add_argument("receipt")
    add_common(verify_parser)
    args = parser.parse_args(argv)
    paths = [args.manifest, args.bindings, args.effort, args.taxonomy, args.policy]
    if args.command == "verify-current":
        paths.append(args.receipt)
    if paths.count("-") > 1:
        parser.error("at most one input may read from stdin")
    try:
        manifest = _load_json(args.manifest)
        bindings = _load_json(args.bindings)
        effort = _load_json(args.effort)
        taxonomy = _load_json(args.taxonomy)
        policy = _load_json(args.policy)
        manifest_items = _schema_items(manifest, "manifest")
        binding_items = _schema_items(bindings, "bindings")
        if args.command == "compile":
            payload = compile_reinvestment_review(manifest_items, binding_items, effort, taxonomy, policy, args.capacity_minutes, wallet=args.wallet, max_closeout_pages=args.max_closeout_pages)
            _write_output(args.output, payload)
            return 0
        receipt = _load_json(args.receipt)
        if not verify_reinvestment_receipt_current(receipt, manifest_items, binding_items, effort, taxonomy, policy, args.capacity_minutes, wallet=args.wallet, max_closeout_pages=args.max_closeout_pages):
            raise ReinvestmentInputError("current receipt verification failed")
        print(receipt["receipt_sha256"])
        return 0
    except (OSError, ReinvestmentInputError, closeout.RevenueCloseoutError, closeout.RevenueCloseoutInputError, settlement.PayoutLookupError, settlement.RevenueSettlementInputError, settlement.RevenueSettlementEvidenceError, rue.RealizedUnitEconomicsInputError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
