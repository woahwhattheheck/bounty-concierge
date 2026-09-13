# SPDX-License-Identifier: MIT
"""Shared primitives for privacy-safe bounty contract receipts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

import requests

RECEIPT_SCHEMA = "bounty-contract-receipt/v1"

VERIFICATION_SCHEMA = "bounty-contract-verification/v1"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

_MAX_TEXT = 1_000_000

_MAX_COLLECTION = 10_000

_AUTHORITY = {
    "external_mutation_performed": False,
    "acceptance_inferred": False,
    "payout_inferred": False,
    "cash_claim": False,
    "time_attested": False,
    "signature": "none",
    "sha256_role": "consistency_not_authentication",
}

_DRIFT_ORDER = (
    "SOURCE_UNAVAILABLE",
    "LIVE_EVIDENCE_INCOMPLETE",
    "LIVE_GENERATION_UNSTABLE",
    "LIVE_EVIDENCE_INVALID",
    "SOURCE_IDENTITY_CHANGED",
    "ISSUE_AUTHORITY_CHANGED",
    "ISSUE_STATE_CHANGED",
    "LOCK_STATE_CHANGED",
    "TITLE_CHANGED",
    "BODY_CHANGED",
    "LABELS_CHANGED",
    "ASSIGNEES_CHANGED",
    "MILESTONE_CHANGED",
    "MAINTAINER_TERMS_CHANGED",
    "UNCLASSIFIED_CONTRACT_DRIFT",
)

class BountyContractError(RuntimeError):
    """Raised when live GitHub evidence cannot be read reliably."""

class BountyContractInputError(ValueError):
    """Raised when a request or stored receipt is not structurally trustworthy."""

class BountyContractEvidenceError(BountyContractError):
    """A fail-closed live evidence condition with a stable public reason code."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code

def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def _hash_text(value: str) -> str:
    return _hash_bytes(value.encode("utf-8"))

def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

def _hash_json(value: Any) -> str:
    return _hash_bytes(_canonical_bytes(value))

def _validate_repo(repo: Any) -> str:
    if type(repo) is not str or not _REPO_RE.fullmatch(repo):
        raise BountyContractInputError("repo must be in owner/name form")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise BountyContractInputError("repo must not contain dot path segments")
    return repo

def _validate_issue_number(number: Any) -> int:
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise BountyContractInputError("issue must be a positive integer")
    return number

def _timestamp(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise BountyContractInputError(f"{field} must be an ISO-8601 timestamp")
    text = value.strip()
    parse_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parse_text)
    except ValueError as exc:
        raise BountyContractInputError(
            f"{field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BountyContractInputError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _now_or(value: str | None, field: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return _timestamp(value, field)

def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def _get_json(
    session: Any,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> Any:
    response: Any = None
    try:
        response = session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(response, "status_code", None)
        if status in {404, 410}:
            raise BountyContractEvidenceError(
                "SOURCE_UNAVAILABLE", f"GitHub source is unavailable: {url}"
            ) from exc
        raise BountyContractError(f"GitHub request failed for {url}: {exc}") from exc
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", f"GitHub response was not valid JSON for {url}"
        ) from exc

def _bounded_text(value: Any, field: str, *, allow_none: bool = False) -> str:
    if value is None and allow_none:
        return ""
    if type(value) is not str or len(value) > _MAX_TEXT:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", f"GitHub {field} was not a bounded string"
        )
    return value

def _required_string(value: Any, field: str) -> str:
    text = _bounded_text(value, field)
    if not text:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", f"GitHub {field} was empty"
        )
    return text

def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", f"GitHub {field} was not a positive integer"
        )
    return value

def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", f"GitHub {field} was not a non-negative integer"
        )
    return value
