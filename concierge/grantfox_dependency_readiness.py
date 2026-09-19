# SPDX-License-Identifier: MIT
"""Fail-closed dependency-readiness receipts for GrantFox issue baselines.

Consumes a verified source-readiness receipt plus pinned prerequisite issue
observations. It never grants provider, implementation, submission, payment, or
wallet authority.
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

from concierge.grantfox_source_readiness import verify_source_readiness_receipt


class GrantFoxDependencyReadinessInputError(ValueError):
    """Raised when dependency-readiness evidence is malformed or contradictory."""


_SCHEMA = "grantfox-dependency-readiness/v1"
_RECEIPT_SCHEMA = "grantfox-dependency-readiness-receipt/v1"
_MAX_DEPENDENCIES = 100
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
        raise GrantFoxDependencyReadinessInputError(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str, *, max_chars: int = 4096) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxDependencyReadinessInputError(
            f"{field} must be a non-empty trimmed string"
        )
    if len(value) > max_chars:
        raise GrantFoxDependencyReadinessInputError(
            f"{field} exceeds {max_chars} characters"
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise GrantFoxDependencyReadinessInputError(
            f"{field} must not contain control characters"
        )
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GrantFoxDependencyReadinessInputError(
            f"{field} must be a positive integer"
        )
    return value


def _require_bounded_age(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GrantFoxDependencyReadinessInputError(f"{field} must be an integer")
    if value < 1 or value > 86400:
        raise GrantFoxDependencyReadinessInputError(
            f"{field} must be between 1 and 86400"
        )
    return value


def _parse_timestamp(value: Any, field: str) -> datetime:
    raw = _require_string(value, field, max_chars=64)
    if not raw.endswith("Z"):
        raise GrantFoxDependencyReadinessInputError(
            f"{field} must be UTC RFC3339 ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxDependencyReadinessInputError(
            f"{field} must be valid RFC3339"
        ) from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise GrantFoxDependencyReadinessInputError(f"{field} must be UTC")
    return parsed


def _canonical_issue_url(owner: str, repo: str, number: int) -> str:
    return (
        "https://github.com/"
        f"{quote(owner, safe='._-')}/{quote(repo, safe='._-')}/issues/{number}"
    )


def _source_identity(source_receipt: dict[str, Any]) -> tuple[str, str, int]:
    identity = _require_object(source_receipt.get("identity"), "source_receipt.identity")
    owner = _require_string(
        identity.get("owner"), "source_receipt.identity.owner", max_chars=100
    )
    repo = _require_string(
        identity.get("repo"), "source_receipt.identity.repo", max_chars=100
    )
    number = _require_positive_int(
        identity.get("issue_number"), "source_receipt.identity.issue_number"
    )
    return owner, repo, number


def _source_freshness_at_evaluation(
    source_receipt: dict[str, Any],
    *,
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Re-evaluate embedded source/provider observation age at consumption time."""
    source = _require_object(
        source_receipt.get("repository_snapshot"),
        "source_receipt.repository_snapshot",
    )
    source_observed_raw = _require_string(
        source.get("observed_at"),
        "source_receipt.repository_snapshot.observed_at",
        max_chars=64,
    )
    source_observed = _parse_timestamp(
        source_observed_raw,
        "source_receipt.repository_snapshot.observed_at",
    )
    source_max_age = _require_bounded_age(
        source.get("max_snapshot_age_seconds"),
        "source_receipt.repository_snapshot.max_snapshot_age_seconds",
    )
    if evaluated_at < source_observed:
        raise GrantFoxDependencyReadinessInputError(
            "evaluated_at must not precede source_receipt repository observation"
        )
    source_age = int((evaluated_at - source_observed).total_seconds())

    queue = _require_object(
        source_receipt.get("queue_receipt"),
        "source_receipt.queue_receipt",
    )
    provider = _require_object(
        queue.get("provider_snapshot"),
        "source_receipt.queue_receipt.provider_snapshot",
    )
    queue_observed_raw = _require_string(
        provider.get("observed_at"),
        "source_receipt.queue_receipt.provider_snapshot.observed_at",
        max_chars=64,
    )
    queue_observed = _parse_timestamp(
        queue_observed_raw,
        "source_receipt.queue_receipt.provider_snapshot.observed_at",
    )
    queue_max_age = _require_bounded_age(
        provider.get("max_snapshot_age_seconds"),
        "source_receipt.queue_receipt.provider_snapshot.max_snapshot_age_seconds",
    )
    if evaluated_at < queue_observed:
        raise GrantFoxDependencyReadinessInputError(
            "evaluated_at must not precede source_receipt provider observation"
        )
    queue_age = int((evaluated_at - queue_observed).total_seconds())

    return {
        "source_observed_at": source_observed_raw,
        "source_snapshot_age_seconds": source_age,
        "source_max_snapshot_age_seconds": source_max_age,
        "source_snapshot_stale": source_age > source_max_age,
        "queue_observed_at": queue_observed_raw,
        "queue_snapshot_age_seconds": queue_age,
        "queue_max_snapshot_age_seconds": queue_max_age,
        "queue_snapshot_stale": queue_age > queue_max_age,
    }


def _normalize_dependencies(
    value: Any,
    *,
    owner: str,
    repo: str,
    current_issue: int,
    evaluated_at: datetime,
    max_age: int,
) -> tuple[list[dict[str, Any]], bool]:
    if type(value) is not list:
        raise GrantFoxDependencyReadinessInputError("dependencies must be a list")
    if len(value) > _MAX_DEPENDENCIES:
        raise GrantFoxDependencyReadinessInputError(
            f"dependencies must contain at most {_MAX_DEPENDENCIES} entries"
        )

    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    any_stale = False
    expected_repo = f"{owner}/{repo}"

    for index, raw in enumerate(value):
        field = f"dependencies[{index}]"
        item = _require_object(raw, field)
        unknown = set(item) - {
            "repository_full_name",
            "issue_number",
            "issue_url",
            "state",
            "observed_at",
            "basis",
            "completion",
        }
        if unknown:
            raise GrantFoxDependencyReadinessInputError(
                f"{field} contains unsupported fields: {sorted(unknown)}"
            )

        repository_full_name = _require_string(
            item.get("repository_full_name"),
            f"{field}.repository_full_name",
            max_chars=201,
        )
        if repository_full_name.casefold() != expected_repo.casefold():
            raise GrantFoxDependencyReadinessInputError(
                f"{field} must reference a prerequisite in {expected_repo}"
            )

        number = _require_positive_int(
            item.get("issue_number"), f"{field}.issue_number"
        )
        if number == current_issue:
            raise GrantFoxDependencyReadinessInputError(
                f"{field} must not self-depend on issue #{current_issue}"
            )
        if number in seen:
            raise GrantFoxDependencyReadinessInputError(
                f"duplicate dependency issue #{number}"
            )
        seen.add(number)

        issue_url = _require_string(
            item.get("issue_url"), f"{field}.issue_url", max_chars=512
        )
        expected_url = _canonical_issue_url(owner, repo, number)
        if issue_url.casefold() != expected_url.casefold():
            raise GrantFoxDependencyReadinessInputError(
                f"{field}.issue_url must equal canonical prerequisite URL {expected_url}"
            )

        state = _require_string(
            item.get("state"), f"{field}.state", max_chars=16
        ).casefold()
        if state not in {"open", "closed"}:
            raise GrantFoxDependencyReadinessInputError(
                f"{field}.state must be open or closed"
            )

        basis = _require_string(item.get("basis"), f"{field}.basis", max_chars=1024)
        observed_raw = _require_string(
            item.get("observed_at"), f"{field}.observed_at", max_chars=64
        )
        observed_at = _parse_timestamp(observed_raw, f"{field}.observed_at")
        if evaluated_at < observed_at:
            raise GrantFoxDependencyReadinessInputError(
                f"{field}.observed_at must not be after evaluated_at"
            )
        age = int((evaluated_at - observed_at).total_seconds())
        any_stale = any_stale or age > max_age

        completion = _require_object(item.get("completion"), f"{field}.completion")
        completion_unknown = set(completion) - {
            "status", "merged_pr_url", "merge_commit_sha", "observed_at"
        }
        if completion_unknown:
            raise GrantFoxDependencyReadinessInputError(
                f"{field}.completion contains unsupported fields: {sorted(completion_unknown)}"
            )
        completion_status = _require_string(
            completion.get("status"), f"{field}.completion.status", max_chars=24
        ).upper()
        if completion_status not in {"LANDED", "NOT_LANDED", "UNKNOWN"}:
            raise GrantFoxDependencyReadinessInputError(
                f"{field}.completion.status must be LANDED, NOT_LANDED, or UNKNOWN"
            )
        completion_observed_raw = _require_string(
            completion.get("observed_at"), f"{field}.completion.observed_at", max_chars=64
        )
        completion_observed_at = _parse_timestamp(
            completion_observed_raw, f"{field}.completion.observed_at"
        )
        if evaluated_at < completion_observed_at:
            raise GrantFoxDependencyReadinessInputError(
                f"{field}.completion.observed_at must not be after evaluated_at"
            )
        completion_age = int((evaluated_at - completion_observed_at).total_seconds())
        any_stale = any_stale or completion_age > max_age

        merged_pr_url = completion.get("merged_pr_url")
        merge_commit_sha = completion.get("merge_commit_sha")
        if completion_status == "LANDED":
            merged_pr_url = _require_string(
                merged_pr_url, f"{field}.completion.merged_pr_url", max_chars=512
            )
            expected_pr_prefix = (
                "https://github.com/"
                f"{quote(owner, safe='._-')}/{quote(repo, safe='._-')}/pull/"
            )
            if not merged_pr_url.casefold().startswith(expected_pr_prefix.casefold()):
                raise GrantFoxDependencyReadinessInputError(
                    f"{field}.completion.merged_pr_url must reference a PR in {expected_repo}"
                )
            pr_number = merged_pr_url[len(expected_pr_prefix):]
            if not pr_number.isdigit() or int(pr_number) < 1 or str(int(pr_number)) != pr_number:
                raise GrantFoxDependencyReadinessInputError(
                    f"{field}.completion.merged_pr_url must be a canonical GitHub pull URL"
                )
            merge_commit_sha = _require_string(
                merge_commit_sha, f"{field}.completion.merge_commit_sha", max_chars=40
            )
            if re.fullmatch(r"[0-9a-f]{40}", merge_commit_sha) is None:
                raise GrantFoxDependencyReadinessInputError(
                    f"{field}.completion.merge_commit_sha must be 40 lowercase hex characters"
                )
        elif merged_pr_url is not None or merge_commit_sha is not None:
            raise GrantFoxDependencyReadinessInputError(
                f"{field}.completion must not claim merge evidence unless status is LANDED"
            )

        normalized.append(
            {
                "repository_full_name": expected_repo,
                "issue_number": number,
                "issue_url": expected_url,
                "state": state,
                "observed_at": observed_raw,
                "snapshot_age_seconds": age,
                "basis": basis,
                "completion": {
                    "status": completion_status,
                    "merged_pr_url": merged_pr_url,
                    "merge_commit_sha": merge_commit_sha,
                    "observed_at": completion_observed_raw,
                    "snapshot_age_seconds": completion_age,
                },
            }
        )
    return normalized, any_stale


def compile_grantfox_dependency_readiness(request: dict[str, Any]) -> dict[str, Any]:
    """Compile prerequisite observations into a deterministic advisory receipt."""
    request = _require_object(request, "request")
    if request.get("schema") != _SCHEMA:
        raise GrantFoxDependencyReadinessInputError(f"schema must equal {_SCHEMA}")

    source_receipt = _require_object(request.get("source_receipt"), "source_receipt")
    if not verify_source_readiness_receipt(source_receipt):
        raise GrantFoxDependencyReadinessInputError(
            "source_receipt failed GrantFox source-readiness verification"
        )
    owner, repo, issue_number = _source_identity(source_receipt)

    evaluated_raw = _require_string(
        request.get("evaluated_at"), "evaluated_at", max_chars=64
    )
    evaluated_at = _parse_timestamp(evaluated_raw, "evaluated_at")
    max_age = _require_bounded_age(
        request.get("max_snapshot_age_seconds", 900),
        "max_snapshot_age_seconds",
    )
    source_freshness = _source_freshness_at_evaluation(
        source_receipt,
        evaluated_at=evaluated_at,
    )

    dependencies, dependency_stale = _normalize_dependencies(
        request.get("dependencies"),
        owner=owner,
        repo=repo,
        current_issue=issue_number,
        evaluated_at=evaluated_at,
        max_age=max_age,
    )
    open_dependencies = [item for item in dependencies if item["state"] == "open"]
    closed_without_landed = [
        item for item in dependencies
        if item["state"] == "closed" and item["completion"]["status"] != "LANDED"
    ]
    source_disposition = source_receipt.get("source_disposition")

    reasons: list[str] = []
    if source_disposition != "SOURCE_ALIGNED":
        reasons.append("SOURCE_NOT_ALIGNED")
    if source_freshness["source_snapshot_stale"]:
        reasons.append("SOURCE_SNAPSHOT_STALE_AT_DEPENDENCY_EVALUATION")
    if source_freshness["queue_snapshot_stale"]:
        reasons.append("QUEUE_RECEIPT_STALE_AT_DEPENDENCY_EVALUATION")
    if dependency_stale:
        reasons.append("DEPENDENCY_SNAPSHOT_STALE")
    if closed_without_landed:
        reasons.append("PREREQUISITE_CLOSED_WITHOUT_LANDED_EVIDENCE")
    if open_dependencies:
        reasons.append("PREREQUISITE_ISSUES_OPEN")

    if "SOURCE_NOT_ALIGNED" in reasons:
        disposition = "HOLD"
        next_action = "RESOLVE_SOURCE_READINESS_BEFORE_DEPENDENCY_CLEARANCE"
    elif (
        "SOURCE_SNAPSHOT_STALE_AT_DEPENDENCY_EVALUATION" in reasons
        or "QUEUE_RECEIPT_STALE_AT_DEPENDENCY_EVALUATION" in reasons
    ):
        disposition = "HOLD"
        next_action = "REFRESH_SOURCE_READINESS_BEFORE_DEPENDENCY_CLEARANCE"
    elif "DEPENDENCY_SNAPSHOT_STALE" in reasons:
        disposition = "HOLD"
        next_action = "REFRESH_PREREQUISITE_ISSUE_OBSERVATIONS"
    elif "PREREQUISITE_CLOSED_WITHOUT_LANDED_EVIDENCE" in reasons:
        disposition = "HOLD"
        next_action = "PROVE_PREREQUISITE_CAPABILITY_LANDED"
    elif open_dependencies:
        disposition = "DEPENDENCY_WAIT"
        next_action = (
            "WAIT_FOR_PREREQUISITES_BEFORE_ASSIGNMENT_DEPENDENT_IMPLEMENTATION"
        )
    else:
        disposition = "DEPENDENCIES_CLEAR"
        next_action = "CONTINUE_WITH_PROVIDER_AND_ASSIGNMENT_GATES"

    body = {
        "schema": _RECEIPT_SCHEMA,
        "request_schema": _SCHEMA,
        "source_receipt": source_receipt,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
        },
        "source_disposition": source_disposition,
        "dependency_disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "dependency_summary": {
            "dependency_count": len(dependencies),
            "open_count": len(open_dependencies),
            "closed_count": len(dependencies) - len(open_dependencies),
            "landed_count": sum(
                1 for item in dependencies if item["completion"]["status"] == "LANDED"
            ),
            "closed_without_landed_issue_numbers": [
                item["issue_number"] for item in closed_without_landed
            ],
            "open_issue_numbers": [
                item["issue_number"] for item in open_dependencies
            ],
        },
        "evidence": {
            "dependencies": dependencies,
            "evaluated_at": evaluated_raw,
            "max_snapshot_age_seconds": max_age,
            "source_freshness_at_evaluation": source_freshness,
        },
        "authority": dict(_AUTHORITY),
    }
    return {**body, "dependency_receipt_sha256": _sha256_json(body)}


def verify_dependency_readiness_receipt(receipt: dict[str, Any]) -> bool:
    """Recompile the receipt and require exact semantic + digest equality."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("dependency_receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    if (
        receipt.get("schema") != _RECEIPT_SCHEMA
        or receipt.get("authority") != _AUTHORITY
    ):
        return False

    try:
        evidence = _require_object(receipt.get("evidence"), "evidence")
        dependencies = evidence.get("dependencies")
        if type(dependencies) is not list:
            return False
        reconstructed = {
            "schema": _SCHEMA,
            "source_receipt": receipt.get("source_receipt"),
            "dependencies": [
                {
                    "repository_full_name": item.get("repository_full_name"),
                    "issue_number": item.get("issue_number"),
                    "issue_url": item.get("issue_url"),
                    "state": item.get("state"),
                    "observed_at": item.get("observed_at"),
                    "basis": item.get("basis"),
                    "completion": {
                        "status": item.get("completion", {}).get("status")
                        if type(item.get("completion")) is dict else None,
                        "merged_pr_url": item.get("completion", {}).get("merged_pr_url")
                        if type(item.get("completion")) is dict else None,
                        "merge_commit_sha": item.get("completion", {}).get("merge_commit_sha")
                        if type(item.get("completion")) is dict else None,
                        "observed_at": item.get("completion", {}).get("observed_at")
                        if type(item.get("completion")) is dict else None,
                    },
                }
                for item in dependencies
            ],
            "evaluated_at": evidence.get("evaluated_at"),
            "max_snapshot_age_seconds": evidence.get(
                "max_snapshot_age_seconds"
            ),
        }
        expected = compile_grantfox_dependency_readiness(reconstructed)
    except (GrantFoxDependencyReadinessInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    summary = receipt["dependency_summary"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"dependency={receipt['dependency_disposition']} "
        f"source={receipt['source_disposition']} "
        f"issue={identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"open={summary['open_count']}/{summary['dependency_count']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"dependency_receipt_sha256={receipt['dependency_receipt_sha256']}"
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
        prog="python -m concierge.grantfox_dependency_readiness",
        description=(
            "Compile an advisory-only dependency-readiness receipt for one "
            "GrantFox issue and its pinned prerequisite issue observations."
        ),
    )
    parser.add_argument(
        "snapshot", help="dependency-readiness JSON path, or - for stdin"
    )
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)

    try:
        receipt = compile_grantfox_dependency_readiness(
            _load_request(args.snapshot)
        )
    except (
        OSError,
        json.JSONDecodeError,
        GrantFoxDependencyReadinessInputError,
    ) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 0 if receipt["dependency_disposition"] == "DEPENDENCIES_CLEAR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
