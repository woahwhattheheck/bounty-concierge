# SPDX-License-Identifier: MIT
"""Live, read-only acceptance and settlement closeout queue for paid PRs.

The module turns a small operator-owned manifest of paid-work PRs into an
action queue backed by current GitHub PR/review/comment state.  It deliberately
does not infer sponsor acceptance, earned money, or payment from a merge.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from concierge.config import GITHUB_TOKEN


_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9_.-]{1,11}$")
_MAX_AMOUNT_SOURCE_CHARS = 64
_MAX_AMOUNT_DIGITS = 30
_MAX_AMOUNT_ABS_EXPONENT = 18
_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_REVIEW_DECISION_STATES = frozenset({"CHANGES_REQUESTED", "APPROVED", "DISMISSED"})
_ACTION_ORDER = {
    "repair_requested": 0,
    "respond_to_maintainer": 1,
    "investigate_closed_unmerged": 2,
    "route_settlement_followup": 3,
    "monitor_settlement": 4,
    "await_acceptance": 5,
}


class RevenueCloseoutError(RuntimeError):
    """Raised when live closeout state cannot be read reliably."""


class RevenueCloseoutInputError(ValueError):
    """Raised when the operator manifest is structurally unreliable."""


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
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
    try:
        response = session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RevenueCloseoutError(f"GitHub request failed for {url}: {exc}") from exc
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise RevenueCloseoutError(
            f"GitHub response was not valid JSON for {url}"
        ) from exc


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RevenueCloseoutError(f"GitHub {context} response was not an object")
    return value


def _parse_timestamp(value: Any, *, field: str, allow_none: bool = True) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RevenueCloseoutInputError(f"{field} must be an ISO-8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RevenueCloseoutInputError(
            f"{field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RevenueCloseoutInputError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_amount(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise RevenueCloseoutInputError("advertised_amount must be a positive decimal")
    source = str(value)
    if len(source) > _MAX_AMOUNT_SOURCE_CHARS:
        raise RevenueCloseoutInputError("advertised_amount representation is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise RevenueCloseoutInputError(
            "advertised_amount must be a positive decimal"
        ) from exc
    if not amount.is_finite() or amount <= 0:
        raise RevenueCloseoutInputError("advertised_amount must be a positive decimal")
    digits = amount.as_tuple().digits
    exponent = amount.as_tuple().exponent
    if (
        len(digits) > _MAX_AMOUNT_DIGITS
        or not isinstance(exponent, int)
        or abs(exponent) > _MAX_AMOUNT_ABS_EXPONENT
    ):
        raise RevenueCloseoutInputError("advertised_amount representation is too large")
    return amount


def _validate_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RevenueCloseoutInputError("each closeout item must be an object")
    repo = raw.get("repo")
    if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
        raise RevenueCloseoutInputError("repo must be in owner/name form")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise RevenueCloseoutInputError("repo must not contain dot path segments")
    number = raw.get("pr")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise RevenueCloseoutInputError("pr must be a positive integer")
    operator_login = raw.get("operator_login")
    if not isinstance(operator_login, str) or not operator_login.strip():
        raise RevenueCloseoutInputError("operator_login must be a non-empty string")
    currency = raw.get("currency")
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
        raise RevenueCloseoutInputError(
            "currency must be an uppercase 2-12 character code"
        )
    amount = _positive_amount(raw.get("advertised_amount"))
    last_seen = _parse_timestamp(
        raw.get("last_seen_at"), field="last_seen_at", allow_none=False
    )
    if last_seen > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise RevenueCloseoutInputError("last_seen_at must not be in the future")
    settlement_url = raw.get("settlement_followup_url")
    if settlement_url is not None:
        if not isinstance(settlement_url, str):
            raise RevenueCloseoutInputError(
                "settlement_followup_url must be an absolute HTTP(S) URL"
            )
        parsed_url = urlsplit(settlement_url)
        if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
            raise RevenueCloseoutInputError(
                "settlement_followup_url must be an absolute HTTP(S) URL"
            )
    expected_head = raw.get("expected_head_sha")
    if expected_head is not None:
        if (
            not isinstance(expected_head, str)
            or not re.fullmatch(r"[0-9a-fA-F]{40}", expected_head)
        ):
            raise RevenueCloseoutInputError(
                "expected_head_sha must be a 40-hex commit SHA"
            )
        expected_head = expected_head.lower()
    return {
        "repo": repo,
        "pr": number,
        "operator_login": operator_login.strip().casefold(),
        "advertised_amount": amount,
        "currency": currency,
        "last_seen_at": last_seen,
        "settlement_followup_url": settlement_url,
        "expected_head_sha": expected_head,
    }


def _safe_user_login(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    login = value.get("login")
    if not isinstance(login, str) or not login.strip():
        return None
    return login.strip()


def _external_maintainer(event: dict[str, Any], operator_login: str) -> bool:
    user = event.get("user")
    login = _safe_user_login(user)
    if login is None or login.casefold() == operator_login:
        return False
    if isinstance(user, dict):
        user_type = user.get("type")
        if (
            isinstance(user_type, str) and user_type.casefold() == "bot"
        ) or login.casefold().endswith("[bot]"):
            return False
    association = event.get("author_association")
    return (
        isinstance(association, str)
        and association.upper() in _MAINTAINER_ASSOCIATIONS
    )


def _event_timestamp(event: dict[str, Any], *fields: str) -> datetime | None:
    for field in fields:
        value = event.get(field)
        if value is None:
            continue
        try:
            return _parse_timestamp(value, field=field, allow_none=False)
        except RevenueCloseoutInputError as exc:
            raise RevenueCloseoutError(
                f"GitHub feedback item had invalid {field}"
            ) from exc
    return None


def _feedback_source_id(kind: str, event: dict[str, Any]) -> tuple[str, int] | None:
    """Return a stable GitHub feedback identity when the API supplied one."""
    raw_id = event.get("id")
    if raw_id is None:
        if kind == "inline_comment":
            raise RevenueCloseoutError("GitHub inline_comment omitted id")
        return None
    if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
        raise RevenueCloseoutError(f"GitHub {kind} feedback item had invalid id")
    return kind, raw_id


def _collect_feedback(
    repo: str,
    number: int,
    operator_login: str,
    *,
    session: Any,
    headers: dict[str, str],
    max_pages: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    endpoints = (
        (
            "review",
            f"https://api.github.com/repos/{repo}/pulls/{number}/reviews",
            ("submitted_at", "created_at"),
        ),
        (
            "inline_comment",
            f"https://api.github.com/repos/{repo}/pulls/{number}/comments",
            ("updated_at", "created_at"),
        ),
        (
            "comment",
            f"https://api.github.com/repos/{repo}/issues/{number}/comments",
            ("updated_at", "created_at"),
        ),
    )
    for kind, url, timestamp_fields in endpoints:
        for page in range(1, max_pages + 1):
            payload = _get_json(
                session,
                url,
                headers=headers,
                params={"per_page": 100, "page": page},
            )
            if not isinstance(payload, list):
                raise RevenueCloseoutError(
                    f"GitHub {kind} response was not a list for {repo}#{number}"
                )
            for event in payload:
                if not isinstance(event, dict):
                    raise RevenueCloseoutError(
                        f"GitHub {kind} response contained a malformed item"
                    )
                source_id = _feedback_source_id(kind, event)
                parent_review_id = None
                if kind == "inline_comment":
                    raw_parent = event.get("pull_request_review_id")
                    if (
                        isinstance(raw_parent, bool)
                        or not isinstance(raw_parent, int)
                        or raw_parent <= 0
                    ):
                        raise RevenueCloseoutError(
                            "GitHub inline_comment omitted valid pull_request_review_id"
                        )
                    parent_review_id = raw_parent
                if not _external_maintainer(event, operator_login):
                    continue
                timestamp = _event_timestamp(event, *timestamp_fields)
                if timestamp is None:
                    raise RevenueCloseoutError(
                        f"GitHub maintainer {kind} omitted a timestamp"
                    )
                state = "COMMENTED"
                if kind == "review":
                    raw_state = event.get("state")
                    if not isinstance(raw_state, str):
                        raise RevenueCloseoutError("GitHub review omitted state")
                    state = raw_state.upper()
                normalized = {
                    "kind": kind,
                    "state": state,
                    "author": _safe_user_login(event.get("user")),
                    "at": timestamp,
                    "url": event.get("html_url")
                    if isinstance(event.get("html_url"), str)
                    else None,
                    "_source_id": source_id,
                    "_parent_review_id": parent_review_id,
                }
                if source_id is not None:
                    prior = seen.get(source_id)
                    if prior is not None:
                        if prior != normalized:
                            raise RevenueCloseoutError(
                                f"GitHub {kind} duplicate id changed across pagination"
                            )
                        continue
                    seen[source_id] = normalized
                results.append(normalized)
            if len(payload) < 100:
                break
        else:
            raise RevenueCloseoutError(
                f"GitHub {kind} pagination exceeded max_pages for {repo}#{number}"
            )

    # The reviews endpoint emits a COMMENTED parent review for line-level review
    # comments.  When the inline-comments endpoint gave us those concrete child
    # obligations, suppress only the overlapping parent notification so one
    # maintainer action is not counted twice. Decision-bearing reviews remain.
    inline_parent_review_ids = {
        event["_parent_review_id"]
        for event in results
        if event["kind"] == "inline_comment"
        and isinstance(event.get("_parent_review_id"), int)
    }
    deduped = []
    for event in results:
        source_id = event.get("_source_id")
        if (
            event["kind"] == "review"
            and event["state"] == "COMMENTED"
            and isinstance(source_id, tuple)
            and len(source_id) == 2
            and source_id[1] in inline_parent_review_ids
        ):
            continue
        deduped.append(event)

    deduped.sort(key=lambda item: item["at"])
    return deduped


def _current_review_decisions(
    feedback: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return each maintainer's latest decision-bearing review.

    COMMENTED reviews are notification events, not decision transitions, so they
    never clear an earlier CHANGES_REQUESTED.  APPROVED and DISMISSED do clear a
    prior change request from the same maintainer.  ``feedback`` is already
    chronological and contains only external human maintainers.
    """
    decisions: dict[str, dict[str, Any]] = {}
    for event in feedback:
        if event["kind"] != "review" or event["state"] not in _REVIEW_DECISION_STATES:
            continue
        author = event["author"]
        if not isinstance(author, str) or not author:
            raise RevenueCloseoutError("GitHub maintainer review omitted author")
        decisions[author.casefold()] = event
    return decisions


def scan_paid_pr(
    raw_item: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Read one paid-work PR and return a safe closeout action receipt."""
    item = _validate_item(raw_item)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise RevenueCloseoutInputError("max_pages must be positive")
    token = token or GITHUB_TOKEN
    headers = _headers(token)
    repo = item["repo"]
    number = item["pr"]
    pr = _object(
        _get_json(
            session,
            f"https://api.github.com/repos/{repo}/pulls/{number}",
            headers=headers,
        ),
        f"pull request {repo}#{number}",
    )

    canonical_url = f"https://github.com/{repo}/pull/{number}"
    if pr.get("html_url") != canonical_url:
        raise RevenueCloseoutError(
            f"GitHub PR identity mismatch for {repo}#{number}"
        )
    author = _safe_user_login(pr.get("user"))
    if author is None or author.casefold() != item["operator_login"]:
        raise RevenueCloseoutInputError(
            f"{repo}#{number} is not authored by operator_login"
        )
    head = pr.get("head")
    if not isinstance(head, dict) or not isinstance(head.get("sha"), str):
        raise RevenueCloseoutError(f"GitHub PR omitted head SHA for {repo}#{number}")
    head_sha = head["sha"].lower()
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise RevenueCloseoutError(f"GitHub PR returned invalid head SHA for {repo}#{number}")
    expected_head = item["expected_head_sha"]
    if expected_head is not None and expected_head != head_sha:
        return {
            "repo": repo,
            "pr": number,
            "canonical_url": canonical_url,
            "head_sha": head_sha,
            "advertised_amount": format(item["advertised_amount"], "f"),
            "currency": item["currency"],
            "state": "HEAD_MOVED",
            "next_action": "repair_requested",
            "reason": "expected_head_moved",
            "new_feedback_count": 0,
            "current_change_request_count": None,
            "latest_feedback": None,
            "settlement_followup_url": item["settlement_followup_url"],
            "cash_status": "not_inferred",
        }

    merged_at_raw = pr.get("merged_at")
    merged_at = None
    if merged_at_raw is not None:
        try:
            merged_at = _parse_timestamp(
                merged_at_raw, field="merged_at", allow_none=False
            )
        except RevenueCloseoutInputError as exc:
            raise RevenueCloseoutError(
                f"GitHub PR returned invalid merged_at for {repo}#{number}"
            ) from exc
    state = pr.get("state")
    if state not in {"open", "closed"}:
        raise RevenueCloseoutError(f"GitHub PR returned invalid state for {repo}#{number}")

    feedback = _collect_feedback(
        repo,
        number,
        item["operator_login"],
        session=session,
        headers=headers,
        max_pages=max_pages,
    )
    review_decisions = _current_review_decisions(feedback)
    current_change_requests = [
        event
        for event in review_decisions.values()
        if event["state"] == "CHANGES_REQUESTED"
    ]
    last_seen = item["last_seen_at"]
    # A timestamp-only cursor cannot uniquely identify GitHub events.  Replay
    # equality conservatively so two distinct events stamped at the same instant
    # cannot cause one to be silently lost.
    new_feedback = [event for event in feedback if event["at"] >= last_seen]
    response_feedback = [
        event
        for event in new_feedback
        if event["kind"] in {"comment", "inline_comment"}
        or (event["kind"] == "review" and event["state"] == "COMMENTED")
    ]

    if merged_at is not None:
        safe_state = "MERGED"
    elif state == "closed":
        safe_state = "CLOSED_UNMERGED"
    else:
        safe_state = "OPEN"

    # Maintainer obligations outrank lifecycle/settlement routing.  The cursor
    # only controls notification freshness; it never clears a current blocker.
    if current_change_requests:
        next_action = "repair_requested"
        reason = "current_maintainer_changes_requested"
    elif response_feedback:
        next_action = "respond_to_maintainer"
        reason = "new_maintainer_feedback"
    elif merged_at is not None:
        if item["settlement_followup_url"] is None:
            next_action = "route_settlement_followup"
            reason = "merged_without_settlement_followup_evidence"
        else:
            next_action = "monitor_settlement"
            reason = "merged_followup_already_routed"
    elif state == "closed":
        next_action = "investigate_closed_unmerged"
        reason = "pr_closed_unmerged"
    elif new_feedback:
        next_action = "await_acceptance"
        reason = "new_nonactionable_maintainer_review"
    else:
        next_action = "await_acceptance"
        reason = "open_without_new_maintainer_feedback"

    latest = new_feedback[-1] if new_feedback else None
    return {
        "repo": repo,
        "pr": number,
        "canonical_url": canonical_url,
        "head_sha": head_sha,
        "advertised_amount": format(item["advertised_amount"], "f"),
        "currency": item["currency"],
        "state": safe_state,
        "merged_at": _iso_or_none(merged_at),
        "next_action": next_action,
        "reason": reason,
        "new_feedback_count": len(new_feedback),
        "current_change_request_count": len(current_change_requests),
        "latest_feedback": None
        if latest is None
        else {
            "kind": latest["kind"],
            "state": latest["state"],
            "author": latest["author"],
            "at": _iso_or_none(latest["at"]),
            "url": latest["url"],
        },
        "settlement_followup_url": item["settlement_followup_url"],
        "cash_status": "not_inferred",
    }


def build_closeout_queue(
    items: list[dict[str, Any]],
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """Scan and deterministically prioritize paid-work closeout items."""
    if not isinstance(items, list):
        raise RevenueCloseoutInputError("items must be a list")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise RevenueCloseoutInputError("max_pages must be positive")
    seen: set[tuple[str, int]] = set()
    manifest_order: dict[tuple[str, int], int] = {}
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        validated = _validate_item(raw)
        identity = (validated["repo"].casefold(), validated["pr"])
        if identity in seen:
            raise RevenueCloseoutInputError(
                f"duplicate closeout item: {validated['repo']}#{validated['pr']}"
            )
        seen.add(identity)
        manifest_order[identity] = index
        results.append(
            scan_paid_pr(raw, token, session=session, max_pages=max_pages)
        )

    def sort_key(result: dict[str, Any]) -> tuple[int, int]:
        identity = (result["repo"].casefold(), result["pr"])
        return (
            _ACTION_ORDER[result["next_action"]],
            manifest_order[identity],
        )

    results.sort(key=sort_key)
    return results


def _load_manifest(path: str) -> dict[str, Any]:
    if path == "-":
        payload = json.load(__import__("sys").stdin)
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RevenueCloseoutInputError("manifest must be an object")
    if payload.get("schema_version") != 1:
        raise RevenueCloseoutInputError("manifest schema_version must be 1")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RevenueCloseoutInputError("manifest items must be a list")
    return payload


def format_summary(results: list[dict[str, Any]]) -> str:
    lines = []
    for row in results:
        lines.append(
            f"{row['repo']}#{row['pr']} {row['currency']} {row['advertised_amount']} "
            f"{row['state']} -> {row['next_action']} ({row['reason']})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_closeout",
        description=(
            "Read current GitHub PR/review state and prioritize paid-work acceptance "
            "and settlement follow-through without inferring earned or paid cash."
        ),
    )
    parser.add_argument("manifest", help="JSON manifest path, or - for stdin")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="emit full safe JSON")
    args = parser.parse_args(argv)
    try:
        payload = _load_manifest(args.manifest)
        results = build_closeout_queue(
            payload["items"],
            max_pages=args.max_pages,
        )
    except (
        OSError,
        json.JSONDecodeError,
        RevenueCloseoutError,
        RevenueCloseoutInputError,
    ) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps({"schema_version": 1, "items": results}, indent=2, sort_keys=True))
    else:
        print(format_summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())