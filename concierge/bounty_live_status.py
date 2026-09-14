# SPDX-License-Identifier: MIT
"""Fail-closed live status preflight for bounty leads.

Aggregator state is discovery metadata only. This module resolves the canonical
GitHub issue identity through GitHub's API and records a self-integrity audit
receipt. A retained receipt is never current qualification authority: the only
positive qualification path is :func:`preflight_further_qualification`, which
performs a fresh request through code-owned transport in the same call.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

import requests

from concierge.config import GITHUB_TOKEN

_RECEIPT_SCHEMA = "bounty-live-status/v3"
_DECISION_SCHEMA = "bounty-live-qualification-decision/v1"
# Compatibility alias for code that inspected the former private constant.
_SCHEMA = _RECEIPT_SCHEMA
_MAX_CURRENT_AGE_SECONDS = 300
_REPO_PART = r"[A-Za-z0-9_.-]+"
_WEB_ISSUE_RE = re.compile(
    rf"^/(?P<owner>{_REPO_PART})/(?P<repo>{_REPO_PART})/issues/(?P<number>[1-9][0-9]*)/?$"
)
_API_ISSUE_RE = re.compile(
    rf"^/repos/(?P<owner>{_REPO_PART})/(?P<repo>{_REPO_PART})/issues/(?P<number>[1-9][0-9]*)/?$"
)
_DISCOVERY_FIELDS = frozenset(
    {
        "source_name",
        "source_url",
        "observed_at",
        "advertised_amount",
        "advertised_state",
        "solver_count",
    }
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _current_utc() -> datetime:
    value = _now_utc()
    if not isinstance(value, datetime):
        raise RuntimeError("UTC clock returned a non-datetime value")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("UTC clock returned a naive datetime")
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _seal(receipt: dict[str, Any]) -> dict[str, Any]:
    """Add an unkeyed audit-integrity digest.

    This helper intentionally does not create qualification authority. Public
    callers can recompute this digest, which is why no retained receipt accessor
    is permitted to return a positive decision.
    """

    sealed = deepcopy(receipt)
    sealed["receipt_sha256"] = hashlib.sha256(_canonical_json(receipt)).hexdigest()
    return sealed


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify receipt self-integrity only; never infer current authority."""

    if not isinstance(receipt, dict):
        return False
    digest = receipt.get("receipt_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    unsigned = deepcopy(receipt)
    unsigned.pop("receipt_sha256", None)
    try:
        calculated = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    except (TypeError, ValueError):
        return False
    return calculated == digest


def is_clear_for_further_qualification(receipt: dict[str, Any]) -> bool:
    """Fail closed for every retained receipt.

    Version 2 incorrectly let caller bytes become current qualification
    authority after only a public SHA-256 self-integrity check. Retained receipts
    are audit evidence only. Call :func:`preflight_further_qualification` to
    perform a fresh, code-owned provider acquisition and receive an immediate
    non-durable decision.
    """

    del receipt
    return False


def _parse_issue_url(issue_url: str) -> tuple[str, int, str]:
    if not isinstance(issue_url, str) or not issue_url.strip():
        raise ValueError("issue_url must be a non-empty GitHub issue URL")
    raw = issue_url.strip()
    if len(raw) > 2048:
        raise ValueError("issue_url is too long")
    parts = urlsplit(raw)
    if (
        parts.scheme.casefold() != "https"
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("issue_url must use credential-free HTTPS without query or fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("issue_url has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("issue_url must not select a non-HTTPS port")

    host = (parts.hostname or "").casefold()
    pattern = _WEB_ISSUE_RE if host in {"github.com", "www.github.com"} else None
    if host == "api.github.com":
        pattern = _API_ISSUE_RE
    if pattern is None:
        raise ValueError("issue_url must target github.com or api.github.com")

    match = pattern.fullmatch(parts.path)
    if match is None:
        raise ValueError("issue_url must target a GitHub issue, not a PR or repository")
    repo = f"{match.group('owner')}/{match.group('repo')}"
    number = int(match.group("number"))
    normalized = f"https://github.com/{repo}/issues/{number}"
    return repo, number, normalized


def _sanitize_discovery(discovery: dict[str, Any] | None) -> dict[str, Any]:
    if discovery is None:
        return {}
    if not isinstance(discovery, dict):
        raise ValueError("discovery must be an object")
    unknown = set(discovery) - _DISCOVERY_FIELDS
    if unknown:
        raise ValueError(f"unsupported discovery field(s): {', '.join(sorted(unknown))}")

    result: dict[str, Any] = {}
    for key in ("source_name", "observed_at", "advertised_amount", "advertised_state"):
        value = discovery.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"{key} must be a non-empty string of at most 256 chars")
        result[key] = value.strip()

    source_url = discovery.get("source_url")
    if source_url is not None:
        if (
            not isinstance(source_url, str)
            or not source_url.strip()
            or len(source_url) > 2048
        ):
            raise ValueError("source_url must be a non-empty URL of at most 2048 chars")
        parts = urlsplit(source_url.strip())
        if (
            parts.scheme.casefold() != "https"
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            raise ValueError("source_url must be a credential-free HTTPS URL")
        result["source_url"] = source_url.strip()

    solver_count = discovery.get("solver_count")
    if solver_count is not None:
        if (
            isinstance(solver_count, bool)
            or not isinstance(solver_count, int)
            or solver_count < 0
            or solver_count > 1_000_000_000
        ):
            raise ValueError("solver_count must be a non-negative bounded integer")
        result["solver_count"] = solver_count
    return result


def _headers(token: str | None) -> dict[str, str]:
    if token is not None and not isinstance(token, str):
        raise ValueError("token must be a string or None")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_get(url: str, *, headers: dict[str, str]):
    """Code-owned GitHub transport seam.

    Production callers cannot supply or select this transport through a public
    API parameter. Tests may replace this private seam inside the test process.
    """

    return requests.get(
        url,
        headers=headers,
        timeout=15,
        allow_redirects=True,
    )


def _result(
    *,
    requested_url: str,
    requested_repo: str,
    number: int,
    discovery: dict[str, Any],
    classification: str,
    reason_code: str,
    provider_response_code_owned: bool,
    provider_state_authoritative: bool = False,
    canonical_repo: str | None = None,
    issue_state: str | None = None,
    issue_updated_at: str | None = None,
    repository_redirected: bool = False,
    clear_at_capture: bool = False,
) -> tuple[dict[str, Any], bool]:
    canonical_url = (
        f"https://github.com/{canonical_repo}/issues/{number}"
        if canonical_repo is not None
        else None
    )
    captured_at = _current_utc()
    fresh_until = captured_at + timedelta(seconds=_MAX_CURRENT_AGE_SECONDS)
    receipt = {
        "schema": _RECEIPT_SCHEMA,
        "requested_issue_url": requested_url,
        "requested_identity": {"repo": requested_repo, "number": number},
        "canonical_issue": (
            {"repo": canonical_repo, "number": number, "url": canonical_url}
            if canonical_repo is not None
            else None
        ),
        "live": {
            "provider": "github",
            "classification": classification,
            "reason_code": reason_code,
            "issue_state": issue_state,
            "issue_updated_at": issue_updated_at,
            "repository_redirected": repository_redirected,
            "verified_at": _format_utc(captured_at),
            "fresh_until": _format_utc(fresh_until),
        },
        "discovery": discovery,
        "authority": {
            "provider_response_code_owned": provider_response_code_owned,
            "github_live_state_is_authoritative_at_capture": provider_state_authoritative,
            "retained_receipt_is_qualification_authority": False,
            "clear_requires_fresh_code_owned_acquisition": True,
            "discovery_state_is_authoritative": False,
            "discovery_amount_is_payout_proof": False,
            "discovery_solver_count_is_claim_authority": False,
            "clear_is_dispatch_authority": False,
            "next_gate_required": True,
            "network_fetches_discovery_source_url": False,
        },
    }
    immediate_clear = bool(
        clear_at_capture
        and provider_response_code_owned
        and provider_state_authoritative
        and classification == "OPEN"
        and reason_code == "GITHUB_ISSUE_OPEN"
    )
    return _seal(receipt), immediate_clear


def _final_api_identity(final_url: str) -> tuple[str, int] | None:
    try:
        parts = urlsplit(final_url)
    except (TypeError, ValueError):
        return None
    if (
        parts.scheme.casefold() != "https"
        or (parts.hostname or "").casefold() != "api.github.com"
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port not in (None, 443):
        return None
    match = _API_ISSUE_RE.fullmatch(parts.path)
    if match is None:
        return None
    return f"{match.group('owner')}/{match.group('repo')}", int(match.group("number"))


def _acquire_live_status(
    issue_url: str,
    discovery: dict[str, Any] | None,
    token: str | None,
) -> tuple[dict[str, Any], bool]:
    requested_repo, number, normalized_url = _parse_issue_url(issue_url)
    discovery_data = _sanitize_discovery(discovery)
    api_url = f"https://api.github.com/repos/{requested_repo}/issues/{number}"
    resolved_token = GITHUB_TOKEN if token is None else token

    try:
        response = _github_get(api_url, headers=_headers(resolved_token))
    except requests.RequestException:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="GITHUB_REQUEST_FAILED",
            provider_response_code_owned=True,
        )

    status = getattr(response, "status_code", None)
    final_url = getattr(response, "url", None)
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not isinstance(final_url, str)
    ):
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="MALFORMED_HTTP_RESPONSE",
            provider_response_code_owned=True,
        )

    final_identity = _final_api_identity(final_url)
    if final_identity is None:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="AMBIGUOUS_OR_CROSS_HOST_REDIRECT",
            provider_response_code_owned=True,
        )

    final_repo, final_number = final_identity
    if final_number != number:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="REDIRECT_ISSUE_NUMBER_CHANGED",
            provider_response_code_owned=True,
        )

    redirected = final_repo.casefold() != requested_repo.casefold()
    if status == 404:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="GITHUB_NOT_FOUND_OR_INACCESSIBLE",
            provider_response_code_owned=True,
            provider_state_authoritative=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )
    if status == 410:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="DELETED_OR_MOVED",
            reason_code="GITHUB_GONE",
            provider_response_code_owned=True,
            provider_state_authoritative=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )
    if status != 200:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code=f"GITHUB_HTTP_{status}",
            provider_response_code_owned=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )

    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="MALFORMED_GITHUB_JSON",
            provider_response_code_owned=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )
    if "pull_request" in payload:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="TARGET_BECAME_PULL_REQUEST",
            provider_response_code_owned=True,
            provider_state_authoritative=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )

    raw_number = payload.get("number")
    state = payload.get("state")
    updated_at = payload.get("updated_at")
    html_url = payload.get("html_url")
    if (
        isinstance(raw_number, bool)
        or not isinstance(raw_number, int)
        or raw_number != number
        or not isinstance(state, str)
        or state.casefold() not in {"open", "closed"}
        or _parse_utc(updated_at) is None
        or not isinstance(html_url, str)
    ):
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="MALFORMED_ISSUE_IDENTITY_OR_STATE",
            provider_response_code_owned=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )

    try:
        payload_repo, payload_number, _ = _parse_issue_url(html_url)
    except ValueError:
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="MALFORMED_CANONICAL_HTML_URL",
            provider_response_code_owned=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )
    if payload_number != number or payload_repo.casefold() != final_repo.casefold():
        return _result(
            requested_url=normalized_url,
            requested_repo=requested_repo,
            number=number,
            discovery=discovery_data,
            classification="UNVERIFIABLE",
            reason_code="CANONICAL_IDENTITY_CONFLICT",
            provider_response_code_owned=True,
            provider_state_authoritative=True,
            canonical_repo=final_repo,
            repository_redirected=redirected,
        )

    classification = state.upper()
    return _result(
        requested_url=normalized_url,
        requested_repo=requested_repo,
        number=number,
        discovery=discovery_data,
        classification=classification,
        reason_code=(
            "GITHUB_ISSUE_OPEN" if classification == "OPEN" else "GITHUB_ISSUE_CLOSED"
        ),
        provider_response_code_owned=True,
        provider_state_authoritative=True,
        canonical_repo=final_repo,
        issue_state=state.casefold(),
        issue_updated_at=updated_at,
        repository_redirected=redirected,
        clear_at_capture=classification == "OPEN",
    )


def inspect_live_status(
    issue_url: str,
    discovery: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Acquire and return a self-integrity audit receipt.

    The HTTP transport is code-owned. The returned receipt is not qualification
    authority and :func:`is_clear_for_further_qualification` always fails closed.
    Use :func:`preflight_further_qualification` for a positive current decision.
    ``None`` uses the configured ambient GitHub token; an explicit empty token
    suppresses Authorization.
    """

    receipt, _ = _acquire_live_status(issue_url, discovery, token)
    return receipt


def preflight_further_qualification(
    issue_url: str,
    discovery: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Perform one fresh provider acquisition and return an immediate decision.

    The positive bit exists only in the direct return value of this function. It
    is intentionally not HMAC/seal-verifiable, durable, replayable, dispatch
    authority, payment authority, or revenue recognition. Callers needing a
    later action must call this function again immediately before that action.
    """

    receipt, clear = _acquire_live_status(issue_url, discovery, token)
    return {
        "schema": _DECISION_SCHEMA,
        "clear_for_further_qualification": clear,
        "receipt": receipt,
        "authority": {
            "fresh_code_owned_acquisition_in_this_call": True,
            "decision_is_durable_or_replayable": False,
            "retained_receipt_is_qualification_authority": False,
            "decision_is_dispatch_authority": False,
            "decision_is_payment_or_revenue_authority": False,
            "next_gate_required": True,
        },
    }
