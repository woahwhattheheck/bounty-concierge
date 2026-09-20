# SPDX-License-Identifier: MIT
"""Deterministic value routing for bounty intake.

This module applies the operator's nominal-value routing floor before deeper
profitability, claimability, assignment, implementation, or payout gates.
It performs no external mutation and never auto-promotes a low-value pile.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

_SCHEMA = "bounty-value-routing/v1"
_POLICY_SCHEMA = "bounty-value-routing-policy/v1"
_RECEIPT_SCHEMA = "bounty-value-routing-receipt/v1"
_MAX_CANDIDATES = 2000
_MAX_EVIDENCE = 20
_ASSET_RE = re.compile(r"^[A-Z][A-Z0-9]{2,11}$")
_GITHUB_ISSUE_PATH_RE = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$"
)
_GITHUB_ISSUE_COMMENT_RE = re.compile(r"^issuecomment-([1-9][0-9]*)$")
_AUTHORITY = {
    "advisory_only": True,
    "claim_authority": False,
    "implementation_authority": False,
    "submission_authority": False,
    "payment_or_wallet_authority": False,
    "fx_conversion": False,
    "automatic_batch_promotion": False,
}


class BountyValueRoutingInputError(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _obj(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise BountyValueRoutingInputError(f"{field} must be an object")
    return value


def _owned_receipt_json(value: Any, field: str = "receipt", depth: int = 0) -> Any:
    """Copy a receipt into verifier-owned exact JSON types before comparisons."""
    if depth > 100:
        raise BountyValueRoutingInputError(f"{field} exceeds maximum JSON nesting depth")
    kind = type(value)
    if kind is dict:
        out: dict[str, Any] = {}
        for key, child in value.items():
            if type(key) is not str:
                raise BountyValueRoutingInputError(
                    f"{field} contains a non-string JSON object key"
                )
            out[key] = _owned_receipt_json(child, f"{field}.{key}", depth + 1)
        return out
    if kind is list:
        return [
            _owned_receipt_json(child, f"{field}[{index}]", depth + 1)
            for index, child in enumerate(value)
        ]
    if kind in {str, int, bool} or value is None:
        return value
    raise BountyValueRoutingInputError(
        f"{field} must contain only exact built-in JSON types"
    )


def _text(value: Any, field: str, *, max_chars: int = 2048) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise BountyValueRoutingInputError(f"{field} must be a non-empty trimmed string")
    if len(value) > max_chars:
        raise BountyValueRoutingInputError(f"{field} exceeds {max_chars} characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise BountyValueRoutingInputError(f"{field} must not contain control characters")
    return value


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise BountyValueRoutingInputError(
            f"{field} must be an exact decimal string or integer"
        )
    if not isinstance(value, (str, int, Decimal)):
        raise BountyValueRoutingInputError(
            f"{field} must be an exact decimal string or integer"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise BountyValueRoutingInputError(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise BountyValueRoutingInputError(f"{field} must be greater than zero")
    if len(parsed.as_tuple().digits) > 64 or abs(parsed.as_tuple().exponent) > 18:
        raise BountyValueRoutingInputError(f"{field} exceeds supported precision")
    return parsed


def _format_decimal(value: Decimal) -> str:
    out = format(value, "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out or "0"


def _utc(value: Any, field: str) -> datetime:
    raw = _text(value, field, max_chars=64)
    if not raw.endswith("Z"):
        raise BountyValueRoutingInputError(f"{field} must be UTC RFC3339 ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise BountyValueRoutingInputError(f"{field} must be valid RFC3339") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise BountyValueRoutingInputError(f"{field} must be UTC")
    return parsed


def _https_url(
    value: Any,
    field: str,
    *,
    allow_github_issue_comment_fragment: bool = False,
) -> str:
    raw = _text(value, field)
    if any(ch.isspace() for ch in raw) or "\\" in raw:
        raise BountyValueRoutingInputError(f"{field} must be a canonical HTTPS URL")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise BountyValueRoutingInputError(f"{field} must be a valid URL") from exc
    fragment_ok = (
        allow_github_issue_comment_fragment
        and (parsed.hostname or "").casefold() in {"github.com", "www.github.com"}
        and _GITHUB_ISSUE_COMMENT_RE.fullmatch(parsed.fragment or "") is not None
    )
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or (parsed.fragment and not fragment_ok)
    ):
        raise BountyValueRoutingInputError(
            f"{field} must be canonical HTTPS without userinfo/port/query"
            " and may use only a canonical GitHub issue-comment fragment"
        )
    return raw


def _github_issue_identity(value: str, *, allow_comment: bool) -> tuple[str, str, int] | None:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() not in {"github.com", "www.github.com"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
    ):
        return None
    match = _GITHUB_ISSUE_PATH_RE.fullmatch(parsed.path)
    if match is None:
        return None
    if parsed.fragment:
        if not allow_comment or _GITHUB_ISSUE_COMMENT_RE.fullmatch(parsed.fragment) is None:
            return None
    return (match.group(1).casefold(), match.group(2).casefold(), int(match.group(3)))


def _evidence_is_source_bound(evidence: dict[str, Any], canonical_source_url: str) -> bool:
    """Return whether v1 can structurally bind amount evidence to this issue."""
    if evidence["scope"] != "ISSUE_SPECIFIC":
        return False
    source_identity = _github_issue_identity(canonical_source_url, allow_comment=False)
    if source_identity is None:
        return False
    authority = evidence["authority"]
    evidence_url = evidence["evidence_url"]
    if authority == "FIRST_PARTY":
        return (
            _github_issue_identity(evidence_url, allow_comment=False)
            == source_identity
            and not urlsplit(evidence_url).fragment
        )
    if authority == "MAINTAINER":
        parsed = urlsplit(evidence_url)
        return (
            bool(parsed.fragment)
            and _github_issue_identity(evidence_url, allow_comment=True)
            == source_identity
        )
    return False


def _normalize_policy(raw: Any) -> dict[str, Any]:
    policy = _obj(raw, "policy")
    if set(policy) != {"schema", "max_evidence_age_seconds", "routes", "assets"}:
        raise BountyValueRoutingInputError(
            "policy must contain exactly schema, max_evidence_age_seconds, routes, assets"
        )
    if policy.get("schema") != _POLICY_SCHEMA:
        raise BountyValueRoutingInputError(
            f"policy.schema must equal {_POLICY_SCHEMA}"
        )
    max_age = policy.get("max_evidence_age_seconds")
    if isinstance(max_age, bool) or not isinstance(max_age, int) or not (60 <= max_age <= 2_592_000):
        raise BountyValueRoutingInputError(
            "policy.max_evidence_age_seconds must be an integer between 60 and 2592000"
        )
    routes = _obj(policy.get("routes"), "policy.routes")
    if set(routes) != {"main_queue", "pile_10_49"}:
        raise BountyValueRoutingInputError(
            "policy.routes must contain exactly main_queue and pile_10_49"
        )
    normalized_routes = {
        "main_queue": _text(routes["main_queue"], "policy.routes.main_queue", max_chars=80),
        "pile_10_49": _text(routes["pile_10_49"], "policy.routes.pile_10_49", max_chars=80),
    }
    assets = _obj(policy.get("assets"), "policy.assets")
    if not assets:
        raise BountyValueRoutingInputError("policy.assets must not be empty")
    normalized_assets: dict[str, dict[str, str]] = {}
    for asset, limits in sorted(assets.items()):
        if type(asset) is not str or _ASSET_RE.fullmatch(asset) is None:
            raise BountyValueRoutingInputError(
                "policy asset keys must be uppercase nominal asset codes"
            )
        limits = _obj(limits, f"policy.assets.{asset}")
        if set(limits) != {"active_floor", "pile_floor"}:
            raise BountyValueRoutingInputError(
                f"policy.assets.{asset} must contain exactly active_floor and pile_floor"
            )
        active = _decimal(limits["active_floor"], f"policy.assets.{asset}.active_floor")
        pile = _decimal(limits["pile_floor"], f"policy.assets.{asset}.pile_floor")
        if pile >= active:
            raise BountyValueRoutingInputError(
                f"policy.assets.{asset}.pile_floor must be below active_floor"
            )
        normalized_assets[asset] = {
            "active_floor": _format_decimal(active),
            "pile_floor": _format_decimal(pile),
        }
    return {
        "schema": _POLICY_SCHEMA,
        "max_evidence_age_seconds": max_age,
        "routes": normalized_routes,
        "assets": normalized_assets,
    }


def _normalize_evidence(raw: Any, field: str, evaluated_at: datetime, max_age: int) -> dict[str, Any]:
    item = _obj(raw, field)
    allowed = {"scope", "authority", "amount", "asset", "evidence_url", "observed_at"}
    if set(item) != allowed:
        raise BountyValueRoutingInputError(
            f"{field} must contain exactly {sorted(allowed)}"
        )
    scope = _text(item["scope"], f"{field}.scope", max_chars=32).upper()
    if scope not in {"ISSUE_SPECIFIC", "MILESTONE_SPECIFIC", "PROGRAM_GENERIC"}:
        raise BountyValueRoutingInputError(f"{field}.scope is unsupported")
    authority = _text(item["authority"], f"{field}.authority", max_chars=32).upper()
    if authority not in {"FIRST_PARTY", "PROVIDER", "MAINTAINER", "AGGREGATOR"}:
        raise BountyValueRoutingInputError(f"{field}.authority is unsupported")
    amount = _decimal(item["amount"], f"{field}.amount")
    asset = _text(item["asset"], f"{field}.asset", max_chars=12).upper()
    if _ASSET_RE.fullmatch(asset) is None:
        raise BountyValueRoutingInputError(f"{field}.asset is invalid")
    evidence_url = _https_url(
        item["evidence_url"],
        f"{field}.evidence_url",
        allow_github_issue_comment_fragment=True,
    )
    observed_raw = _text(item["observed_at"], f"{field}.observed_at", max_chars=64)
    observed_at = _utc(observed_raw, f"{field}.observed_at")
    if observed_at > evaluated_at:
        raise BountyValueRoutingInputError(f"{field}.observed_at must not be in the future")
    age = int((evaluated_at - observed_at).total_seconds())
    return {
        "scope": scope,
        "authority": authority,
        "amount": _format_decimal(amount),
        "asset": asset,
        "evidence_url": evidence_url,
        "observed_at": observed_raw,
        "age_seconds": age,
        "fresh": age <= max_age,
    }


def _normalize_candidate(raw: Any, index: int, evaluated_at: datetime, policy: dict[str, Any]) -> dict[str, Any]:
    field = f"candidates[{index}]"
    item = _obj(raw, field)
    if set(item) != {"work_id", "canonical_source_url", "reward_evidence"}:
        raise BountyValueRoutingInputError(
            f"{field} must contain exactly work_id, canonical_source_url, reward_evidence"
        )
    work_id = _text(item["work_id"], f"{field}.work_id", max_chars=200)
    source = _https_url(item["canonical_source_url"], f"{field}.canonical_source_url")
    evidence = item["reward_evidence"]
    if type(evidence) is not list:
        raise BountyValueRoutingInputError(f"{field}.reward_evidence must be a list")
    if len(evidence) > _MAX_EVIDENCE:
        raise BountyValueRoutingInputError(
            f"{field}.reward_evidence must contain at most {_MAX_EVIDENCE} entries"
        )
    normalized = [
        _normalize_evidence(
            value,
            f"{field}.reward_evidence[{e_index}]",
            evaluated_at,
            policy["max_evidence_age_seconds"],
        )
        for e_index, value in enumerate(evidence)
    ]
    return {
        "work_id": work_id,
        "canonical_source_url": source,
        "reward_evidence": normalized,
    }


def _route_candidate(candidate: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    trusted_specific = [
        e
        for e in candidate["reward_evidence"]
        if _evidence_is_source_bound(e, candidate["canonical_source_url"])
    ]
    fresh_specific = [e for e in trusted_specific if e["fresh"]]
    reasons: list[str] = []
    selected: dict[str, Any] | None = None

    if trusted_specific and not fresh_specific:
        disposition = "HOLD_STALE_SPECIFIC_AMOUNT"
        reasons.append("SPECIFIC_AMOUNT_EVIDENCE_STALE")
    elif not fresh_specific:
        disposition = "HOLD_ISSUE_AMOUNT_UNVERIFIED"
        reasons.append("NO_FRESH_SOURCE_BOUND_ISSUE_AMOUNT")
    else:
        amount_keys = {(e["asset"], e["amount"]) for e in fresh_specific}
        if len(amount_keys) != 1:
            disposition = "HOLD_CONFLICTING_SPECIFIC_AMOUNTS"
            reasons.append("CONFLICTING_FRESH_SPECIFIC_AMOUNT_EVIDENCE")
        else:
            selected = max(fresh_specific, key=lambda e: e["observed_at"])
            limits = policy["assets"].get(selected["asset"])
            if limits is None:
                disposition = "HOLD_UNSUPPORTED_ASSET"
                reasons.append("NO_NOMINAL_POLICY_FOR_ASSET")
            else:
                amount = Decimal(selected["amount"])
                active = Decimal(limits["active_floor"])
                pile = Decimal(limits["pile_floor"])
                if amount >= active:
                    disposition = "VALUE_50_PLUS"
                elif amount >= pile:
                    disposition = "PILE_10_49"
                else:
                    disposition = "DROP_UNDER_10"

    route = None
    if disposition == "VALUE_50_PLUS":
        route = policy["routes"]["main_queue"]
    elif disposition == "PILE_10_49":
        route = policy["routes"]["pile_10_49"]

    return {
        "work_id": candidate["work_id"],
        "canonical_source_url": candidate["canonical_source_url"],
        "disposition": disposition,
        "recommended_route": route,
        "selected_amount": (
            {
                "amount": selected["amount"],
                "asset": selected["asset"],
                "scope": selected["scope"],
                "authority": selected["authority"],
                "evidence_url": selected["evidence_url"],
                "observed_at": selected["observed_at"],
            }
            if selected is not None
            else None
        ),
        "reason_codes": reasons,
        "reward_evidence": candidate["reward_evidence"],
    }


def compile_bounty_value_routing(request: dict[str, Any]) -> dict[str, Any]:
    request = _obj(request, "request")
    if set(request) != {"schema", "policy", "evaluated_at", "candidates"}:
        raise BountyValueRoutingInputError(
            "request must contain exactly schema, policy, evaluated_at, candidates"
        )
    if request.get("schema") != _SCHEMA:
        raise BountyValueRoutingInputError(f"schema must equal {_SCHEMA}")
    policy = _normalize_policy(request.get("policy"))
    evaluated_raw = _text(request.get("evaluated_at"), "evaluated_at", max_chars=64)
    evaluated_at = _utc(evaluated_raw, "evaluated_at")
    candidates = request.get("candidates")
    if type(candidates) is not list or len(candidates) > _MAX_CANDIDATES:
        raise BountyValueRoutingInputError(
            f"candidates must be a list with at most {_MAX_CANDIDATES} entries"
        )
    normalized_candidates = [
        _normalize_candidate(raw, index, evaluated_at, policy)
        for index, raw in enumerate(candidates)
    ]
    work_ids: set[str] = set()
    source_urls: set[str] = set()
    for candidate in normalized_candidates:
        if candidate["work_id"] in work_ids:
            raise BountyValueRoutingInputError("candidate work_id values must be unique")
        if candidate["canonical_source_url"] in source_urls:
            raise BountyValueRoutingInputError(
                "candidate canonical_source_url values must be unique"
            )
        work_ids.add(candidate["work_id"])
        source_urls.add(candidate["canonical_source_url"])

    routed = [_route_candidate(candidate, policy) for candidate in normalized_candidates]
    dispositions = (
        "VALUE_50_PLUS",
        "PILE_10_49",
        "DROP_UNDER_10",
        "HOLD_STALE_SPECIFIC_AMOUNT",
        "HOLD_ISSUE_AMOUNT_UNVERIFIED",
        "HOLD_CONFLICTING_SPECIFIC_AMOUNTS",
        "HOLD_UNSUPPORTED_ASSET",
    )
    counts = {
        name: sum(1 for row in routed if row["disposition"] == name)
        for name in dispositions
    }
    normalized_input = {
        "schema": _SCHEMA,
        "policy": policy,
        "evaluated_at": evaluated_raw,
        "candidates": [
            {
                "work_id": c["work_id"],
                "canonical_source_url": c["canonical_source_url"],
                "reward_evidence": [
                    {
                        key: evidence[key]
                        for key in (
                            "scope", "authority", "amount", "asset",
                            "evidence_url", "observed_at"
                        )
                    }
                    for evidence in c["reward_evidence"]
                ],
            }
            for c in normalized_candidates
        ],
    }
    body = {
        "schema": _RECEIPT_SCHEMA,
        "input": normalized_input,
        "counts": counts,
        "candidates": routed,
        "routing_rule": (
            "fresh source-bound canonical issue amount only; nominal >=active floor to main; "
            "pile floor..<active to pile; below pile dropped; provider/milestone/mirror "
            "evidence is context-only in v1; no FX or batch auto-promotion"
        ),
        "authority": dict(_AUTHORITY),
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    try:
        receipt = _owned_receipt_json(receipt)
    except (BountyValueRoutingInputError, RecursionError):
        return False
    if type(receipt) is not dict:
        return False
    if receipt.get("schema") != _RECEIPT_SCHEMA:
        return False
    if receipt.get("authority") != _AUTHORITY:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    try:
        expected = compile_bounty_value_routing(receipt.get("input"))
    except (BountyValueRoutingInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    c = receipt["counts"]
    return (
        f"value_50_plus={c['VALUE_50_PLUS']} pile_10_49={c['PILE_10_49']} "
        f"drop_under_10={c['DROP_UNDER_10']} "
        f"hold_unverified={c['HOLD_ISSUE_AMOUNT_UNVERIFIED']} "
        f"receipt_sha256={receipt['receipt_sha256']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_value_router",
        description="Route verified bounty values before deeper economics/claimability gates.",
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.request == "-":
            import sys
            payload = json.load(sys.stdin)
        else:
            payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
        receipt = compile_bounty_value_routing(payload)
    except (OSError, json.JSONDecodeError, BountyValueRoutingInputError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2, sort_keys=True) if args.json else format_summary(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
