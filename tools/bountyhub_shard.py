#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Partition a sanitized BountyHub intake snapshot without any network calls.

This is an advisory collision-reduction helper, not a claim or assignment system.
It consumes only the shareable projection produced by bountyhub_intake.py.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

MAX_BYTES = 4 * 1024 * 1024
MAX_WORKERS = 4096


class ShardError(ValueError):
    """Invalid sanitized snapshot or shard arguments."""


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ShardError("snapshot exceeds the 4 MiB limit")
    return raw


def load_snapshot(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise ShardError("snapshot exceeds the 4 MiB limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ShardError("snapshot is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ShardError("snapshot must be a JSON object")
    schema = payload.get("schema")
    if not isinstance(schema, str) or not schema.startswith("bountyhub-public-intake/"):
        raise ShardError("snapshot is not a sanitized BountyHub intake projection")
    if not isinstance(payload.get("rows"), list):
        raise ShardError("snapshot must contain a rows list")
    return payload


def _validate_worker_count(worker_count: int) -> None:
    if type(worker_count) is not int or not 1 <= worker_count <= MAX_WORKERS:
        raise ShardError(f"worker-count must be between 1 and {MAX_WORKERS}")


def _validate_worker_slot(worker_slot: int, worker_count: int) -> None:
    _validate_worker_count(worker_count)
    if type(worker_slot) is not int or not 0 <= worker_slot < worker_count:
        raise ShardError("worker-index must be between 0 and worker-count - 1")


def _slot(value: str, worker_count: int) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % worker_count


def reconciliation_status(snapshot: dict[str, Any]) -> str:
    """Age-check a shared overlay again at consumption, not only at creation."""
    if "github_reconciliation" not in snapshot:
        return "not_applied"
    meta = snapshot["github_reconciliation"]
    try:
        if not isinstance(meta, dict):
            return "invalid"
        observed = datetime.fromisoformat(meta["observed_at"].replace("Z", "+00:00"))
        catalog_stamp = meta.get("catalog_observed_at")
        catalog_freshness = meta.get("catalog_freshness")
        legacy = (
            snapshot.get("schema") == "bountyhub-public-intake/compact-v1"
            and catalog_freshness == "legacy_compact_without_timestamp"
            and "retrieved_at" not in snapshot
        )
        if catalog_freshness != "fresh" and not legacy:
            return "invalid"
        catalog_observed = (
            datetime.fromisoformat(catalog_stamp.replace("Z", "+00:00"))
            if isinstance(catalog_stamp, str) else None
        )
        maximum = meta["max_age_seconds"]
        if (observed.tzinfo is None or type(maximum) is not int
                or maximum <= 0 or
                (not legacy and (catalog_observed is None or catalog_observed.tzinfo is None))):
            return "invalid"
        now = datetime.now(timezone.utc)
        ages = ((now - observed).total_seconds(),)
        if catalog_observed is not None:
            ages += ((now - catalog_observed).total_seconds(),)
        return "future" if any(age < 0 for age in ages) else (
            "stale" if any(age > maximum for age in ages) else "fresh"
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return "invalid"


def candidate_groups(
    snapshot: dict[str, Any], *, overlay_status: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Return unique candidate work keys with their already-sanitized listing rows."""
    groups: dict[str, list[dict[str, Any]]] = {}
    status = overlay_status if overlay_status is not None else reconciliation_status(snapshot)
    for row in snapshot["rows"]:
        if not isinstance(row, dict) or row.get("catalog_candidate") is not True:
            continue
        if status != "not_applied" and (
            status != "fresh" or row.get("reconciled_candidate") is not True
        ):
            continue
        work_key = row.get("work_key")
        if not isinstance(work_key, str) or not work_key:
            continue
        groups.setdefault(work_key, []).append(row)
    return groups


def _project_listings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only sanitized listing fields needed by workers."""
    return [
        {
            key: row.get(key)
            for key in (
                "listing_id", "title", "advertised_usd",
                "provider_funding_status", "assignment_type",
                "assignee_username", "reconciliation_reasons",
            )
            if key in row
        }
        for row in rows
    ]


def _shard_for_slot(
    snapshot: dict[str, Any], worker_slot: int, worker_count: int
) -> dict[str, Any]:
    """Return the deterministic work subset for one exact slot."""
    _validate_worker_slot(worker_slot, worker_count)
    overlay_status = reconciliation_status(snapshot)
    groups = candidate_groups(snapshot, overlay_status=overlay_status)
    assigned = []
    for work_key in sorted(groups):
        if _slot(work_key, worker_count) != worker_slot:
            continue
        assigned.append({
            "work_key": work_key,
            "listings": _project_listings(groups[work_key]),
        })

    return {
        "schema": "bountyhub-work-shard/v1",
        "source_schema": snapshot.get("schema"),
        "source_retrieved_at": snapshot.get("retrieved_at"),
        "source_raw_sha256": snapshot.get("raw_sha256"),
        "github_reconciliation_status": overlay_status,
        "algorithm": "sha256-mod-v1",
        "worker_count": worker_count,
        "worker_slot": worker_slot,
        "candidate_work_key_count": len(groups),
        "assigned_work_key_count": len(assigned),
        "work": assigned,
        "interpretation": [
            "This shard is advisory collision reduction, not a BountyHub claim or team lock.",
            "Explicit worker indexes are disjoint only when the wave assigns each index to at most one worker.",
            "Reconcile live GitHub state and current swarm ownership before implementation.",
            "Duplicate provider cards for one work_key stay grouped and are never summed.",
            "Use one shared sanitized snapshot; this tool performs zero network requests.",
            "When an overlay is supplied, stale/future/invalid observations yield no shard; refresh the shared capture, not one copy per agent.",
        ],
    }


def shard(snapshot: dict[str, Any], worker_key: str, worker_count: int) -> dict[str, Any]:
    """Backward-compatible hashed worker-key assignment."""
    if not isinstance(worker_key, str) or not worker_key.strip() or len(worker_key) > 200:
        raise ShardError("worker-key must be 1-200 non-whitespace characters")
    _validate_worker_count(worker_count)
    worker_slot = _slot(worker_key, worker_count)
    result = _shard_for_slot(snapshot, worker_slot, worker_count)
    result["assignment_mode"] = "hashed-worker-key"
    result["worker_key_sha256"] = hashlib.sha256(
        worker_key.encode("utf-8")
    ).hexdigest()
    return result


def shard_indexed(
    snapshot: dict[str, Any], worker_index: int, worker_count: int
) -> dict[str, Any]:
    """Assign an exact, orchestrator-provided slot with no worker-slot collisions."""
    result = _shard_for_slot(snapshot, worker_index, worker_count)
    result["assignment_mode"] = "explicit-worker-index"
    result["worker_index"] = worker_index
    return result


def plan_indexed(
    snapshot: dict[str, Any], worker_count: int, algorithm: str = "sha256-mod-v1"
) -> dict[str, Any]:
    """Build one authoritative manifest for every explicit worker slot."""
    _validate_worker_count(worker_count)
    if algorithm not in {"sha256-mod-v1", "balanced-active-v1"}:
        raise ShardError("unknown wave-plan algorithm")
    overlay_status = reconciliation_status(snapshot)
    groups = candidate_groups(snapshot, overlay_status=overlay_status)
    keys = sorted(groups)
    slots = [
        {"worker_index": index, "assigned_work_key_count": 0, "work": []}
        for index in range(worker_count)
    ]
    active_count = min(worker_count, len(keys))
    for position, work_key in enumerate(keys):
        worker_index = (
            position % active_count
            if algorithm == "balanced-active-v1" and active_count
            else _slot(work_key, worker_count)
        )
        slots[worker_index]["work"].append({
            "work_key": work_key,
            "listings": _project_listings(groups[work_key]),
        })
        slots[worker_index]["assigned_work_key_count"] += 1

    counts = [slot["assigned_work_key_count"] for slot in slots]
    active_worker_indices = [index for index, count in enumerate(counts) if count > 0]
    fingerprint_payload = "".join(f"{key}\n" for key in keys).encode("utf-8")
    return {
        "schema": "bountyhub-wave-plan/v1",
        "source_schema": snapshot.get("schema"),
        "source_retrieved_at": snapshot.get("retrieved_at"),
        "source_raw_sha256": snapshot.get("raw_sha256"),
        "github_reconciliation_status": overlay_status,
        "algorithm": algorithm,
        "worker_count": worker_count,
        "candidate_work_key_count": len(keys),
        "candidate_set_sha256": hashlib.sha256(fingerprint_payload).hexdigest(),
        "nonempty_worker_count": len(active_worker_indices),
        "idle_worker_count": worker_count - len(active_worker_indices),
        "active_worker_indices": active_worker_indices,
        "max_assigned_work_key_count": max(counts, default=0),
        "slot_work_key_counts": counts,
        "slots": slots,
        "interpretation": [
            "Publish one shared plan per wave and assign each worker_index at most once.",
            "Every eligible work_key appears in exactly one slot under this worker_count.",
            "Dispatch only active_worker_indices when dedicating workers to this plan; idle slots have no BountyHub work.",
            "balanced-active-v1 uses the smallest active prefix needed for the candidate set and keeps slot loads within one work key.",
            "This plan is advisory collision reduction, not a provider assignment.",
            "Reconcile live source state and current swarm ownership before implementation.",
            "Duplicate provider cards for one work_key stay grouped and are never summed.",
            "The planner performs zero network requests; refresh the shared capture when stale.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="sanitized intake JSON")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--worker-key",
        help="ephemeral worker/session identity; hashes into a slot and may collide",
    )
    selector.add_argument(
        "--worker-index",
        type=int,
        help="explicit zero-based slot assigned uniquely by the wave orchestrator",
    )
    selector.add_argument(
        "--all-workers",
        action="store_true",
        help="emit one authoritative manifest containing every explicit worker slot",
    )
    parser.add_argument("--worker-count", type=int, required=True, help="advisory shard count")
    parser.add_argument(
        "--plan-algorithm",
        choices=("sha256-mod-v1", "balanced-active-v1"),
        default="sha256-mod-v1",
        help="assignment algorithm for --all-workers; default preserves hashed placement",
    )
    parser.add_argument("--output", type=Path, help="write shard JSON instead of stdout")
    args = parser.parse_args(argv)

    try:
        snapshot = load_snapshot(_read_bounded(args.input))
        if args.all_workers:
            result = plan_indexed(snapshot, args.worker_count, args.plan_algorithm)
        elif args.plan_algorithm != "sha256-mod-v1":
            raise ShardError("--plan-algorithm is only valid with --all-workers")
        elif args.worker_index is not None:
            result = shard_indexed(snapshot, args.worker_index, args.worker_count)
        else:
            result = shard(snapshot, args.worker_key, args.worker_count)
        output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (ShardError, OSError) as exc:
        print(f"bountyhub-shard: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
