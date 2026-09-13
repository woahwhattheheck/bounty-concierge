# SPDX-License-Identifier: MIT
"""Fail-closed review hardening for bounty contract evidence.

This module closes evidence-integrity gaps found during independent review of the
v1 bounty-contract carrier without changing the persisted receipt schema.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import requests

from concierge import bounty_contract_live as live
from concierge import bounty_contract_receipt as legacy_receipt
from concierge.bounty_contract_common import (
    _AUTHORITY,
    BountyContractEvidenceError,
    BountyContractInputError,
    _bounded_text,
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
from concierge.config import GITHUB_TOKEN


def _comment_generation(comments: list[dict[str, Any]]) -> tuple[tuple[int, str], ...]:
    """Bind a read generation to every authority-driving comment field.

    GitHub issue-comment timestamps have only second-level resolution.  An ID plus
    ``updated_at`` therefore cannot prove that two reads observed the same semantic
    comment.  The in-memory generation marker binds identity, author/association,
    timestamps and body content for *every* comment.  Raw text/login values are not
    persisted in a contract receipt.
    """
    generation: list[tuple[int, str]] = []
    seen: set[int] = set()
    for comment in comments:
        if type(comment) is not dict:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comment was malformed"
            )
        comment_id = _positive_int(comment.get("id"), "comment id")
        if comment_id in seen:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comments contained duplicate IDs"
            )
        seen.add(comment_id)

        user = comment.get("user")
        if type(user) is not dict:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comment author was malformed"
            )
        login = _required_string(user.get("login"), "comment author login").casefold()
        user_type = _required_string(user.get("type"), "comment author type")
        node_id = _required_string(comment.get("node_id"), "comment node_id")
        association = _required_string(
            comment.get("author_association"), "comment author_association"
        ).upper()
        created_at_raw = _required_string(comment.get("created_at"), "comment created_at")
        updated_at_raw = _required_string(comment.get("updated_at"), "comment updated_at")
        body = comment.get("body")
        if body is None:
            body = ""
        body = _bounded_text(body, "comment body")
        try:
            created_at = _timestamp(created_at_raw, "comment created_at")
            updated_at = _timestamp(updated_at_raw, "comment updated_at")
        except BountyContractInputError as exc:
            raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc

        generation.append(
            (
                comment_id,
                _hash_json(
                    {
                        "comment_id": comment_id,
                        "node_id": node_id,
                        "author_login_sha256": _hash_text(login),
                        "author_type": user_type,
                        "author_association": association,
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "body_sha256": _hash_text(body),
                    }
                ),
            )
        )
    return tuple(sorted(generation))


def _read_stable_generation(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Read one issue generation with content-bound issue and comment fencing."""
    repo = _validate_repo(repo)
    number = _validate_issue_number(number)
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise BountyContractInputError("max_pages must be a positive integer")
    headers = _headers(token or GITHUB_TOKEN)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"

    issue_before = live._get_json(session, issue_url, headers=headers)
    if type(issue_before) is not dict:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue response was not an object"
        )
    comments_before = live._fetch_comments(
        session, repo, number, headers=headers, max_pages=max_pages
    )
    issue_middle = live._get_json(session, issue_url, headers=headers)
    if type(issue_middle) is not dict:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue response was not an object"
        )
    comments_after = live._fetch_comments(
        session, repo, number, headers=headers, max_pages=max_pages
    )
    issue_after = live._get_json(session, issue_url, headers=headers)
    if type(issue_after) is not dict:
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID", "GitHub issue response was not an object"
        )

    issue_markers = {
        live._issue_generation_marker(issue_before, repo, number),
        live._issue_generation_marker(issue_middle, repo, number),
        live._issue_generation_marker(issue_after, repo, number),
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

    source = live._source_from_issue(issue_after, repo, number)
    issue_terms = live._issue_terms(issue_after)
    contract = {
        **issue_terms,
        "maintainer_terms": live._maintainer_terms(comments_after),
    }
    source_updated_at_raw = _required_string(
        issue_after.get("updated_at"), "issue updated_at"
    )
    try:
        source_updated_at = _timestamp(source_updated_at_raw, "issue updated_at")
    except BountyContractInputError as exc:
        raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc
    return {
        "source": source,
        "source_updated_at": source_updated_at,
        "contract": contract,
        "contract_sha256": _hash_json(contract),
    }


def _validate_authority(value: Any) -> dict[str, Any]:
    """Require exact authority keys, value types and values.

    Python equality intentionally treats ``False == 0 == 0.0``.  Authority is a
    typed security boundary, so equality alone is insufficient.
    """
    if type(value) is not dict or set(value) != set(_AUTHORITY):
        raise BountyContractInputError("receipt authority boundary is invalid")
    for key, expected in _AUTHORITY.items():
        actual = value[key]
        if type(actual) is not type(expected) or actual != expected:
            raise BountyContractInputError("receipt authority boundary is invalid")
    return deepcopy(_AUTHORITY)


def validate_receipt(value: Any) -> dict[str, Any]:
    """Strict v1 receipt validation with typed authority semantics."""
    if type(value) is dict and "authority" in value:
        _validate_authority(value["authority"])
    return legacy_receipt.validate_receipt(value)
