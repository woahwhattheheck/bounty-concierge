# SPDX-License-Identifier: MIT
"""Evidence-bound internal custody for active bounty work.

This module sits between bounty selection and PR closeout.  It records *internal*
work custody only: it never creates a GitHub claim, contacts a sponsor, submits a
PR, mutates a wallet/provider, or infers acceptance, payout, cash, or revenue.

The compiler consumes already-produced qualification and availability receipts,
binds each candidate to their exact canonical bytes, validates an immutable
claim-event ledger, applies explicit operator capacity policy, and emits a
byte-stable receipt suitable for offline verification.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


SCHEMA = "active-claim-portfolio/v1"
_MAX_INPUT_BYTES = 2 * 1024 * 1024
_MAX_ITEMS = 1000
_MAX_SAFE_INT = (1 << 53) - 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SECRET_RE = re.compile(
    r"(?i)(?:\bBearer\s+[A-Za-z0-9._~+/-]{12,}|\bgh[pousr]_[A-Za-z0-9]{20,}|"
    r"\bsk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

_CANDIDATE_KEYS = frozenset(
    {
        "repo",
        "number",
        "sponsor_key",
        "worker_id",
        "observed_at",
        "reward_currency",
        "reward_minor",
        "qualification",
        "availability",
    }
)
_EVENT_KEYS = frozenset(
    {
        "event_id",
        "repo",
        "number",
        "sponsor_key",
        "worker_id",
        "opportunity_digest",
        "state",
        "event_at",
        "predecessor_event_id",
        "predecessor_event_digest",
        "evidence_ref",
        "evidence_sha256",
    }
)
_POLICY_KEYS = frozenset(
    {
        "version",
        "max_active_claims_total",
        "max_active_claims_per_worker",
        "max_active_claims_per_sponsor",
        "sponsor_overrides",
        "max_claim_age_seconds",
    }
)
_STATES = frozenset({"CLAIMED", "RELEASED", "COMPLETED_INTERNAL"})
_TERMINAL_STATES = frozenset({"RELEASED", "COMPLETED_INTERNAL"})


class ActiveClaimPortfolioError(ValueError):
    """Raised when an input cannot be interpreted safely."""


def _strict_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActiveClaimPortfolioError("duplicate JSON object key: %s" % key)
        result[key] = value
    return result


def _validate_json_tree(value: Any, path: str = "$", depth: int = 0) -> None:
    if depth > 32:
        raise ActiveClaimPortfolioError("JSON input nesting is too deep at %s" % path)
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if isinstance(value, bool) or abs(value) > _MAX_SAFE_INT:
            raise ActiveClaimPortfolioError("unsafe integer at %s" % path)
        return
    if isinstance(value, float):
        raise ActiveClaimPortfolioError("floating-point values are not accepted at %s" % path)
    if isinstance(value, list):
        if len(value) > _MAX_ITEMS:
            raise ActiveClaimPortfolioError("array too large at %s" % path)
        for index, item in enumerate(value):
            _validate_json_tree(item, "%s[%d]" % (path, index), depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_ITEMS:
            raise ActiveClaimPortfolioError("object too large at %s" % path)
        for key, item in value.items():
            if not isinstance(key, str):
                raise ActiveClaimPortfolioError("non-string JSON object key at %s" % path)
            _validate_json_tree(item, "%s.%s" % (path, key), depth + 1)
        return
    raise ActiveClaimPortfolioError("unsupported JSON value at %s" % path)


def _canonical_bytes(value: Any) -> bytes:
    _validate_json_tree(value)
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ActiveClaimPortfolioError("value is not canonical JSON") from exc
    return text.encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _parse_utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise ActiveClaimPortfolioError("%s must be canonical UTC YYYY-MM-DDTHH:MM:SSZ" % name)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ActiveClaimPortfolioError("%s is not a real UTC timestamp" % name) from exc
    # strptime accepts years that still round-trip; enforce exact textual round-trip.
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ActiveClaimPortfolioError("%s is not canonical UTC" % name)
    return parsed


def _safe_int(value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActiveClaimPortfolioError("%s must be an integer" % name)
    minimum = 1 if positive else 0
    if value < minimum or value > _MAX_SAFE_INT:
        bound = "positive" if positive else "non-negative"
        raise ActiveClaimPortfolioError("%s must be a safe %s integer" % (name, bound))
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ActiveClaimPortfolioError("%s must be a bounded opaque identifier" % name)
    if _SECRET_RE.search(value):
        raise ActiveClaimPortfolioError("%s appears to contain secret material" % name)
    return value


def _repo(value: Any) -> str:
    if not isinstance(value, str) or _REPO_RE.fullmatch(value) is None:
        raise ActiveClaimPortfolioError("repo must be in owner/name form")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ActiveClaimPortfolioError("%s must be a lowercase SHA-256 digest" % name)
    return value


def _evidence_ref(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ActiveClaimPortfolioError("evidence_ref must be a non-empty string <=256 chars")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ActiveClaimPortfolioError("evidence_ref contains control characters")
    if _SECRET_RE.search(value):
        raise ActiveClaimPortfolioError("evidence_ref appears to contain secret material")
    return value


def _exact_keys(value: Any, expected: Set[str], name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ActiveClaimPortfolioError("%s must be an object" % name)
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ActiveClaimPortfolioError(
            "%s has unexpected keys (missing=%s extra=%s)" % (name, missing, extra)
        )
    return value


def _normalize_policy(policy: Any) -> Dict[str, Any]:
    obj = _exact_keys(policy, set(_POLICY_KEYS), "policy")
    version = _identifier(obj["version"], "policy.version")
    total = _safe_int(obj["max_active_claims_total"], "policy.max_active_claims_total")
    per_worker = _safe_int(
        obj["max_active_claims_per_worker"], "policy.max_active_claims_per_worker"
    )
    per_sponsor = _safe_int(
        obj["max_active_claims_per_sponsor"], "policy.max_active_claims_per_sponsor"
    )
    max_age = _safe_int(
        obj["max_claim_age_seconds"], "policy.max_claim_age_seconds", positive=True
    )
    raw_overrides = obj["sponsor_overrides"]
    if not isinstance(raw_overrides, dict):
        raise ActiveClaimPortfolioError("policy.sponsor_overrides must be an object")
    overrides: Dict[str, int] = {}
    for raw_key in sorted(raw_overrides):
        sponsor = _identifier(raw_key, "policy.sponsor_overrides key")
        overrides[sponsor] = _safe_int(
            raw_overrides[raw_key], "policy.sponsor_overrides[%s]" % sponsor
        )
    return {
        "version": version,
        "max_active_claims_total": total,
        "max_active_claims_per_worker": per_worker,
        "max_active_claims_per_sponsor": per_sponsor,
        "sponsor_overrides": overrides,
        "max_claim_age_seconds": max_age,
    }


def _normalize_candidate(candidate: Any, as_of_dt: datetime) -> Dict[str, Any]:
    obj = _exact_keys(candidate, set(_CANDIDATE_KEYS), "candidate")
    _validate_json_tree(obj)
    repo = _repo(obj["repo"])
    number = _safe_int(obj["number"], "candidate.number", positive=True)
    sponsor = _identifier(obj["sponsor_key"], "candidate.sponsor_key")
    worker = _identifier(obj["worker_id"], "candidate.worker_id")
    observed_at = obj["observed_at"]
    observed_dt = _parse_utc(observed_at, "candidate.observed_at")
    currency = obj["reward_currency"]
    if not isinstance(currency, str) or _CURRENCY_RE.fullmatch(currency) is None:
        raise ActiveClaimPortfolioError("candidate.reward_currency is invalid")
    reward_minor = _safe_int(obj["reward_minor"], "candidate.reward_minor")
    qualification = obj["qualification"]
    availability = obj["availability"]
    if not isinstance(qualification, dict) or not isinstance(availability, dict):
        raise ActiveClaimPortfolioError("candidate upstream receipts must be objects")
    _validate_json_tree(qualification, "$.candidate.qualification")
    _validate_json_tree(availability, "$.candidate.availability")

    issues: List[str] = []
    if observed_dt > as_of_dt:
        issues.append("FUTURE_CANDIDATE_EVIDENCE")
    if qualification.get("disposition") != "ACTIONABLE" or qualification.get("dispatch") is not True:
        issues.append("QUALIFICATION_NOT_ACTIONABLE")
    if availability.get("schema") != "bounty-availability/v1":
        issues.append("AVAILABILITY_SCHEMA_MISMATCH")
    availability_number = availability.get("number")
    if (
        availability.get("repo") != repo
        or isinstance(availability_number, bool)
        or not isinstance(availability_number, int)
        or availability_number != number
    ):
        issues.append("AVAILABILITY_IDENTITY_MISMATCH")
    if availability.get("disposition") != "CLEAR" or availability.get("dispatch") is not True:
        issues.append("AVAILABILITY_NOT_CLEAR")

    qualification_sha = _sha256_json(qualification)
    availability_sha = _sha256_json(availability)
    generation = {
        "repo": repo,
        "number": number,
        "sponsor_key": sponsor,
        "observed_at": observed_at,
        "reward_currency": currency,
        "reward_minor": reward_minor,
        "qualification_sha256": qualification_sha,
        "availability_sha256": availability_sha,
    }
    return {
        "repo": repo,
        "number": number,
        "opportunity_id": "%s#%d" % (repo, number),
        "sponsor_key": sponsor,
        "worker_id": worker,
        "observed_at": observed_at,
        "reward_currency": currency,
        "reward_minor": reward_minor,
        "qualification": qualification,
        "availability": availability,
        "qualification_sha256": qualification_sha,
        "availability_sha256": availability_sha,
        "generation_sha256": _sha256_json(generation),
        "issues": sorted(set(issues)),
        "raw_sha256": _sha256_json(obj),
        "raw": obj,
    }


def _normalize_event(event: Any, as_of_dt: datetime) -> Dict[str, Any]:
    obj = _exact_keys(event, set(_EVENT_KEYS), "event")
    _validate_json_tree(obj)
    event_id = _identifier(obj["event_id"], "event.event_id")
    repo = _repo(obj["repo"])
    number = _safe_int(obj["number"], "event.number", positive=True)
    sponsor = _identifier(obj["sponsor_key"], "event.sponsor_key")
    worker = _identifier(obj["worker_id"], "event.worker_id")
    opportunity_digest = _digest(obj["opportunity_digest"], "event.opportunity_digest")
    state_value = obj["state"]
    if not isinstance(state_value, str) or state_value not in _STATES:
        raise ActiveClaimPortfolioError("event.state is unsupported")
    event_at = obj["event_at"]
    event_dt = _parse_utc(event_at, "event.event_at")
    predecessor_id = obj["predecessor_event_id"]
    predecessor_digest = obj["predecessor_event_digest"]
    if predecessor_id is not None:
        predecessor_id = _identifier(predecessor_id, "event.predecessor_event_id")
    if predecessor_digest is not None:
        predecessor_digest = _digest(
            predecessor_digest, "event.predecessor_event_digest"
        )
    evidence_ref = _evidence_ref(obj["evidence_ref"])
    evidence_sha = _digest(obj["evidence_sha256"], "event.evidence_sha256")
    issues: List[str] = []
    if event_dt > as_of_dt:
        issues.append("FUTURE_EVENT")
    if (predecessor_id is None) != (predecessor_digest is None):
        issues.append("PREDECESSOR_PAIR_INCOMPLETE")
    event_sha = _sha256_json(obj)
    return {
        "event_id": event_id,
        "repo": repo,
        "number": number,
        "opportunity_id": "%s#%d" % (repo, number),
        "sponsor_key": sponsor,
        "worker_id": worker,
        "opportunity_digest": opportunity_digest,
        "state": state_value,
        "event_at": event_at,
        "event_dt": event_dt,
        "predecessor_event_id": predecessor_id,
        "predecessor_event_digest": predecessor_digest,
        "evidence_ref": evidence_ref,
        "evidence_sha256": evidence_sha,
        "event_sha256": event_sha,
        "issues": sorted(set(issues)),
        "raw": obj,
    }


def _dedupe_candidates(
    candidates: Any, as_of_dt: datetime
) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    if not isinstance(candidates, list):
        raise ActiveClaimPortfolioError("candidates must be an array")
    if len(candidates) > _MAX_ITEMS:
        raise ActiveClaimPortfolioError("too many candidates")
    normalized = [_normalize_candidate(item, as_of_dt) for item in candidates]
    # Exact replay collapses. Different rows for one opportunity remain visible as a conflict.
    by_raw: Dict[str, Dict[str, Any]] = {}
    for item in normalized:
        by_raw.setdefault(item["raw_sha256"], item)
    unique = list(by_raw.values())
    unique.sort(key=lambda item: (item["opportunity_id"], item["raw_sha256"]))
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in unique:
        grouped[item["opportunity_id"]].append(item)
    digest_input = [item["raw"] for item in unique]
    return grouped, _sha256_json(digest_input)


def _dedupe_events(
    events: Any, as_of_dt: datetime
) -> Tuple[List[Dict[str, Any]], Dict[str, Set[str]], List[Dict[str, Any]], str]:
    if not isinstance(events, list):
        raise ActiveClaimPortfolioError("events must be an array")
    if len(events) > _MAX_ITEMS:
        raise ActiveClaimPortfolioError("too many events")
    normalized = [_normalize_event(item, as_of_dt) for item in events]
    normalized.sort(key=lambda item: (item["event_id"], item["event_sha256"]))

    # Collapse byte-identical event replay before binding the input set.  The
    # receipt therefore treats exact replay as truly idempotent, while changed
    # bytes under one event ID remain visible as separate variants below.
    exact_unique: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in normalized:
        exact_unique[(item["event_id"], item["event_sha256"])] = item
    canonical_unique = [exact_unique[key] for key in sorted(exact_unique)]
    digest_input = [item["raw"] for item in canonical_unique]
    set_sha = _sha256_json(digest_input)

    variants: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for item in canonical_unique:
        variants[item["event_id"]][item["event_sha256"]] = item

    conflicts: Dict[str, Set[str]] = defaultdict(set)
    conflict_rows: List[Dict[str, Any]] = []
    unique: List[Dict[str, Any]] = []
    for event_id in sorted(variants):
        by_sha = variants[event_id]
        if len(by_sha) > 1:
            involved = sorted({item["opportunity_id"] for item in by_sha.values()})
            for opp in involved:
                conflicts[opp].add("EVENT_ID_REUSE")
            conflict_rows.append(
                {
                    "code": "EVENT_ID_REUSE",
                    "event_id": event_id,
                    "opportunity_ids": involved,
                    "variant_sha256": sorted(by_sha),
                }
            )
            continue
        unique.append(next(iter(by_sha.values())))
    unique.sort(key=lambda item: (item["opportunity_id"], item["event_at"], item["event_id"]))
    return unique, conflicts, conflict_rows, set_sha


def _analyze_events(
    events: List[Dict[str, Any]],
    seed_conflicts: Dict[str, Set[str]],
) -> Tuple[
    Dict[str, Set[str]],
    Dict[str, Dict[str, Any]],
    List[Dict[str, Any]],
]:
    conflicts: Dict[str, Set[str]] = defaultdict(set)
    for opp, codes in seed_conflicts.items():
        conflicts[opp].update(codes)

    by_opp: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_opp[event["opportunity_id"]].append(event)
        for issue in event["issues"]:
            conflicts[event["opportunity_id"]].add(issue)

    active: Dict[str, Dict[str, Any]] = {}
    ledger_conflicts: List[Dict[str, Any]] = []

    for opp in sorted(by_opp):
        rows = by_opp[opp]
        generations: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            generations[row["opportunity_digest"]].append(row)

        active_generations: List[Dict[str, Any]] = []
        for generation_sha in sorted(generations):
            generation = generations[generation_sha]
            by_id = {row["event_id"]: row for row in generation}
            roots = [row for row in generation if row["predecessor_event_id"] is None]
            generation_codes: Set[str] = set()
            if len(roots) != 1:
                generation_codes.add("CLAIM_ROOT_CARDINALITY")
            root: Optional[Dict[str, Any]] = roots[0] if len(roots) == 1 else None
            if root is not None and root["state"] != "CLAIMED":
                generation_codes.add("FIRST_EVENT_NOT_CLAIMED")

            children: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for row in generation:
                pred_id = row["predecessor_event_id"]
                if pred_id is None:
                    continue
                pred = by_id.get(pred_id)
                if pred is None:
                    generation_codes.add("PREDECESSOR_MISSING")
                    continue
                if row["predecessor_event_digest"] != pred["event_sha256"]:
                    generation_codes.add("PREDECESSOR_DIGEST_MISMATCH")
                if row["event_dt"] < pred["event_dt"]:
                    generation_codes.add("EVENT_TIME_REGRESSION")
                if row["worker_id"] != pred["worker_id"]:
                    generation_codes.add("WORKER_LINEAGE_DRIFT")
                if row["sponsor_key"] != pred["sponsor_key"]:
                    generation_codes.add("SPONSOR_LINEAGE_DRIFT")
                children[pred_id].append(row)
                if pred["state"] != "CLAIMED" or row["state"] not in _TERMINAL_STATES:
                    generation_codes.add("STATE_REGRESSION_OR_INVALID_TRANSITION")

            for parent_id, child_rows in children.items():
                if len(child_rows) > 1:
                    generation_codes.add("MULTIPLE_TERMINAL_CHILDREN")
                parent = by_id[parent_id]
                if parent["state"] in _TERMINAL_STATES:
                    generation_codes.add("TERMINAL_EVENT_HAS_CHILD")

            if generation_codes:
                conflicts[opp].update(generation_codes)
                ledger_conflicts.append(
                    {
                        "code": "LINEAGE_INVALID",
                        "opportunity_id": opp,
                        "opportunity_digest": generation_sha,
                        "reason_codes": sorted(generation_codes),
                    }
                )
                continue

            assert root is not None  # guarded above; never relied on under -O for validation
            root_children = children.get(root["event_id"], [])
            if not root_children:
                active_generations.append(root)

        if len(active_generations) > 1:
            conflicts[opp].add("MULTIPLE_ACTIVE_GENERATIONS")
            ledger_conflicts.append(
                {
                    "code": "MULTIPLE_ACTIVE_GENERATIONS",
                    "opportunity_id": opp,
                    "event_ids": sorted(item["event_id"] for item in active_generations),
                }
            )
        elif len(active_generations) == 1 and not conflicts.get(opp):
            active[opp] = active_generations[0]

    for opp in sorted(conflicts):
        if conflicts[opp]:
            ledger_conflicts.append(
                {
                    "code": "OPPORTUNITY_LEDGER_CONFLICT",
                    "opportunity_id": opp,
                    "reason_codes": sorted(conflicts[opp]),
                }
            )
    ledger_conflicts.sort(
        key=lambda row: (
            str(row.get("opportunity_id", "")),
            str(row.get("code", "")),
            _sha256_json(row),
        )
    )
    return conflicts, active, ledger_conflicts


def _counts(active: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    workers: Dict[str, int] = defaultdict(int)
    sponsors: Dict[str, int] = defaultdict(int)
    for row in active.values():
        workers[row["worker_id"]] += 1
        sponsors[row["sponsor_key"]] += 1
    return dict(sorted(workers.items())), dict(sorted(sponsors.items()))


def _sponsor_limit(policy: Dict[str, Any], sponsor: str) -> int:
    return policy["sponsor_overrides"].get(
        sponsor, policy["max_active_claims_per_sponsor"]
    )


def compile_active_claim_portfolio(
    candidates: Any,
    events: Any,
    policy: Any,
    *,
    as_of: str,
) -> Dict[str, Any]:
    """Compile a deterministic internal claim-custody receipt.

    ``READY_FOR_INTERNAL_CLAIM`` is permission only for the operator's *internal*
    work assignment.  It is never authority to post a claim externally.
    """
    as_of_dt = _parse_utc(as_of, "as_of")
    normalized_policy = _normalize_policy(policy)
    candidate_groups, candidate_set_sha = _dedupe_candidates(candidates, as_of_dt)
    unique_events, seed_conflicts, event_id_conflict_rows, event_set_sha = _dedupe_events(
        events, as_of_dt
    )
    event_conflicts, active, lineage_conflicts = _analyze_events(
        unique_events, seed_conflicts
    )
    worker_counts, sponsor_counts = _counts(active)
    # Any ambiguous ledger lineage can hide an active reservation.  Do not let
    # an unrelated READY result undercount capacity while that ambiguity exists.
    capacity_ledger_uncertain = bool(event_id_conflict_rows or lineage_conflicts)

    results: List[Dict[str, Any]] = []
    for opportunity_id in sorted(candidate_groups):
        variants = candidate_groups[opportunity_id]
        if len(variants) != 1:
            results.append(
                {
                    "opportunity_id": opportunity_id,
                    "generation_sha256": None,
                    "worker_id": None,
                    "sponsor_key": None,
                    "reward_currency": None,
                    "reward_minor": None,
                    "disposition": "CONFLICT_HOLD",
                    "reason_codes": ["CANDIDATE_VARIANT_CONFLICT"],
                    "active_worker_id": None,
                    "claim_age_seconds": None,
                }
            )
            continue

        candidate = variants[0]
        reasons: List[str] = list(candidate["issues"])
        if event_conflicts.get(opportunity_id):
            reasons.extend(sorted(event_conflicts[opportunity_id]))

        disposition: str
        active_worker: Optional[str] = None
        age_seconds: Optional[int] = None
        active_row = active.get(opportunity_id)

        if reasons:
            disposition = "CONFLICT_HOLD"
        elif active_row is not None:
            active_worker = active_row["worker_id"]
            age_seconds = int((as_of_dt - active_row["event_dt"]).total_seconds())
            if active_row["opportunity_digest"] != candidate["generation_sha256"]:
                disposition = "UPSTREAM_CHANGED_HOLD"
                reasons.append("ACTIVE_CLAIM_BINDS_OLD_GENERATION")
            elif age_seconds >= normalized_policy["max_claim_age_seconds"]:
                disposition = "STALE_CLAIM_REVIEW"
                reasons.append("CLAIM_REVIEW_AGE_REACHED")
            else:
                disposition = "ACTIVE_OWNED"
        else:
            cap_reasons: List[str] = []
            if capacity_ledger_uncertain:
                # This is a conflict rather than a normal policy-capacity hold:
                # the number of active claims is not authoritative yet.
                disposition = "CONFLICT_HOLD"
                reasons.append("LEDGER_CAPACITY_UNCERTAIN")
                results.append(
                    {
                        "opportunity_id": opportunity_id,
                        "generation_sha256": candidate["generation_sha256"],
                        "worker_id": candidate["worker_id"],
                        "sponsor_key": candidate["sponsor_key"],
                        "reward_currency": candidate["reward_currency"],
                        "reward_minor": candidate["reward_minor"],
                        "disposition": disposition,
                        "reason_codes": sorted(set(reasons)),
                        "active_worker_id": active_worker,
                        "claim_age_seconds": age_seconds,
                    }
                )
                continue
            if len(active) >= normalized_policy["max_active_claims_total"]:
                cap_reasons.append("TOTAL_ACTIVE_CAP_REACHED")
            if (
                worker_counts.get(candidate["worker_id"], 0)
                >= normalized_policy["max_active_claims_per_worker"]
            ):
                cap_reasons.append("WORKER_ACTIVE_CAP_REACHED")
            sponsor_limit = _sponsor_limit(normalized_policy, candidate["sponsor_key"])
            if sponsor_counts.get(candidate["sponsor_key"], 0) >= sponsor_limit:
                cap_reasons.append("SPONSOR_ACTIVE_CAP_REACHED")
            if cap_reasons:
                disposition = "CAPACITY_HOLD"
                reasons.extend(cap_reasons)
            else:
                disposition = "READY_FOR_INTERNAL_CLAIM"

        results.append(
            {
                "opportunity_id": opportunity_id,
                "generation_sha256": candidate["generation_sha256"],
                "worker_id": candidate["worker_id"],
                "sponsor_key": candidate["sponsor_key"],
                "reward_currency": candidate["reward_currency"],
                "reward_minor": candidate["reward_minor"],
                "disposition": disposition,
                "reason_codes": sorted(set(reasons)),
                "active_worker_id": active_worker,
                "claim_age_seconds": age_seconds,
            }
        )

    ledger_conflicts = list(event_id_conflict_rows) + list(lineage_conflicts)
    ledger_conflicts.sort(
        key=lambda row: (
            str(row.get("opportunity_id", "")),
            str(row.get("code", "")),
            _sha256_json(row),
        )
    )

    body: Dict[str, Any] = {
        "schema": SCHEMA,
        "as_of": as_of,
        "policy": normalized_policy,
        "policy_sha256": _sha256_json(normalized_policy),
        "candidate_set_sha256": candidate_set_sha,
        "event_set_sha256": event_set_sha,
        "capacity": {
            "active_claims_total": len(active),
            "active_claims_by_worker": worker_counts,
            "active_claims_by_sponsor": sponsor_counts,
            "ledger_capacity_uncertain": capacity_ledger_uncertain,
        },
        "results": results,
        "ledger_conflicts": ledger_conflicts,
        "authority": {
            "effect": "internal_work_custody_only",
            "external_github_claim": False,
            "sponsor_or_maintainer_contact": False,
            "upstream_submission": False,
            "wallet_or_payment_mutation": False,
            "provider_mutation": False,
            "acceptance_or_payout_assertion": False,
            "cash_assertion": False,
            "revenue_recognition": False,
            "completed_internal_is_sponsor_acceptance": False,
        },
    }
    receipt_sha = _sha256_json(body)
    receipt = dict(body)
    receipt["receipt_sha256"] = receipt_sha
    return receipt


def verify_active_claim_portfolio_receipt(
    receipt: Any,
    candidates: Any,
    events: Any,
    policy: Any,
    *,
    as_of: str,
) -> bool:
    if not isinstance(receipt, dict):
        return False
    provided_sha = receipt.get("receipt_sha256")
    if not isinstance(provided_sha, str) or _SHA256_RE.fullmatch(provided_sha) is None:
        return False
    without_sha = dict(receipt)
    without_sha.pop("receipt_sha256", None)
    try:
        if _sha256_json(without_sha) != provided_sha:
            return False
        expected = compile_active_claim_portfolio(
            candidates, events, policy, as_of=as_of
        )
        return _canonical_bytes(receipt) == _canonical_bytes(expected)
    except ActiveClaimPortfolioError:
        return False


def _load_json_file(path: str) -> Any:
    target = Path(path)
    try:
        with target.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ActiveClaimPortfolioError("input must be a regular file: %s" % path)
            if info.st_size > _MAX_INPUT_BYTES:
                raise ActiveClaimPortfolioError("input file is too large: %s" % path)
            data = handle.read(_MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise ActiveClaimPortfolioError("cannot read input %s: %s" % (path, exc)) from exc
    if len(data) > _MAX_INPUT_BYTES:
        raise ActiveClaimPortfolioError("input file is too large: %s" % path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ActiveClaimPortfolioError("input is not UTF-8: %s" % path) from exc
    try:
        value = json.loads(text, object_pairs_hook=_strict_pairs)
    except json.JSONDecodeError as exc:
        raise ActiveClaimPortfolioError("invalid JSON in %s" % path) from exc
    _validate_json_tree(value)
    return value


def _exclusive_write_text(path: str, text: str) -> None:
    target = Path(path)
    parent = target.parent if str(target.parent) else Path(".")
    if not parent.exists() or not parent.is_dir():
        raise ActiveClaimPortfolioError("output parent directory does not exist")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(target), flags, 0o600)
    except OSError as exc:
        raise ActiveClaimPortfolioError("refusing unsafe/existing output %s: %s" % (path, exc)) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ActiveClaimPortfolioError("output is not a regular file")
        payload = text.encode("utf-8")
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _summary(receipt: Dict[str, Any]) -> str:
    counts: Dict[str, int] = defaultdict(int)
    for row in receipt["results"]:
        counts[row["disposition"]] += 1
    rendered = ",".join("%s=%d" % (key, counts[key]) for key in sorted(counts)) or "none"
    return "schema=%s receipt=%s %s" % (
        receipt["schema"],
        receipt["receipt_sha256"],
        rendered,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.active_claim_portfolio",
        description="Compile or verify evidence-bound internal bounty claim custody.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--candidates", required=True)
        p.add_argument("--events", required=True)
        p.add_argument("--policy", required=True)
        p.add_argument("--as-of", required=True)

    compile_parser = sub.add_parser("compile")
    add_common(compile_parser)
    compile_parser.add_argument("--output", required=True)

    verify_parser = sub.add_parser("verify")
    add_common(verify_parser)
    verify_parser.add_argument("--receipt", required=True)

    args = parser.parse_args(argv)
    try:
        candidates = _load_json_file(args.candidates)
        events = _load_json_file(args.events)
        policy = _load_json_file(args.policy)
        if args.command == "compile":
            receipt = compile_active_claim_portfolio(
                candidates, events, policy, as_of=args.as_of
            )
            _exclusive_write_text(args.output, _pretty_json(receipt))
            print(_summary(receipt))
            return 0
        receipt = _load_json_file(args.receipt)
        valid = verify_active_claim_portfolio_receipt(
            receipt, candidates, events, policy, as_of=args.as_of
        )
        if valid:
            print("valid=true receipt_sha256=%s" % receipt["receipt_sha256"])
            return 0
        print("valid=false")
        return 2
    except ActiveClaimPortfolioError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
