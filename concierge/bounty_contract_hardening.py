# SPDX-License-Identifier: MIT
"""Review hardening for bounty contract evidence and stored receipts.

This module closes three evidence-integrity boundaries around the original
contract implementation:

* the private double-read marker binds the exact authority-driving content of
  every issue comment, not only GitHub's second-granularity update timestamp;
* stored receipt authority metadata is validated with exact keys and exact
  Python value types before legacy normalization; and
* the public module uses create-exclusive output publication so a claim-time
  receipt cannot be overwritten by a later command.
"""

from __future__ import annotations

from typing import Any

import requests

from concierge.config import GITHUB_TOKEN
from concierge.bounty_contract_common import (
    _AUTHORITY,
    BountyContractEvidenceError,
    BountyContractInputError,
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
from concierge.bounty_contract_live import (
    _fetch_comments,
    _issue_generation_marker,
    _issue_terms,
    _maintainer_terms,
    _source_from_issue,
)
from concierge.bounty_contract_receipt import validate_receipt as _validate_receipt_legacy


def _normalized_timestamp(value: Any, field: str) -> str:
    text = _required_string(value, field)
    try:
        return _timestamp(text, field)
    except BountyContractInputError as exc:
        raise BountyContractEvidenceError("LIVE_EVIDENCE_INVALID", str(exc)) from exc


def _optional_user_identity(user: dict[str, Any]) -> dict[str, Any]:
    """Bind all stable user identity fields returned by the provider.

    GitHub's canonical issue-comment payload includes numeric and node IDs. The
    login/type pair remains required so restricted test doubles and compatible
    provider projections are still content-bound. Numeric and node IDs must be
    supplied together when either is present; production GitHub responses bind
    both.
    """

    login = _required_string(user.get("login"), "comment author login")
    user_type = _required_string(user.get("type"), "comment author type")
    raw_id = user.get("id")
    raw_node = user.get("node_id")
    if (raw_id is None) != (raw_node is None):
        raise BountyContractEvidenceError(
            "LIVE_EVIDENCE_INVALID",
            "GitHub comment author identity was incomplete",
        )
    result: dict[str, Any] = {
        "login_sha256": _hash_text(login.casefold()),
        "type": user_type,
    }
    if raw_id is not None:
        result["id"] = _positive_int(raw_id, "comment author id")
        result["node_id"] = _required_string(raw_node, "comment author node_id")
    return result


def _comment_generation_digest(comments: list[dict[str, Any]]) -> str:
    """Return a canonical digest of every authority-driving comment field."""

    normalized: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_nodes: set[str] = set()
    for comment in comments:
        if not isinstance(comment, dict):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub issue comment was malformed"
            )
        comment_id = _positive_int(comment.get("id"), "comment id")
        node_id = _required_string(comment.get("node_id"), "comment node_id")
        if comment_id in seen_ids or node_id in seen_nodes:
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID",
                "GitHub issue comments contained duplicate identities",
            )
        seen_ids.add(comment_id)
        seen_nodes.add(node_id)

        association = _required_string(
            comment.get("author_association"), "comment author_association"
        ).upper()
        user = comment.get("user")
        if not isinstance(user, dict):
            raise BountyContractEvidenceError(
                "LIVE_EVIDENCE_INVALID", "GitHub comment author was malformed"
            )
        body = comment.get("body")
        if body is None:
            body = ""
        body = _bounded_text(body, "comment body")
        normalized.append(
            {
                "id": comment_id,
                "node_id": node_id,
                "author": _optional_user_identity(user),
                "author_association": association,
                "created_at": _normalized_timestamp(
                    comment.get("created_at"), "comment created_at"
                ),
                "updated_at": _normalized_timestamp(
                    comment.get("updated_at"), "comment updated_at"
                ),
                "body_sha256": _hash_text(body),
            }
        )
    normalized.sort(key=lambda item: (item["id"], item["node_id"]))
    return _hash_json(normalized)


def _read_stable_generation(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Read one issue generation with content-bound comment stability."""

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
    before_marker = _comment_generation_digest(comments_before)
    after_marker = _comment_generation_digest(comments_after)
    if len(issue_markers) != 1 or before_marker != after_marker:
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
    contract = {
        **_issue_terms(issue_after),
        "maintainer_terms": _maintainer_terms(comments_after),
    }
    source_updated_at = _normalized_timestamp(
        issue_after.get("updated_at"), "issue updated_at"
    )
    return {
        "source": source,
        "source_updated_at": source_updated_at,
        "contract": contract,
        "contract_sha256": _hash_json(contract),
    }


def _validate_authority_exact(value: Any) -> None:
    if type(value) is not dict or set(value) != set(_AUTHORITY):
        raise BountyContractInputError("receipt authority boundary is invalid")
    for key, expected in _AUTHORITY.items():
        supplied = value[key]
        if type(supplied) is not type(expected) or supplied != expected:
            raise BountyContractInputError("receipt authority boundary is invalid")


def validate_receipt(value: Any) -> dict[str, Any]:
    """Reject authority type confusion before legacy receipt normalization."""

    if type(value) is not dict or "authority" not in value:
        raise BountyContractInputError("receipt shape is invalid")
    _validate_authority_exact(value["authority"])
    return _validate_receipt_legacy(value)
