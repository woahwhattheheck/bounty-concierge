# SPDX-License-Identifier: MIT
"""Fail-closed lease validation for delayed bounty work orders.

A work order is derived from a canonical bounty capture, but builders may consume
it later. This module binds that handoff to the source generation: before source
mutation, compare the original capture with a newly collected capture. The
comparison itself is offline and performs no provider reads.
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from concierge.bounty_capture import CaptureInputError, replay_capture


LEASE_SCHEMA = "bounty-work-order-lease/v1"
HEAD_RECONCILIATION_SCHEMA = "bounty-work-order-head-reconciliation/v1"
_MAX_CAPTURE_BYTES = 4 * 1024 * 1024
_MAX_RECONCILIATION_JSON_BYTES = 64 * 1024
_MAX_RECONCILIATION_PATHS = 512
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class WorkOrderLeaseError(ValueError):
    """A lease input cannot be safely compared."""


def _reconciliation_text(value: Any, name: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\0" in value
        or len(value.encode("utf-8")) > maximum
    ):
        raise WorkOrderLeaseError(f"{name} must be a non-empty bounded string")
    return value


def _reconciliation_sha(value: Any, name: str) -> str:
    value = _reconciliation_text(value, name, maximum=40).lower()
    if _GIT_SHA.fullmatch(value) is None:
        raise WorkOrderLeaseError(f"{name} must be a 40-character Git SHA")
    return value


def _reconciliation_path(value: Any, name: str, *, scope: bool = False) -> str:
    raw = _reconciliation_text(value, name, maximum=4096).replace("\\", "/")
    recursive = scope and raw.endswith("/**")
    if recursive:
        raw = raw[:-3]
    prefix = scope and raw.endswith("/")
    raw = raw.rstrip("/")
    if (
        not raw
        or raw.startswith("/")
        or "//" in raw
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise WorkOrderLeaseError(f"{name} must be a relative repository path")
    if recursive or prefix:
        return raw + "/"
    return raw


def _scope_contains(scope: str, path: str) -> bool:
    return path.startswith(scope) if scope.endswith("/") else path == scope


def _canonical_receipt_sha256(value: dict[str, Any]) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _provider_readback(value: Any) -> Any:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkOrderLeaseError("provider_readback must be canonical JSON") from exc
    if len(raw) > _MAX_RECONCILIATION_JSON_BYTES:
        raise WorkOrderLeaseError("provider_readback exceeds 64 KiB")
    return json.loads(raw.decode("utf-8"))


def reconcile_tracked_head(
    *,
    operation_id: str,
    repo: str,
    branch: str,
    expected_head: str,
    observed_head: str,
    claimed_paths: list[str],
    touched_paths: list[str],
    owner: str,
    source: str,
    provider_readback: Any,
) -> dict[str, Any]:
    """Classify one stale-boundary head read without polling the provider.

    The caller performs exactly one fresh head read and one expected->observed
    compare at a recovery, collision, or publication boundary. This pure helper
    then decides whether the lease should be tombstoned, rebased, or held.
    """
    operation_id = _reconciliation_text(operation_id, "operation_id", maximum=256)
    repo = _reconciliation_text(repo, "repo", maximum=256)
    if _REPOSITORY.fullmatch(repo) is None:
        raise WorkOrderLeaseError("repo must be owner/name")
    branch = _reconciliation_text(branch, "branch", maximum=512)
    expected_head = _reconciliation_sha(expected_head, "expected_head")
    observed_head = _reconciliation_sha(observed_head, "observed_head")
    owner = _reconciliation_text(owner, "owner", maximum=256)
    source = _reconciliation_text(source, "source", maximum=512)
    if not isinstance(claimed_paths, list) or not isinstance(touched_paths, list):
        raise WorkOrderLeaseError("claimed_paths and touched_paths must be lists")
    if len(claimed_paths) > _MAX_RECONCILIATION_PATHS or len(touched_paths) > _MAX_RECONCILIATION_PATHS:
        raise WorkOrderLeaseError("reconciliation path list exceeds 512 entries")

    claimed = sorted(
        set(
            _reconciliation_path(value, f"claimed_paths[{index}]", scope=True)
            for index, value in enumerate(claimed_paths)
        )
    )
    touched = sorted(
        set(
            _reconciliation_path(value, f"touched_paths[{index}]")
            for index, value in enumerate(touched_paths)
        )
    )
    if not claimed:
        raise WorkOrderLeaseError("claimed_paths must contain at least one path")

    moved = observed_head != expected_head
    overlap = sorted(
        path for path in touched if any(_scope_contains(scope, path) for scope in claimed)
    )

    if not moved:
        classification = "UNCHANGED_STALE"
        status = "STALE"
        action = "RETAIN_STALE_LEASE"
        dispatch = False
        tombstone = False
        rebase = False
        next_expected_head = expected_head
    elif not touched:
        classification = "MISSING_COMPARE"
        status = "STALE"
        action = "HOLD_FOR_COMPARE"
        dispatch = False
        tombstone = False
        rebase = False
        next_expected_head = expected_head
    elif overlap:
        all_touched_scoped = all(
            any(_scope_contains(scope, path) for scope in claimed) for path in touched
        )
        all_claimed_touched = all(
            any(_scope_contains(scope, path) for path in touched) for scope in claimed
        )
        classification = (
            "CLAIMED_SCOPE_COMPLETE"
            if all_touched_scoped and all_claimed_touched
            else "CLAIMED_SCOPE_OVERLAP"
        )
        status = "COLLISION_RECONCILIATION"
        action = "TOMBSTONE_DUPLICATE"
        dispatch = False
        tombstone = True
        rebase = False
        next_expected_head = observed_head
    else:
        classification = "ORTHOGONAL_ADVANCE"
        status = "READY"
        action = "REBASE_EXPECTED_HEAD"
        dispatch = True
        tombstone = False
        rebase = True
        next_expected_head = observed_head

    core = {
        "schema": HEAD_RECONCILIATION_SCHEMA,
        "operation_id": operation_id,
        "repo": repo,
        "branch": branch,
        "expected_head": expected_head,
        "observed_head": observed_head,
        "next_expected_head": next_expected_head,
        "claimed_paths": claimed,
        "touched_paths": touched,
        "overlap_paths": overlap,
        "owner": owner,
        "source": source,
        "provider_readback": _provider_readback(provider_readback),
        "classification": classification,
        "status": status,
        "action": action,
        "dispatch": dispatch,
        "tombstone": tombstone,
        "rebase": rebase,
        "authority": {
            "boundary_only": True,
            "continuous_provider_polling": False,
            "provider_mutation": False,
            "source_mutation": False,
            "claim_mutation": False,
            "payment_mutation": False,
        },
    }
    receipt = dict(core)
    receipt["receipt_sha256"] = _canonical_receipt_sha256(core)
    return receipt



def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise WorkOrderLeaseError(f"{name} must be an aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise WorkOrderLeaseError(f"{name} must be an aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkOrderLeaseError(f"{name} must be an aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _capture_threshold(capture: Any) -> int:
    if not isinstance(capture, dict):
        raise WorkOrderLeaseError("capture must be a JSON object")
    policy = capture.get("policy")
    if not isinstance(policy, dict):
        raise WorkOrderLeaseError("capture policy is missing")
    threshold = policy.get("saturation_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise WorkOrderLeaseError("capture saturation_threshold is invalid")
    return threshold


def _validated_projection(capture: dict[str, Any]) -> dict[str, Any]:
    """Validate a capture through the normal replay path, then reduce safe state."""
    threshold = _capture_threshold(capture)
    try:
        _, qualification, evidence = replay_capture(
            capture, saturation_threshold=threshold
        )
    except CaptureInputError as exc:
        raise WorkOrderLeaseError(str(exc)) from exc

    baseline = capture["baseline"]
    return {
        "repo": capture["repo"],
        "number": capture["number"],
        "submission_target": capture.get("submission_target"),
        "policy": capture["policy"],
        "assignment": capture["assignment"],
        "generation": capture["generation"],
        "authority": capture["authority"],
        "canonical_audit": baseline["canonical_audit"],
        "linked_prs": baseline.get("linked_prs", []),
        "qualification": {
            "dispatch": qualification.get("dispatch"),
            "disposition": qualification.get("disposition"),
            "reason_codes": qualification.get("reason_codes", []),
        },
        "capture_receipt_sha256": evidence["capture_receipt_sha256"],
        "source_generation_sha256": evidence["source_generation_sha256"],
        "observed_at": evidence["observed_at"],
        "completed_at": evidence["completed_at"],
    }


def _compare_projections(
    original: dict[str, Any], refreshed: dict[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []

    identity = (original["repo"], original["number"])
    refreshed_identity = (refreshed["repo"], refreshed["number"])
    if refreshed_identity != identity:
        reasons.append("ISSUE_IDENTITY_CHANGED")

    if _timestamp(refreshed["completed_at"], "refreshed completed_at") <= _timestamp(
        original["completed_at"], "original completed_at"
    ):
        reasons.append("REFRESH_NOT_NEWER")

    if original["submission_target"] != refreshed["submission_target"]:
        reasons.append("SUBMISSION_TARGET_CHANGED")
    if original["policy"] != refreshed["policy"]:
        reasons.append("POLICY_CHANGED")
    if original["assignment"] != refreshed["assignment"]:
        reasons.append("ASSIGNMENT_CHANGED")
    if original["generation"] != refreshed["generation"]:
        reasons.append("ISSUE_GENERATION_CHANGED")
    if original["authority"] != refreshed["authority"]:
        reasons.append("AUTHORITY_CHANGED")
    if (
        original["canonical_audit"] != refreshed["canonical_audit"]
        or original["linked_prs"] != refreshed["linked_prs"]
    ):
        reasons.append("COMPETITION_CHANGED")
    if original["qualification"] != refreshed["qualification"]:
        reasons.append("QUALIFICATION_CHANGED")

    semantic_changed = (
        original["source_generation_sha256"]
        != refreshed["source_generation_sha256"]
    )
    if semantic_changed and not any(
        reason
        in {
            "SUBMISSION_TARGET_CHANGED",
            "POLICY_CHANGED",
            "ASSIGNMENT_CHANGED",
            "ISSUE_GENERATION_CHANGED",
            "AUTHORITY_CHANGED",
            "COMPETITION_CHANGED",
            "QUALIFICATION_CHANGED",
        }
        for reason in reasons
    ):
        reasons.append("SOURCE_GENERATION_CHANGED")

    # Preserve first occurrence while keeping deterministic diagnostic order.
    reasons = list(dict.fromkeys(reasons))
    ready = not reasons and not semantic_changed

    return {
        "schema": LEASE_SCHEMA,
        "status": "READY" if ready else "STALE",
        "dispatch": ready,
        "repo": original["repo"],
        "number": original["number"],
        "reason_codes": reasons,
        "original": {
            "capture_receipt_sha256": original["capture_receipt_sha256"],
            "source_generation_sha256": original["source_generation_sha256"],
            "observed_at": original["observed_at"],
            "completed_at": original["completed_at"],
        },
        "refreshed": {
            "capture_receipt_sha256": refreshed["capture_receipt_sha256"],
            "source_generation_sha256": refreshed["source_generation_sha256"],
            "observed_at": refreshed["observed_at"],
            "completed_at": refreshed["completed_at"],
        },
    }


def compare_work_order_captures(
    original_capture: dict[str, Any], refreshed_capture: dict[str, Any]
) -> dict[str, Any]:
    """Return READY only when a later canonical capture has identical semantics."""
    return _compare_projections(
        _validated_projection(original_capture),
        _validated_projection(refreshed_capture),
    )


def _load_capture(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise WorkOrderLeaseError(f"cannot read {path}") from exc
    if size > _MAX_CAPTURE_BYTES:
        raise WorkOrderLeaseError(
            f"{path} exceeds the {_MAX_CAPTURE_BYTES}-byte capture limit"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkOrderLeaseError(f"{path} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise WorkOrderLeaseError(f"{path} must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the capture that authorized a bounty work order with a fresh "
            "capture before source mutation."
        )
    )
    parser.add_argument("original_capture", type=Path)
    parser.add_argument("refreshed_capture", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = compare_work_order_captures(
            _load_capture(args.original_capture),
            _load_capture(args.refreshed_capture),
        )
    except WorkOrderLeaseError as exc:
        if args.json:
            print(json.dumps({"schema": LEASE_SCHEMA, "status": "ERROR", "error": str(exc)}))
        else:
            print(f"ERROR: {exc}")
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        reasons = ",".join(result["reason_codes"]) or "none"
        print(
            f"{result['status']} {result['repo']}#{result['number']} "
            f"reasons={reasons}"
        )
    return 0 if result["dispatch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
