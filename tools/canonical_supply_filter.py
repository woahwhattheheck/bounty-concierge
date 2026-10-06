#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Classify caller-supplied canonical bounty snapshots without network calls."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
SCHEMA = "canonical-supply-snapshot/v1"
OUTPUT_SCHEMA = "canonical-supply-filter/v1"
DECISIONS = (
    "BUILD_ALLOWED",
    "CONTINUE_OWNED",
    "OCCUPIED",
    "PRUNE",
    "VERIFY_REQUIRED",
)
TERMINAL_ISSUE_STATES = {"closed", "deleted", "not_found"}
OPEN_ISSUE_STATES = {"open"}
CONSUMED_REWARD_STATES = {"consumed", "awarded", "paid", "closed"}
AVAILABLE_REWARD_STATES = {"available", "open"}
ACTIVE_CLAIM_STATES = {"active", "claimed", "assigned"}
INACTIVE_CLAIM_STATES = {"released", "rejected", "expired", "withdrawn", "closed"}
ACTIVE_PR_STATES = {"open", "draft"}
TERMINAL_PR_STATES = {"merged"}
INACTIVE_PR_STATES = {"closed"}


class FilterError(ValueError):
    """Invalid offline evidence; safe to display."""


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise FilterError("snapshot exceeds the 2 MiB input limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FilterError("snapshot is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise FilterError("snapshot root must be a JSON object")
    return value


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FilterError(f"{field} must be an ISO timestamp with a timezone")
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise FilterError(f"{field} must be an ISO timestamp with a timezone") from exc
    if stamp.tzinfo is None:
        raise FilterError(f"{field} must include a timezone")
    return stamp.astimezone(timezone.utc)


def normalize_owner(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value.casefold() if value else None


def require_list(item: dict[str, Any], key: str, reasons: list[str]) -> list[Any] | None:
    value = item.get(key)
    if not isinstance(value, list):
        reasons.append(f"{key}_missing_or_invalid")
        return None
    return value


def claim_owners(values: list[Any], reasons: list[str]) -> set[str]:
    owners: set[str] = set()
    for index, claim in enumerate(values):
        if not isinstance(claim, dict):
            reasons.append(f"claims[{index}]_invalid")
            continue
        owner = normalize_owner(claim.get("owner"))
        status = str(claim.get("status", "")).strip().casefold()
        if owner is None or not status:
            reasons.append(f"claims[{index}]_incomplete")
            continue
        if status in ACTIVE_CLAIM_STATES:
            owners.add(owner)
        elif status not in INACTIVE_CLAIM_STATES:
            reasons.append(f"claims[{index}]_status_unknown")
    return owners


def pr_owners(values: list[Any], reasons: list[str]) -> tuple[set[str], bool]:
    active: set[str] = set()
    merged = False
    for index, pr in enumerate(values):
        if not isinstance(pr, dict):
            reasons.append(f"same_scope_prs[{index}]_invalid")
            continue
        owner = normalize_owner(pr.get("owner"))
        state = str(pr.get("state", "")).strip().casefold()
        if owner is None or not state:
            reasons.append(f"same_scope_prs[{index}]_incomplete")
            continue
        if state in ACTIVE_PR_STATES:
            active.add(owner)
        elif state in TERMINAL_PR_STATES:
            merged = True
        elif state not in INACTIVE_PR_STATES:
            reasons.append(f"same_scope_prs[{index}]_state_unknown")
    return active, merged


def classify(
    item: dict[str, Any],
    *,
    owner: str,
    stale: bool,
) -> tuple[str, list[str]]:
    if stale:
        return "VERIFY_REQUIRED", ["snapshot_stale"]

    reasons: list[str] = []
    issue_state = str(item.get("issue_state", "")).strip().casefold()
    reward_state = str(item.get("reward_state", "")).strip().casefold()
    if issue_state in TERMINAL_ISSUE_STATES:
        return "PRUNE", [f"issue_{issue_state}"]
    if reward_state in CONSUMED_REWARD_STATES:
        return "PRUNE", [f"reward_{reward_state}"]
    if issue_state not in OPEN_ISSUE_STATES:
        reasons.append("issue_state_unknown")
    if reward_state not in AVAILABLE_REWARD_STATES:
        reasons.append("reward_state_unknown")

    assignees_raw = require_list(item, "assignees", reasons)
    claims_raw = require_list(item, "claims", reasons)
    prs_raw = require_list(item, "same_scope_prs", reasons)

    assignees: set[str] = set()
    if assignees_raw is not None:
        for index, value in enumerate(assignees_raw):
            normalized = normalize_owner(value)
            if normalized is None:
                reasons.append(f"assignees[{index}]_invalid")
            else:
                assignees.add(normalized)

    claims = claim_owners(claims_raw, reasons) if claims_raw is not None else set()
    prs, merged_pr = pr_owners(prs_raw, reasons) if prs_raw is not None else (set(), False)
    if merged_pr:
        return "PRUNE", ["same_scope_pr_merged"]
    if reasons:
        return "VERIFY_REQUIRED", reasons

    self_owner = owner.casefold()
    active_owners = assignees | claims | prs
    others = active_owners - {self_owner}
    self_present = self_owner in active_owners

    if others:
        result_reasons = [f"active_owner:{name}" for name in sorted(others)]
        if self_present:
            result_reasons.append("ownership_conflict_with_self")
        return "OCCUPIED", result_reasons
    if self_present:
        source_reasons: list[str] = []
        if self_owner in assignees:
            source_reasons.append("self_assigned")
        if self_owner in claims:
            source_reasons.append("self_claim_active")
        if self_owner in prs:
            source_reasons.append("self_same_scope_pr_open")
        return "CONTINUE_OWNED", source_reasons
    return "BUILD_ALLOWED", ["fresh_open_unowned_supply"]


def evaluate(
    snapshot: dict[str, Any],
    *,
    owner: str,
    max_age_seconds: int,
    now: datetime,
) -> dict[str, Any]:
    if snapshot.get("schema") != SCHEMA:
        raise FilterError(f"snapshot schema must be {SCHEMA!r}")
    captured = parse_time(snapshot.get("captured_at"), "captured_at")
    age_seconds = max(0.0, (now - captured).total_seconds())
    stale = age_seconds > max_age_seconds

    items = snapshot.get("items")
    if not isinstance(items, list):
        raise FilterError("snapshot items must be an array")

    output_items: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            decision = "VERIFY_REQUIRED"
            reasons = [f"item_{index}_not_object"]
            rendered: dict[str, Any] = {"row_index": index}
        else:
            decision, reasons = classify(item, owner=owner, stale=stale)
            rendered = dict(item)
        counts[decision] += 1
        rendered.update(
            {
                "canonical_decision": decision,
                "build_allowed": decision in {"BUILD_ALLOWED", "CONTINUE_OWNED"},
                "new_build_allowed": decision == "BUILD_ALLOWED",
                "decision_reasons": reasons,
            }
        )
        output_items.append(rendered)

    return {
        "schema": OUTPUT_SCHEMA,
        "source_schema": SCHEMA,
        "owner": owner,
        "captured_at": captured.isoformat(),
        "evaluated_at": now.isoformat(),
        "snapshot_age_seconds": round(age_seconds, 3),
        "max_age_seconds": max_age_seconds,
        "snapshot_fresh": not stale,
        "counts": {decision: counts.get(decision, 0) for decision in DECISIONS},
        "items": output_items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--owner", required=True, help="current swarm/GitHub owner identity")
    parser.add_argument("--max-age-seconds", type=int, default=900)
    parser.add_argument("--now", help="override evaluation time for offline replay")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        owner = args.owner.strip()
        if not owner:
            raise FilterError("owner must not be empty")
        if args.max_age_seconds <= 0:
            raise FilterError("max-age-seconds must be positive")
        now = parse_time(args.now, "now") if args.now else datetime.now(timezone.utc)
        result = evaluate(
            read_json(args.snapshot),
            owner=owner,
            max_age_seconds=args.max_age_seconds,
            now=now,
        )
        payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(payload, encoding="utf-8")
        else:
            sys.stdout.write(payload)
    except (FilterError, OSError) as exc:
        print(f"canonical-supply-filter: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
