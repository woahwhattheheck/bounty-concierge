# SPDX-License-Identifier: MIT
"""Offline original-author bounty claim-gap audit.

Accepts a provider-captured JSON snapshot. It NEVER contacts GitHub or a payout
provider, posts comments, edits PRs, or claims that an award has been paid.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "mova-claim-gap-audit/v1"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GIT_SHA = re.compile(r"^[a-f0-9]{40}$")
CLAIM = re.compile(
    r"(?m)^[ \t]*/claim[ \t]+(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?"
    r"#(?P<number>[1-9][0-9]*)(?=\s|$)",
    re.IGNORECASE,
)
ACTION = re.compile(r"\b(?:claim|request|seek|ask|demand)(?:s|ed|ing)?\b", re.I)
REWARD = re.compile(r"\b(?:bounty|reward|compensation|payment|payout|allocation)\b", re.I)
WAIVER = re.compile(
    r"\b(?:i am not claiming|not a claim|do not claim|not requesting|"
    r"waiv(?:e|ing) (?:the )?(?:bounty|reward|compensation|payment)|"
    r"forfeit(?:ing)? (?:the )?(?:bounty|reward|compensation|payment))\b",
    re.I,
)


class ClaimAuditError(ValueError):
    """Malformed or unsafe snapshot evidence."""


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ClaimAuditError(f"{field} must be an object")
    return value


def _int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ClaimAuditError(f"{field} must be a positive integer")
    return value


def _text(value: Any, field: str, maximum: int = 200_000) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ClaimAuditError(f"{field} must be a bounded string")
    return value


def _repo(value: Any) -> str:
    value = _text(value, "repo", 200)
    if not REPO.fullmatch(value):
        raise ClaimAuditError("repo must be owner/name")
    return value


def _utc(value: Any) -> datetime:
    value = _text(value, "observed_at", 64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaimAuditError("observed_at must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClaimAuditError("observed_at must have a UTC offset")
    return parsed.astimezone(timezone.utc)


def _authored(author: Any, actor: dict[str, Any]) -> bool:
    if not isinstance(author, dict):
        return False
    return (
        author.get("login") == actor["login"]
        and type(author.get("id")) is int
        and author["id"] == actor["id"]
    )


def _prose(value: str) -> str:
    # Quoted discussions and fenced code are not author claim statements.
    clean = re.sub(r"(?s)\x60{3}.*?\x60{3}", "", value)
    return "\n".join(line for line in clean.splitlines()
                     if not line.lstrip().startswith(">"))


def _claim_for(value: str, repo: str, issue_number: int) -> bool:
    for hit in CLAIM.finditer(_prose(value)):
        if int(hit.group("number")) != issue_number:
            continue
        qualified = hit.group("repo")
        if qualified is None or qualified.casefold() == repo.casefold():
            return True
    return False


def _compensation_request(value: str) -> bool:
    prose = _prose(value)
    if WAIVER.search(prose):
        return False
    # An unpaid-state note alone is not an affirmative monetary request.
    return bool(ACTION.search(prose) and REWARD.search(prose))


def audit(snapshot: Any, *, now: datetime | None = None) -> dict[str, Any]:
    data = _object(snapshot, "snapshot")
    if data.get("schema") != SCHEMA:
        raise ClaimAuditError(f"schema must be {SCHEMA}")
    actor = _object(data.get("actor"), "actor")
    actor_login = _text(actor.get("login"), "actor.login", 128)
    if not actor_login or not re.fullmatch(r"[A-Za-z0-9-]+", actor_login):
        raise ClaimAuditError("actor.login must be a GitHub login")
    actor_id = _int(actor.get("id"), "actor.id")
    actor = {"login": actor_login, "id": actor_id}
    observed = _utc(data.get("observed_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = current - observed
    fresh = -timedelta(minutes=5) <= age <= timedelta(hours=24)

    prs = data.get("pull_requests")
    issues = data.get("issues")
    if not isinstance(prs, list) or not isinstance(issues, list):
        raise ClaimAuditError("pull_requests and issues must be arrays")
    if len(prs) > 500 or len(issues) > 2_000:
        raise ClaimAuditError("snapshot exceeds bounded scan")
    by_pr: dict[tuple[str, int], dict[str, Any]] = {}
    for item in prs:
        item = _object(item, "pull_request")
        repo, number = _repo(item.get("repo")), _int(item.get("number"), "pr.number")
        key = (repo.casefold(), number)
        if key in by_pr:
            raise ClaimAuditError(f"duplicate PR {repo}#{number}")
        sha = _text(item.get("head_sha"), "pr.head_sha", 40)
        if not GIT_SHA.fullmatch(sha):
            raise ClaimAuditError(f"PR {repo}#{number} head_sha invalid")
        _text(item.get("body"), "pr.body")
        by_pr[key] = item

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in issues:
        item = _object(item, "issue")
        repo, number = _repo(item.get("repo")), _int(item.get("number"), "issue.number")
        key = (repo.casefold(), number)
        if key in seen:
            raise ClaimAuditError(f"duplicate issue {repo}#{number}")
        seen.add(key)
        pr_number = _int(item.get("pr_number"), "issue.pr_number")
        pr = by_pr.get((repo.casefold(), pr_number))
        if item.get("state") not in ("open", "closed"):
            raise ClaimAuditError("issue.state must be open or closed")
        comments = item.get("comments")
        if not isinstance(comments, list) or len(comments) > 1_000:
            raise ClaimAuditError("issue.comments must be a bounded array")
        issue_claims = []
        for comment in comments:
            comment = _object(comment, "issue.comment")
            body = _text(comment.get("body"), "issue.comment.body")
            if _authored(comment.get("author"), actor) and _claim_for(body, repo, number):
                issue_claims.append(comment)
        issue_claimed = bool(issue_claims)
        pr_claimed = bool(pr and _claim_for(pr["body"], repo, number))
        pr_reward_requested = bool(pr and _compensation_request(pr["body"]))
        gaps: list[str] = []
        hold: str | None = None

        if not fresh:
            hold = "HOLD_STALE_CAPTURE"
        elif pr is None:
            hold = "HOLD_MISSING_PR_SNAPSHOT"
        elif not _authored(pr.get("author"), actor):
            hold = "HOLD_NOT_ORIGINAL_AUTHOR"
        elif item["state"] != "open" or pr.get("state") != "open":
            hold = "HOLD_CLOSED_PRESERVE_EXISTING"
        elif item.get("comments_complete") is not True:
            hold = "HOLD_PARTIAL_ISSUE_TIMELINE"
        else:
            if not issue_claimed:
                gaps.append("MISSING_ORIGINAL_ISSUE_CLAIM")
            if not pr_claimed:
                gaps.append("MISSING_PR_ISSUE_CLAIM")
            if not pr_reward_requested:
                gaps.append("MISSING_PR_COMPENSATION_REQUEST")

        results.append({
            "issue": f"{repo}#{number}",
            "issue_url": f"https://github.com/{repo}/issues/{number}",
            "pr_url": f"https://github.com/{repo}/pull/{pr_number}",
            "pr_head": pr["head_sha"] if pr else None,
            "state": hold or ("ACTIONABLE_GAPS" if gaps else "CLAIMS_PRESENT"),
            "gaps": gaps,
            "original_issue_claim_seen": issue_claimed,
            "original_pr_claim_seen": pr_claimed,
            "pr_compensation_request_seen": pr_reward_requested,
            "award_status": "NOT_VERIFIED",
            "evidence": "operator-supplied-provider-snapshot",
        })

    results.sort(key=lambda row: row["issue"].casefold())
    summary = {
        "total": len(results),
        "actionable": sum(row["state"] == "ACTIONABLE_GAPS" for row in results),
        "held": sum(row["state"].startswith("HOLD_") for row in results),
        "already_claimed": sum(row["state"] == "CLAIMS_PRESENT" for row in results),
    }
    return {
        "schema": SCHEMA + "/report",
        "actor": actor,
        "observed_at": observed.isoformat(),
        "fresh": fresh,
        "summary": summary,
        "items": results,
        "side_effects": "NONE",
        "payment_proof": "NONE",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="locally captured provider JSON")
    parser.add_argument("--fail-on-gaps", action="store_true",
                        help="exit 2 when audit finds actionable missing claims")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.snapshot.read_text(encoding="utf-8"))
        report = audit(data)
    except (ClaimAuditError, OSError, ValueError) as exc:
        print(f"claim audit: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if args.fail_on_gaps and report["summary"]["actionable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
