# SPDX-License-Identifier: MIT
"""Tamper-evident advisory census for GrantFox issue implementation carriers.

This module answers a narrow question before a GrantFox issue is emitted as fresh
supply: does current GitHub evidence already contain an implementation carrier
for the same canonical issue?  It does not apply, assign, mutate repositories,
or grant provider/payment authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class GrantFoxCarrierCensusInputError(ValueError):
    """Raised when carrier-census evidence is malformed or ambiguous."""


_SCHEMA = "grantfox-carrier-census/v1"
_RECEIPT_SCHEMA = "grantfox-carrier-census-receipt/v1"
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_ISSUE_RE = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$")
_PR_RE = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_URL_CHARS = 2048
_AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "implementation_write_authority": False,
    "submission_authority": False,
    "payment_or_wallet_authority": False,
}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GrantFoxCarrierCensusInputError(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxCarrierCensusInputError(f"{field} must be a non-empty trimmed string")
    return value


def _strict_url(value: Any, field: str) -> tuple[str, str]:
    raw = _require_string(value, field)
    if len(raw) > _MAX_URL_CHARS:
        raise GrantFoxCarrierCensusInputError(f"{field} exceeds {_MAX_URL_CHARS} characters")
    if any(ch.isspace() or ord(ch) == 0x7F for ch in raw):
        raise GrantFoxCarrierCensusInputError(f"{field} must not contain whitespace")
    if "\\" in raw or "%" in raw or "?" in raw or "#" in raw:
        raise GrantFoxCarrierCensusInputError(f"{field} must be canonical and unaliased")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise GrantFoxCarrierCensusInputError(f"{field} must be a valid URL") from exc
    if parsed.scheme.casefold() != "https":
        raise GrantFoxCarrierCensusInputError(f"{field} must use https")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise GrantFoxCarrierCensusInputError(f"{field} must not contain userinfo or a port")
    host = (parsed.hostname or "").casefold()
    if host not in _GITHUB_HOSTS:
        raise GrantFoxCarrierCensusInputError(f"{field} must use github.com")
    if parsed.query or parsed.fragment or parsed.path.startswith("//") or "//" in parsed.path:
        raise GrantFoxCarrierCensusInputError(f"{field} must be canonical and unaliased")
    return host, parsed.path


def _issue_identity(value: Any, field: str) -> tuple[str, str, int]:
    _, path = _strict_url(value, field)
    match = _ISSUE_RE.fullmatch(path)
    if match is None:
        raise GrantFoxCarrierCensusInputError(f"{field} is not a canonical GitHub issue URL")
    owner, repo, number = match.groups()
    return owner.casefold(), repo.casefold(), int(number)


def _pr_identity(value: Any, field: str) -> tuple[str, str, int]:
    _, path = _strict_url(value, field)
    match = _PR_RE.fullmatch(path)
    if match is None:
        raise GrantFoxCarrierCensusInputError(f"{field} is not a canonical GitHub pull URL")
    owner, repo, number = match.groups()
    return owner.casefold(), repo.casefold(), int(number)


def _parse_timestamp(value: Any, field: str) -> datetime:
    raw = _require_string(value, field)
    if not raw.endswith("Z"):
        raise GrantFoxCarrierCensusInputError(f"{field} must be UTC RFC3339 ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxCarrierCensusInputError(f"{field} must be valid RFC3339") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise GrantFoxCarrierCensusInputError(f"{field} must be UTC")
    return parsed


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GrantFoxCarrierCensusInputError(f"{field} must be a non-negative integer")
    return value


def _normalize_observations(
    value: Any,
    *,
    issue_owner: str,
    issue_repo: str,
) -> list[dict[str, Any]]:
    if type(value) is not list:
        raise GrantFoxCarrierCensusInputError("carriers must be a list")
    if len(value) > 100:
        raise GrantFoxCarrierCensusInputError("carriers must contain at most 100 observations")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for index, raw in enumerate(value):
        carrier = _require_object(raw, f"carriers[{index}]")
        allowed = {"pr_url", "state", "issue_relation", "process_disposition", "head_sha"}
        extras = set(carrier) - allowed
        if extras:
            raise GrantFoxCarrierCensusInputError(
                f"carriers[{index}] contains unsupported fields: {sorted(extras)}"
            )
        pr_url = _require_string(carrier.get("pr_url"), f"carriers[{index}].pr_url")
        owner, repo, number = _pr_identity(pr_url, f"carriers[{index}].pr_url")
        if (owner, repo) != (issue_owner, issue_repo):
            raise GrantFoxCarrierCensusInputError(
                f"carriers[{index}].pr_url must belong to the canonical issue repository"
            )
        identity = (owner, repo, number)
        if identity in seen:
            raise GrantFoxCarrierCensusInputError("carriers must not contain duplicate pull requests")
        seen.add(identity)
        state = _require_string(carrier.get("state"), f"carriers[{index}].state").casefold()
        if state not in {"open", "closed", "merged"}:
            raise GrantFoxCarrierCensusInputError(
                f"carriers[{index}].state must be open, closed, or merged"
            )
        relation = _require_string(
            carrier.get("issue_relation"), f"carriers[{index}].issue_relation"
        ).casefold()
        if relation not in {"closes", "references", "none"}:
            raise GrantFoxCarrierCensusInputError(
                f"carriers[{index}].issue_relation must be closes, references, or none"
            )
        process = _require_string(
            carrier.get("process_disposition"), f"carriers[{index}].process_disposition"
        ).casefold()
        if process not in {"normal", "closed_unassigned", "superseded", "unknown"}:
            raise GrantFoxCarrierCensusInputError(
                f"carriers[{index}].process_disposition is unsupported"
            )
        if state != "closed" and process == "closed_unassigned":
            raise GrantFoxCarrierCensusInputError(
                f"carriers[{index}].process_disposition closed_unassigned requires state=closed"
            )
        head_sha = carrier.get("head_sha")
        if head_sha is not None:
            head_sha = _require_string(head_sha, f"carriers[{index}].head_sha").casefold()
            if _SHA_RE.fullmatch(head_sha) is None:
                raise GrantFoxCarrierCensusInputError(
                    f"carriers[{index}].head_sha must be a 40-character lowercase hex SHA"
                )
        normalized.append(
            {
                "pr_url": pr_url,
                "pr_number": number,
                "state": state,
                "issue_relation": relation,
                "process_disposition": process,
                "head_sha": head_sha,
            }
        )
    return normalized


def compile_grantfox_carrier_census(request: dict[str, Any]) -> dict[str, Any]:
    """Compile current PR observations into an advisory carrier-census receipt."""
    request = _require_object(request, "request")
    allowed = {
        "schema",
        "canonical_issue_url",
        "carriers",
        "observed_at",
        "evaluated_at",
        "max_snapshot_age_seconds",
    }
    extras = set(request) - allowed
    if extras:
        raise GrantFoxCarrierCensusInputError(f"request contains unsupported fields: {sorted(extras)}")
    if request.get("schema") != _SCHEMA:
        raise GrantFoxCarrierCensusInputError(f"schema must equal {_SCHEMA}")
    canonical_issue_url = _require_string(request.get("canonical_issue_url"), "canonical_issue_url")
    owner, repo, issue_number = _issue_identity(canonical_issue_url, "canonical_issue_url")
    carriers = _normalize_observations(request.get("carriers", []), issue_owner=owner, issue_repo=repo)
    observed = _parse_timestamp(request.get("observed_at"), "observed_at")
    evaluated = _parse_timestamp(request.get("evaluated_at"), "evaluated_at")
    if evaluated < observed:
        raise GrantFoxCarrierCensusInputError("evaluated_at must not precede observed_at")
    max_age = _require_nonnegative_int(
        request.get("max_snapshot_age_seconds", 900), "max_snapshot_age_seconds"
    )
    if max_age < 1 or max_age > 86400:
        raise GrantFoxCarrierCensusInputError(
            "max_snapshot_age_seconds must be between 1 and 86400"
        )
    age = int((evaluated - observed).total_seconds())
    relevant = [c for c in carriers if c["issue_relation"] in {"closes", "references"}]
    active = [c for c in relevant if c["state"] in {"open", "merged"}]
    process_closed = [
        c
        for c in relevant
        if c["state"] == "closed" and c["process_disposition"] == "closed_unassigned"
    ]
    other_closed = [c for c in relevant if c["state"] == "closed" and c not in process_closed]
    reasons: list[str] = []
    if age > max_age:
        reasons.append("CARRIER_CENSUS_STALE")
    if active:
        reasons.append("ACTIVE_OR_MERGED_CARRIER_PRESENT")
    if process_closed:
        reasons.append("PROCESS_CLOSED_REUSABLE_CARRIER_PRESENT")
    if other_closed:
        reasons.append("CLOSED_CARRIER_REVIEW_REQUIRED")

    if age > max_age:
        disposition = "HOLD"
        next_action = "REFRESH_CARRIER_CENSUS"
    elif active:
        disposition = "REUSE_EXISTING_CARRIER"
        next_action = "REVIEW_EXISTING_CARRIER_BEFORE_NEW_WORK"
    elif process_closed:
        disposition = "REAPPLY_WITH_REUSABLE_CARRIER"
        next_action = "OBTAIN_ASSIGNMENT_THEN_REUSE_OR_REBASE_EXISTING_CARRIER"
    elif other_closed:
        disposition = "REVIEW_CLOSED_CARRIER"
        next_action = "DETERMINE_WHY_CARRIER_CLOSED_BEFORE_NEW_WORK"
    else:
        disposition = "CLEAR_FOR_QUEUE_EVALUATION"
        next_action = "CONTINUE_TO_PROVIDER_AND_SOURCE_READINESS_GATES"

    body = {
        "schema": _RECEIPT_SCHEMA,
        "request_schema": _SCHEMA,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "canonical_issue_url": canonical_issue_url,
        },
        "disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reasons,
        "census": {
            "observed_at": request["observed_at"],
            "evaluated_at": request["evaluated_at"],
            "snapshot_age_seconds": age,
            "max_snapshot_age_seconds": max_age,
            "observed_pr_count": len(carriers),
            "relevant_carrier_count": len(relevant),
            "active_or_merged_count": len(active),
            "process_closed_count": len(process_closed),
            "other_closed_count": len(other_closed),
            "carriers": carriers,
        },
        "authority": dict(_AUTHORITY),
    }
    return {**body, "carrier_receipt_sha256": _sha256_json(body)}


def verify_carrier_census_receipt(receipt: dict[str, Any]) -> bool:
    """Recompile embedded evidence and verify exact receipt equality."""
    if type(receipt) is not dict or receipt.get("schema") != _RECEIPT_SCHEMA:
        return False
    digest = receipt.get("carrier_receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    if receipt.get("authority") != _AUTHORITY:
        return False
    identity = receipt.get("identity")
    census = receipt.get("census")
    if type(identity) is not dict or type(census) is not dict:
        return False
    carriers = census.get("carriers")
    if type(carriers) is not list:
        return False
    reconstructed_carriers = []
    for carrier in carriers:
        if type(carrier) is not dict:
            return False
        reconstructed_carriers.append(
            {
                "pr_url": carrier.get("pr_url"),
                "state": carrier.get("state"),
                "issue_relation": carrier.get("issue_relation"),
                "process_disposition": carrier.get("process_disposition"),
                "head_sha": carrier.get("head_sha"),
            }
        )
    request = {
        "schema": _SCHEMA,
        "canonical_issue_url": identity.get("canonical_issue_url"),
        "carriers": reconstructed_carriers,
        "observed_at": census.get("observed_at"),
        "evaluated_at": census.get("evaluated_at"),
        "max_snapshot_age_seconds": census.get("max_snapshot_age_seconds"),
    }
    try:
        expected = compile_grantfox_carrier_census(request)
    except (GrantFoxCarrierCensusInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    census = receipt["census"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"carrier={receipt['disposition']} issue={identity['owner']}/{identity['repo']}#"
        f"{identity['issue_number']} relevant={census['relevant_carrier_count']} "
        f"next={receipt['advisory_next_action']} reasons={reasons} "
        f"carrier_receipt_sha256={receipt['carrier_receipt_sha256']}"
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
        prog="python -m concierge.grantfox_carrier_census",
        description="Compile an advisory, tamper-evident GrantFox carrier-census receipt.",
    )
    parser.add_argument("snapshot", help="carrier-census JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)
    try:
        receipt = compile_grantfox_carrier_census(_load(args.snapshot))
    except (OSError, json.JSONDecodeError, GrantFoxCarrierCensusInputError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 2 if receipt["disposition"] != "CLEAR_FOR_QUEUE_EVALUATION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
