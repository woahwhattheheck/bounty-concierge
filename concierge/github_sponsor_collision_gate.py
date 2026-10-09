# SPDX-License-Identifier: MIT
"""Original-author PR publisher with a live, all-state sponsor issue-carrier fence.

A collision is a HOLD, not a successful duplicate submission.  These checks
consume first-party GitHub reads, never switch credentials, and never write to
GitHub themselves.  The supplied transport is the only write capability.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, Optional, TypeVar

import requests

from concierge.github_intake_cache import classify_github_403
from concierge.github_publish_preflight import execute_publish_operation

T = TypeVar("T")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[a-f0-9]{40}$")
_LOGIN = re.compile(r"^[A-Za-z0-9-]+$")
_API = "https://api.github.com"
_MAX_PAGES = 12


class SponsorCollisionPreflightError(RuntimeError):
    """Provider read, identity or pagination was incomplete: do not publish."""


class SponsorIssueCarrierCollision(RuntimeError):
    """One first-party sponsor PR already points to this issue/head."""

    def __init__(self, receipt: dict[str, Any]) -> None:
        self.receipt = receipt
        super().__init__("existing sponsor PR requires original-author review")


def _get_json(session: Any, url: str, token: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = session.get(url, headers=headers, timeout=12, allow_redirects=False)
        status = response.status_code
        if status != 200:
            rate = response.headers.get("X-RateLimit-Remaining")
            retry = response.headers.get("Retry-After")
            if status in {403, 429}:
                # A secondary throttle can have a healthy primary quota and
                # no Retry-After header; GitHub's response text is decisive.
                remaining = int(rate) if isinstance(rate, str) and rate.isdecimal() else None
                retry_seconds = (
                    int(retry) if isinstance(retry, str) and retry.isdecimal() else None
                )
                classification = classify_github_403(
                    str(getattr(response, "text", "")),
                    remaining=remaining,
                    retry_after_seconds=retry_seconds,
                )
                if classification == "SCOPE_DENIED":
                    scope = "INTEGRATION_SCOPE_DENIED"
                elif classification in {"PRIMARY_RATE_LIMITED", "SECONDARY_RATE_LIMITED"}:
                    scope = classification
                elif status == 429:
                    scope = "RATE_LIMITED"
                else:
                    scope = "PROVIDER_HTTP_ERROR"
            else:
                scope = "PROVIDER_HTTP_ERROR"
            raise SponsorCollisionPreflightError(
                f"GitHub issue-carrier read blocked: HTTP {status} ({scope}); "
                "no write attempted"
            )
        return response.json()
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise SponsorCollisionPreflightError(
            "GitHub issue-carrier read incomplete; no write attempted"
        ) from exc


def _identity(repo: str, issue_number: int, actor: str, head: str) -> tuple[str, int, str, str]:
    if not isinstance(repo, str) or _REPO.fullmatch(repo) is None:
        raise ValueError("repo must be owner/name")
    if type(issue_number) is not int or issue_number <= 0:
        raise ValueError("issue_number must be positive")
    if not isinstance(actor, str) or _LOGIN.fullmatch(actor) is None:
        raise ValueError("actor must be a GitHub login")
    if not isinstance(head, str) or _SHA.fullmatch(head) is None:
        raise ValueError("expected_head must be a lowercase 40-digit Git SHA")
    return repo, issue_number, actor, head


def _user_and_repo(session: Any, token: str, repo: str, actor: str,
                   actor_id: int | None) -> dict[str, Any]:
    identity = _get_json(session, f"{_API}/user", token)
    if (type(identity) is not dict or not isinstance(identity.get("login"), str)
            or identity["login"].casefold() != actor.casefold()
            or actor_id is not None and identity.get("id") != actor_id):
        raise SponsorCollisionPreflightError("GitHub token actor mismatch; no write attempted")
    state = _get_json(session, f"{_API}/repos/{repo}", token)
    if (type(state) is not dict or type(state.get("archived")) is not bool
            or type(state.get("full_name")) is not str
            or state["full_name"].casefold() != repo.casefold()):
        raise SponsorCollisionPreflightError("Sponsor repository metadata is not source-bound")
    return {"full_name": state["full_name"], "archived": state["archived"]}


def _issue_mentions(pr: dict[str, Any], repo: str, issue_number: int) -> bool:
    """Conservatively flag literal issue keys in title/body, not unrelated numbers."""
    title = pr["title"]
    body = pr["body"] or ""
    bare = re.compile(rf"(?<![A-Za-z0-9_])#{issue_number}(?![0-9])")
    # A canonical issue URL or owner/repo#n is also a claim.
    # A substring comparison aliases #55 to #550 and needlessly blocks a real
    # paid issue carrier. Only the exact numeric GitHub issue identity matches.
    canonical = re.compile(
        rf"https://github\.com/{re.escape(repo)}/issues/{issue_number}(?![0-9])",
        flags=re.IGNORECASE,
    )
    if canonical.search(title + "\n" + body) is not None:
        return True
    if re.search(rf"{re.escape(repo)}#{issue_number}(?![0-9])",
                 title + "\n" + body, flags=re.IGNORECASE):
        return True
    # Bare issue-key mentions are REVIEW holds: some are related-only, and
    # those require human scope review before a new payable submission.
    return bare.search(title) is not None or bare.search(body) is not None


def _census(session: Any, token: str, repo: str, issue_number: int,
            actor: str, expected_head: str, max_pages: int) -> tuple[list[dict[str, Any]], int]:
    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for page in range(1, max_pages + 1):
        # state=all catches same-author merged/closed source, not just open PRs.
        url = f"{_API}/repos/{repo}/pulls?state=all&per_page=100&page={page}"
        rows = _get_json(session, url, token)
        if type(rows) is not list or len(rows) > 100:
            raise SponsorCollisionPreflightError("Sponsor pull-request page malformed")
        for pr in rows:
            if type(pr) is not dict:
                raise SponsorCollisionPreflightError("Sponsor PR row malformed")
            number = pr.get("number")
            title, body, link = pr.get("title"), pr.get("body"), pr.get("html_url")
            owner = pr.get("user")
            h = pr.get("head")
            b = pr.get("base")
            if (type(number) is not int or number <= 0 or number in seen
                    or type(title) is not str or type(body) not in {str, type(None)}
                    or type(link) is not str or type(owner) is not dict
                    or type(owner.get("login")) is not str or type(h) is not dict
                    or type(h.get("sha")) is not str or _SHA.fullmatch(h["sha"]) is None
                    or type(b) is not dict or type(b.get("ref")) is not str):
                raise SponsorCollisionPreflightError("Sponsor PR page lacks canonical identity")
            if link.casefold() != f"https://github.com/{repo}/pull/{number}".casefold():
                raise SponsorCollisionPreflightError("Sponsor PR link mismatches destination")
            seen.add(number)
            exact_source = (h["sha"] == expected_head and
                            owner["login"].casefold() == actor.casefold())
            if exact_source or _issue_mentions(pr, repo, issue_number):
                matches.append({
                    "number": number,
                    "url": link,
                    "author_login": owner["login"],
                    "state": pr.get("state"),
                    "merged": pr.get("merged_at") is not None,
                    "base_ref": b["ref"],
                    "head_sha": h["sha"],
                    "exact_original_head": exact_source,
                    "issue_key_mentioned": _issue_mentions(pr, repo, issue_number),
                })
        if len(rows) < 100:
            return matches, page
    # A capped first page/list is NOT evidence that nothing overlaps.
    raise SponsorCollisionPreflightError(
        "Sponsor PR census exceeded page cap; cannot prove no competing carrier"
    )


def publish_sponsor_issue_pr(
    ledger_path: str,
    token: str,
    *,
    rail: str,
    actor: str,
    actor_id: int | None,
    operation: str,
    repo: str,
    issue_number: int,
    carrier: str,
    expected_head: str,
    transport: Callable[[], T],
    session: Any = None,
    max_pages: int = _MAX_PAGES,
    cooldown_scope: Optional[str] = None,
    recovery_owner: Optional[str] = None,
    scope_breaker_path: Optional[str] = None,
) -> T | dict[str, Any]:
    """Publish via existing quota/credential gate with fresh issue-carrier reads.

    Authenticated token must be the identity used by transport; actor_id pins
    the payout-owning author.  Not a cross-agent atomic lock: the final upstream
    REST create must still be idempotently recovered on 422/403 ambiguity.
    """
    repo, issue_number, actor, expected_head = _identity(
        repo, issue_number, actor, expected_head
    )
    if not isinstance(token, str) or not token:
        raise SponsorCollisionPreflightError("An authenticated GitHub token is required")
    if actor_id is not None and (type(actor_id) is not int or actor_id <= 0):
        raise ValueError("actor_id must be positive or null")
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be 1..100")
    if not callable(transport):
        raise ValueError("transport must be callable")
    client = requests if session is None else session
    state_cache: dict[str, Any] = {}

    def repository_state() -> dict[str, Any]:
        # Mandatory first-party check, before collision reads or write quotas.
        state = _user_and_repo(client, token, repo, actor, actor_id)
        state_cache.update(state)
        return state

    def live_reconcile() -> None:
        matches, pages = _census(
            client, token, repo, issue_number, actor, expected_head, max_pages
        )
        if matches:
            receipt = {
                "schema": "sponsor-issue-carrier-collision/v1",
                "status": "HOLD_EXISTING_ISSUE_CARRIER",
                "provider_write_called": False,
                "repo": repo,
                "issue_number": issue_number,
                "expected_head": expected_head,
                "actor": actor,
                "snapshot_at": datetime.now(timezone.utc).isoformat(),
                "pages_fetched": pages,
                "matching_prs": matches,
                "repository_archived": state_cache.get("archived"),
                "next_action": "REVIEW_EXISTING_PR_BEFORE_NEW_CLAIM_OR_PUBLISH",
            }
            payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            receipt["receipt_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
            raise SponsorIssueCarrierCollision(receipt)
        return None

    try:
        return execute_publish_operation(
            ledger_path, token, rail=rail, actor=actor, operation=operation,
            action="create-pull-request", repo=repo, carrier=carrier,
            expected_head=expected_head, transport=transport,
            cooldown_scope=cooldown_scope, recovery_owner=recovery_owner,
            scope_breaker_path=scope_breaker_path,
            provider_repository_state=repository_state,
            provider_reconcile=live_reconcile,
        )
    except SponsorIssueCarrierCollision as exc:
        return exc.receipt
