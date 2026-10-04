# SPDX-License-Identifier: MIT
"""Canonical GitHub audit signals for bounty triage.

Bounty platforms and issue labels can lag canonical repository state.  This
module cross-checks an issue against pull requests that explicitly reference it
and reports competition plus a conservative stale-listing signal.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import re
import sys
from copy import deepcopy
from typing import Any

import requests

from concierge.config import GITHUB_TOKEN
from concierge.bounty_contract_common import BountyContractEvidenceError
from concierge.bounty_contract_live import _comment_page_has_next
from concierge.submission_packet import validate_submission_target


_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_MAINTAINER_EXPIRY_PATTERNS = (
    re.compile(
        r"(?:^|[.!:]\s+)(?:this|the)\s+bounty\s+(?:has\s+been\s+expired|has\s+expired|is\s+expired|expired)\b(?![^.!\n]*\?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!:]\s+)(?:this|the)\s+bounty\s+(?:has\s+been\s+|has\s+|is\s+|was\s+)?cancel(?:l)?ed\b(?![^.!\n]*\?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!:]\s+)(?:this|the)\s+bounty\s+is\s+no\s+longer\s+(?:active|available|offered)\b(?![^.!\n]*\?)",
        re.IGNORECASE,
    ),
)


class BountyAuditError(RuntimeError):
    """Raised when canonical GitHub state cannot be read reliably."""


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
    response_metadata: dict[str, Any] | None = None,
) -> Any:
    try:
        response = session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BountyAuditError(f"GitHub request failed for {url}: {exc}") from exc
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise BountyAuditError(f"GitHub response was not valid JSON for {url}") from exc
    if response_metadata is not None:
        response_headers = getattr(response, "headers", None)
        if isinstance(response_headers, Mapping):
            response_metadata["link"] = response_headers.get("Link", response_headers.get("link"))
    return payload


def _object_payload(value: Any, context: str) -> dict[str, Any]:
    """Require one GitHub object response before callers dereference fields."""
    if not isinstance(value, dict):
        raise BountyAuditError(f"GitHub {context} response was not an object")
    return value


def _issue_reference_pattern(
    repo: str, number: int, *, allow_short: bool = True
) -> re.Pattern[str]:
    owner, name = repo.split("/", 1)
    full_url = rf"https?://github\.com/{re.escape(owner)}/{re.escape(name)}/issues/{number}(?!\w)"
    qualified_ref = rf"(?<![A-Za-z0-9_.-]){re.escape(owner)}/{re.escape(name)}#{number}(?!\w)"
    short_ref = rf"(?<![A-Za-z0-9_.-])#{number}(?!\w)"
    patterns = [full_url, qualified_ref]
    if allow_short:
        patterns.append(short_ref)
    return re.compile("(?:" + "|".join(patterns) + ")", re.IGNORECASE)


def references_issue(
    pr: dict[str, Any], repo: str, number: int, *, pr_repo: str | None = None
) -> bool:
    """Return True only when a PR title/body explicitly references the issue.

    GitHub search is deliberately used only for candidate discovery because a
    bare number search can also match unrelated larger issue numbers or prose.
    A short #number refers to the PR's own repository, so a foreign PR must use
    the source issue's full URL or owner/repository#number reference.
    """
    text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
    allow_short = pr_repo is None or pr_repo.casefold() == repo.casefold()
    return bool(_issue_reference_pattern(repo, number, allow_short=allow_short).search(text))


def _competition_level(open_pr_count: int) -> str:
    if open_pr_count == 0:
        return "none"
    if open_pr_count == 1:
        return "low"
    if open_pr_count <= 3:
        return "medium"
    return "high"


def _is_explicit_maintainer_expiry(comment: dict[str, Any]) -> bool:
    association = comment.get("author_association")
    if not isinstance(association, str) or association.upper() not in _MAINTAINER_ASSOCIATIONS:
        return False
    body = comment.get("body")
    if not isinstance(body, str):
        return False
    return any(pattern.search(body) for pattern in _MAINTAINER_EXPIRY_PATTERNS)


def _maintainer_expiry_evidence(
    session: Any,
    repo: str,
    number: int,
    *,
    headers: dict[str, str],
    max_pages: int,
) -> tuple[list[dict[str, Any]], bool]:
    evidence: list[dict[str, Any]] = []
    comments_url = f"https://api.github.com/repos/{repo}/issues/{number}/comments"
    for page in range(1, max_pages + 1):
        metadata: dict[str, Any] = {}
        payload = _get_json(
            session,
            comments_url,
            headers=headers,
            params={"per_page": 100, "page": page},
            response_metadata=metadata,
        )
        if not isinstance(payload, list):
            raise BountyAuditError(f"GitHub issue comments response was not a list for {repo}#{number}")
        for comment in payload:
            if not isinstance(comment, dict):
                raise BountyAuditError(f"GitHub issue comments response contained a malformed item for {repo}#{number}")
            if not _is_explicit_maintainer_expiry(comment):
                continue
            user = comment.get("user")
            login = user.get("login") if isinstance(user, dict) else None
            evidence.append(
                {
                    "url": comment.get("html_url") or "",
                    "author_association": str(comment.get("author_association") or "").upper(),
                    "author": login or "",
                    "created_at": comment.get("created_at"),
                    "body": comment.get("body") or "",
                }
            )
        try:
            has_next = _comment_page_has_next(metadata, comments_url, page)
        except BountyContractEvidenceError as exc:
            raise BountyAuditError(f"GitHub issue comments pagination was invalid for {repo}#{number}") from exc
        if has_next is False or (has_next is None and len(payload) < 100):
            return evidence, False
    return evidence, True


def audit_bounty(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    submission_target: dict[str, str] | None = None,
    _pr_detail_cache: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit one GitHub bounty issue against canonical repository signals.

    ``stale_listing_signal`` is intentionally conservative: it is true only
    when the issue is still open and either an explicitly linked PR has already
    merged or a repository maintainer explicitly says that the bounty expired,
    was cancelled, or is no longer offered.  That is a review signal, not proof
    that the full bounty was satisfied. An existing explicit submission_target
    adds its delivery repository to the bounded PR census. Issue comments and
    maintainer authority remain attached to the original bounty repository.
    """
    if "/" not in repo or not repo.split("/", 1)[0] or not repo.split("/", 1)[1]:
        raise ValueError("repo must be in owner/name form")
    if number <= 0:
        raise ValueError("number must be a positive issue number")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    if submission_target is not None:
        submission_target = validate_submission_target(
            submission_target, f"https://github.com/{repo}/issues/{number}"
        )

    # requests.get creates and closes a Session for every call. Keep one pool
    # for this traversal without changing ownership of caller-supplied sessions.
    if session is requests:
        with requests.Session() as owned_session:
            return audit_bounty(
                repo, number, token, session=owned_session, max_pages=max_pages,
                submission_target=submission_target,
                _pr_detail_cache=_pr_detail_cache,
            )

    token = token or GITHUB_TOKEN
    headers = _headers(token)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    issue = _object_payload(
        _get_json(session, issue_url, headers=headers),
        f"issue {repo}#{number}",
    )
    if "pull_request" in issue:
        raise ValueError(f"{repo}#{number} is a pull request, not an issue")

    search_repos = [repo]
    if (submission_target is not None
            and submission_target["repository"].casefold() != repo.casefold()):
        search_repos.append(submission_target["repository"])
    cross_repository = len(search_repos) > 1
    candidates: list[tuple[str, dict[str, Any]]] = []
    search_truncated = False
    search_url = "https://api.github.com/search/issues"
    for search_repo in search_repos:
        for page in range(1, max_pages + 1):
            payload = _object_payload(
                _get_json(
                    session,
                    search_url,
                    headers=headers,
                    params={"q": f"repo:{search_repo} is:pr {number}", "per_page": 100, "page": page},
                ),
                f"search in {search_repo} for {repo}#{number}",
            )
            if payload.get("incomplete_results") is True:
                search_truncated = True
            items = payload.get("items", [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise BountyAuditError(f"GitHub search response contained malformed items for {repo}#{number}")
            candidates.extend((search_repo, item) for item in items)
            if len(items) < 100:
                break
            # A full final page needs no empty-page request to prove completion.
            total_count = payload.get("total_count")
            if len(items) == 100 and type(total_count) is int and total_count == page * 100:
                break
        else:
            # Keep incompleteness from either bounded repository traversal.
            search_truncated = True

    exact_candidates: dict[tuple[str, int], tuple[str, dict[str, Any]]] = {}
    for pr_repo, candidate in candidates:
        pr_number = candidate.get("number")
        if (type(pr_number) is not int or pr_number <= 0
                or not references_issue(candidate, repo, number, pr_repo=pr_repo)):
            continue
        exact_candidates[(pr_repo.casefold(), pr_number)] = (pr_repo, candidate)

    linked_prs: list[dict[str, Any]] = []
    for identity in sorted(exact_candidates):
        pr_repo, candidate = exact_candidates[identity]
        pr_number = identity[1]
        if _pr_detail_cache is not None and identity in _pr_detail_cache:
            detail = deepcopy(_pr_detail_cache[identity])
        else:
            detail = _object_payload(
                _get_json(
                    session,
                    f"https://api.github.com/repos/{pr_repo}/pulls/{pr_number}",
                    headers=headers,
                ),
                f"pull request {pr_repo}#{pr_number}",
            )
            if _pr_detail_cache is not None:
                # Share only a successful payload; each consumer owns its copy.
                _pr_detail_cache[identity] = deepcopy(detail)
        if not references_issue(detail, repo, number, pr_repo=pr_repo):
            # Search discovery may lag an edited reference in either repository.
            continue
        merged = bool(detail.get("merged_at"))
        state = detail.get("state") or "unknown"
        linked_prs.append(
            {
                "number": pr_number,
                **({"repository": pr_repo} if cross_repository else {}),
                "title": detail.get("title") or candidate.get("title") or "",
                "url": detail.get("html_url") or candidate.get("html_url") or "",
                "state": state,
                "draft": bool(detail.get("draft")),
                "merged": merged,
                "merged_at": detail.get("merged_at"),
            }
        )

    open_pr_count = sum(1 for pr in linked_prs if pr["state"] == "open" and not pr["merged"])
    merged_pr_count = sum(1 for pr in linked_prs if pr["merged"])
    closed_unmerged_pr_count = sum(1 for pr in linked_prs if pr["state"] == "closed" and not pr["merged"])
    issue_state = issue.get("state") or "unknown"

    maintainer_expiry_comments: list[dict[str, Any]] = []
    comments_truncated = False
    if issue_state == "open":
        maintainer_expiry_comments, comments_truncated = _maintainer_expiry_evidence(
            session,
            repo,
            number,
            headers=headers,
            max_pages=max_pages,
        )
    search_truncated = search_truncated or comments_truncated
    maintainer_expiry_signal = bool(maintainer_expiry_comments)

    return {
        "repo": repo,
        "number": number,
        **({"submission_target": submission_target} if submission_target is not None else {}),
        "issue_url": issue.get("html_url") or f"https://github.com/{repo}/issues/{number}",
        "issue_state": issue_state,
        "linked_pr_count": len(linked_prs),
        "open_pr_count": open_pr_count,
        "merged_pr_count": merged_pr_count,
        "closed_unmerged_pr_count": closed_unmerged_pr_count,
        "competition_level": _competition_level(open_pr_count),
        "maintainer_expiry_signal": maintainer_expiry_signal,
        "maintainer_expiry_comment_count": len(maintainer_expiry_comments),
        "maintainer_expiry_comments": maintainer_expiry_comments,
        "stale_listing_signal": issue_state == "open" and (merged_pr_count > 0 or maintainer_expiry_signal),
        "search_truncated": search_truncated,
        "linked_prs": linked_prs,
    }


def audit_bounties(bounties: list[dict[str, Any]], token: str | None = None, *, session: Any = requests, max_pages: int = 10) -> list[dict[str, Any]]:
    """Read each case-insensitive issue/explicit-target combination once per invocation.

    Canonical PR details are shared within this batch when distinct issues link
    the same PR. Later invocations and standalone audits perform fresh reads.
    """
    if session is requests:
        with requests.Session() as owned_session:
            return audit_bounties(
                bounties, token, session=owned_session, max_pages=max_pages
            )

    audited = []
    pr_detail_cache: dict[tuple[str, int], dict[str, Any]] = {}
    audit_by_issue: dict[tuple[str, int, str | None], dict[str, Any]] = {}
    for bounty in bounties:
        row = dict(bounty)
        repo, number = row["repo"], int(row["number"])
        target = row.get("submission_target")
        if target is not None:
            target = validate_submission_target(
                target, f"https://github.com/{repo}/issues/{number}"
            )
        # Target evidence is part of the report, not just a search hint. Do not
        # reuse another row's repository scope or retained instruction source.
        target_key = (
            json.dumps(target, sort_keys=True, separators=(",", ":"))
            if target is not None else None
        )
        key = (repo.casefold(), number, target_key)
        if key not in audit_by_issue:
            audit_by_issue[key] = audit_bounty(
                repo,
                key[1],
                token,
                session=session,
                max_pages=max_pages,
                submission_target=target,
                _pr_detail_cache=pr_detail_cache,
            )
        # Each row owns its nested report. A caller editing one scout's result
        # must not alter another row or the evidence reused later in this batch.
        row["canonical_audit"] = deepcopy(audit_by_issue[key])
        # Repository casing does not change GitHub identity, but each row keeps
        # its caller's spelling in the report and source-repository PR markers.
        row["canonical_audit"]["repo"] = repo
        for pr in row["canonical_audit"]["linked_prs"]:
            if pr.get("repository", "").casefold() == key[0]:
                pr["repository"] = repo
        audited.append(row)
    return audited


def format_summary(audit: dict[str, Any]) -> str:
    """Format the high-signal audit fields for a human operator."""
    signal = "YES" if audit["stale_listing_signal"] else "no"
    maintainer_signal = "YES" if audit.get("maintainer_expiry_signal") else "no"
    truncation = " (search truncated)" if audit["search_truncated"] else ""
    return (
        f"{audit['repo']}#{audit['number']} issue={audit['issue_state']} "
        f"linked_prs={audit['linked_pr_count']} open={audit['open_pr_count']} "
        f"merged={audit['merged_pr_count']} closed_unmerged={audit['closed_unmerged_pr_count']} "
        f"competition={audit['competition_level']} maintainer_expiry_signal={maintainer_signal} "
        f"stale_listing_signal={signal}{truncation}"
    )


def main(argv: list[str] | None = None) -> int:
    """Run a one-issue canonical audit from the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_audit",
        description="Cross-check a bounty issue against canonical linked GitHub PR state.",
    )
    parser.add_argument("repo", help="Repository in owner/name form")
    parser.add_argument("issue", type=int, help="Bounty issue number")
    parser.add_argument(
        "--max-pages", type=int, default=10,
        help="Maximum pages per GitHub search/comment read (default: 10); incomplete reads exit 2",
    )
    parser.add_argument("--json", action="store_true", help="Emit full JSON audit")
    parser.add_argument(
        "--submission-target", type=Path,
        help="JSON file containing the existing explicit submission_target record",
    )
    args = parser.parse_args(argv)

    target = None
    if args.submission_target is not None:
        try:
            target = validate_submission_target(
                json.loads(args.submission_target.read_text(encoding="utf-8")),
                f"https://github.com/{args.repo}/issues/{args.issue}",
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    result = audit_bounty(
        args.repo, args.issue, max_pages=args.max_pages, submission_target=target
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
        for pr in result["linked_prs"]:
            status = "merged" if pr["merged"] else pr["state"]
            draft = " draft" if pr["draft"] else ""
            identity = f"{pr['repository']}#{pr['number']}" if "repository" in pr else f"#{pr['number']}"
            print(f"  PR {identity}: {status}{draft} - {pr['title']} - {pr['url']}")
    if result["search_truncated"]:
        print(
            "PARTIAL: GitHub search or comment history is incomplete; "
            "collected evidence is retained, but this is not a complete census. "
            "Increase --max-pages if the page bound was reached, or retry the source read.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
