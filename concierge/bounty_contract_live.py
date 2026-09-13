# SPDX-License-Identifier: MIT
"""Stable GitHub evidence capture for bounty contract receipts."""

from __future__ import annotations

from typing import Any
import re

import requests

from concierge.config import GITHUB_TOKEN
from concierge.bounty_contract_common import (
    BountyContractEvidenceError,
    BountyContractInputError,
    _MAX_COLLECTION,
    _MAINTAINER_ASSOCIATIONS,
    _bounded_text,
    _get_json,
    _hash_json,
    _hash_text,
    _headers,
    _nonnegative_int,
    _positive_int,
    _required_string,
    _timestamp,
    _validate_issue_number,
    _validate_repo,
)

def _source_from_issue(issue: Any, repo: str, number: int) -> dict[str, Any]:
    if not isinstance(issue, dict):
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue response was not an object"
        )
    if "pull_request" in issue:
        raise BountyContractEvidenceError(
            "SOURCE_IDENTITY_CHANGED", f"{repo}#{number} resolved to a pull request"
        )
    canonical_url = f"https://github.com/{repo}/issues/{number}"
    api_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    repository_url = f"https://api.github.com/repos/{repo}"
    if (
        issue.get("html_url") != canonical_url
        or issue.get("url") != api_url
        or issue.get("repository_url") != repository_url
        or issue.get("number") != number
    ):
        raise BountyContractEvidenceError(
            "SOURCE_IDENTITY_CHANGED", f"GitHub issue identity mismatch for {repo}#{number}"
        )
    created_at = _required_string(issue.get("created_at"), "issue created_at")
    try:
        created_at = _timestamp(created_at, "issue created_at")
    except BountyContractInputError as exc:
        raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc
    return {
        "repo": repo,
        "issue": number,
        "canonical_url": canonical_url,
        "api_url": api_url,
        "issue_id": _positive_int(issue.get("id"), "issue id"),
        "node_id": _required_string(issue.get("node_id"), "issue node_id"),
        "created_at": created_at,
    }

def _labels(issue: dict[str, Any]) -> list[dict[str, Any]]:
    raw = issue.get("labels")
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > _MAX_COLLECTION:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue labels were not a bounded list"
        )
    labels: list[dict[str, Any]] = []
    ids: set[int] = set()
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue label was malformed"
            )
        label_id = _positive_int(item.get("id"), "label id")
        name = _required_string(item.get("name"), "label name")
        name_key = name.casefold()
        if label_id in ids or name_key in names:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue labels contained duplicates"
            )
        ids.add(label_id)
        names.add(name_key)
        color = _required_string(item.get("color"), "label color").lower()
        if not re.fullmatch(r"[0-9a-f]{6}", color):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue label color was invalid"
            )
        default = item.get("default")
        if type(default) is not bool:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue label default flag was malformed"
            )
        description = item.get("description")
        if description is None:
            description = ""
        description = _bounded_text(description, "label description")
        labels.append(
            {
                "id": label_id,
                "node_id": _required_string(item.get("node_id"), "label node_id"),
                "name": name,
                "color": color,
                "default": default,
                "description_sha256": _hash_text(description),
            }
        )
    labels.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    return labels

def _assignees(issue: dict[str, Any]) -> list[dict[str, Any]]:
    raw = issue.get("assignees")
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > _MAX_COLLECTION:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue assignees were not a bounded list"
        )
    assignees: list[dict[str, Any]] = []
    ids: set[int] = set()
    logins: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue assignee was malformed"
            )
        user_id = _positive_int(item.get("id"), "assignee id")
        login = _required_string(item.get("login"), "assignee login").casefold()
        if user_id in ids or login in logins:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue assignees contained duplicates"
            )
        ids.add(user_id)
        logins.add(login)
        assignees.append(
            {
                "id": user_id,
                "node_id": _required_string(item.get("node_id"), "assignee node_id"),
                "login": login,
                "type": _required_string(item.get("type"), "assignee type"),
            }
        )
    assignees.sort(key=lambda item: (item["login"], item["id"]))
    return assignees

def _milestone(issue: dict[str, Any]) -> dict[str, Any] | None:
    raw = issue.get("milestone")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue milestone was malformed"
        )
    state = raw.get("state")
    if state not in {"open", "closed"}:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub milestone state was invalid"
        )
    due_on = raw.get("due_on")
    if due_on is not None:
        due_raw = _required_string(due_on, "milestone due_on")
        try:
            due_on = _timestamp(due_raw, "milestone due_on")
        except BountyContractInputError as exc:
            raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc
    description = raw.get("description")
    if description is None:
        description = ""
    description = _bounded_text(description, "milestone description")
    return {
        "number": _positive_int(raw.get("number"), "milestone number"),
        "state": state,
        "due_on": due_on,
        "title_sha256": _hash_text(_bounded_text(raw.get("title"), "milestone title")),
        "description_sha256": _hash_text(description),
    }

def _issue_author(issue: dict[str, Any]) -> dict[str, str]:
    user = issue.get("user")
    if not isinstance(user, dict):
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue author was malformed"
        )
    login = _required_string(user.get("login"), "issue author login")
    user_type = _required_string(user.get("type"), "issue author type")
    association = _required_string(
        issue.get("author_association"), "issue author association"
    ).upper()
    return {
        "login_sha256": _hash_text(login.casefold()),
        "type": user_type,
        "association": association,
    }

def _issue_terms(issue: dict[str, Any]) -> dict[str, Any]:
    state = issue.get("state")
    if state not in {"open", "closed"}:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue state was invalid"
        )
    state_reason = issue.get("state_reason")
    if state_reason is not None and type(state_reason) is not str:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue state_reason was malformed"
        )
    locked = issue.get("locked")
    if type(locked) is not bool:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue locked state was malformed"
        )
    title = _bounded_text(issue.get("title"), "issue title")
    body = issue.get("body")
    if body is None:
        body = ""
    body = _bounded_text(body, "issue body")
    return {
        "author": _issue_author(issue),
        "state": state,
        "state_reason": state_reason,
        "locked": locked,
        "title_sha256": _hash_text(title),
        "body_sha256": _hash_text(body),
        "labels": _labels(issue),
        "assignees": _assignees(issue),
        "milestone": _milestone(issue),
    }

def _comment_generation(comments: list[dict[str, Any]]) -> tuple[tuple[int, str], ...]:
    generation: list[tuple[int, str]] = []
    seen: set[int] = set()
    for comment in comments:
        if not isinstance(comment, dict):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comment was malformed"
            )
        comment_id = _positive_int(comment.get("id"), "comment id")
        if comment_id in seen:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comments contained duplicate IDs"
            )
        seen.add(comment_id)
        updated_at = _required_string(comment.get("updated_at"), "comment updated_at")
        try:
            updated_at = _timestamp(updated_at, "comment updated_at")
        except BountyContractInputError as exc:
            raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc
        generation.append((comment_id, updated_at))
    return tuple(sorted(generation))

def _maintainer_terms(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for comment in comments:
        association = comment.get("author_association")
        if not isinstance(association, str):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub comment author_association was malformed"
            )
        association = association.upper()
        if association not in _MAINTAINER_ASSOCIATIONS:
            continue
        user = comment.get("user")
        if not isinstance(user, dict):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub maintainer comment author was malformed"
            )
        login = _required_string(user.get("login"), "maintainer comment login")
        body = comment.get("body")
        if body is None:
            body = ""
        body = _bounded_text(body, "maintainer comment body")
        created_at = _required_string(comment.get("created_at"), "comment created_at")
        updated_at = _required_string(comment.get("updated_at"), "comment updated_at")
        try:
            created_at = _timestamp(created_at, "comment created_at")
            updated_at = _timestamp(updated_at, "comment updated_at")
        except BountyContractInputError as exc:
            raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc
        terms.append(
            {
                "comment_id": _positive_int(comment.get("id"), "comment id"),
                "node_id": _required_string(comment.get("node_id"), "comment node_id"),
                "author_login_sha256": _hash_text(login.casefold()),
                "author_association": association,
                "created_at": created_at,
                "updated_at": updated_at,
                "body_sha256": _hash_text(body),
            }
        )
    terms.sort(key=lambda item: item["comment_id"])
    return terms

def _issue_generation_marker(issue: dict[str, Any], repo: str, number: int) -> str:
    source = _source_from_issue(issue, repo, number)
    terms = _issue_terms(issue)
    updated_at = _required_string(issue.get("updated_at"), "issue updated_at")
    try:
        updated_at = _timestamp(updated_at, "issue updated_at")
    except BountyContractInputError as exc:
        raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc
    comments = _nonnegative_int(issue.get("comments"), "issue comments")
    return _hash_json(
        {
            "source": source,
            "terms": terms,
            "updated_at": updated_at,
            "comments": comments,
        }
    )

def _fetch_comments(
    session: Any,
    repo: str,
    number: int,
    *,
    headers: dict[str, str],
    max_pages: int,
) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repo}/issues/{number}/comments"
    comments: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = _get_json(
            session,
            url,
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(payload, list):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comments response was not a list"
            )
        if len(payload) > 100:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comments page exceeded requested size"
            )
        comments.extend(payload)
        if len(comments) > _MAX_COLLECTION:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INCOMPLETE", "GitHub issue comment evidence exceeded limit"
            )
        if len(payload) < 100:
            _comment_generation(comments)
            return comments
    raise BountyContractEvidenceError(
        "LIVE_EVIDENCE_INCOMPLETE",
        f"GitHub issue comments exceeded max_pages={max_pages}",
    )

def _read_stable_generation(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    repo = _validate_repo(repo)
    number = _validate_issue_number(number)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise BountyContractInputError("max_pages must be a positive integer")
    headers = _headers(token or GITHUB_TOKEN)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"

    issue_before = _get_json(session, issue_url, headers=headers)
    if not isinstance(issue_before, dict):
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue response was not an object"
        )
    comments_before = _fetch_comments(
        session, repo, number, headers=headers, max_pages=max_pages
    )
    issue_middle = _get_json(session, issue_url, headers=headers)
    if not isinstance(issue_middle, dict):
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue response was not an object"
        )
    comments_after = _fetch_comments(
        session, repo, number, headers=headers, max_pages=max_pages
    )
    issue_after = _get_json(session, issue_url, headers=headers)
    if not isinstance(issue_after, dict):
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue response was not an object"
        )

    issue_markers = {
        _issue_generation_marker(issue_before, repo, number),
        _issue_generation_marker(issue_middle, repo, number),
        _issue_generation_marker(issue_after, repo, number),
    }
    comment_before_marker = _comment_generation(comments_before)
    comment_after_marker = _comment_generation(comments_after)
    if len(issue_markers) != 1 or comment_before_marker != comment_after_marker:
        raise BountyContractEvidenceError(
            "LIVE_GENERATION_UNSTABLE",
            f"GitHub issue/comment generation changed while reading {repo}#{number}",
        )

    expected_comments = _nonnegative_int(issue_after.get("comments"), "issue comments")
    if len(comments_after) != expected_comments:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INCOMPLETE",
            f"GitHub returned {len(comments_after)} of {expected_comments} issue comments",
        )

    source = _source_from_issue(issue_after, repo, number)
    issue_terms = _issue_terms(issue_after)
    contract = {
        **issue_terms,
        "maintainer_terms": _maintainer_terms(comments_after),
    }
    source_updated_at = _required_string(issue_after.get("updated_at"), "issue updated_at")
    try:
        source_updated_at = _timestamp(source_updated_at, "issue updated_at")
    except BountyContractInputError as exc:
        raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc
    return {
        "source": source,
        "source_updated_at": source_updated_at,
        "contract": contract,
        "contract_sha256": _hash_json(contract),
    }
