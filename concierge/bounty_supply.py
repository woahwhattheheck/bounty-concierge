# SPDX-License-Identifier: MIT
"""Deterministic queue router for source-qualified bounty snapshots.

This module deliberately performs no network I/O and no currency conversion.
Callers fetch/capture canonical evidence once, then reuse that immutable snapshot
only inside an explicit freshness window.  The existing bounty_qualification
gate remains the authority for source safety, live-state completeness,
competition, and advertised reward consistency; this module applies operator
queue economics, evidence freshness, and canonical issue deduplication.

Routes:
    ACTIVE -- fresh, source-qualified fixed USD reward >= active floor.
    MAYBE  -- fresh, source-qualified fixed USD reward >= maybe floor, below active.
    HOLD   -- evidence is incomplete/ambiguous/stale or no fixed USD floor is proven.
    PRUNE  -- terminally rejected evidence or fresh reward below the maybe floor.

Output is safe to persist: source body/comment text is never copied into rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from concierge.bounty_acceptance_safety_gate import (
    BountyAcceptanceSafetyInputError,
    SCHEMA as ACCEPTANCE_SAFETY_SCHEMA,
    compile_bounty_acceptance_safety_gate,
)
from concierge.bounty_qualification import QualificationInputError, qualify_dispatch


_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ROUTE_ORDER = {"ACTIVE": 0, "MAYBE": 1, "HOLD": 2, "PRUNE": 3}
_ACTIVE_FLOOR_USD = Decimal("50")
_MAYBE_FLOOR_USD = Decimal("10")
_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


class SupplyInputError(ValueError):
    """Raised when a supply snapshot cannot be routed safely."""


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise SupplyInputError(f"{name} must be a finite non-negative decimal")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SupplyInputError(
            f"{name} must be a finite non-negative decimal"
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise SupplyInputError(f"{name} must be a finite non-negative decimal")
    return amount


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SupplyInputError(f"{name} must be an offset-aware ISO-8601 timestamp")
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SupplyInputError(
            f"{name} must be an offset-aware ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SupplyInputError(f"{name} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _age_seconds(evaluated: datetime, observed: datetime) -> Decimal:
    delta = evaluated - observed
    return (
        Decimal(delta.days * 86400 + delta.seconds)
        + Decimal(delta.microseconds) / Decimal(1000000)
    )


def _policy(
    active_floor_usd: Any,
    maybe_floor_usd: Any,
    max_age_seconds: Any,
    saturation_threshold: int,
) -> dict[str, Any]:
    active = _decimal(active_floor_usd, "active_floor_usd")
    maybe = _decimal(maybe_floor_usd, "maybe_floor_usd")
    max_age = _decimal(max_age_seconds, "max_age_seconds")

    # Owner economics are an invariant, not a caller preference.  Keeping the
    # parameters for API compatibility is deliberate, but any attempted override
    # fails closed so $10-49 can never be promoted into ACTIVE/main.
    if active != _ACTIVE_FLOOR_USD or maybe != _MAYBE_FLOOR_USD:
        raise SupplyInputError(
            "bounty floors are fixed: active_floor_usd=50 and maybe_floor_usd=10"
        )
    if (
        isinstance(saturation_threshold, bool)
        or not isinstance(saturation_threshold, int)
        or saturation_threshold <= 0
    ):
        raise SupplyInputError("saturation_threshold must be a positive integer")
    return {
        "active_floor_usd": "50",
        "maybe_floor_usd": "10",
        "max_age_seconds": _decimal_text(max_age),
        "saturation_threshold": saturation_threshold,
    }


def _identity(snapshot: dict[str, Any]) -> tuple[str, str, int]:
    repo = snapshot.get("repo")
    number = snapshot.get("number")
    if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo.strip()):
        raise SupplyInputError("repo must be an owner/repository slug")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise SupplyInputError("number must be a positive integer")

    # GitHub owner/repository names are case-insensitive for identity purposes.
    canonical_repo = repo.strip().casefold()
    return f"{canonical_repo}#{number}", canonical_repo, number


def _authoritative_usd(signals: dict[str, Any]) -> Decimal | None:
    advertised = signals.get("advertised_reward_usd", [])
    live = signals.get("live_label_reward_usd", [])
    if len(advertised) == 1:
        return _decimal(advertised[0], "advertised_reward_usd")
    if len(advertised) == 0 and len(live) == 1:
        return _decimal(live[0], "live_label_reward_usd")
    return None


def _receipt(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Return only qualification fields that are safe for persistence/deduping."""
    return {
        "disposition": result["disposition"],
        "reason_codes": list(result.get("reason_codes", [])),
        "signals": result.get("signals", {}),
    }


def _acceptance_text(snapshot: dict[str, Any]) -> str:
    """Collect sponsor-controlled acceptance prose without persisting it."""
    parts: list[str] = []
    for key in ("title", "body"):
        value = snapshot.get(key)
        if isinstance(value, str) and value:
            parts.append(value)

    for key in ("contribution_terms", "requirements"):
        value = snapshot.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))

    comments = snapshot.get("comments")
    if isinstance(comments, list):
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            association = comment.get("author_association")
            body = comment.get("body")
            if (
                isinstance(association, str)
                and association.strip().upper() in _MAINTAINER_ASSOCIATIONS
                and isinstance(body, str)
            ):
                parts.append(body)
    return "\n".join(parts)


def _acceptance_safety(
    snapshot: dict[str, Any],
    *,
    repo: str,
    number: int,
    observed_at: str,
    evaluated_at: str,
) -> dict[str, Any]:
    """Run the broader acceptance-text gate and persist only safe receipt fields."""
    source_text = _acceptance_text(snapshot)
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    issue_url = f"https://github.com/{repo}/issues/{number}"
    request = {
        "schema": ACCEPTANCE_SAFETY_SCHEMA,
        "issue_url": issue_url,
        "source_url": issue_url,
        "source_text": source_text,
        "source_content_sha256": source_digest,
        "observed_at": observed_at,
        "evaluated_at": evaluated_at,
    }
    try:
        receipt = compile_bounty_acceptance_safety_gate(request)
    except BountyAcceptanceSafetyInputError:
        return {
            "disposition": "HOLD_SAFETY_GATE_INPUT_INVALID",
            "reason_codes": ["SAFETY_GATE_INPUT_INVALID"],
            "receipt_sha256": None,
        }
    return {
        "disposition": receipt["disposition"],
        "reason_codes": list(receipt.get("reason_codes", [])),
        "receipt_sha256": receipt["receipt_sha256"],
    }


def _freshness(
    snapshot: dict[str, Any],
    *,
    evaluated: datetime,
    max_age_seconds: Decimal,
) -> tuple[str, str | None, str | None]:
    observed_raw = snapshot.get("observed_at")
    if observed_raw is None:
        return "MISSING", None, None

    observed = _timestamp(observed_raw, "observed_at")
    age = _age_seconds(evaluated, observed)
    observed_text = _timestamp_text(observed)
    age_text = _decimal_text(age)

    if age < 0:
        return "FUTURE", observed_text, age_text
    if age > max_age_seconds:
        return "EXPIRED", observed_text, age_text
    return "FRESH", observed_text, age_text


def route_snapshot(
    snapshot: dict[str, Any],
    *,
    evaluated_at: Any,
    active_floor_usd: Any = "50",
    maybe_floor_usd: Any = "10",
    max_age_seconds: Any = "900",
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Route one canonical bounty snapshot without copying source prose."""
    if not isinstance(snapshot, dict):
        raise SupplyInputError("snapshot must be an object")

    policy = _policy(
        active_floor_usd,
        maybe_floor_usd,
        max_age_seconds,
        saturation_threshold,
    )
    evaluated = _timestamp(evaluated_at, "evaluated_at")
    evaluated_text = _timestamp_text(evaluated)
    canonical_id, repo, number = _identity(snapshot)

    try:
        qualification = qualify_dispatch(
            snapshot, saturation_threshold=saturation_threshold
        )
    except QualificationInputError as exc:
        raise SupplyInputError(str(exc)) from exc

    reward = _authoritative_usd(qualification.get("signals", {}))
    disposition = qualification["disposition"]
    freshness, observed_text, age_text = _freshness(
        snapshot,
        evaluated=evaluated,
        max_age_seconds=Decimal(policy["max_age_seconds"]),
    )
    acceptance_safety = {
        "disposition": "NOT_EVALUATED",
        "reason_codes": [],
        "receipt_sha256": None,
    }
    # Evaluate on every ACTIONABLE row, including stale/future twins. Freshness
    # still owns the route; skipping the gate here made identical snapshots look
    # like semantic conflicts (NOT_EVALUATED vs ACCEPTANCE_TEXT_CLEAR).
    if disposition == "ACTIONABLE":
        acceptance_safety = _acceptance_safety(
            snapshot,
            repo=repo,
            number=number,
            observed_at=observed_text,
            evaluated_at=evaluated_text,
        )

    router_codes: list[str] = []

    if disposition == "REJECT":
        route = "PRUNE"
        queue = None
        router_codes.append("QUALIFICATION_REJECTED")
        reward = None
    elif disposition == "HOLD":
        route = "HOLD"
        queue = None
        router_codes.append("QUALIFICATION_HOLD")
        reward = None
    elif freshness != "FRESH":
        route = "HOLD"
        queue = None
        reward = None
        router_codes.append(
            {
                "MISSING": "SOURCE_OBSERVATION_MISSING",
                "FUTURE": "SOURCE_OBSERVATION_IN_FUTURE",
                "EXPIRED": "SOURCE_EVIDENCE_EXPIRED",
            }[freshness]
        )
    elif acceptance_safety["disposition"] != "ACCEPTANCE_TEXT_CLEAR":
        route = "HOLD"
        queue = None
        reward = None
        router_codes.append("UNTRUSTED_ACCEPTANCE_TEXT")
    elif reward is None:
        # RTC-only and other non-USD offers intentionally stay HOLD.  This layer
        # never invents an exchange rate or treats marketplace prose as fixed USD.
        route = "HOLD"
        queue = None
        router_codes.append("USD_FLOOR_NOT_VERIFIABLE")
    else:
        active = Decimal(policy["active_floor_usd"])
        maybe = Decimal(policy["maybe_floor_usd"])
        if reward >= active:
            route = "ACTIVE"
            queue = "main"
            router_codes.append("MEETS_ACTIVE_USD_FLOOR")
        elif reward >= maybe:
            route = "MAYBE"
            queue = "bounty-pile-10-49"
            router_codes.append("BELOW_ACTIVE_USD_FLOOR")
        else:
            route = "PRUNE"
            queue = None
            router_codes.append("BELOW_MAYBE_USD_FLOOR")

    row = {
        "canonical_id": canonical_id,
        "repo": repo,
        "number": number,
        "route": route,
        "queue": queue,
        "reward_usd": _decimal_text(reward) if reward is not None else None,
        "observed_at": observed_text,
        "evaluated_at": evaluated_text,
        "evidence_age_seconds": age_text,
        "freshness": freshness,
        "qualification_disposition": disposition,
        "qualification_reason_codes": list(
            qualification.get("reason_codes", [])
        ),
        "router_reason_codes": router_codes,
        "policy": policy,
        "qualification_evidence": _safe_evidence(qualification),
        "acceptance_safety_evidence": acceptance_safety,
        "source_row_count": 1,
    }
    row["receipt_sha256"] = _receipt(row)
    return row


def _semantic_acceptance_reasons(row: dict[str, Any]) -> list[str]:
    """Keep only text-derived hold reasons in the duplicate signature.

    Gate input/freshness failures are time-dependent and must not turn two
    otherwise identical generations into CONFLICT.
    """
    evidence = row.get("acceptance_safety_evidence") or {}
    return sorted(
        {
            reason
            for reason in evidence.get("reason_codes", [])
            if isinstance(reason, str) and reason.startswith("REQUESTS_")
        }
    )


def _semantic_signature(row: dict[str, Any]) -> str:
    """Bind source semantics while allowing newer identical observations to win."""
    return _receipt(
        {
            "canonical_id": row["canonical_id"],
            "qualification_disposition": row["qualification_disposition"],
            "qualification_reason_codes": row["qualification_reason_codes"],
            "qualification_evidence": row["qualification_evidence"],
            "acceptance_safety_reason_codes": _semantic_acceptance_reasons(row),
        }
    )


def _observation_sort_key(row: dict[str, Any]) -> datetime:
    # Parse the normalized timestamp rather than sorting ISO text: variable
    # fractional-second precision is not lexicographically time-ordered.
    # Missing evidence is older than any bound observation. A future timestamp
    # remains newest and therefore wins into a fail-closed FUTURE hold.
    observed = row["observed_at"]
    if observed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _timestamp(observed, "observed_at")


def _conflict_row(
    canonical_id: str,
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    repo, issue_text = canonical_id.rsplit("#", 1)
    signatures = sorted({_semantic_signature(candidate) for candidate in rows})
    dispositions = {candidate["qualification_disposition"] for candidate in rows}
    if "REJECT" in dispositions:
        qualification_disposition = "REJECT"
        route = "PRUNE"
        router_codes = [
            "CONFLICTING_DUPLICATE_EVIDENCE",
            "QUALIFICATION_REJECTED",
        ]
    elif "HOLD" in dispositions:
        qualification_disposition = "HOLD"
        route = "HOLD"
        router_codes = [
            "CONFLICTING_DUPLICATE_EVIDENCE",
            "QUALIFICATION_HOLD",
        ]
    else:
        qualification_disposition = "ACTIONABLE"
        route = "HOLD"
        router_codes = ["CONFLICTING_DUPLICATE_EVIDENCE"]

    qualification_reasons = sorted(
        {
            reason
            for candidate in rows
            for reason in candidate.get("qualification_reason_codes", [])
        }
    )
    acceptance_reasons = sorted(
        {
            reason
            for candidate in rows
            for reason in candidate.get("acceptance_safety_evidence", {}).get(
                "reason_codes", []
            )
        }
    )
    row = {
        "canonical_id": canonical_id,
        "repo": repo,
        "number": int(issue_text),
        "route": route,
        "queue": None,
        "reward_usd": None,
        "observed_at": None,
        "evaluated_at": evaluated_at,
        "evidence_age_seconds": None,
        "freshness": "CONFLICT",
        "qualification_disposition": qualification_disposition,
        "qualification_reason_codes": qualification_reasons,
        "router_reason_codes": router_codes,
        "policy": policy,
        "qualification_evidence": {
            "disposition": qualification_disposition,
            "reason_codes": qualification_reasons,
            "signals": {},
        },
        "acceptance_safety_evidence": {
            "disposition": "CONFLICT",
            "reason_codes": acceptance_reasons,
            "receipt_sha256": None,
        },
        "conflict_candidate_signatures": signatures,
        "source_row_count": len(rows),
    }
    row["receipt_sha256"] = _receipt(row)
    return row


def _route_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    reward = (
        Decimal(row["reward_usd"])
        if row["reward_usd"] is not None
        else Decimal("-1")
    )
    return (_ROUTE_ORDER[row["route"]], -reward, row["canonical_id"])


def route_supply(
    snapshots: list[dict[str, Any]],
    *,
    evaluated_at: Any,
    active_floor_usd: Any = "50",
    maybe_floor_usd: Any = "10",
    max_age_seconds: Any = "900",
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Route and deduplicate a collection of canonical bounty snapshots."""
    if not isinstance(snapshots, list):
        raise SupplyInputError("snapshots must be a list")

    policy = _policy(
        active_floor_usd,
        maybe_floor_usd,
        max_age_seconds,
        saturation_threshold,
    )
    evaluated = _timestamp(evaluated_at, "evaluated_at")
    evaluated_text = _timestamp_text(evaluated)
    grouped: dict[str, list[dict[str, Any]]] = {}

    for snapshot in snapshots:
        row = route_snapshot(
            snapshot,
            evaluated_at=evaluated_text,
            active_floor_usd=policy["active_floor_usd"],
            maybe_floor_usd=policy["maybe_floor_usd"],
            max_age_seconds=policy["max_age_seconds"],
            saturation_threshold=saturation_threshold,
        )
        grouped.setdefault(row["canonical_id"], []).append(row)

    rows: list[dict[str, Any]] = []
    for canonical_id in sorted(grouped):
        candidates = grouped[canonical_id]
        signatures = {_semantic_signature(row) for row in candidates}
        if len(signatures) != 1:
            rows.append(
                _conflict_row(canonical_id, candidates, policy, evaluated_text)
            )
            continue

        # Identical source semantics from multiple generations are not a conflict.
        # Bind the newest observation so an old duplicate cannot keep a fresh row
        # stale, while a future-dated newest row still fails closed.
        row = dict(max(candidates, key=_observation_sort_key))
        row["source_row_count"] = len(candidates)
        row["receipt_sha256"] = _receipt(
            {key: value for key, value in row.items() if key != "receipt_sha256"}
        )
        rows.append(row)

    rows.sort(key=_route_sort_key)
    counts = {route: 0 for route in _ROUTE_ORDER}
    for row in rows:
        counts[row["route"]] += 1

    result = {
        "schema_version": 2,
        "evaluated_at": evaluated_text,
        "policy": policy,
        "counts": counts,
        "rows": rows,
    }
    result["receipt_sha256"] = _receipt(result)
    return result


def _load_candidates(path: str) -> list[dict[str, Any]]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

    if isinstance(payload, dict) and "candidates" in payload:
        payload = payload["candidates"]
    elif isinstance(payload, dict):
        payload = [payload]

    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise SupplyInputError(
            "input JSON must be a snapshot, a list of snapshots, or an object "
            "with a candidates list"
        )
    return payload


def format_summary(result: dict[str, Any]) -> str:
    counts = result["counts"]
    return (
        f"ACTIVE={counts['ACTIVE']} MAYBE={counts['MAYBE']} "
        f"HOLD={counts['HOLD']} PRUNE={counts['PRUNE']} "
        f"receipt={result['receipt_sha256']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_supply",
        description=(
            "Route source-qualified bounty snapshots into ACTIVE/MAYBE/HOLD/PRUNE "
            "inside an explicit evidence-freshness window."
        ),
    )
    parser.add_argument("snapshot", help="JSON snapshot path, or - for stdin")
    parser.add_argument(
        "--evaluated-at",
        required=True,
        help="offset-aware ISO-8601 time used to evaluate evidence freshness",
    )
    parser.add_argument(
        "--max-age-seconds",
        default="900",
        help="maximum canonical snapshot age for ACTIVE/MAYBE routing (default: 900)",
    )
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--json", action="store_true", help="Emit full result JSON")
    args = parser.parse_args(argv)

    try:
        candidates = _load_candidates(args.snapshot)
        result = route_supply(
            candidates,
            evaluated_at=args.evaluated_at,
            max_age_seconds=args.max_age_seconds,
            saturation_threshold=args.saturation_threshold,
        )
    except (
        OSError,
        json.JSONDecodeError,
        SupplyInputError,
    ) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
