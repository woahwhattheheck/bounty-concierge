# SPDX-License-Identifier: MIT
"""End-to-end paid-work preflight with issue-thread competition pressure.

``bounty_audit`` establishes canonical issue/PR state and
``bounty_qualification`` decides whether work is safe to dispatch.  This module
fills the missing ``attempt_count`` input from canonical GitHub issue comments
without letting arbitrary commenters influence contribution-term rejection.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

import requests

from concierge.bounty_audit import BountyAuditError, audit_bounty
from concierge.bounty_qualification import (
    QualificationInputError,
    qualify_dispatch,
)
from concierge.config import GITHUB_TOKEN


_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_ATTEMPT_COMMAND_RE = re.compile(
    r"(?im)^\s*/(?:attempt(?:\s+#?\d+)?|claim(?:\s+#?\d+)?|opire\s+try)(?:\s|$)"
)
_ATTEMPT_PHRASE_RE = re.compile(
    r"(?i)\b(?:claiming\s+(?:this|the)\s+bounty|"
    r"i(?:'m|\s+am)\s+(?:working\s+on|taking)\s+this(?:\s+bounty)?|"
    r"i(?:'d|\s+would)\s+like\s+to\s+work\s+on\s+this(?:\s+bounty)?)\b"
)


class BountyPreflightError(RuntimeError):
    """Raised when canonical GitHub preflight inputs cannot be read reliably."""


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
        raise BountyPreflightError(f"GitHub request failed for {url}: {exc}") from exc
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise BountyPreflightError(f"GitHub response was not valid JSON for {url}") from exc


def _object_payload(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BountyPreflightError(f"GitHub {context} response was not an object")
    return value


def _is_external_human(comment: dict[str, Any]) -> tuple[bool, str]:
    association = comment.get("author_association")
    if isinstance(association, str) and association.upper() in _MAINTAINER_ASSOCIATIONS:
        return False, ""
    user = comment.get("user")
    if not isinstance(user, dict):
        return False, ""
    login = user.get("login")
    if not isinstance(login, str) or not login.strip():
        return False, ""
    user_type = user.get("type")
    if (
        isinstance(user_type, str) and user_type.casefold() == "bot"
    ) or login.casefold().endswith("[bot]"):
        return False, ""
    return True, login.casefold()


def _signals_attempt(body: str, repo: str) -> bool:
    if _ATTEMPT_COMMAND_RE.search(body) or _ATTEMPT_PHRASE_RE.search(body):
        return True
    same_repo_pr = re.compile(
        rf"https?://github\.com/{re.escape(repo)}/pull/\d+(?!\d)", re.IGNORECASE
    )
    return bool(same_repo_pr.search(body))


def collect_issue_context(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Collect safe qualification inputs plus conservative claim pressure.

    Issue comments contribute only to ``attempt_count``.  Raw comment text is
    never forwarded into qualification, so neither an arbitrary claimant nor a
    negated maintainer remark can inject a lexical private-context phrase and
    force a terminal rejection.
    """
    if "/" not in repo or not repo.split("/", 1)[0] or not repo.split("/", 1)[1]:
        raise ValueError("repo must be in owner/name form")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError("number must be a positive issue number")
    if (
        isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages <= 0
    ):
        raise ValueError("max_pages must be positive")

    token = token or GITHUB_TOKEN
    headers = _headers(token)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    issue = _object_payload(
        _get_json(session, issue_url, headers=headers),
        f"issue {repo}#{number}",
    )
    if "pull_request" in issue:
        raise ValueError(f"{repo}#{number} is a pull request, not an issue")

    claimant_logins: set[str] = set()
    attempt_signal_count = 0
    comments_truncated = False
    comments_url = f"https://api.github.com/repos/{repo}/issues/{number}/comments"

    for page in range(1, max_pages + 1):
        payload = _get_json(
            session,
            comments_url,
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(payload, list):
            raise BountyPreflightError(
                f"GitHub issue comments response was not a list for {repo}#{number}"
            )
        for comment in payload:
            if not isinstance(comment, dict):
                raise BountyPreflightError(
                    f"GitHub issue comments response contained a malformed item for {repo}#{number}"
                )
            body = comment.get("body")
            if body is not None and not isinstance(body, str):
                raise BountyPreflightError(
                    f"GitHub issue comment body was not a string for {repo}#{number}"
                )
            body = body or ""
            external_human, login = _is_external_human(comment)
            if external_human and _signals_attempt(body, repo):
                attempt_signal_count += 1
                claimant_logins.add(login)
        if len(payload) < 100:
            break
    else:
        comments_truncated = True

    return {
        "body": issue.get("body") if issue.get("body") is not None else "",
        "labels": issue.get("labels") if issue.get("labels") is not None else [],
        "attempt_count": len(claimant_logins),
        "attempt_signal_count": attempt_signal_count,
        "comments_truncated": comments_truncated,
    }


def preflight_bounty(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Return an operator-safe paid-work preflight result for one issue."""
    context = collect_issue_context(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
    )
    raw_audit = audit_bounty(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
    )
    if not isinstance(raw_audit, dict):
        raise BountyPreflightError("canonical bounty audit did not return an object")
    audit = dict(raw_audit)
    if context["comments_truncated"]:
        audit["search_truncated"] = True

    snapshot = {
        "body": context["body"],
        "labels": context["labels"],
        "attempt_count": context["attempt_count"],
        "canonical_audit": audit,
    }
    qualification = qualify_dispatch(
        snapshot,
        saturation_threshold=saturation_threshold,
    )
    return {
        "repo": repo,
        "number": number,
        "attempt_count": context["attempt_count"],
        "attempt_signal_count": context["attempt_signal_count"],
        "comments_truncated": context["comments_truncated"],
        "canonical_audit": audit,
        "qualification": qualification,
    }


def format_summary(result: dict[str, Any]) -> str:
    audit = result["canonical_audit"]
    qualification = result["qualification"]
    return (
        f"{result['repo']}#{result['number']} attempts={result['attempt_count']} "
        f"open_prs={audit.get('open_pr_count', 'unknown')} "
        f"disposition={qualification['disposition']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_preflight",
        description=(
            "Audit canonical bounty state, issue-thread claim pressure, "
            "and dispatch safety."
        ),
    )
    parser.add_argument("repo", help="Repository in owner/name form")
    parser.add_argument("issue", type=int, help="Bounty issue number")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--json", action="store_true", help="Emit full safe JSON result")
    args = parser.parse_args(argv)

    try:
        result = preflight_bounty(
            args.repo,
            args.issue,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
        )
    except (
        BountyPreflightError,
        BountyAuditError,
        QualificationInputError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))

    disposition = result["qualification"]["disposition"]
    if disposition == "ACTIONABLE":
        return 0
    if disposition == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
