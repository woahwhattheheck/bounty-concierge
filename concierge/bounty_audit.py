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
    """Unreliable GitHub state, optionally retaining an interrupted batch.

    ``partial_report`` contains completed audits plus any issue-local unavailable
    outcomes. A shared failure leaves its row and later rows unaudited. Library
    callers receive this exception rather than a success-shaped partial list.
    """

    partial_report: dict[str, Any] | None = None
    http_status: int | None = None
    retry_after: str | None = None
    rate_limit_reset: str | None = None
    request_url: str | None = None
    rate_limit_remaining: int | None = None


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
        error = BountyAuditError(f"GitHub request failed for {url}: {exc}")
        error.request_url = url
        failed_response = getattr(exc, "response", None)
        status = getattr(failed_response, "status_code", None)
        error.http_status = status if type(status) is int else None
        failed_headers = getattr(failed_response, "headers", None)
        if isinstance(failed_headers, Mapping):
            retry_after = failed_headers.get("Retry-After", failed_headers.get("retry-after"))
            reset = failed_headers.get("X-RateLimit-Reset", failed_headers.get("x-ratelimit-reset"))
            error.retry_after = retry_after if isinstance(retry_after, str) else None
            error.rate_limit_reset = reset if isinstance(reset, str) else None
            remaining = failed_headers.get("X-RateLimit-Remaining", failed_headers.get("x-ratelimit-remaining"))
            if isinstance(remaining, str):
                try:
                    error.rate_limit_remaining = int(remaining)
                except ValueError:
                    pass
        if failed_response is not None:
            try:
                failed_response.close()
            except Exception:
                # Cleanup must not replace the provider failure or its metadata.
                pass
        raise error from exc
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

    Linked PR author/head metadata comes only from canonical PR detail. Missing
    fields stay null; this observation does not establish assignment or payment.
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
        author = detail.get("user")
        head = detail.get("head")
        head_repo = head.get("repo") if isinstance(head, dict) else None
        author_login = author.get("login") if isinstance(author, dict) else None
        head_repository = head_repo.get("full_name") if isinstance(head_repo, dict) else None
        head_ref = head.get("ref") if isinstance(head, dict) else None
        head_sha = head.get("sha") if isinstance(head, dict) else None
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
                "author_login": author_login if isinstance(author_login, str) and author_login else None,
                "head_repository": head_repository if isinstance(head_repository, str) and head_repository else None,
                "head_ref": head_ref if isinstance(head_ref, str) and head_ref else None,
                "head_sha": head_sha if isinstance(head_sha, str) and head_sha else None,
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


def _batch_error_details(exc: BountyAuditError) -> dict[str, Any]:
    return {
        "type": "BountyAuditError", "message": str(exc),
        "http_status": exc.http_status, "retry_after": exc.retry_after,
        "rate_limit_reset": exc.rate_limit_reset,
    }


def _batch_partial_report(
    bounties: list[dict[str, Any]], audited: list[dict[str, Any]],
    exc: BountyAuditError, failed_row: int, remaining_start: int,
    unavailable: list[dict[str, Any]],
) -> dict[str, Any]:
    # A cached input audit must not masquerade as an observation from this run.
    remaining = [
        {key: value for key, value in candidate.items() if key != "canonical_audit"}
        for candidate in bounties[remaining_start:]
    ]
    report = {
        "status": "PARTIAL", "input_count": len(bounties),
        "audited_count": len(audited), "remaining_count": len(remaining),
        "failed_row": failed_row, "rows": deepcopy(audited),
        "remaining_candidates": deepcopy(remaining), "error": _batch_error_details(exc),
    }
    if unavailable:
        report.update(
            unavailable_count=len(unavailable),
            unavailable_candidates=deepcopy(unavailable),
            traversal_complete=remaining_start == len(bounties),
        )
    return report


def audit_bounties(bounties: list[dict[str, Any]], token: str | None = None, *, session: Any = requests, max_pages: int = 10) -> list[dict[str, Any]]:
    """Read each case-insensitive issue/explicit-target combination once per invocation.

    Canonical PR details are shared within this batch when distinct issues link
    the same PR. Later invocations and standalone audits perform fresh reads.
    A canonical issue 404/410 is retained as unavailable while independent rows
    continue. Other provider/evidence failures stop immediately. Any failed row
    raises BountyAuditError with explicit outcomes in ``partial_report``; there
    are no retries or fabricated audits for unavailable issues.
    """
    if session is requests:
        with requests.Session() as owned_session:
            return audit_bounties(
                bounties, token, session=owned_session, max_pages=max_pages
            )

    audited = []
    pr_detail_cache: dict[tuple[str, int], dict[str, Any]] = {}
    audit_by_issue: dict[tuple[str, int, str | None], dict[str, Any]] = {}
    unavailable_by_issue: dict[tuple[str, int], BountyAuditError] = {}
    unavailable: list[dict[str, Any]] = []
    first_unavailable: tuple[int, BountyAuditError] | None = None
    for index, bounty in enumerate(bounties):
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
        issue_key = (repo.casefold(), number)
        key = (*issue_key, target_key)
        if key not in audit_by_issue:
            try:
                if issue_key in unavailable_by_issue:
                    raise unavailable_by_issue[issue_key]
                audit_by_issue[key] = audit_bounty(
                    repo,
                    key[1],
                    token,
                    session=session,
                    max_pages=max_pages,
                    submission_target=target,
                    _pr_detail_cache=pr_detail_cache,
                )
            except BountyAuditError as exc:
                if (
                    exc.http_status in {404, 410}
                    and exc.request_url is not None
                    and exc.request_url.casefold() == f"https://api.github.com/repos/{repo}/issues/{key[1]}".casefold()
                    and exc.retry_after is None and exc.rate_limit_remaining != 0
                ):
                    # Unavailable is an observation, not proof of permanent
                    # deletion or permission to submit. Keep it out of the
                    # automatic remaining-list recovery path, with full input
                    # identity/metadata retained for source resolution.
                    # Issue availability is independent of target instructions;
                    # successful audits still retain their complete target key.
                    unavailable_by_issue[issue_key] = exc
                    unavailable.append({
                        "input_row": index + 1, "status": "ISSUE_UNAVAILABLE",
                        "candidate": deepcopy({key: value for key, value in row.items()
                                               if key != "canonical_audit"}),
                        "error": _batch_error_details(exc),
                    })
                    if first_unavailable is None:
                        first_unavailable = index + 1, exc
                    continue
                exc.partial_report = _batch_partial_report(
                    bounties, audited, exc, index + 1, index, unavailable,
                )
                raise
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
    if first_unavailable is not None:
        failed_row, exc = first_unavailable
        exc.partial_report = _batch_partial_report(
            bounties, audited, exc, failed_row, len(bounties), unavailable,
        )
        raise exc
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


def _load_audit_batch(source: str) -> list[dict[str, Any]]:
    """Read and validate the whole bounded shortlist before any provider call."""
    limit = 1024 * 1024
    if source == "-":
        raw = getattr(sys.stdin, "buffer", sys.stdin).read(limit + 1)
    else:
        with open(source, "rb") as stream:
            raw = stream.read(limit + 1)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > limit:
        raise ValueError("batch input exceeds 1 MiB")

    def reject_constant(value: str) -> None:
        raise ValueError(f"batch input contains non-JSON constant {value}")

    payload = json.loads(raw, parse_constant=reject_constant)
    if isinstance(payload, dict) and set(payload) == {"candidates"}:
        payload = payload["candidates"]
    if not isinstance(payload, list):
        raise ValueError('batch input must be a list or an exact {"candidates": [...]} object')
    for index, row in enumerate(payload, 1):
        if not isinstance(row, dict):
            raise ValueError(f"batch row {index} must be an object")
        repo, number = row.get("repo"), row.get("number")
        if (not isinstance(repo, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+", repo) is None
                or repo.split("/")[1] in {".", ".."}):
            raise ValueError(f"batch row {index} repo must be an owner/name repository")
        if type(number) is not int or number < 1:
            raise ValueError(f"batch row {index} number must be a positive integer")
        if row.get("submission_target") is not None:
            validate_submission_target(
                row["submission_target"], f"https://github.com/{repo}/issues/{number}"
            )
    return payload


def main(argv: list[str] | None = None) -> int:
    """Run a canonical audit for one issue or a retained shortlist."""
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_audit",
        description="Cross-check a bounty issue against canonical linked GitHub PR state.",
    )
    parser.add_argument("repo", nargs="?", help="Repository in owner/name form")
    parser.add_argument("issue", nargs="?", type=int, help="Bounty issue number")
    parser.add_argument(
        "--batch", metavar="FILE",
        help="Audit a JSON shortlist up to 1 MiB; use - for stdin; provider failures retain partial output and exit 2",
    )
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
    partial_report = None

    if args.batch is not None:
        if args.repo is not None or args.issue is not None or args.submission_target is not None:
            parser.error("--batch cannot be combined with repo, issue or --submission-target")
        try:
            rows = _load_audit_batch(args.batch)
        except (OSError, ValueError, RecursionError) as exc:
            parser.error(str(exc))
        try:
            result = audit_bounties(rows, max_pages=args.max_pages)
            audits = [row["canonical_audit"] for row in result]
        except BountyAuditError as exc:
            if exc.partial_report is None:
                raise
            partial_report = exc.partial_report
            result = partial_report
            audits = [row["canonical_audit"] for row in partial_report["rows"]]
    else:
        if args.repo is None or args.issue is None:
            parser.error("repo and issue are required unless --batch is supplied")
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
        audits = [result]
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for audit in audits:
            print(format_summary(audit))
            for pr in audit["linked_prs"]:
                status = "merged" if pr["merged"] else pr["state"]
                draft = " draft" if pr["draft"] else ""
                identity = f"{pr['repository']}#{pr['number']}" if "repository" in pr else f"#{pr['number']}"
                metadata = " ".join(
                    f"{key}={pr[key]}"
                    for key in ("author_login", "head_repository", "head_ref", "head_sha")
                    if pr.get(key) is not None
                )
                suffix = f" [{metadata}]" if metadata else ""
                print(f"  PR {identity}: {status}{draft} - {pr['title']} - {pr['url']}{suffix}")
    if partial_report is not None:
        error = partial_report["error"]
        if partial_report.get("traversal_complete"):
            print(
                f"PARTIAL: retained {partial_report['audited_count']} of "
                f"{partial_report['input_count']} input rows; traversal completed. "
                f"{partial_report['unavailable_count']} unavailable issue rows need source resolution.",
                file=sys.stderr,
            )
        else:
            print(
                f"PARTIAL: retained {partial_report['audited_count']} of "
                f"{partial_report['input_count']} input rows; stopped at row "
                f"{partial_report['failed_row']}. {partial_report['remaining_count']} "
                f"rows still need an audit. {error['message']}",
                file=sys.stderr,
            )
        for outcome in partial_report.get("unavailable_candidates", []):
            candidate = outcome["candidate"]
            print(
                f"Unavailable input row {outcome['input_row']}: "
                f"{candidate['repo']}#{candidate['number']} HTTP {outcome['error']['http_status']}; "
                "resolve the source before explicitly adding it to another shortlist.",
                file=sys.stderr,
            )
        if not partial_report.get("traversal_complete") and (
            error["retry_after"] is not None or error["rate_limit_reset"] is not None
        ):
            print(
                f"Provider cooldown metadata: Retry-After={error['retry_after']!r}; "
                f"X-RateLimit-Reset={error['rate_limit_reset']!r}. "
                "Respect the provider cooldown before retrying remaining_candidates; "
                "no retry was made.",
                file=sys.stderr,
            )
        return 2
    if any(audit["search_truncated"] for audit in audits):
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
