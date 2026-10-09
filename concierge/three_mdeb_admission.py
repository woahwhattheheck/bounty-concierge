# SPDX-License-Identifier: MIT
"""Read-only, fail-closed admission for the 3mdeb/Dasharo bounty programme.

Input is a previously captured, source-backed issue snapshot. This module never
fetches GitHub, assigns a contributor, files a claim, or asserts an award.
Financial and maintainer proof must come from the sponsor, not from a board card.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "3mdeb-assignment-admission/v1"
ACTORS = frozenset({"woahwhattheheck", "tokenjunkielabs"})
CATEGORIES = frozenset({"bounty-warmup", "bounty-easy", "bounty-medium", "bounty-hard"})
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PR_URL = re.compile(r"^/[^/]+/[^/]+/pull/[0-9]+/?$")
_OC_EXPENSE = re.compile(r"^/3mdeb_com/expenses/[0-9]+/?$")


def _url(value: Any, host: str, path: re.Pattern[str]) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname == host
        and not parts.username
        and not parts.password
        and port is None
        and not parts.query
        and not parts.fragment
        and bool(path.fullmatch(parts.path))
    )


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _login(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, dict):
        return str(value.get("login", "")).lower()
    return ""


def _labels(issue: dict[str, Any]) -> set[str]:
    values = issue.get("labels", [])
    if not isinstance(values, list):
        return set()
    return {
        _login(item.get("name", "")) if isinstance(item, dict) else _login(item)
        for item in values
    }


def sponsor_paid_merge_verified(history: Any) -> bool:
    """Evidence must identify this payer, a merged source PR, and PAID expense."""
    if not isinstance(history, list):
        return False
    for item in history:
        if not isinstance(item, dict):
            continue
        if (
            item.get("sponsor") == "3mdeb"
            and item.get("merged") is True
            and item.get("paid") is True
            and _date(item.get("paid_at")) is not None
            and _url(item.get("merged_pr_url"), "github.com", _PR_URL)
            and _url(item.get("expense_url"), "opencollective.com", _OC_EXPENSE)
        ):
            return True
    return False


def _reward_verified(issue: dict[str, Any], minimum_usd: Decimal) -> bool:
    offer = issue.get("offer")
    if not isinstance(offer, dict) or offer.get("confirmed_by_sponsor") is not True:
        return False
    if offer.get("currency") != "USD" or not _url(
        offer.get("source_url"),
        "github.com",
        re.compile(r"^/[^/]+/[^/]+/issues/[0-9]+/?$"),
    ):
        return False
    raw = offer.get("amount")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return False
    try:
        amount = Decimal(str(raw))
    except InvalidOperation:
        return False
    return amount.is_finite() and amount >= minimum_usd


def classify_issue(
    issue: dict[str, Any],
    *,
    paid_merge_history: Any,
    actor: str,
    as_of: datetime,
    minimum_usd: Decimal = Decimal("15"),
) -> dict[str, Any]:
    """No BUILD_READY unless cash, current assignment, and collision proofs exist."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must have a UTC offset")
    if actor not in ACTORS:
        raise ValueError("actor must be an authorized original contributor")
    now = as_of.astimezone(timezone.utc)
    reasons: list[str] = []
    repo, number = issue.get("repository"), issue.get("number")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        reasons.append("INVALID_REPOSITORY")
    if type(number) is not int or number <= 0:
        reasons.append("INVALID_ISSUE_NUMBER")
    if issue.get("state") != "open":
        reasons.append("ISSUE_NOT_OPEN")
    labels = _labels(issue)
    if "bounty" not in labels:
        reasons.append("BOUNTY_LABEL_MISSING")
    if len(labels.intersection(CATEGORIES)) != 1:
        reasons.append("EXACT_CATEGORY_LABEL_REQUIRED")
    if not sponsor_paid_merge_verified(paid_merge_history):
        reasons.append("SAME_SPONSOR_PAID_MERGE_UNVERIFIED")
    if not _reward_verified(issue, minimum_usd):
        reasons.append("CONFIRMED_USD_REWARD_REQUIRED")

    # A missing search receipt is not evidence that a competing PR is absent.
    open_prs = issue.get("open_prs")
    if not isinstance(open_prs, list):
        reasons.append("OPEN_PR_SEARCH_UNVERIFIED")
        open_prs = []
    for pr in open_prs:
        if not isinstance(pr, dict) or pr.get("state") not in ("open", "closed"):
            reasons.append("OPEN_PR_SEARCH_UNVERIFIED")
            continue
        if pr["state"] != "open":
            continue
        owner = _login(pr.get("user", pr.get("author")))
        if not owner:
            reasons.append("OPEN_PR_SEARCH_UNVERIFIED")
        elif owner in ACTORS:
            reasons.append("ORIGINAL_CARRIER_ALREADY_EXISTS")
        else:
            reasons.append("COMPETING_OPEN_PR")

    activity_at = _date(issue.get("last_maintainer_action_at"))
    if (
        activity_at is None
        or activity_at > now + timedelta(days=1)
        or now - activity_at > timedelta(days=90)
    ):
        reasons.append("CURRENT_MAINTAINER_RESPONSE_UNVERIFIED")

    assignees = issue.get("assignees", [])
    if not isinstance(assignees, list):
        reasons.append("ASSIGNEES_UNVERIFIED")
        assignees = []
    assigned_logins = {_login(x) for x in assignees}
    if assigned_logins and actor.lower() not in assigned_logins:
        reasons.append("ASSIGNED_TO_ANOTHER_CONTRIBUTOR")
    approval = issue.get("maintainer_assignment_proof")
    approved = False
    if isinstance(approval, dict):
        comment = approval.get("comment_url", "")
        if isinstance(comment, str):
            base, separator, anchor = comment.partition("#")
            approved = (
                separator == "#"
                and approval.get("actor") == actor
                and approval.get("approved") is True
                and anchor.startswith("issuecomment-")
                and anchor[13:].isdigit()
                and _url(
                    base,
                    "github.com",
                    re.compile(r"^/[^/]+/[^/]+/issues/[0-9]+/?$"),
                )
            )
    if actor in assigned_logins and not approved:
        reasons.append("MAINTAINER_ASSIGNMENT_PROOF_MISSING")
    if issue.get("hardware_required") is True and issue.get("hardware_validation_ready") is not True:
        reasons.append("HARDWARE_VALIDATION_NOT_READY")

    reasons = sorted(set(reasons))
    if not reasons and actor in assigned_logins and approved:
        status = "BUILD_READY"
    elif not reasons and not assigned_logins:
        status = "REQUEST_ASSIGNMENT"
    else:
        status = "HOLD"
    return {
        "repository": repo,
        "number": number,
        "actor": actor,
        "status": status,
        "reasons": reasons,
        "claim_status": "NO_CLAIM_EMITTED",
        "payout_status": "NOT_VERIFIED",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", help="JSON with issues and paid_merge_history")
    p.add_argument("--as-of", required=True, help="offset-aware ISO 8601 evaluation time")
    p.add_argument("--actor", choices=sorted(ACTORS), required=True)
    args = p.parse_args(argv)
    now = _date(args.as_of)
    if now is None:
        p.error("--as-of must be an offset-aware ISO 8601 timestamp")
    with open(args.snapshot, "r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        p.error("snapshot must contain an issues array")
    if not all(isinstance(item, dict) for item in payload["issues"]):
        p.error("every issue snapshot must be an object")
    results = [
        classify_issue(
            item,
            paid_merge_history=payload.get("paid_merge_history"),
            actor=args.actor,
            as_of=now,
        )
        for item in payload["issues"]
    ]
    print(json.dumps({"schema": SCHEMA, "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
