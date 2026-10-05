#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Partition a sanitized BountyHub intake snapshot without any network calls.

This is an advisory collision-reduction helper, not a claim or assignment system.
It consumes only the shareable projection produced by bountyhub_intake.py.
"""
from __future__ import annotations

import argparse
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


def candidate_groups(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return unique candidate work keys with their already-sanitized listing rows."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot["rows"]:
        if not isinstance(row, dict) or row.get("catalog_candidate") is not True:
            continue
        work_key = row.get("work_key")
        if not isinstance(work_key, str) or not work_key:
            continue
        groups.setdefault(work_key, []).append(row)
    return groups


def _shard_for_slot(
    snapshot: dict[str, Any], worker_slot: int, worker_count: int
) -> dict[str, Any]:
    """Return the deterministic work subset for one exact slot."""
    _validate_worker_slot(worker_slot, worker_count)
    groups = candidate_groups(snapshot)
    assigned = []
    for work_key in sorted(groups):
        if _slot(work_key, worker_count) != worker_slot:
            continue
        listings = []
        for row in groups[work_key]:
            listings.append({
                key: row.get(key)
                for key in (
                    "listing_id", "title", "advertised_usd",
                    "provider_funding_status", "assignment_type",
                    "assignee_username", "reconciliation_reasons",
                )
                if key in row
            })
        assigned.append({"work_key": work_key, "listings": listings})

    return {
        "schema": "bountyhub-work-shard/v1",
        "source_schema": snapshot.get("schema"),
        "source_retrieved_at": snapshot.get("retrieved_at"),
        "source_raw_sha256": snapshot.get("raw_sha256"),
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
    parser.add_argument("--worker-count", type=int, required=True, help="advisory shard count")
    parser.add_argument("--output", type=Path, help="write shard JSON instead of stdout")
    args = parser.parse_args(argv)

    try:
        snapshot = load_snapshot(_read_bounded(args.input))
        if args.worker_index is not None:
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
