# SPDX-License-Identifier: MIT
"""Deterministic batch compiler for GrantFox carrier-census snapshots.

Each child request is compiled through grantfox_carrier_census. The batch adds
duplicate-issue rejection, canonical ordering, disposition counts, and one
parent digest so a discovery wave can suppress stale or occupied issues before
publishing fresh bounty supply. It grants no provider or repository authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from concierge.grantfox_carrier_census import (
    GrantFoxCarrierCensusInputError,
    compile_grantfox_carrier_census,
    verify_carrier_census_receipt,
)


class GrantFoxCarrierBatchInputError(ValueError):
    """Raised when a carrier-census batch is malformed or ambiguous."""


_SCHEMA = "grantfox-carrier-batch/v1"
_CHILD_REQUEST_SCHEMA = "grantfox-carrier-census/v1"
_CHILD_RECEIPT_SCHEMA = "grantfox-carrier-census-receipt/v1"
_MAX_SNAPSHOTS = 500
_DISPOSITIONS = (
    "CLEAR_FOR_QUEUE_EVALUATION",
    "REUSE_EXISTING_CARRIER",
    "REAPPLY_WITH_REUSABLE_CARRIER",
    "REVIEW_CLOSED_CARRIER",
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
        raise GrantFoxCarrierBatchInputError(f"{field} must be an object")
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


def compile_grantfox_carrier_batch(request: dict[str, Any]) -> dict[str, Any]:
    """Compile many carrier-census snapshots into one permutation-stable receipt."""
    request = _require_object(request, "request")
    allowed = {"schema", "snapshots"}
    extras = set(request) - allowed
    if extras:
        raise GrantFoxCarrierBatchInputError(
            f"request contains unsupported fields: {sorted(extras)}"
        )
    if request.get("schema") != _SCHEMA:
        raise GrantFoxCarrierBatchInputError(f"schema must equal {_SCHEMA}")

    snapshots = request.get("snapshots")
    if type(snapshots) is not list:
        raise GrantFoxCarrierBatchInputError("snapshots must be a list")
    if not snapshots:
        raise GrantFoxCarrierBatchInputError("snapshots must not be empty")
    if len(snapshots) > _MAX_SNAPSHOTS:
        raise GrantFoxCarrierBatchInputError(
            f"snapshots must contain at most {_MAX_SNAPSHOTS} entries"
        )

    children: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for index, snapshot in enumerate(snapshots):
        if type(snapshot) is not dict:
            raise GrantFoxCarrierBatchInputError(
                f"snapshots[{index}] must be an object"
            )
        if snapshot.get("schema") != _CHILD_REQUEST_SCHEMA:
            raise GrantFoxCarrierBatchInputError(
                f"snapshots[{index}].schema must equal {_CHILD_REQUEST_SCHEMA}"
            )
        try:
            child = compile_grantfox_carrier_census(snapshot)
        except GrantFoxCarrierCensusInputError as exc:
            raise GrantFoxCarrierBatchInputError(
                f"snapshots[{index}] failed child census: {exc}"
            ) from exc
        key = _canonical_key(child)
        if key in seen:
            raise GrantFoxCarrierBatchInputError(
                f"duplicate canonical issue identity: {_canonical_id(child)}"
            )
        seen.add(key)
        children.append(child)

    children.sort(key=_canonical_key)
    counts = {disposition: 0 for disposition in _DISPOSITIONS}
    buckets = {disposition: [] for disposition in _DISPOSITIONS}
    active_or_merged_carriers = 0
    reusable_process_closed_carriers = 0
    total_relevant_carriers = 0

    for child in children:
        disposition = child.get("disposition")
        if disposition not in counts:
            raise GrantFoxCarrierBatchInputError(
                f"child emitted unsupported disposition: {disposition}"
            )
        canonical_id = _canonical_id(child)
        counts[disposition] += 1
        buckets[disposition].append(canonical_id)
        census = child["census"]
        active_or_merged_carriers += census["active_or_merged_count"]
        reusable_process_closed_carriers += census["process_closed_count"]
        total_relevant_carriers += census["relevant_carrier_count"]

    suppressed = (
        counts["REUSE_EXISTING_CARRIER"]
        + counts["REAPPLY_WITH_REUSABLE_CARRIER"]
        + counts["REVIEW_CLOSED_CARRIER"]
        + counts["HOLD"]
    )
    body = {
        "schema": _SCHEMA,
        "child_request_schema": _CHILD_REQUEST_SCHEMA,
        "child_receipt_schema": _CHILD_RECEIPT_SCHEMA,
        "issue_count": len(children),
        "counts": counts,
        "issues_by_disposition": buckets,
        "summary": {
            "clear_for_queue_count": counts["CLEAR_FOR_QUEUE_EVALUATION"],
            "suppressed_or_review_count": suppressed,
            "relevant_carrier_count": total_relevant_carriers,
            "active_or_merged_carrier_count": active_or_merged_carriers,
            "process_closed_reusable_carrier_count": reusable_process_closed_carriers,
            "rule": (
                "Only CLEAR_FOR_QUEUE_EVALUATION may continue to provider/source "
                "queue evaluation; every other disposition is suppressed or held "
                "for review."
            ),
        },
        "authority": dict(_AUTHORITY),
        "children": children,
    }
    return {**body, "batch_receipt_sha256": _sha256_json(body)}


def verify_carrier_batch_receipt(receipt: dict[str, Any]) -> bool:
    """Verify child receipts, ordering, counts, summaries, authority, and digest."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("batch_receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False

    body = dict(receipt)
    body.pop("batch_receipt_sha256", None)
    if body.get("schema") != _SCHEMA:
        return False
    if body.get("child_request_schema") != _CHILD_REQUEST_SCHEMA:
        return False
    if body.get("child_receipt_schema") != _CHILD_RECEIPT_SCHEMA:
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
    counts = {disposition: 0 for disposition in _DISPOSITIONS}
    buckets = {disposition: [] for disposition in _DISPOSITIONS}
    active = 0
    process_closed = 0
    relevant = 0

    for child in children:
        if not verify_carrier_census_receipt(child):
            return False
        key = _canonical_key(child)
        if key in seen:
            return False
        if prior is not None and key <= prior:
            return False
        seen.add(key)
        prior = key
        disposition = child.get("disposition")
        if disposition not in counts:
            return False
        canonical_id = _canonical_id(child)
        counts[disposition] += 1
        buckets[disposition].append(canonical_id)
        census = child.get("census")
        if type(census) is not dict:
            return False
        a = census.get("active_or_merged_count")
        p = census.get("process_closed_count")
        r = census.get("relevant_carrier_count")
        if any(type(value) is not int or value < 0 for value in (a, p, r)):
            return False
        active += a
        process_closed += p
        relevant += r

    if body.get("counts") != counts or body.get("issues_by_disposition") != buckets:
        return False
    suppressed = (
        counts["REUSE_EXISTING_CARRIER"]
        + counts["REAPPLY_WITH_REUSABLE_CARRIER"]
        + counts["REVIEW_CLOSED_CARRIER"]
        + counts["HOLD"]
    )
    summary = body.get("summary")
    if type(summary) is not dict:
        return False
    if summary.get("clear_for_queue_count") != counts["CLEAR_FOR_QUEUE_EVALUATION"]:
        return False
    if summary.get("suppressed_or_review_count") != suppressed:
        return False
    if summary.get("relevant_carrier_count") != relevant:
        return False
    if summary.get("active_or_merged_carrier_count") != active:
        return False
    if summary.get("process_closed_reusable_carrier_count") != process_closed:
        return False

    return _sha256_json(body) == digest


def format_summary(receipt: dict[str, Any]) -> str:
    counts = receipt["counts"]
    summary = receipt["summary"]
    return (
        f"issues={receipt['issue_count']} "
        f"clear={counts['CLEAR_FOR_QUEUE_EVALUATION']} "
        f"active={counts['REUSE_EXISTING_CARRIER']} "
        f"reapply={counts['REAPPLY_WITH_REUSABLE_CARRIER']} "
        f"review={counts['REVIEW_CLOSED_CARRIER']} "
        f"hold={counts['HOLD']} "
        f"suppressed_or_review={summary['suppressed_or_review_count']} "
        f"batch_receipt_sha256={receipt['batch_receipt_sha256']}"
    )


def _load(path: str) -> dict[str, Any]:
    if path == "-":
        import sys
        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    return _require_object(payload, "request")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.grantfox_carrier_batch",
        description=(
            "Compile many GrantFox carrier censuses into one advisory-only, "
            "permutation-stable supply-hygiene receipt."
        ),
    )
    parser.add_argument("batch", help="carrier-census batch JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full batch receipt")
    args = parser.parse_args(argv)
    try:
        receipt = compile_grantfox_carrier_batch(_load(args.batch))
    except (OSError, json.JSONDecodeError, GrantFoxCarrierBatchInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 2 if receipt["summary"]["suppressed_or_review_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
