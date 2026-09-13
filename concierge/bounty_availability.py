# SPDX-License-Identifier: MIT
"""Fail-closed live bounty availability authority.

GitHub issue ``state == "open"`` is necessary but not sufficient evidence that
new paid work remains available. Maintainers sometimes leave an issue open after
accepting a submission, awarding a slot, exhausting capacity, or cancelling a
bounty. This module performs a separate, read-only availability check over
canonical issue comments and refuses new dispatch on explicit maintainer
terminal-outcome signals.

The result is deliberately privacy-safe: comment bodies and user logins are
never returned. Text classification is syntactic and conservative; a terminal
signal means "human review required before new work", not that any particular
person has been paid or that revenue has been earned.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

import requests

from concierge.config import GITHUB_TOKEN


_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MENTION = r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_SUBJECT = (
    rf"(?:{_MENTION}|"
    r"\b(?:submission|entry|asset\s+pack|pack|claim|"
    r"pull\s+request|pr\s*#?\d+|work)\b)"
)
_NEGATION_RE = re.compile(
    r"(?i)\b(?:not|isn['’]?t|wasn['’]?t|no|never|without|pending|awaiting)\b"
)
_ACCEPTANCE_RE = re.compile(
    rf"(?i)(?:{_SUBJECT}).{{0,96}}\b"
    r"(?:accepted|selected\s+as\s+(?:the\s+)?winner|winner)\b|"
    r"\b(?:accepted|winner)\b.{0,96}"
    rf"(?:{_SUBJECT})"
)
_AWARD_RE = re.compile(
    rf"(?i)\b(?:awarded\s+to|award\s+(?:goes|went)\s+to)\b.{{0,96}}"
    rf"(?:{_SUBJECT})|(?:{_SUBJECT}).{{0,96}}\bawarded\b"
)
_CAP_CLOSED_RE = re.compile(
    r"(?i)\b(?:bounty|submissions?|slots?|capacity)\b.{0,64}"
    r"\b(?:full|closed|filled|exhausted|reached)\b|"
    r"\b(?:no\s+more|stop)\s+(?:new\s+)?submissions?\b"
)
_CANCELLED_RE = re.compile(
    r"(?i)\b(?:bounty|reward|task)\b.{0,48}"
    r"\b(?:cancelled|canceled|withdrawn|voided)\b|"
    r"\b(?:cancelled|canceled|withdrawn|voided)\b.{0,48}"
    r"\b(?:bounty|reward|task)\b"
)

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MAINTAINER_ACCEPTANCE_SIGNAL", _ACCEPTANCE_RE),
    ("MAINTAINER_AWARD_SIGNAL", _AWARD_RE),
    ("MAINTAINER_CAP_CLOSED_SIGNAL", _CAP_CLOSED_RE),
    ("MAINTAINER_CANCELLED_SIGNAL", _CANCELLED_RE),
)


class BountyAvailabilityError(RuntimeError):
    """Raised when canonical availability evidence cannot be read reliably."""


@dataclass(frozen=True)
class _CommentEvidence:
    marker: tuple[object, ...]
    signals: tuple[str, ...]
    comment_id: int
    association: str


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
        raise BountyAvailabilityError(
            f"GitHub request failed for {url}: {exc}"
        ) from exc
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise BountyAvailabilityError(
            f"GitHub response was not valid JSON for {url}"
        ) from exc


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BountyAvailabilityError(f"GitHub {context} response was not an object")
    return value


def _issue_marker(issue: dict[str, Any]) -> tuple[object, ...]:
    if "pull_request" in issue:
        raise BountyAvailabilityError("canonical bounty target became a pull request")
    raw_id = issue.get("id")
    number = issue.get("number")
    state = issue.get("state")
    updated_at = issue.get("updated_at")
    comments = issue.get("comments")
    title = issue.get("title")
    body = issue.get("body")
    if (
        isinstance(raw_id, bool)
        or not isinstance(raw_id, int)
        or raw_id <= 0
        or isinstance(number, bool)
        or not isinstance(number, int)
        or number <= 0
        or not isinstance(state, str)
        or not state
        or not isinstance(updated_at, str)
        or not updated_at
        or isinstance(comments, bool)
        or not isinstance(comments, int)
        or comments < 0
        or (title is not None and not isinstance(title, str))
        or (body is not None and not isinstance(body, str))
    ):
        raise BountyAvailabilityError(
            "canonical issue generation metadata was malformed"
        )
    title_text = title or ""
    body_text = body or ""
    return (
        raw_id,
        number,
        state.casefold(),
        updated_at,
        comments,
        hashlib.sha256(title_text.encode("utf-8")).hexdigest(),
        hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
    )


def _candidate_lines(text: str) -> Iterable[str]:
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith(">"):
            continue
        stripped = re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+)", "", stripped)
        yield stripped


def _terminal_signals(text: str) -> tuple[str, ...]:
    found: set[str] = set()
    for line in _candidate_lines(text):
        # Questions are not terminal assertions.
        if "?" in line:
            continue
        for code, pattern in _RULES:
            match = pattern.search(line)
            if match is None:
                continue
            context = line[max(0, match.start() - 40) : match.end()]
            if _NEGATION_RE.search(context):
                continue
            found.add(code)
    return tuple(sorted(found))


def _comment_evidence(comment: dict[str, Any]) -> _CommentEvidence:
    raw_id = comment.get("id")
    created_at = comment.get("created_at")
    updated_at = comment.get("updated_at")
    association = comment.get("author_association")
    body = comment.get("body")
    user = comment.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    user_type = user.get("type") if isinstance(user, dict) else None
    if body is None:
        body = ""
    if (
        isinstance(raw_id, bool)
        or not isinstance(raw_id, int)
        or raw_id <= 0
        or not isinstance(created_at, str)
        or not created_at
        or not isinstance(updated_at, str)
        or not updated_at
        or not isinstance(association, str)
        or not association
        or not isinstance(body, str)
        or not isinstance(login, str)
        or not login.strip()
        or not isinstance(user_type, str)
        or not user_type
    ):
        raise BountyAvailabilityError("canonical issue comment evidence was malformed")

    normalized_association = association.upper()
    signals = (
        _terminal_signals(body)
        if normalized_association in _MAINTAINER_ASSOCIATIONS
        else ()
    )
    marker = (
        raw_id,
        login.casefold(),
        user_type.casefold(),
        normalized_association,
        created_at,
        updated_at,
        hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )
    return _CommentEvidence(
        marker=marker,
        signals=signals,
        comment_id=raw_id,
        association=normalized_association,
    )


def _read_comments(
    repo: str,
    number: int,
    token: str | None,
    *,
    session: Any,
    max_pages: int,
) -> tuple[tuple[_CommentEvidence, ...], bool]:
    headers = _headers(token or GITHUB_TOKEN)
    url = f"https://api.github.com/repos/{repo}/issues/{number}/comments"
    evidence: list[_CommentEvidence] = []
    for page in range(1, max_pages + 1):
        payload = _get_json(
            session,
            url,
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(payload, list):
            raise BountyAvailabilityError(
                "GitHub issue comments response was not a list"
            )
        for item in payload:
            if not isinstance(item, dict):
                raise BountyAvailabilityError(
                    "GitHub issue comments response contained a malformed item"
                )
            evidence.append(_comment_evidence(item))
        if len(payload) < 100:
            return tuple(evidence), False
    return tuple(evidence), True


def _safe_hold(
    *,
    repo: str,
    number: int,
    code: str,
    issue_state: str | None,
    signal_evidence: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    evidence = signal_evidence or []
    signal_codes = sorted(
        {
            str(item["signal"])
            for item in evidence
            if isinstance(item, dict) and isinstance(item.get("signal"), str)
        }
    )
    return {
        "schema": "bounty-availability/v1",
        "repo": repo,
        "number": number,
        "disposition": "HOLD",
        "dispatch": False,
        "reason_code": code,
        "issue_state": issue_state,
        "signal_codes": signal_codes,
        "evidence": evidence,
        "authority": {
            "effect": "new_work_dispatch_only",
            "terminal_signal_is_payout_proof": False,
            "terminal_signal_is_revenue_proof": False,
            "raw_comment_text_retained": False,
            "user_identity_retained": False,
        },
    }


def inspect_bounty_availability(
    repo: str,
    number: int,
    token: str | None = None,
    *,
    session: Any = requests,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Return a fail-closed availability receipt for one canonical GitHub issue.

    A CLEAR result only means this guard found no explicit terminal maintainer
    signal in one stable, complete generation. It does not replace reward,
    provenance, competition, skill, submission, or payout gates.
    """
    if not isinstance(repo, str) or _REPO_RE.fullmatch(repo) is None:
        raise ValueError("repo must be in owner/name form")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError("number must be a positive integer")
    if (
        isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages <= 0
        or max_pages > 100
    ):
        raise ValueError("max_pages must be an integer between 1 and 100")

    token = token or GITHUB_TOKEN
    headers = _headers(token)
    issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"

    issue_before = _object(
        _get_json(session, issue_url, headers=headers),
        f"issue {repo}#{number}",
    )
    before_marker = _issue_marker(issue_before)
    if issue_before.get("number") != number:
        raise BountyAvailabilityError("canonical issue number did not match request")

    comments_before, truncated_before = _read_comments(
        repo, number, token, session=session, max_pages=max_pages
    )
    comments_after, truncated_after = _read_comments(
        repo, number, token, session=session, max_pages=max_pages
    )

    issue_after = _object(
        _get_json(session, issue_url, headers=headers),
        f"issue {repo}#{number}",
    )
    after_marker = _issue_marker(issue_after)
    issue_state = (
        issue_after.get("state").casefold()
        if isinstance(issue_after.get("state"), str)
        else None
    )

    if truncated_before or truncated_after:
        return _safe_hold(
            repo=repo,
            number=number,
            code="COMMENT_HISTORY_TRUNCATED",
            issue_state=issue_state,
        )
    if before_marker != after_marker:
        return _safe_hold(
            repo=repo,
            number=number,
            code="ISSUE_GENERATION_CHANGED",
            issue_state=issue_state,
        )

    before_markers = tuple(item.marker for item in comments_before)
    after_markers = tuple(item.marker for item in comments_after)
    if before_markers != after_markers:
        return _safe_hold(
            repo=repo,
            number=number,
            code="COMMENT_GENERATION_CHANGED",
            issue_state=issue_state,
        )

    if issue_state != "open":
        return _safe_hold(
            repo=repo,
            number=number,
            code="ISSUE_NOT_OPEN",
            issue_state=issue_state,
        )

    terminal: list[dict[str, object]] = []
    for item in comments_before:
        for signal in item.signals:
            terminal.append(
                {
                    "comment_id": item.comment_id,
                    "association": item.association,
                    "signal": signal,
                }
            )
    terminal.sort(
        key=lambda item: (
            int(item["comment_id"]),
            str(item["signal"]),
            str(item["association"]),
        )
    )
    if terminal:
        return _safe_hold(
            repo=repo,
            number=number,
            code="MAINTAINER_TERMINAL_OUTCOME",
            issue_state=issue_state,
            signal_evidence=terminal,
        )

    return {
        "schema": "bounty-availability/v1",
        "repo": repo,
        "number": number,
        "disposition": "CLEAR",
        "dispatch": True,
        "reason_code": None,
        "issue_state": "open",
        "signal_codes": [],
        "evidence": [],
        "authority": {
            "effect": "new_work_dispatch_only",
            "terminal_signal_is_payout_proof": False,
            "terminal_signal_is_revenue_proof": False,
            "raw_comment_text_retained": False,
            "user_identity_retained": False,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    signals = ",".join(result.get("signal_codes", [])) or "none"
    reason = result.get("reason_code") or "none"
    return (
        f"{result['repo']}#{result['number']} "
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"reason={reason} signals={signals}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_availability",
        description=(
            "Fail closed on explicit maintainer terminal-outcome signals before "
            "starting new bounty work."
        ),
    )
    parser.add_argument("repo", help="canonical GitHub repository in owner/name form")
    parser.add_argument("issue", type=int, help="canonical bounty issue number")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = inspect_bounty_availability(
            args.repo,
            args.issue,
            max_pages=args.max_pages,
        )
    except (BountyAvailabilityError, ValueError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    return 0 if result["dispatch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
