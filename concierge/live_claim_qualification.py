# SPDX-License-Identifier: MIT
"""Live canonical qualification guard for ``concierge claim``.

The static paid-work rules live in :mod:`concierge.bounty_qualification`.  This
module binds those rules to the exact GitHub repository/issue requested by the
installed CLI, scans the issue's live labels/body/comments, and combines them
with the canonical linked-PR audit before claim instructions are emitted.
"""

from __future__ import annotations

from typing import Any

import requests

from concierge.bounty_audit import BountyAuditError, audit_bounty
from concierge.bounty_qualification import QualificationInputError, qualify_dispatch
from concierge.config import GITHUB_TOKEN


class LiveQualificationError(RuntimeError):
    """Raised when canonical GitHub qualification cannot be completed."""


class ClaimQualificationBlocked(RuntimeError):
    """Raised when a live claim preflight reaches HOLD or REJECT."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        self.exit_code = 3 if result.get("disposition") == "REJECT" else 2
        super().__init__(format_summary(result))


def format_summary(result: dict[str, Any]) -> str:
    """Return a compact source-text-free claim qualification summary."""
    codes = ",".join(result.get("reason_codes", [])) or "none"
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} reasons={codes}"
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
        raise LiveQualificationError(
            "canonical GitHub qualification request failed"
        ) from exc
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise LiveQualificationError(
            "canonical GitHub qualification returned invalid JSON"
        ) from exc


def _live_issue_context(
    repo: str,
    number: int,
    *,
    token: str | None,
    session: Any,
    max_pages: int,
) -> tuple[str, list[str], list[str], bool]:
    """Return body, label names, comment bodies, and truncation state.

    Raw source text is retained only in-memory long enough for qualification;
    it is never copied into the returned decision object.
    """
    headers = _headers(token)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    issue = _get_json(session, issue_url, headers=headers)
    if not isinstance(issue, dict):
        raise LiveQualificationError(
            "canonical GitHub issue response was not an object"
        )
    if "pull_request" in issue:
        raise LiveQualificationError("claim target is a pull request, not an issue")

    body = issue.get("body") or ""
    if not isinstance(body, str):
        raise LiveQualificationError("canonical GitHub issue body was malformed")

    raw_labels = issue.get("labels", [])
    if not isinstance(raw_labels, list):
        raise LiveQualificationError("canonical GitHub issue labels were malformed")
    labels: list[str] = []
    for item in raw_labels:
        if isinstance(item, str):
            labels.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            labels.append(item["name"])
        else:
            raise LiveQualificationError(
                "canonical GitHub issue labels contained a malformed item"
            )

    comment_count = issue.get("comments", 0)
    if (
        isinstance(comment_count, bool)
        or not isinstance(comment_count, int)
        or comment_count < 0
    ):
        raise LiveQualificationError(
            "canonical GitHub issue comment count was malformed"
        )

    comments: list[str] = []
    truncated = False
    if comment_count:
        for page in range(1, max_pages + 1):
            payload = _get_json(
                session,
                f"{issue_url}/comments",
                headers=headers,
                params={"per_page": 100, "page": page},
            )
            if not isinstance(payload, list):
                raise LiveQualificationError(
                    "canonical GitHub issue comments response was not a list"
                )
            for item in payload:
                if not isinstance(item, dict) or not isinstance(item.get("body"), str):
                    raise LiveQualificationError(
                        "canonical GitHub issue comments contained a malformed item"
                    )
                comments.append(item["body"])
            if len(payload) < 100:
                break
        else:
            truncated = True
    return body, labels, comments, truncated


def _append_hold(result: dict[str, Any], code: str, message: str) -> None:
    """Add a safe HOLD signal without weakening an existing REJECT."""
    if code not in result.get("reason_codes", []):
        result.setdefault("reason_codes", []).append(code)
        result.setdefault("reasons", []).append(
            {"code": code, "severity": "HOLD", "message": message}
        )
    result.setdefault("signals", {})["qualification_context_truncated"] = True
    if result.get("disposition") != "REJECT":
        result["disposition"] = "HOLD"
        result["dispatch"] = False


def qualify_live_bounty(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Qualify one exact live GitHub bounty issue for claim dispatch."""
    if "/" not in repo or not all(repo.split("/", 1)):
        raise QualificationInputError("repo must be in owner/name form")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise QualificationInputError("number must be a positive issue number")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise QualificationInputError("max_pages must be positive")

    token = token or GITHUB_TOKEN
    body, labels, comments, context_truncated = _live_issue_context(
        repo,
        number,
        token=token,
        session=session,
        max_pages=max_pages,
    )
    try:
        audit = audit_bounty(
            repo,
            number,
            token,
            session=session,
            max_pages=max_pages,
        )
    except (BountyAuditError, ValueError) as exc:
        raise LiveQualificationError(
            "canonical GitHub audit could not be completed"
        ) from exc

    # The same exact repo/issue arguments feed both the live content read and
    # canonical audit, so there is no user-supplied snapshot identity to swap.
    result = qualify_dispatch(
        {
            "body": body,
            "labels": labels,
            "comments": comments,
            "canonical_audit": audit,
        },
        saturation_threshold=saturation_threshold,
    )
    result.setdefault("signals", {})["target_repo"] = repo
    result["signals"]["target_issue"] = number
    result["signals"]["qualification_context_truncated"] = context_truncated
    if context_truncated:
        _append_hold(
            result,
            "QUALIFICATION_CONTEXT_INCOMPLETE",
            "Issue comment qualification context was truncated.",
        )
    return result


def _option_value(args: list[str], name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == name:
            return args[index + 1] if index + 1 < len(args) else None
        prefix = f"{name}="
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def preflight_claim_argv(
    argv: list[str],
    *,
    token: str | None = None,
    session: Any = requests,
) -> dict[str, Any] | None:
    """Fail closed before installed CLI claim instructions reach the operator.

    Syntax/help failures remain the primary parser's responsibility. ``--dry-run``
    remains a non-network preview and therefore deliberately skips the live gate.
    """
    try:
        command_index = argv.index("claim")
    except ValueError:
        return None

    tail = argv[command_index + 1 :]
    if "--dry-run" in argv or "-h" in tail or "--help" in tail:
        return None

    issue_text = _option_value(tail, "--issue")
    if issue_text is None:
        return None
    try:
        issue = int(issue_text)
    except ValueError:
        return None
    if issue <= 0:
        return None

    repo = _option_value(tail, "--repo") or "Scottcjn/rustchain-bounties"
    if "/" not in repo:
        repo = f"Scottcjn/{repo}"

    result = qualify_live_bounty(repo, issue, token, session=session)
    if not result["dispatch"]:
        raise ClaimQualificationBlocked(result)
    return result
