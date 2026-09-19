# SPDX-License-Identifier: MIT
"""Fail-closed advisory gate for GrantFox issue queue snapshots.

This module converts a freshly observed GrantFox issue-page snapshot into a
deterministic advisory receipt. It does not apply to issues, post comments,
create branches, submit pull requests, move funds, or grant any provider-side
authority.
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


class GrantFoxQueueInputError(ValueError):
    """Raised when a GrantFox queue snapshot is malformed or ambiguous."""


_SCHEMA = "grantfox-queue-gate/v1"
_GFOX_HOST = "contribute.grantfox.xyz"
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GFOX_PATH_RE = re.compile(
    r"^/org/([A-Za-z0-9_.-]+)/repo/([A-Za-z0-9_.-]+)/issue/([1-9][0-9]*)/?$"
)
_GITHUB_ISSUE_PATH_RE = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$"
)
_GITHUB_PR_PATH_RE = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?$"
)
_MAX_URL_CHARS = 2048


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GrantFoxQueueInputError(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise GrantFoxQueueInputError(f"{field} must be a non-empty trimmed string")
    return value


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GrantFoxQueueInputError(f"{field} must be a non-negative integer")
    return value


def _strict_url_parts(value: Any, field: str) -> tuple[str, str]:
    source = _require_string(value, field)
    if len(source) > _MAX_URL_CHARS:
        raise GrantFoxQueueInputError(f"{field} exceeds {_MAX_URL_CHARS} characters")
    if any(character.isspace() or ord(character) == 0x7F for character in source):
        raise GrantFoxQueueInputError(f"{field} must not contain whitespace")
    if "\\" in source or "%" in source:
        raise GrantFoxQueueInputError(
            f"{field} must not contain backslash or encoded path aliases"
        )
    if "?" in source or "#" in source:
        raise GrantFoxQueueInputError(
            f"{field} must not contain query or fragment delimiters"
        )

    try:
        parsed = urlsplit(source)
        port = parsed.port
    except ValueError as exc:
        raise GrantFoxQueueInputError(f"{field} must be a valid URL") from exc

    if parsed.scheme.casefold() != "https":
        raise GrantFoxQueueInputError(f"{field} must use https")
    if parsed.username is not None or parsed.password is not None:
        raise GrantFoxQueueInputError(f"{field} must not contain userinfo")
    if port is not None:
        raise GrantFoxQueueInputError(f"{field} must not contain an explicit port")
    if parsed.query or parsed.fragment:
        raise GrantFoxQueueInputError(f"{field} must not contain query or fragment")
    host = (parsed.hostname or "").casefold()
    if not host:
        raise GrantFoxQueueInputError(f"{field} must contain a host")
    if parsed.path.startswith("//") or "//" in parsed.path:
        raise GrantFoxQueueInputError(f"{field} must not contain repeated separators")
    return host, parsed.path


def _grantfox_identity(value: Any, field: str) -> tuple[str, str, int]:
    host, path = _strict_url_parts(value, field)
    if host != _GFOX_HOST:
        raise GrantFoxQueueInputError(f"{field} must use {_GFOX_HOST}")
    match = _GFOX_PATH_RE.fullmatch(path)
    if match is None:
        raise GrantFoxQueueInputError(f"{field} is not a canonical GrantFox issue URL")
    owner, repo, number = match.groups()
    return owner.casefold(), repo.casefold(), int(number)


def _github_issue_identity(value: Any, field: str) -> tuple[str, str, int]:
    host, path = _strict_url_parts(value, field)
    if host not in _GITHUB_HOSTS:
        raise GrantFoxQueueInputError(f"{field} must use github.com")
    match = _GITHUB_ISSUE_PATH_RE.fullmatch(path)
    if match is None:
        raise GrantFoxQueueInputError(f"{field} is not a canonical GitHub issue URL")
    owner, repo, number = match.groups()
    return owner.casefold(), repo.casefold(), int(number)


def _github_pr_identity(value: Any, field: str) -> tuple[str, str, int]:
    host, path = _strict_url_parts(value, field)
    if host not in _GITHUB_HOSTS:
        raise GrantFoxQueueInputError(f"{field} must use github.com")
    match = _GITHUB_PR_PATH_RE.fullmatch(path)
    if match is None:
        raise GrantFoxQueueInputError(f"{field} is not a canonical GitHub pull URL")
    owner, repo, number = match.groups()
    return owner.casefold(), repo.casefold(), int(number)


def _parse_timestamp(value: Any, field: str) -> datetime:
    raw = _require_string(value, field)
    if not raw.endswith("Z"):
        raise GrantFoxQueueInputError(f"{field} must be UTC RFC3339 ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise GrantFoxQueueInputError(f"{field} must be valid RFC3339") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise GrantFoxQueueInputError(f"{field} must be UTC")
    return parsed


def _normalize_login(value: Any, field: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    login = _require_string(value, field)
    if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login) is None:
        raise GrantFoxQueueInputError(f"{field} must be a GitHub-style login")
    return login.casefold()


def _normalize_labels(value: Any) -> list[str]:
    if type(value) is not list:
        raise GrantFoxQueueInputError("labels must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        label = _require_string(item, f"labels[{index}]")
        folded = " ".join(label.split()).casefold()
        if folded in seen:
            raise GrantFoxQueueInputError("labels must not contain duplicates")
        seen.add(folded)
        normalized.append(label)
    return normalized


def compile_grantfox_queue_gate(request: dict[str, Any]) -> dict[str, Any]:
    """Compile one fresh GrantFox page observation into an advisory-only receipt."""
    request = _require_object(request, "request")
    if request.get("schema") != _SCHEMA:
        raise GrantFoxQueueInputError(f"schema must equal {_SCHEMA}")

    listing_url = _require_string(request.get("listing_url"), "listing_url")
    canonical_issue_url = _require_string(
        request.get("canonical_issue_url"), "canonical_issue_url"
    )
    listing_identity = _grantfox_identity(listing_url, "listing_url")
    canonical_identity = _github_issue_identity(
        canonical_issue_url, "canonical_issue_url"
    )
    if listing_identity != canonical_identity:
        raise GrantFoxQueueInputError(
            "listing_url and canonical_issue_url identify different issues"
        )

    actor_login = _normalize_login(request.get("actor_login"), "actor_login")
    assigned_to = _normalize_login(
        request.get("assigned_to"), "assigned_to", allow_none=True
    )
    actor_applied = request.get("actor_applied")
    if type(actor_applied) is not bool:
        raise GrantFoxQueueInputError("actor_applied must be a boolean")

    issue_state = _require_string(request.get("issue_state"), "issue_state").casefold()
    if issue_state not in {"open", "closed"}:
        raise GrantFoxQueueInputError("issue_state must be open or closed")

    application_count = _require_nonnegative_int(
        request.get("application_count"), "application_count"
    )
    threshold = _require_nonnegative_int(
        request.get("application_pressure_threshold", 3),
        "application_pressure_threshold",
    )
    if threshold < 1 or threshold > 100:
        raise GrantFoxQueueInputError(
            "application_pressure_threshold must be between 1 and 100"
        )

    linked_pr_urls = request.get("linked_pr_urls", [])
    if type(linked_pr_urls) is not list:
        raise GrantFoxQueueInputError("linked_pr_urls must be a list")
    linked_pr_identities: list[str] = []
    for index, url in enumerate(linked_pr_urls):
        owner, repo, number = _github_pr_identity(url, f"linked_pr_urls[{index}]")
        identity = f"{owner}/{repo}#{number}"
        if identity in linked_pr_identities:
            raise GrantFoxQueueInputError("linked_pr_urls must not contain duplicates")
        linked_pr_identities.append(identity)

    labels = _normalize_labels(request.get("labels", []))
    labels_folded = {" ".join(label.split()).casefold() for label in labels}

    observed = _parse_timestamp(request.get("observed_at"), "observed_at")
    evaluated = _parse_timestamp(request.get("evaluated_at"), "evaluated_at")
    if evaluated < observed:
        raise GrantFoxQueueInputError("evaluated_at must not precede observed_at")
    age_seconds = int((evaluated - observed).total_seconds())
    max_age_seconds = _require_nonnegative_int(
        request.get("max_snapshot_age_seconds", 900), "max_snapshot_age_seconds"
    )
    if max_age_seconds < 1 or max_age_seconds > 86400:
        raise GrantFoxQueueInputError(
            "max_snapshot_age_seconds must be between 1 and 86400"
        )

    reason_codes: list[str] = []
    if issue_state != "open":
        reason_codes.append("CANONICAL_ISSUE_NOT_OPEN")
    if age_seconds > max_age_seconds:
        reason_codes.append("SNAPSHOT_STALE")
    if linked_pr_identities:
        reason_codes.append("LINKED_PR_PRESENT")
    if assigned_to is not None and assigned_to != actor_login:
        reason_codes.append("ASSIGNED_TO_OTHER")
    if (
        assigned_to is None
        and not actor_applied
        and application_count >= threshold
    ):
        reason_codes.append("APPLICATION_PRESSURE_HIGH")

    if reason_codes:
        disposition = "HOLD"
        next_action = "STOP_AND_REFRESH_OR_REUSE_EXISTING_WORK"
    elif assigned_to == actor_login:
        disposition = "IMPLEMENTATION_ELIGIBLE"
        next_action = "IMPLEMENT_ASSIGNED_SCOPE"
    elif actor_applied:
        disposition = "WAIT_ASSIGNMENT"
        next_action = "WAIT_FOR_PROVIDER_ASSIGNMENT"
    else:
        disposition = "APPLY_ELIGIBLE"
        next_action = "APPLY_THROUGH_PROVIDER_ROUTE"

    maybe_rewarded = "maybe rewarded" in labels_folded
    reward_status = "POSSIBLE_DISCRETIONARY" if maybe_rewarded else "UNVERIFIED"

    owner, repo, issue_number = listing_identity
    body = {
        "schema": _SCHEMA,
        "disposition": disposition,
        "advisory_next_action": next_action,
        "reason_codes": reason_codes,
        "identity": {
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "listing_url": listing_url,
            "canonical_issue_url": canonical_issue_url,
        },
        "provider_snapshot": {
            "issue_state": issue_state,
            "assigned_to": assigned_to,
            "actor_login": actor_login,
            "actor_applied": actor_applied,
            "application_count": application_count,
            "application_pressure_threshold": threshold,
            "linked_pr_urls": list(linked_pr_urls),
            "labels": labels,
            "observed_at": request["observed_at"],
            "evaluated_at": request["evaluated_at"],
            "snapshot_age_seconds": age_seconds,
            "max_snapshot_age_seconds": max_age_seconds,
        },
        "reward": {
            "status": reward_status,
            "explicit_amount_verified": False,
            "award_verified": False,
            "payment_verified": False,
            "rule": (
                "GrantFox campaign or Maybe Rewarded labels are eligibility signals "
                "only; they are not an award, fixed amount, or payment receipt."
            ),
        },
        "authority": {
            "advisory_only": True,
            "provider_application_authority": False,
            "implementation_write_authority": False,
            "submission_authority": False,
            "payment_or_wallet_authority": False,
        },
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify deterministic receipt integrity and the advisory authority ceiling."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    authority = body.get("authority")
    if type(authority) is not dict:
        return False
    if authority != {
        "advisory_only": True,
        "provider_application_authority": False,
        "implementation_write_authority": False,
        "submission_authority": False,
        "payment_or_wallet_authority": False,
    }:
        return False
    return _sha256_json(body) == digest


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    return (
        f"disposition={receipt['disposition']} "
        f"issue={identity['owner']}/{identity['repo']}#{identity['issue_number']} "
        f"next={receipt['advisory_next_action']} "
        f"reward={receipt['reward']['status']} reasons={reasons} "
        f"receipt_sha256={receipt['receipt_sha256']}"
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
        prog="python -m concierge.grantfox_queue_gate",
        description=(
            "Compile an advisory-only GrantFox queue receipt from a fresh provider "
            "snapshot. This command performs no provider mutation."
        ),
    )
    parser.add_argument("snapshot", help="snapshot JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)

    try:
        receipt = compile_grantfox_queue_gate(_load_request(args.snapshot))
    except (OSError, json.JSONDecodeError, GrantFoxQueueInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 2 if receipt["disposition"] == "HOLD" else 0


if __name__ == "__main__":
    raise SystemExit(main())
