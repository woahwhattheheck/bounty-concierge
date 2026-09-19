# SPDX-License-Identifier: MIT
"""Strict landed-capability fulfillment receipts for GrantFox prerequisites.

This module is a compatibility safety layer over grantfox_dependency_readiness/v1.
That v1 receipt records issue closure, which is not by itself proof that prerequisite
capability landed. This compiler requires explicit, fresh default-branch landing
evidence for every declared prerequisite before emitting DEPENDENCIES_FULFILLED.

It is advisory-only and grants no provider, implementation, submission, payment,
wallet, or reward authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from concierge.grantfox_dependency_readiness import (
    verify_dependency_readiness_receipt,
)


class GrantFoxDependencyFulfillmentInputError(ValueError):
    """Raised when dependency-fulfillment evidence is malformed or contradictory."""


_SCHEMA = "grantfox-dependency-fulfillment/v1"
_RECEIPT_SCHEMA = "grantfox-dependency-fulfillment-receipt/v1"
_MAX_DEPENDENCIES = 100
_HEX40 = re.compile(r"[0-9a-f]{40}")
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
        raise GrantFoxDependencyFulfillmentInputError(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str, *, max_chars: int = 4096) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} must be a non-empty trimmed string"
        )
    if len(value) > max_chars:
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} exceeds {max_chars} characters"
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} must not contain control characters"
        )
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} must be a positive integer"
        )
    return value


def _require_sha(value: Any, field: str) -> str:
    raw = _require_string(value, field, max_chars=40)
    if _HEX40.fullmatch(raw) is None:
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} must be a lowercase 40-hex git commit SHA"
        )
    return raw


def _require_bounded_age(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GrantFoxDependencyFulfillmentInputError(f"{field} must be an integer")
    if value < 1 or value > 86400:
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} must be between 1 and 86400"
        )
    return value


def _parse_timestamp(value: Any, field: str) -> datetime:
    raw = _require_string(value, field, max_chars=64)
    if not raw.endswith("Z"):
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} must be UTC RFC3339 ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxDependencyFulfillmentInputError(
            f"{field} must be valid RFC3339"
        ) from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise GrantFoxDependencyFulfillmentInputError(f"{field} must be UTC")
    return parsed


def _repo_prefix(owner: str, repo: str) -> str:
    return (
        "https://github.com/"
        f"{quote(owner, safe='._-')}/{quote(repo, safe='._-')}"
    )


def _identity(dependency_receipt: dict[str, Any]) -> tuple[str, str, int]:
    identity = _require_object(
        dependency_receipt.get("identity"), "dependency_receipt.identity"
    )
    owner = _require_string(
        identity.get("owner"), "dependency_receipt.identity.owner", max_chars=100
    )
    repo = _require_string(
        identity.get("repo"), "dependency_receipt.identity.repo", max_chars=100
    )
    issue_number = _require_positive_int(
        identity.get("issue_number"), "dependency_receipt.identity.issue_number"
    )
    return owner, repo, issue_number


def _declared_dependencies(dependency_receipt: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = _require_object(
        dependency_receipt.get("evidence"), "dependency_receipt.evidence"
    )
    raw = evidence.get("dependencies")
    if type(raw) is not list:
        raise GrantFoxDependencyFulfillmentInputError(
            "dependency_receipt.evidence.dependencies must be a list"
        )
    if len(raw) > _MAX_DEPENDENCIES:
        raise GrantFoxDependencyFulfillmentInputError(
            f"dependency receipt exceeds {_MAX_DEPENDENCIES} dependencies"
        )
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item_raw in enumerate(raw):
        item = _require_object(
            item_raw, f"dependency_receipt.evidence.dependencies[{index}]"
        )
        number = _require_positive_int(
            item.get("issue_number"),
            f"dependency_receipt.evidence.dependencies[{index}].issue_number",
        )
        if number in seen:
            raise GrantFoxDependencyFulfillmentInputError(
                f"dependency receipt repeats issue #{number}"
            )
        seen.add(number)
        state = _require_string(
            item.get("state"),
            f"dependency_receipt.evidence.dependencies[{index}].state",
            max_chars=16,
        ).casefold()
        result.append({"issue_number": number, "state": state})
    return result


def _normalize_landings(
    value: Any,
    *,
    owner: str,
    repo: str,
    dependency_numbers: set[int],
    evaluated_at: datetime,
    max_age: int,
) -> tuple[list[dict[str, Any]], bool]:
    if type(value) is not list:
        raise GrantFoxDependencyFulfillmentInputError("landings must be a list")
    if len(value) > _MAX_DEPENDENCIES:
        raise GrantFoxDependencyFulfillmentInputError(
            f"landings must contain at most {_MAX_DEPENDENCIES} entries"
        )

    expected_repo = f"{owner}/{repo}"
    prefix = _repo_prefix(owner, repo)
    seen: set[int] = set()
    normalized: list[dict[str, Any]] = []
    any_stale = False

    for index, raw in enumerate(value):
        field = f"landings[{index}]"
        item = _require_object(raw, field)
        allowed = {
            "repository_full_name",
            "issue_number",
            "evidence_kind",
            "evidence_url",
            "pull_request_number",
            "landed_commit_sha",
            "default_branch",
            "observed_default_branch_head_sha",
            "contains_landed_commit",
            "observed_at",
            "basis",
        }
        unknown = set(item) - allowed
        if unknown:
            raise GrantFoxDependencyFulfillmentInputError(
                f"{field} contains unsupported fields: {sorted(unknown)}"
            )

        repository_full_name = _require_string(
            item.get("repository_full_name"),
            f"{field}.repository_full_name",
            max_chars=201,
        )
        if repository_full_name.casefold() != expected_repo.casefold():
            raise GrantFoxDependencyFulfillmentInputError(
                f"{field} must reference {expected_repo}"
            )

        number = _require_positive_int(item.get("issue_number"), f"{field}.issue_number")
        if number not in dependency_numbers:
            raise GrantFoxDependencyFulfillmentInputError(
                f"{field} references undeclared prerequisite issue #{number}"
            )
        if number in seen:
            raise GrantFoxDependencyFulfillmentInputError(
                f"duplicate landing evidence for issue #{number}"
            )
        seen.add(number)

        kind = _require_string(
            item.get("evidence_kind"), f"{field}.evidence_kind", max_chars=32
        )
        if kind not in {"merged_pull_request", "default_branch_commit"}:
            raise GrantFoxDependencyFulfillmentInputError(
                f"{field}.evidence_kind must be merged_pull_request or default_branch_commit"
            )

        landed_sha = _require_sha(
            item.get("landed_commit_sha"), f"{field}.landed_commit_sha"
        )
        observed_head = _require_sha(
            item.get("observed_default_branch_head_sha"),
            f"{field}.observed_default_branch_head_sha",
        )
        default_branch = _require_string(
            item.get("default_branch"), f"{field}.default_branch", max_chars=200
        )
        if item.get("contains_landed_commit") is not True:
            raise GrantFoxDependencyFulfillmentInputError(
                f"{field}.contains_landed_commit must be true"
            )

        evidence_url = _require_string(
            item.get("evidence_url"), f"{field}.evidence_url", max_chars=512
        )
        pr_number: int | None = None
        if kind == "merged_pull_request":
            pr_number = _require_positive_int(
                item.get("pull_request_number"), f"{field}.pull_request_number"
            )
            expected_url = f"{prefix}/pull/{pr_number}"
        else:
            if "pull_request_number" in item:
                raise GrantFoxDependencyFulfillmentInputError(
                    f"{field}.pull_request_number is only valid for merged_pull_request"
                )
            expected_url = f"{prefix}/commit/{landed_sha}"
        if evidence_url.casefold() != expected_url.casefold():
            raise GrantFoxDependencyFulfillmentInputError(
                f"{field}.evidence_url must equal {expected_url}"
            )

        observed_raw = _require_string(
            item.get("observed_at"), f"{field}.observed_at", max_chars=64
        )
        observed_at = _parse_timestamp(observed_raw, f"{field}.observed_at")
        if evaluated_at < observed_at:
            raise GrantFoxDependencyFulfillmentInputError(
                f"{field}.observed_at must not be after evaluated_at"
            )
        age = int((evaluated_at - observed_at).total_seconds())
        any_stale = any_stale or age > max_age

        basis = _require_string(item.get("basis"), f"{field}.basis", max_chars=1024)
        normalized_item = {
            "repository_full_name": expected_repo,
            "issue_number": number,
            "evidence_kind": kind,
            "evidence_url": expected_url,
            "landed_commit_sha": landed_sha,
            "default_branch": default_branch,
            "observed_default_branch_head_sha": observed_head,
            "contains_landed_commit": True,
            "observed_at": observed_raw,
            "snapshot_age_seconds": age,
            "basis": basis,
        }
        if pr_number is not None:
            normalized_item["pull_request_number"] = pr_number
        normalized.append(normalized_item)

    normalized.sort(key=lambda item: item["issue_number"])
    return normalized, any_stale


def compile_grantfox_dependency_fulfillment(request: dict[str, Any]) -> dict[str, Any]:
    """Compile strict landing evidence over a verified dependency-readiness receipt."""
    request = _require_object(request, "request")
    if request.get("schema") != _SCHEMA:
        raise GrantFoxDependencyFulfillmentInputError(f"schema must equal {_SCHEMA}")

    dependency_receipt = _require_object(
        request.get("dependency_receipt"), "dependency_receipt"
    )
    if not verify_dependency_readiness_receipt(dependency_receipt):
        raise GrantFoxDependencyFulfillmentInputError(
            "dependency_receipt failed GrantFox dependency-readiness verification"
        )

    owner, repo, issue_number = _identity(dependency_receipt)
    dependencies = _declared_dependencies(dependency_receipt)
    dependency_numbers = {item["issue_number"] for item in dependencies}

    evaluated_raw = _require_string(
        request.get("evaluated_at"), "evaluated_at", max_chars=64
    )
    evaluated_at = _parse_timestamp(evaluated_raw, "evaluated_at")
    max_age = _require_bounded_age(
        request.get("max_snapshot_age_seconds", 900),
        "max_snapshot_age_seconds",
    )
    landings, stale = _normalize_landings(
        request.get("landings", []),
        owner=owner,
        repo=repo,
        dependency_numbers=dependency_numbers,
        evaluated_at=evaluated_at,
        max_age=max_age,
    )
    landing_by_issue = {item["issue_number"]: item for item in landings}
    missing = sorted(dependency_numbers - set(landing_by_issue))

    upstream_disposition = dependency_receipt.get("dependency_disposition")
    reasons: list[str] = []
    if upstream_disposition != "DEPENDENCIES_CLEAR":
        reasons.append("DEPENDENCY_READINESS_NOT_CLEAR")
    if missing:
        reasons.append("PREREQUISITE_LANDING_EVIDENCE_MISSING")
    if stale:
        reasons.append("FULFILLMENT_EVIDENCE_STALE")

    if "DEPENDENCY_READINESS_NOT_CLEAR" in reasons:
        disposition = "HOLD"
        next_action = "RESOLVE_DEPENDENCY_READINESS_BEFORE_FULFILLMENT"
    elif "FULFILLMENT_EVIDENCE_STALE" in reasons:
        disposition = "HOLD"
        next_action = "REFRESH_DEFAULT_BRANCH_LANDING_EVIDENCE"
    elif missing:
        disposition = "FULFILLMENT_WAIT"
        next_action = "PROVE_PREREQUISITE_CAPABILITY_LANDED"
    else:
        disposition = "DEPENDENCIES_FULFILLED"
        next_action = "CONTINUE_WITH_PROVIDER_AND_ASSIGNMENT_GATES"

    body = {
        "schema": _RECEIPT_SCHEMA,
        "request_schema": _SCHEMA,
        "dependency_receipt": dependency_receipt,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
        },
        "dependency_disposition": upstream_disposition,
        "fulfillment_disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "fulfillment_summary": {
            "dependency_count": len(dependencies),
            "landing_evidence_count": len(landings),
            "missing_issue_numbers": missing,
        },
        "evidence": {
            "landings": landings,
            "evaluated_at": evaluated_raw,
            "max_snapshot_age_seconds": max_age,
        },
        "authority": dict(_AUTHORITY),
    }
    return {**body, "fulfillment_receipt_sha256": _sha256_json(body)}


def verify_dependency_fulfillment_receipt(receipt: dict[str, Any]) -> bool:
    """Recompile from embedded inputs and require exact semantic/digest equality."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("fulfillment_receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    if receipt.get("schema") != _RECEIPT_SCHEMA or receipt.get("authority") != _AUTHORITY:
        return False
    try:
        evidence = _require_object(receipt.get("evidence"), "evidence")
        landings = evidence.get("landings")
        if type(landings) is not list:
            return False
        reconstructed_landings = []
        for item in landings:
            raw = {
                "repository_full_name": item.get("repository_full_name"),
                "issue_number": item.get("issue_number"),
                "evidence_kind": item.get("evidence_kind"),
                "evidence_url": item.get("evidence_url"),
                "landed_commit_sha": item.get("landed_commit_sha"),
                "default_branch": item.get("default_branch"),
                "observed_default_branch_head_sha": item.get(
                    "observed_default_branch_head_sha"
                ),
                "contains_landed_commit": item.get("contains_landed_commit"),
                "observed_at": item.get("observed_at"),
                "basis": item.get("basis"),
            }
            if item.get("evidence_kind") == "merged_pull_request":
                raw["pull_request_number"] = item.get("pull_request_number")
            reconstructed_landings.append(raw)
        expected = compile_grantfox_dependency_fulfillment(
            {
                "schema": _SCHEMA,
                "dependency_receipt": receipt.get("dependency_receipt"),
                "landings": reconstructed_landings,
                "evaluated_at": evidence.get("evaluated_at"),
                "max_snapshot_age_seconds": evidence.get(
                    "max_snapshot_age_seconds"
                ),
            }
        )
    except (GrantFoxDependencyFulfillmentInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    summary = receipt["fulfillment_summary"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"fulfillment={receipt['fulfillment_disposition']} "
        f"dependency={receipt['dependency_disposition']} "
        f"issue={identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"landed={summary['landing_evidence_count']}/{summary['dependency_count']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"fulfillment_receipt_sha256={receipt['fulfillment_receipt_sha256']}"
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
        prog="python -m concierge.grantfox_dependency_fulfillment",
        description=(
            "Compile a strict landed-capability fulfillment receipt over a "
            "GrantFox dependency-readiness receipt."
        ),
    )
    parser.add_argument("snapshot", help="dependency-fulfillment JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)
    try:
        receipt = compile_grantfox_dependency_fulfillment(_load_request(args.snapshot))
    except (
        OSError,
        json.JSONDecodeError,
        GrantFoxDependencyFulfillmentInputError,
    ) as exc:
        parser.error(str(exc))
    print(
        json.dumps(receipt, indent=2, sort_keys=True)
        if args.json
        else format_summary(receipt)
    )
    return 0 if receipt["fulfillment_disposition"] == "DEPENDENCIES_FULFILLED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
