# SPDX-License-Identifier: MIT
"""End-to-end paid-work preflight with issue-thread competition pressure.

``bounty_audit`` establishes canonical issue/PR state and
``bounty_qualification`` decides whether work is safe to dispatch. This module
fills the missing ``attempt_count`` input from canonical GitHub issue comments,
binds formal GitHub assignment state, and authority-binds maintainer
contribution terms for credential safety.
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
from concierge.credential_safety import (
    apply_credential_gate,
    credential_gate_signal_types,
)


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


def _has_maintainer_authority(item: dict[str, Any]) -> bool:
    association = item.get("author_association")
    return (
        isinstance(association, str)
        and association.upper() in _MAINTAINER_ASSOCIATIONS
    )


def _is_external_human(comment: dict[str, Any]) -> tuple[bool, str]:
    if _has_maintainer_authority(comment):
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


def _normalized_operator_login(operator_login: str | None) -> str | None:
    if operator_login is None:
        return None
    if not isinstance(operator_login, str) or not operator_login.strip():
        raise ValueError("operator_login must be a non-empty string when provided")
    return operator_login.strip().casefold()


def _assignee_state(
    issue: dict[str, Any], operator_login: str | None
) -> dict[str, Any]:
    """Reduce public GitHub assignees to privacy-safe occupancy signals.

    Assignment is stronger custody evidence than a low comment/PR count. We do
    not copy assignee names into receipts; only deterministic counts and whether
    the configured operator is among the assignees leave this function.
    """
    raw_assignees = issue.get("assignees")
    if raw_assignees is None:
        raw_assignees = []
    if not isinstance(raw_assignees, list):
        raise BountyPreflightError("GitHub issue assignees response was not a list")

    assignees: set[str] = set()
    for item in raw_assignees:
        if not isinstance(item, dict):
            raise BountyPreflightError("GitHub issue assignees contained a malformed item")
        login = item.get("login")
        if not isinstance(login, str) or not login.strip():
            raise BountyPreflightError(
                "GitHub issue assignee did not contain a non-empty login"
            )
        assignees.add(login.strip().casefold())

    operator = _normalized_operator_login(operator_login)
    assigned_to_operator = operator is not None and operator in assignees
    foreign_assignees = assignees - ({operator} if operator is not None else set())
    return {
        "formal_assignee_count": len(assignees),
        "assigned_to_operator": assigned_to_operator,
        "foreign_assignee_count": len(foreign_assignees),
    }


def _apply_assignee_gate(
    qualification: dict[str, Any], assignee_state: dict[str, Any]
) -> dict[str, Any]:
    """Hold dispatch when canonical issue assignment belongs elsewhere.

    Existing REJECT outcomes stay REJECT. The gate is intentionally independent
    of the configured saturation threshold: one formal foreign assignee is
    sufficient evidence that another contributor currently owns the issue.
    """
    if not isinstance(qualification, dict):
        raise BountyPreflightError("qualification result was not an object")

    result = dict(qualification)
    signals = result.get("signals")
    if signals is None:
        signals = {}
    elif not isinstance(signals, dict):
        raise BountyPreflightError("qualification signals were not an object")
    else:
        signals = dict(signals)
    signals.update(assignee_state)
    result["signals"] = signals

    if assignee_state["foreign_assignee_count"] <= 0:
        return result

    reasons = result.get("reasons")
    if reasons is None:
        reasons = []
    elif not isinstance(reasons, list):
        raise BountyPreflightError("qualification reasons were not a list")
    else:
        reasons = list(reasons)

    reason_codes = result.get("reason_codes")
    if reason_codes is None:
        reason_codes = []
    elif not isinstance(reason_codes, list):
        raise BountyPreflightError("qualification reason_codes were not a list")
    else:
        reason_codes = list(reason_codes)

    if "FORMALLY_ASSIGNED" not in reason_codes:
        reason_codes.append("FORMALLY_ASSIGNED")
        reasons.append(
            {
                "code": "FORMALLY_ASSIGNED",
                "severity": "HOLD",
                "message": (
                    "Canonical GitHub state formally assigns this issue to another "
                    "contributor; do not dispatch duplicate paid work."
                ),
            }
        )

    result["reasons"] = reasons
    result["reason_codes"] = reason_codes
    if result.get("disposition") != "REJECT":
        result["disposition"] = "HOLD"
        result["dispatch"] = False
    return result


def collect_issue_context(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    operator_login: str | None = None,
) -> dict[str, Any]:
    """Collect safe qualification inputs plus conservative claim pressure.

    External comments contribute only to ``attempt_count``. Raw external comment
    text is never forwarded into qualification. OWNER/MEMBER/COLLABORATOR terms
    are reduced immediately to generic credential-safety signals, and assignees
    are reduced to counts/operator-membership so raw assignee identities do not
    leave the collection boundary.
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
    _normalized_operator_login(operator_login)

    token = token or GITHUB_TOKEN
    headers = _headers(token)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    issue = _object_payload(
        _get_json(session, issue_url, headers=headers),
        f"issue {repo}#{number}",
    )
    if "pull_request" in issue:
        raise ValueError(f"{repo}#{number} is a pull request, not an issue")

    issue_body = issue.get("body")
    if issue_body is None:
        issue_body = ""
    if not isinstance(issue_body, str):
        raise BountyPreflightError(
            f"GitHub issue body was not a string for {repo}#{number}"
        )
    assignee_state = _assignee_state(issue, operator_login)

    claimant_logins: set[str] = set()
    attempt_signal_count = 0
    comments_truncated = False
    credential_signals: set[str] = set()
    if _has_maintainer_authority(issue):
        credential_signals.update(credential_gate_signal_types([issue_body]))

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
            if _has_maintainer_authority(comment) and body.strip():
                credential_signals.update(credential_gate_signal_types([body]))
            external_human, login = _is_external_human(comment)
            if external_human and _signals_attempt(body, repo):
                attempt_signal_count += 1
                claimant_logins.add(login)
        if len(payload) < 100:
            break
    else:
        comments_truncated = True

    return {
        "title": issue.get("title") if issue.get("title") is not None else "",
        "body": issue_body,
        "labels": issue.get("labels") if issue.get("labels") is not None else [],
        "attempt_count": len(claimant_logins),
        "attempt_signal_count": attempt_signal_count,
        "comments_truncated": comments_truncated,
        "credential_gate_signal_types": sorted(credential_signals),
        **assignee_state,
    }


def preflight_bounty(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    operator_login: str | None = None,
) -> dict[str, Any]:
    """Return an operator-safe paid-work preflight result for one issue."""
    context = collect_issue_context(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
        operator_login=operator_login,
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
        "title": context["title"],
        "body": context["body"],
        "labels": context["labels"],
        "attempt_count": context["attempt_count"],
        "canonical_audit": audit,
    }
    qualification = qualify_dispatch(
        snapshot,
        saturation_threshold=saturation_threshold,
    )
    qualification = apply_credential_gate(
        qualification,
        context["credential_gate_signal_types"],
    )
    assignee_state = {
        "formal_assignee_count": context["formal_assignee_count"],
        "assigned_to_operator": context["assigned_to_operator"],
        "foreign_assignee_count": context["foreign_assignee_count"],
    }
    qualification = _apply_assignee_gate(qualification, assignee_state)

    return {
        "repo": repo,
        "number": number,
        "attempt_count": context["attempt_count"],
        "attempt_signal_count": context["attempt_signal_count"],
        "comments_truncated": context["comments_truncated"],
        **assignee_state,
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
            "Audit canonical bounty state, formal assignment, issue-thread claim "
            "pressure, and dispatch safety."
        ),
    )
    parser.add_argument("repo", help="Repository in owner/name form")
    parser.add_argument("issue", type=int, help="Bounty issue number")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument(
        "--operator-login",
        help=(
            "GitHub login that may already own the issue; when omitted, any formal "
            "assignee holds dispatch"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit full safe JSON result")
    args = parser.parse_args(argv)

    try:
        result = preflight_bounty(
            args.repo,
            args.issue,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
            operator_login=args.operator_login,
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
