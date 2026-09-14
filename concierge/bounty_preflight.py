# SPDX-License-Identifier: MIT
"""End-to-end paid-work preflight with issue-thread competition pressure.

``bounty_audit`` establishes canonical issue/PR state and
``bounty_qualification`` decides whether work is safe to dispatch. This module
fills the missing ``attempt_count`` input from canonical GitHub issue comments,
binds formal GitHub assignment state to authenticated operator identity,
authority-binds maintainer contribution terms for credential safety, and
revalidates the canonical issue/comment generation immediately before an
ACTIONABLE dispatch decision is returned.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
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
_AUTHENTICATED_USER_URL = "https://api.github.com/user"
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


class _CapturedIssueResponse:
    """Minimal requests-compatible response for one frozen canonical issue."""

    def __init__(self, payload: dict[str, Any]):
        self._payload = deepcopy(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return deepcopy(self._payload)


class _CapturedIssueSession:
    """Replay one captured issue generation while forwarding all other reads."""

    def __init__(
        self,
        session: Any,
        issue_url: str,
        issue_snapshot: dict[str, Any],
    ):
        self._session = session
        self._issue_url = issue_url
        self._issue_snapshot = deepcopy(issue_snapshot)

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        timeout: int = 15,
    ) -> Any:
        if url == self._issue_url:
            return _CapturedIssueResponse(self._issue_snapshot)
        return self._session.get(
            url,
            headers=headers,
            params=params,
            timeout=timeout,
        )


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


def _authenticated_operator_login(
    session: Any,
    *,
    headers: dict[str, str],
    token: str | None,
    asserted_login: str | None,
) -> str | None:
    """Return the same-token GitHub principal; an assertion never authorizes alone."""
    asserted = _normalized_operator_login(asserted_login)
    if not token:
        if asserted is not None:
            raise BountyPreflightError(
                "operator_login requires an authenticated GitHub token"
            )
        return None

    payload = _object_payload(
        _get_json(session, _AUTHENTICATED_USER_URL, headers=headers),
        "authenticated user",
    )
    login = payload.get("login")
    if not isinstance(login, str) or not login.strip():
        raise BountyPreflightError(
            "GitHub authenticated user response did not contain a non-empty login"
        )
    authenticated = login.strip().casefold()
    if asserted is not None and asserted != authenticated:
        raise BountyPreflightError(
            "operator_login did not match authenticated GitHub identity"
        )
    return authenticated


def _assignee_state(
    issue: dict[str, Any], operator_login: str | None
) -> dict[str, Any]:
    """Reduce captured GitHub assignees to privacy-safe occupancy signals."""
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


def _label_names(issue: dict[str, Any]) -> tuple[str, ...]:
    raw_labels = issue.get("labels")
    if raw_labels is None:
        raw_labels = []
    if not isinstance(raw_labels, list):
        raise BountyPreflightError("GitHub issue labels response was not a list")
    names: list[str] = []
    for item in raw_labels:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name")
        else:
            name = None
        if not isinstance(name, str):
            raise BountyPreflightError("GitHub issue label did not contain a string name")
        names.append(name)
    return tuple(sorted(names))


def _assignee_logins(issue: dict[str, Any]) -> tuple[str, ...]:
    raw_assignees = issue.get("assignees")
    if raw_assignees is None:
        raw_assignees = []
    if not isinstance(raw_assignees, list):
        raise BountyPreflightError("GitHub issue assignees response was not a list")
    logins: list[str] = []
    for item in raw_assignees:
        if not isinstance(item, dict):
            raise BountyPreflightError("GitHub issue assignees contained a malformed item")
        login = item.get("login")
        if not isinstance(login, str) or not login.strip():
            raise BountyPreflightError(
                "GitHub issue assignee did not contain a non-empty login"
            )
        logins.append(login.strip().casefold())
    return tuple(sorted(set(logins)))


def _issue_generation_marker(issue: dict[str, Any]) -> tuple[Any, ...]:
    """Return authority-relevant issue state without retaining unrelated metadata."""
    state = issue.get("state")
    title = issue.get("title")
    body = issue.get("body")
    updated_at = issue.get("updated_at")
    comments = issue.get("comments")
    if not isinstance(state, str) or not state:
        raise BountyPreflightError("GitHub issue state generation metadata was malformed")
    if title is None:
        title = ""
    if body is None:
        body = ""
    if not isinstance(title, str) or not isinstance(body, str):
        raise BountyPreflightError("GitHub issue text generation metadata was malformed")
    if not isinstance(updated_at, str) or not updated_at:
        raise BountyPreflightError("GitHub issue updated_at generation metadata was malformed")
    if isinstance(comments, bool) or not isinstance(comments, int) or comments < 0:
        raise BountyPreflightError("GitHub issue comment-count generation metadata was malformed")
    return (
        state.casefold(),
        title,
        body,
        _label_names(issue),
        _assignee_logins(issue),
        comments,
        updated_at,
    )


def _comment_generation_entry(comment: dict[str, Any]) -> tuple[int, str, str] | None:
    """Bind every field that can change claim/maintainer classification, privately."""
    comment_id = comment.get("id")
    updated_at = comment.get("updated_at")
    body = comment.get("body")
    association = comment.get("author_association")
    user = comment.get("user")
    if body is None:
        body = ""
    if association is not None and not isinstance(association, str):
        return None
    if user is not None and not isinstance(user, dict):
        return None
    if (
        isinstance(comment_id, bool)
        or not isinstance(comment_id, int)
        or comment_id <= 0
        or not isinstance(updated_at, str)
        or not updated_at
        or not isinstance(body, str)
    ):
        return None

    login: str | None = None
    user_type: str | None = None
    if isinstance(user, dict):
        raw_login = user.get("login")
        raw_type = user.get("type")
        if raw_login is not None and not isinstance(raw_login, str):
            return None
        if raw_type is not None and not isinstance(raw_type, str):
            return None
        login = raw_login
        user_type = raw_type

    projection = {
        "author_association": association,
        "body": body,
        "user_login": login,
        "user_type": user_type,
    }
    semantic_digest = hashlib.sha256(
        json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return comment_id, updated_at, semantic_digest


def _collect_comment_generation(
    repo: str,
    number: int,
    token: str | None,
    *,
    session: Any,
    max_pages: int,
) -> tuple[tuple[tuple[int, str, str], ...], bool]:
    """Read a privacy-safe content/authority-bound issue-comment generation marker."""
    headers = _headers(token or GITHUB_TOKEN)
    comments_url = f"https://api.github.com/repos/{repo}/issues/{number}/comments"
    entries: list[tuple[int, str, str]] = []
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
            marker = _comment_generation_entry(comment)
            if marker is None:
                raise BountyPreflightError(
                    f"GitHub issue comment generation metadata was malformed for {repo}#{number}"
                )
            entries.append(marker)
        if len(payload) < 100:
            return tuple(entries), False
    return tuple(entries), True


def _canonical_generation_stable(
    repo: str,
    number: int,
    token: str | None,
    *,
    session: Any,
    max_pages: int,
    issue_snapshot: dict[str, Any],
    comment_generation: tuple[tuple[int, str, str], ...] | None,
) -> bool:
    """Revalidate the exact issue/comment generation before dispatch authority."""
    if comment_generation is None:
        raise BountyPreflightError(
            "GitHub issue comment generation metadata was incomplete during preflight"
        )
    headers = _headers(token or GITHUB_TOKEN)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    initial_issue = _issue_generation_marker(issue_snapshot)

    before = _object_payload(
        _get_json(session, issue_url, headers=headers),
        f"issue {repo}#{number}",
    )
    if "pull_request" in before:
        raise BountyPreflightError(f"{repo}#{number} became a pull request")
    if _issue_generation_marker(before) != initial_issue:
        return False

    current_comments, truncated = _collect_comment_generation(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
    )
    if truncated or current_comments != comment_generation:
        return False

    after = _object_payload(
        _get_json(session, issue_url, headers=headers),
        f"issue {repo}#{number}",
    )
    if "pull_request" in after:
        raise BountyPreflightError(f"{repo}#{number} became a pull request")
    return _issue_generation_marker(after) == initial_issue


def _apply_generation_gate(
    qualification: dict[str, Any], generation_stable: bool
) -> dict[str, Any]:
    """Hold an otherwise actionable dispatch when canonical generation moved."""
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
    signals["canonical_generation_stable"] = generation_stable
    result["signals"] = signals
    if generation_stable:
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
    if "CANONICAL_GENERATION_CHANGED" not in reason_codes:
        reason_codes.append("CANONICAL_GENERATION_CHANGED")
        reasons.append(
            {
                "code": "CANONICAL_GENERATION_CHANGED",
                "severity": "HOLD",
                "message": (
                    "Canonical GitHub issue/comment state changed while preflight "
                    "was running; rerun before dispatching paid work."
                ),
            }
        )
    result["reasons"] = reasons
    result["reason_codes"] = reason_codes
    result["dispatch"] = False
    if result.get("disposition") != "REJECT":
        result["disposition"] = "HOLD"
    return result


def _apply_assignee_gate(
    qualification: dict[str, Any], assignee_state: dict[str, Any]
) -> dict[str, Any]:
    """Hold dispatch when the captured issue generation is assigned elsewhere."""
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
    result["dispatch"] = False
    if result.get("disposition") != "REJECT":
        result["disposition"] = "HOLD"
    return result


def _collect_issue_context_with_snapshot(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    operator_login: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect safe context plus the exact canonical issue generation used."""

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
    issue = deepcopy(
        _object_payload(
            _get_json(session, issue_url, headers=headers),
            f"issue {repo}#{number}",
        )
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

    anonymous_assignee_state = _assignee_state(issue, None)
    if anonymous_assignee_state["formal_assignee_count"] > 0:
        authenticated_operator = _authenticated_operator_login(
            session,
            headers=headers,
            token=token,
            asserted_login=operator_login,
        )
        assignee_state = _assignee_state(issue, authenticated_operator)
    else:
        assignee_state = anonymous_assignee_state

    claimant_logins: set[str] = set()
    attempt_signal_count = 0
    comments_truncated = False
    comment_generation: list[tuple[int, str, str]] = []
    comment_generation_complete = True
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
            marker = _comment_generation_entry(comment)
            if marker is None:
                comment_generation_complete = False
            else:
                comment_generation.append(marker)
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

    return (
        {
            "title": issue.get("title") if issue.get("title") is not None else "",
            "body": issue_body,
            "labels": issue.get("labels") if issue.get("labels") is not None else [],
            "attempt_count": len(claimant_logins),
            "attempt_signal_count": attempt_signal_count,
            "comments_truncated": comments_truncated,
            "credential_gate_signal_types": sorted(credential_signals),
            "_comment_generation": (
                tuple(comment_generation) if comment_generation_complete else None
            ),
            **assignee_state,
        },
        issue,
    )


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
    are reduced immediately to generic credential-safety signals. Formal
    assignment is reduced to counts and membership against the authenticated
    same-token GitHub principal; a caller-supplied login is only an assertion.
    Raw identities never leave the collection boundary.
    """
    context, _issue_snapshot = _collect_issue_context_with_snapshot(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
        operator_login=operator_login,
    )
    return {key: value for key, value in context.items() if not key.startswith("_")}


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
    """Return an operator-safe paid-work preflight result for one issue.

    Issue-derived qualification metadata, formal assignment, credential authority,
    and canonical issue state are bound to one captured GitHub issue generation.
    Formal assignment can be treated as operator-owned only when the same token's
    authenticated GitHub principal matches; ``operator_login`` is never authority
    by itself. PR competition and maintainer-comment reads remain live. Before an
    actionable result is returned, the issue authority fields and privacy-safe
    content/authority-bound comment generation are re-read and must still match.
    """
    context, issue_snapshot = _collect_issue_context_with_snapshot(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
        operator_login=operator_login,
    )
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    audit_session = _CapturedIssueSession(session, issue_url, issue_snapshot)
    raw_audit = audit_bounty(
        repo,
        number,
        token,
        session=audit_session,
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

    if qualification.get("dispatch") is True:
        generation_stable = _canonical_generation_stable(
            repo,
            number,
            token,
            session=session,
            max_pages=max_pages,
            issue_snapshot=issue_snapshot,
            comment_generation=context["_comment_generation"],
        )
        qualification = _apply_generation_gate(qualification, generation_stable)

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
            "Optional assertion of the authenticated GitHub login for formal "
            "assignment; it never authorizes dispatch by itself"
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
