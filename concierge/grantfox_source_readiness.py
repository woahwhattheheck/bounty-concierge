# SPDX-License-Identifier: MIT
"""Deterministic source-readiness receipts for GrantFox issue baselines.

This module binds a verified GrantFox queue receipt to one immutable repository
snapshot and the source references cited by an issue. It detects stale paths and
symbols before a worker applies or begins assignment-dependent implementation.
It performs no provider, GitHub, wallet, submission, or payout mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from concierge.grantfox_queue_gate import verify_receipt as verify_queue_receipt


class GrantFoxSourceReadinessInputError(ValueError):
    """Raised when a source-readiness snapshot is malformed or contradictory."""


_SCHEMA = "grantfox-source-readiness/v1"
_RECEIPT_SCHEMA = "grantfox-source-readiness-receipt/v1"
_MAX_EXPECTATIONS = 200
_MAX_MATCHES_PER_EXPECTATION = 100
_MAX_REPLACEMENTS = 200
_MAX_PATH_CHARS = 1024
_MAX_SYMBOL_CHARS = 512
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
        raise GrantFoxSourceReadinessInputError(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str, *, max_chars: int = 4096) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxSourceReadinessInputError(
            f"{field} must be a non-empty trimmed string"
        )
    if len(value) > max_chars:
        raise GrantFoxSourceReadinessInputError(
            f"{field} exceeds {max_chars} characters"
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise GrantFoxSourceReadinessInputError(
            f"{field} must not contain control characters"
        )
    return value


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GrantFoxSourceReadinessInputError(
            f"{field} must be a non-negative integer"
        )
    return value


def _parse_timestamp(value: Any, field: str) -> datetime:
    raw = _require_string(value, field, max_chars=64)
    if not raw.endswith("Z"):
        raise GrantFoxSourceReadinessInputError(
            f"{field} must be UTC RFC3339 ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxSourceReadinessInputError(
            f"{field} must be valid RFC3339"
        ) from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise GrantFoxSourceReadinessInputError(f"{field} must be UTC")
    return parsed


def _normalize_repo(value: Any, field: str) -> str:
    repo = _require_string(value, field, max_chars=201)
    component = r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})"
    if re.fullmatch(rf"{component}/{component}", repo) is None:
        raise GrantFoxSourceReadinessInputError(
            f"{field} must be an owner/repository name"
        )
    return repo


def _normalize_sha(value: Any, field: str) -> str:
    sha = _require_string(value, field, max_chars=64).casefold()
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise GrantFoxSourceReadinessInputError(
            f"{field} must be a 40-character Git commit/blob SHA"
        )
    return sha


def _normalize_path(value: Any, field: str) -> str:
    path = _require_string(value, field, max_chars=_MAX_PATH_CHARS)
    if path.startswith("/") or path.endswith("/"):
        raise GrantFoxSourceReadinessInputError(
            f"{field} must be a repository-relative file path"
        )
    if "\\" in path or "//" in path:
        raise GrantFoxSourceReadinessInputError(
            f"{field} must use canonical forward-slash separators"
        )
    pieces = path.split("/")
    if any(piece in {"", ".", ".."} for piece in pieces):
        raise GrantFoxSourceReadinessInputError(
            f"{field} must not contain empty, dot, or parent segments"
        )
    return path


def _normalize_symbol(value: Any, field: str) -> str:
    return _require_string(value, field, max_chars=_MAX_SYMBOL_CHARS)


def _normalize_match(value: Any, field: str) -> dict[str, str]:
    item = _require_object(value, field)
    unknown = set(item) - {"path", "blob_sha"}
    if unknown:
        raise GrantFoxSourceReadinessInputError(
            f"{field} contains unsupported fields: {sorted(unknown)}"
        )
    return {
        "path": _normalize_path(item.get("path"), f"{field}.path"),
        "blob_sha": _normalize_sha(item.get("blob_sha"), f"{field}.blob_sha"),
    }


def _normalize_expectations(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list:
        raise GrantFoxSourceReadinessInputError("expectations must be a list")
    if not value:
        raise GrantFoxSourceReadinessInputError("expectations must not be empty")
    if len(value) > _MAX_EXPECTATIONS:
        raise GrantFoxSourceReadinessInputError(
            f"expectations must contain at most {_MAX_EXPECTATIONS} entries"
        )

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        field = f"expectations[{index}]"
        item = _require_object(raw, field)
        unknown = set(item) - {"kind", "value", "matches"}
        if unknown:
            raise GrantFoxSourceReadinessInputError(
                f"{field} contains unsupported fields: {sorted(unknown)}"
            )
        kind = _require_string(item.get("kind"), f"{field}.kind", max_chars=16)
        if kind not in {"path", "symbol"}:
            raise GrantFoxSourceReadinessInputError(
                f"{field}.kind must be path or symbol"
            )
        expected = (
            _normalize_path(item.get("value"), f"{field}.value")
            if kind == "path"
            else _normalize_symbol(item.get("value"), f"{field}.value")
        )
        key = (kind, expected)
        if key in seen:
            raise GrantFoxSourceReadinessInputError(
                f"duplicate expectation: {kind}:{expected}"
            )
        seen.add(key)

        matches = item.get("matches")
        if type(matches) is not list:
            raise GrantFoxSourceReadinessInputError(f"{field}.matches must be a list")
        if len(matches) > _MAX_MATCHES_PER_EXPECTATION:
            raise GrantFoxSourceReadinessInputError(
                f"{field}.matches must contain at most "
                f"{_MAX_MATCHES_PER_EXPECTATION} entries"
            )

        normalized_matches: list[dict[str, str]] = []
        match_seen: set[tuple[str, str]] = set()
        for match_index, match in enumerate(matches):
            normalized_match = _normalize_match(
                match, f"{field}.matches[{match_index}]"
            )
            match_key = (
                normalized_match["path"],
                normalized_match["blob_sha"],
            )
            if match_key in match_seen:
                raise GrantFoxSourceReadinessInputError(
                    f"{field}.matches must not contain duplicates"
                )
            match_seen.add(match_key)
            normalized_matches.append(normalized_match)

        if kind == "path":
            if len(normalized_matches) > 1:
                raise GrantFoxSourceReadinessInputError(
                    f"{field} path expectation may have at most one exact match"
                )
            if normalized_matches and normalized_matches[0]["path"] != expected:
                raise GrantFoxSourceReadinessInputError(
                    f"{field} path match must equal the expected path"
                )

        normalized.append(
            {"kind": kind, "value": expected, "matches": normalized_matches}
        )
    return normalized


def _normalize_replacements(
    value: Any,
    expectation_keys: set[tuple[str, str]],
    missing_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    if type(value) is not list:
        raise GrantFoxSourceReadinessInputError("replacements must be a list")
    if len(value) > _MAX_REPLACEMENTS:
        raise GrantFoxSourceReadinessInputError(
            f"replacements must contain at most {_MAX_REPLACEMENTS} entries"
        )

    normalized: list[dict[str, Any]] = []
    seen_for: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        field = f"replacements[{index}]"
        item = _require_object(raw, field)
        unknown = set(item) - {
            "for_kind",
            "for_value",
            "replacement_kind",
            "replacement_value",
            "evidence",
        }
        if unknown:
            raise GrantFoxSourceReadinessInputError(
                f"{field} contains unsupported fields: {sorted(unknown)}"
            )

        for_kind = _require_string(
            item.get("for_kind"), f"{field}.for_kind", max_chars=16
        )
        if for_kind not in {"path", "symbol"}:
            raise GrantFoxSourceReadinessInputError(
                f"{field}.for_kind must be path or symbol"
            )
        for_value = (
            _normalize_path(item.get("for_value"), f"{field}.for_value")
            if for_kind == "path"
            else _normalize_symbol(item.get("for_value"), f"{field}.for_value")
        )
        source_key = (for_kind, for_value)
        if source_key not in expectation_keys:
            raise GrantFoxSourceReadinessInputError(
                f"{field} must reference an existing expectation"
            )
        if source_key not in missing_keys:
            raise GrantFoxSourceReadinessInputError(
                f"{field} may only replace a missing expectation"
            )
        if source_key in seen_for:
            raise GrantFoxSourceReadinessInputError(
                f"duplicate replacement for {for_kind}:{for_value}"
            )
        seen_for.add(source_key)

        replacement_kind = _require_string(
            item.get("replacement_kind"),
            f"{field}.replacement_kind",
            max_chars=16,
        )
        if replacement_kind not in {"path", "symbol"}:
            raise GrantFoxSourceReadinessInputError(
                f"{field}.replacement_kind must be path or symbol"
            )
        replacement_value = (
            _normalize_path(
                item.get("replacement_value"), f"{field}.replacement_value"
            )
            if replacement_kind == "path"
            else _normalize_symbol(
                item.get("replacement_value"), f"{field}.replacement_value"
            )
        )

        evidence = item.get("evidence")
        if type(evidence) is not list or not evidence:
            raise GrantFoxSourceReadinessInputError(
                f"{field}.evidence must be a non-empty list"
            )
        if len(evidence) > _MAX_MATCHES_PER_EXPECTATION:
            raise GrantFoxSourceReadinessInputError(
                f"{field}.evidence must contain at most "
                f"{_MAX_MATCHES_PER_EXPECTATION} entries"
            )
        normalized_evidence = [
            _normalize_match(entry, f"{field}.evidence[{evidence_index}]")
            for evidence_index, entry in enumerate(evidence)
        ]
        if len({(x["path"], x["blob_sha"]) for x in normalized_evidence}) != len(
            normalized_evidence
        ):
            raise GrantFoxSourceReadinessInputError(
                f"{field}.evidence must not contain duplicates"
            )

        normalized.append(
            {
                "for_kind": for_kind,
                "for_value": for_value,
                "replacement_kind": replacement_kind,
                "replacement_value": replacement_value,
                "evidence": normalized_evidence,
            }
        )
    return normalized


def _queue_identity(queue_receipt: dict[str, Any]) -> tuple[str, str, int]:
    identity = queue_receipt.get("identity")
    if type(identity) is not dict:
        raise GrantFoxSourceReadinessInputError(
            "queue_receipt.identity must be an object"
        )
    owner = identity.get("owner")
    repo = identity.get("repo")
    number = identity.get("issue_number")
    if type(owner) is not str or type(repo) is not str:
        raise GrantFoxSourceReadinessInputError(
            "queue_receipt identity owner/repo must be strings"
        )
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise GrantFoxSourceReadinessInputError(
            "queue_receipt identity issue_number must be positive"
        )
    return owner.casefold(), repo.casefold(), number


def compile_grantfox_source_readiness(request: dict[str, Any]) -> dict[str, Any]:
    """Compile a pinned source baseline into a deterministic advisory receipt."""
    request = _require_object(request, "request")
    if request.get("schema") != _SCHEMA:
        raise GrantFoxSourceReadinessInputError(f"schema must equal {_SCHEMA}")

    queue_receipt = _require_object(request.get("queue_receipt"), "queue_receipt")
    if not verify_queue_receipt(queue_receipt):
        raise GrantFoxSourceReadinessInputError(
            "queue_receipt failed GrantFox queue receipt verification"
        )
    owner, repo, issue_number = _queue_identity(queue_receipt)

    source = _require_object(
        request.get("repository_snapshot"), "repository_snapshot"
    )
    unknown_source = set(source) - {
        "repository_full_name",
        "default_branch",
        "commit_sha",
        "observed_at",
    }
    if unknown_source:
        raise GrantFoxSourceReadinessInputError(
            "repository_snapshot contains unsupported fields: "
            f"{sorted(unknown_source)}"
        )
    repository_full_name = _normalize_repo(
        source.get("repository_full_name"),
        "repository_snapshot.repository_full_name",
    )
    repo_owner, repo_name = repository_full_name.split("/", 1)
    if (repo_owner.casefold(), repo_name.casefold()) != (owner, repo):
        raise GrantFoxSourceReadinessInputError(
            "repository_snapshot identity does not match queue_receipt issue identity"
        )

    default_branch = _require_string(
        source.get("default_branch"),
        "repository_snapshot.default_branch",
        max_chars=255,
    )
    if (
        default_branch.startswith("/")
        or default_branch.endswith("/")
        or ".." in default_branch
    ):
        raise GrantFoxSourceReadinessInputError(
            "repository_snapshot.default_branch must be a canonical branch name"
        )
    commit_sha = _normalize_sha(
        source.get("commit_sha"), "repository_snapshot.commit_sha"
    )
    observed_at = _parse_timestamp(
        source.get("observed_at"), "repository_snapshot.observed_at"
    )
    evaluated_at = _parse_timestamp(request.get("evaluated_at"), "evaluated_at")
    if evaluated_at < observed_at:
        raise GrantFoxSourceReadinessInputError(
            "evaluated_at must not precede repository_snapshot.observed_at"
        )
    max_age = _require_nonnegative_int(
        request.get("max_snapshot_age_seconds", 900), "max_snapshot_age_seconds"
    )
    if max_age < 1 or max_age > 86400:
        raise GrantFoxSourceReadinessInputError(
            "max_snapshot_age_seconds must be between 1 and 86400"
        )
    source_age = int((evaluated_at - observed_at).total_seconds())

    expectations = _normalize_expectations(request.get("expectations"))
    expectation_keys = {(item["kind"], item["value"]) for item in expectations}
    missing = [item for item in expectations if not item["matches"]]
    missing_keys = {(item["kind"], item["value"]) for item in missing}
    replacements = _normalize_replacements(
        request.get("replacements", []), expectation_keys, missing_keys
    )
    replacement_keys = {
        (item["for_kind"], item["for_value"]) for item in replacements
    }

    queue_provider = queue_receipt.get("provider_snapshot")
    if type(queue_provider) is not dict:
        raise GrantFoxSourceReadinessInputError(
            "queue_receipt.provider_snapshot must be an object"
        )
    queue_observed = _parse_timestamp(
        queue_provider.get("observed_at"),
        "queue_receipt.provider_snapshot.observed_at",
    )
    queue_max_age = _require_nonnegative_int(
        queue_provider.get("max_snapshot_age_seconds"),
        "queue_receipt.provider_snapshot.max_snapshot_age_seconds",
    )
    queue_age_at_evaluation = int((evaluated_at - queue_observed).total_seconds())
    if queue_age_at_evaluation < 0:
        raise GrantFoxSourceReadinessInputError(
            "evaluated_at must not precede queue_receipt provider observation"
        )

    reasons: list[str] = []
    if queue_age_at_evaluation > queue_max_age:
        reasons.append("QUEUE_RECEIPT_STALE")
    if source_age > max_age:
        reasons.append("SOURCE_SNAPSHOT_STALE")
    if missing:
        reasons.append("ISSUE_SOURCE_REFERENCES_MISSING")
    unresolved = missing_keys - replacement_keys
    if unresolved:
        reasons.append("UNRESOLVED_SOURCE_DRIFT")

    if "QUEUE_RECEIPT_STALE" in reasons or "SOURCE_SNAPSHOT_STALE" in reasons:
        disposition = "HOLD"
        next_action = "REFRESH_PROVIDER_AND_SOURCE_SNAPSHOTS"
    elif unresolved:
        disposition = "HOLD"
        next_action = "RESOLVE_SOURCE_DRIFT_BEFORE_APPLICATION_OR_IMPLEMENTATION"
    elif missing:
        disposition = "SOURCE_DRIFT_REPLAN"
        next_action = (
            "REPLAN_AGAINST_PINNED_SOURCE_BEFORE_APPLICATION_OR_IMPLEMENTATION"
        )
    else:
        disposition = "SOURCE_ALIGNED"
        next_action = "USE_PINNED_SOURCE_BASELINE"

    normalized_source = {
        "repository_full_name": repository_full_name,
        "default_branch": default_branch,
        "commit_sha": commit_sha,
        "observed_at": source["observed_at"],
        "evaluated_at": request["evaluated_at"],
        "snapshot_age_seconds": source_age,
        "max_snapshot_age_seconds": max_age,
    }
    body = {
        "schema": _RECEIPT_SCHEMA,
        "request_schema": _SCHEMA,
        "queue_receipt": queue_receipt,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
        },
        "queue_disposition": queue_receipt.get("disposition"),
        "repository_snapshot": normalized_source,
        "source_disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "drift": {
            "expectation_count": len(expectations),
            "missing_count": len(missing),
            "resolved_replacement_count": len(replacement_keys),
            "unresolved_count": len(unresolved),
            "missing_references": [
                {"kind": item["kind"], "value": item["value"]} for item in missing
            ],
        },
        "evidence": {
            "expectations": expectations,
            "replacements": replacements,
            "queue_age_at_evaluation_seconds": queue_age_at_evaluation,
        },
        "authority": dict(_AUTHORITY),
    }
    return {**body, "source_receipt_sha256": _sha256_json(body)}


def verify_source_readiness_receipt(receipt: dict[str, Any]) -> bool:
    """Verify queue integrity, source semantics, authority, and receipt digest."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("source_receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    if receipt.get("schema") != _RECEIPT_SCHEMA:
        return False
    if receipt.get("authority") != _AUTHORITY:
        return False
    queue_receipt = receipt.get("queue_receipt")
    if type(queue_receipt) is not dict or not verify_queue_receipt(queue_receipt):
        return False

    try:
        source = _require_object(
            receipt.get("repository_snapshot"), "repository_snapshot"
        )
        evidence = _require_object(receipt.get("evidence"), "evidence")
        reconstructed = {
            "schema": _SCHEMA,
            "queue_receipt": queue_receipt,
            "repository_snapshot": {
                "repository_full_name": source.get("repository_full_name"),
                "default_branch": source.get("default_branch"),
                "commit_sha": source.get("commit_sha"),
                "observed_at": source.get("observed_at"),
            },
            "expectations": evidence.get("expectations"),
            "replacements": evidence.get("replacements"),
            "evaluated_at": source.get("evaluated_at"),
            "max_snapshot_age_seconds": source.get("max_snapshot_age_seconds"),
        }
        expected = compile_grantfox_source_readiness(reconstructed)
    except (GrantFoxSourceReadinessInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    drift = receipt["drift"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"source={receipt['source_disposition']} "
        f"queue={receipt['queue_disposition']} "
        f"issue={identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"missing={drift['missing_count']} unresolved={drift['unresolved_count']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"source_receipt_sha256={receipt['source_receipt_sha256']}"
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
        prog="python -m concierge.grantfox_source_readiness",
        description=(
            "Compile a deterministic, advisory-only source-readiness receipt "
            "for one GrantFox issue and pinned repository snapshot."
        ),
    )
    parser.add_argument("snapshot", help="source-readiness JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)

    try:
        receipt = compile_grantfox_source_readiness(_load_request(args.snapshot))
    except (OSError, json.JSONDecodeError, GrantFoxSourceReadinessInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 2 if receipt["source_disposition"] == "HOLD" else 0


if __name__ == "__main__":
    raise SystemExit(main())
