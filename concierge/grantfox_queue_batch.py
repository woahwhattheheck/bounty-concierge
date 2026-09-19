# SPDX-License-Identifier: MIT
"""Deterministic batch compiler for GrantFox queue-gate snapshots.

Every child snapshot is compiled through ``grantfox_queue_gate``. The batch
layer adds duplicate-identity rejection, canonical ordering, exact disposition
counts, and a parent receipt. It adds no provider mutation or payment authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from concierge.grantfox_queue_gate import (
    GrantFoxQueueInputError,
    compile_grantfox_queue_gate,
    verify_receipt,
)


class GrantFoxBatchInputError(ValueError):
    """Raised when the batch envelope is malformed or semantically ambiguous."""


_SCHEMA = "grantfox-queue-batch/v1"
_CHILD_SCHEMA = "grantfox-queue-gate/v1"
_MAX_SNAPSHOTS = 500
_DISPOSITIONS = (
    "APPLY_ELIGIBLE",
    "WAIT_ASSIGNMENT",
    "IMPLEMENTATION_ELIGIBLE",
    "HOLD",
)
_AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "implementation_write_authority": False,
    "submission_authority": False,
    "payment_or_wallet_authority": False,
}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GrantFoxBatchInputError(f"{field} must be an object")
    return value


def _canonical_key(receipt: dict[str, Any]) -> tuple[str, str, int]:
    identity = receipt["identity"]
    return (
        identity["owner"].casefold(),
        identity["repo"].casefold(),
        identity["issue_number"],
    )


def _canonical_id(receipt: dict[str, Any]) -> str:
    owner, repo, number = _canonical_key(receipt)
    return f"{owner}/{repo}#{number}"


def compile_grantfox_queue_batch(request: dict[str, Any]) -> dict[str, Any]:
    """Compile a set of issue snapshots into one permutation-stable receipt."""
    request = _require_object(request, "request")
    if request.get("schema") != _SCHEMA:
        raise GrantFoxBatchInputError(f"schema must equal {_SCHEMA}")

    snapshots = request.get("snapshots")
    if type(snapshots) is not list:
        raise GrantFoxBatchInputError("snapshots must be a list")
    if not snapshots:
        raise GrantFoxBatchInputError("snapshots must not be empty")
    if len(snapshots) > _MAX_SNAPSHOTS:
        raise GrantFoxBatchInputError(
            f"snapshots must contain at most {_MAX_SNAPSHOTS} entries"
        )

    child_receipts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for index, snapshot in enumerate(snapshots):
        if type(snapshot) is not dict:
            raise GrantFoxBatchInputError(f"snapshots[{index}] must be an object")
        try:
            child = compile_grantfox_queue_gate(snapshot)
        except GrantFoxQueueInputError as exc:
            raise GrantFoxBatchInputError(
                f"snapshots[{index}] failed child gate: {exc}"
            ) from exc
        key = _canonical_key(child)
        if key in seen:
            raise GrantFoxBatchInputError(
                f"duplicate canonical issue identity: {_canonical_id(child)}"
            )
        seen.add(key)
        child_receipts.append(child)

    child_receipts.sort(key=_canonical_key)

    counts = {disposition: 0 for disposition in _DISPOSITIONS}
    buckets = {disposition: [] for disposition in _DISPOSITIONS}
    possible_discretionary = 0
    for child in child_receipts:
        disposition = child["disposition"]
        if disposition not in counts:
            raise GrantFoxBatchInputError(
                f"child emitted unsupported disposition: {disposition}"
            )
        counts[disposition] += 1
        buckets[disposition].append(_canonical_id(child))
        if child["reward"]["status"] == "POSSIBLE_DISCRETIONARY":
            possible_discretionary += 1

    body = {
        "schema": _SCHEMA,
        "child_schema": _CHILD_SCHEMA,
        "issue_count": len(child_receipts),
        "counts": counts,
        "issues_by_disposition": buckets,
        "reward_summary": {
            "possible_discretionary_count": possible_discretionary,
            "explicit_award_count": 0,
            "verified_payment_count": 0,
            "rule": (
                "Batching never upgrades GrantFox campaign or Maybe Rewarded "
                "metadata into an award, amount, payment, or economic score."
            ),
        },
        "authority": dict(_AUTHORITY),
        "children": child_receipts,
    }
    return {**body, "batch_receipt_sha256": _sha256_json(body)}


def verify_batch_receipt(receipt: dict[str, Any]) -> bool:
    """Verify the parent digest, child digests, counts, uniqueness, and authority."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("batch_receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False

    body = dict(receipt)
    body.pop("batch_receipt_sha256", None)
    if body.get("schema") != _SCHEMA or body.get("child_schema") != _CHILD_SCHEMA:
        return False
    if body.get("authority") != _AUTHORITY:
        return False

    children = body.get("children")
    if type(children) is not list or not children or len(children) > _MAX_SNAPSHOTS:
        return False
    if body.get("issue_count") != len(children):
        return False

    seen: set[tuple[str, str, int]] = set()
    prior: tuple[str, str, int] | None = None
    actual_counts = {disposition: 0 for disposition in _DISPOSITIONS}
    actual_buckets = {disposition: [] for disposition in _DISPOSITIONS}
    possible_discretionary = 0

    for child in children:
        if not verify_receipt(child):
            return False
        key = _canonical_key(child)
        if key in seen:
            return False
        if prior is not None and key <= prior:
            return False
        prior = key
        seen.add(key)
        disposition = child.get("disposition")
        if disposition not in actual_counts:
            return False
        actual_counts[disposition] += 1
        actual_buckets[disposition].append(_canonical_id(child))
        if child.get("reward", {}).get("status") == "POSSIBLE_DISCRETIONARY":
            possible_discretionary += 1

    if body.get("counts") != actual_counts:
        return False
    if body.get("issues_by_disposition") != actual_buckets:
        return False

    reward_summary = body.get("reward_summary")
    if type(reward_summary) is not dict:
        return False
    if reward_summary.get("possible_discretionary_count") != possible_discretionary:
        return False
    if reward_summary.get("explicit_award_count") != 0:
        return False
    if reward_summary.get("verified_payment_count") != 0:
        return False

    return _sha256_json(body) == digest


def format_summary(receipt: dict[str, Any]) -> str:
    counts = receipt["counts"]
    return (
        f"issues={receipt['issue_count']} "
        f"apply={counts['APPLY_ELIGIBLE']} "
        f"wait={counts['WAIT_ASSIGNMENT']} "
        f"implement={counts['IMPLEMENTATION_ELIGIBLE']} "
        f"hold={counts['HOLD']} "
        f"possible_discretionary="
        f"{receipt['reward_summary']['possible_discretionary_count']} "
        f"batch_receipt_sha256={receipt['batch_receipt_sha256']}"
    )


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    return _require_object(payload, "request")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.grantfox_queue_batch",
        description=(
            "Compile many GrantFox queue snapshots into one advisory-only, "
            "permutation-stable receipt."
        ),
    )
    parser.add_argument("batch", help="batch JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full batch receipt")
    args = parser.parse_args(argv)

    try:
        receipt = compile_grantfox_queue_batch(_load_request(args.batch))
    except (OSError, json.JSONDecodeError, GrantFoxBatchInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
