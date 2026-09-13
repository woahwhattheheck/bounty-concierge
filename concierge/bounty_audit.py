# SPDX-License-Identifier: MIT
"""Canonical GitHub audit signals for bounty triage.

Bounty platforms and issue labels can lag canonical repository state.  This
module cross-checks an issue against pull requests that explicitly reference it
and reports competition plus a conservative stale-listing signal.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

import requests

from concierge.config import GITHUB_TOKEN


class BountyAuditError(RuntimeError):
    """Raised when canonical GitHub state cannot be read reliably."""


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(session: Any, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None) -> Any:
    try:
        response = session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BountyAuditError(f"GitHub request failed for {url}: {exc}") from exc
    return response.json()


def _issue_reference_pattern(repo: str, number: int) -> re.Pattern[str]:
    owner, name = repo.split("/", 1)
    full_url = rf"https?://github\.com/{re.escape(owner)}/{re.escape(name)}/issues/{number}(?!\d)"
    qualified_ref = rf"(?<![A-Za-z0-9_.-]){re.escape(owner)}/{re.escape(name)}#{number}(?!\d)"
    short_ref = rf"(?<![A-Za-z0-9_.-])#{number}(?!\d)"
    return re.compile(rf"(?:{full_url}|{qualified_ref}|{short_ref})", re.IGNORECASE)


def references_issue(pr: dict[str, Any], repo: str, number: int) -> bool:
    """Return True only when a PR title/body explicitly references the issue.

    GitHub search is deliberately used only for candidate discovery because a
    bare number search can also match unrelated larger issue numbers or prose.
    """
    text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
    return bool(_issue_reference_pattern(repo, number).search(text))


def _competition_level(open_pr_count: int) -> str:
    if open_pr_count == 0:
        return "none"
    if open_pr_count == 1:
        return "low"
    if open_pr_count <= 3:
        return "medium"
    return "high"


def audit_bounty(repo: str, number: int, token: str | None = None, *, session: Any = requests, max_pages: int = 10) -> dict[str, Any]:
    """Audit one GitHub bounty issue against canonical linked pull requests.

    ``stale_listing_signal`` is intentionally conservative: it is true only
    when the issue is still open while at least one explicitly linked PR has
    already merged.  That is a review signal, not proof that the full bounty was
    satisfied; broad issues can legitimately have partial merged PRs.
    """
    if "/" not in repo or not repo.split("/", 1)[0] or not repo.split("/", 1)[1]:
        raise ValueError("repo must be in owner/name form")
    if number <= 0:
        raise ValueError("number must be a positive issue number")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")

    token = token or GITHUB_TOKEN
    headers = _headers(token)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    issue = _get_json(session, issue_url, headers=headers)
    if "pull_request" in issue:
        raise ValueError(f"{repo}#{number} is a pull request, not an issue")

    candidates: list[dict[str, Any]] = []
    search_truncated = False
    search_url = "https://api.github.com/search/issues"
    for page in range(1, max_pages + 1):
        payload = _get_json(
            session,
            search_url,
            headers=headers,
            params={"q": f"repo:{repo} is:pr {number}", "per_page": 100, "page": page},
        )
        items = payload.get("items", [])
        candidates.extend(items)
        if len(items) < 100:
            break
    else:
        # Search results may be capped by GitHub; make truncation explicit.
        search_truncated = True

    exact_candidates: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        pr_number = candidate.get("number")
        if not isinstance(pr_number, int) or not references_issue(candidate, repo, number):
            continue
        exact_candidates[pr_number] = candidate

    linked_prs: list[dict[str, Any]] = []
    for pr_number in sorted(exact_candidates):
        detail = _get_json(
            session,
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
            headers=headers,
        )
        merged = bool(detail.get("merged_at"))
        state = detail.get("state") or "unknown"
        linked_prs.append(
            {
                "number": pr_number,
                "title": detail.get("title") or exact_candidates[pr_number].get("title") or "",
                "url": detail.get("html_url") or exact_candidates[pr_number].get("html_url") or "",
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

    return {
        "repo": repo,
        "number": number,
        "issue_url": issue.get("html_url") or f"https://github.com/{repo}/issues/{number}",
        "issue_state": issue_state,
        "linked_pr_count": len(linked_prs),
        "open_pr_count": open_pr_count,
        "merged_pr_count": merged_pr_count,
        "closed_unmerged_pr_count": closed_unmerged_pr_count,
        "competition_level": _competition_level(open_pr_count),
        "stale_listing_signal": issue_state == "open" and merged_pr_count > 0,
        "search_truncated": search_truncated,
        "linked_prs": linked_prs,
    }


def audit_bounties(bounties: list[dict[str, Any]], token: str | None = None, *, session: Any = requests, max_pages: int = 10) -> list[dict[str, Any]]:
    """Return bounty records enriched with a nested ``canonical_audit`` field."""
    audited = []
    for bounty in bounties:
        row = dict(bounty)
        row["canonical_audit"] = audit_bounty(
            row["repo"],
            int(row["number"]),
            token,
            session=session,
            max_pages=max_pages,
        )
        audited.append(row)
    return audited


def format_summary(audit: dict[str, Any]) -> str:
    """Format the high-signal audit fields for a human operator."""
    signal = "YES" if audit["stale_listing_signal"] else "no"
    truncation = " (search truncated)" if audit["search_truncated"] else ""
    return (
        f"{audit['repo']}#{audit['number']} issue={audit['issue_state']} "
        f"linked_prs={audit['linked_pr_count']} open={audit['open_pr_count']} "
        f"merged={audit['merged_pr_count']} closed_unmerged={audit['closed_unmerged_pr_count']} "
        f"competition={audit['competition_level']} stale_listing_signal={signal}{truncation}"
    )


def main(argv: list[str] | None = None) -> int:
    """Run a one-issue canonical audit from the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_audit",
        description="Cross-check a bounty issue against canonical linked GitHub PR state.",
    )
    parser.add_argument("repo", help="Repository in owner/name form")
    parser.add_argument("issue", type=int, help="Bounty issue number")
    parser.add_argument("--max-pages", type=int, default=10, help="Maximum GitHub search pages (default: 10)")
    parser.add_argument("--json", action="store_true", help="Emit full JSON audit")
    args = parser.parse_args(argv)

    result = audit_bounty(args.repo, args.issue, max_pages=args.max_pages)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
        for pr in result["linked_prs"]:
            status = "merged" if pr["merged"] else pr["state"]
            draft = " draft" if pr["draft"] else ""
            print(f"  PR #{pr['number']}: {status}{draft} - {pr['title']} - {pr['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
