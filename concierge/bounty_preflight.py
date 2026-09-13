# SPDX-License-Identifier: MIT
"""Fail-closed live GitHub state preflight for externally advertised bounties.

Aggregator metadata is provenance only. A lead is OPEN only when its issue URL
resolves to a live canonical GitHub issue whose API state is currently open.
No claim, comment, payment, wallet, or other mutation is performed here.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import re
from typing import Any
from urllib.parse import urlparse

import requests

from concierge.config import GITHUB_TOKEN


OPEN = "OPEN"
CLOSED = "CLOSED"
DELETED_OR_MOVED = "DELETED_OR_MOVED"
UNVERIFIABLE = "UNVERIFIABLE"

_GITHUB_ISSUE_PATH = re.compile(r"^/([^/]+)/([^/]+)/issues/([1-9][0-9]*)/?$")


@dataclass(frozen=True)
class LeadProvenance:
    source_url: str
    source_timestamp: str
    bounty_amount: str | None = None
    solver_count: int | None = None


@dataclass(frozen=True)
class PreflightResult:
    authority_state: str
    requested_issue_url: str
    canonical_issue_url: str | None
    canonical_repo: str | None
    issue_number: int | None
    moved: bool
    provenance: LeadProvenance
    reason: str


def _parse_issue_url(url: str) -> tuple[str, int] | None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() != "github.com":
        return None
    match = _GITHUB_ISSUE_PATH.fullmatch(parsed.path)
    if not match or parsed.params or parsed.query or parsed.fragment:
        return None
    owner, repo, number = match.groups()
    return f"{owner}/{repo}", int(number)


def _github_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _unverifiable(
    issue_url: str,
    provenance: LeadProvenance,
    reason: str,
    *,
    final_url: str | None = None,
    moved: bool = False,
) -> PreflightResult:
    return PreflightResult(
        authority_state=UNVERIFIABLE,
        requested_issue_url=issue_url,
        canonical_issue_url=final_url,
        canonical_repo=None,
        issue_number=None,
        moved=moved,
        provenance=provenance,
        reason=reason,
    )


def preflight_bounty_listing(
    issue_url: str,
    provenance: LeadProvenance,
    token: str | None = None,
    *,
    session: Any = requests,
) -> PreflightResult:
    """Resolve one advertised issue and return canonical live state.

    Redirect resolution is unauthenticated so an unexpected redirect can never
    forward the GitHub token. The token is sent only to ``api.github.com``
    after the final browser URL has been validated as an exact GitHub issue.
    Transport failures and ambiguous payloads fail closed to ``UNVERIFIABLE``.
    """
    if _parse_issue_url(issue_url) is None:
        raise ValueError(
            "issue_url must be an exact https://github.com/owner/repo/issues/N URL"
        )
    if not isinstance(provenance.source_url, str) or not provenance.source_url.strip():
        raise ValueError("source_url must be a non-empty string")
    if (
        not isinstance(provenance.source_timestamp, str)
        or not provenance.source_timestamp.strip()
    ):
        raise ValueError("source_timestamp must be a non-empty string")
    if provenance.solver_count is not None and (
        isinstance(provenance.solver_count, bool)
        or not isinstance(provenance.solver_count, int)
        or provenance.solver_count < 0
    ):
        raise ValueError("solver_count must be a non-negative integer or null")

    try:
        page = session.get(
            issue_url,
            headers={"Accept": "text/html"},
            timeout=15,
            allow_redirects=True,
        )
    except requests.RequestException:
        return _unverifiable(
            issue_url, provenance, "issue URL could not be resolved"
        )

    final_url = getattr(page, "url", None)
    moved = (
        isinstance(final_url, str)
        and final_url.rstrip("/") != issue_url.rstrip("/")
    )
    status = getattr(page, "status_code", None)

    # requests caps redirect traversal and raises on loops; still reject a
    # suspiciously long successful chain supplied by a custom session.
    history = getattr(page, "history", ()) or ()
    if len(history) > 10:
        return _unverifiable(
            issue_url,
            provenance,
            "redirect chain exceeded limit",
            final_url=final_url,
            moved=moved,
        )

    if status in {404, 410}:
        return PreflightResult(
            authority_state=DELETED_OR_MOVED,
            requested_issue_url=issue_url,
            canonical_issue_url=final_url if isinstance(final_url, str) else None,
            canonical_repo=None,
            issue_number=None,
            moved=moved,
            provenance=provenance,
            reason="no live canonical issue resolved",
        )
    if status != 200 or not isinstance(final_url, str):
        return _unverifiable(
            issue_url,
            provenance,
            "issue URL returned no authoritative live state",
            final_url=final_url if isinstance(final_url, str) else None,
            moved=moved,
        )

    identity = _parse_issue_url(final_url)
    if identity is None:
        return _unverifiable(
            issue_url,
            provenance,
            "redirect target is not an exact GitHub issue URL",
            final_url=final_url,
            moved=moved,
        )
    repo, number = identity

    api_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    try:
        response = session.get(
            api_url,
            headers=_github_headers(token or GITHUB_TOKEN),
            params=None,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, TypeError, ValueError):
        return _unverifiable(
            issue_url,
            provenance,
            "canonical GitHub API state unavailable",
            final_url=final_url,
            moved=moved,
        )

    if not isinstance(payload, dict):
        return _unverifiable(
            issue_url,
            provenance,
            "canonical GitHub issue payload was not an object",
            final_url=final_url,
            moved=moved,
        )
    if "pull_request" in payload:
        return _unverifiable(
            issue_url,
            provenance,
            "resolved target is a pull request, not an issue",
            final_url=final_url,
            moved=moved,
        )

    state = payload.get("state")
    if state not in {"open", "closed"}:
        return _unverifiable(
            issue_url,
            provenance,
            "canonical GitHub issue state was missing or invalid",
            final_url=final_url,
            moved=moved,
        )

    canonical_url = payload.get("html_url")
    if not isinstance(canonical_url, str) or _parse_issue_url(canonical_url) != identity:
        canonical_url = final_url

    return PreflightResult(
        authority_state=OPEN if state == "open" else CLOSED,
        requested_issue_url=issue_url,
        canonical_issue_url=canonical_url,
        canonical_repo=repo,
        issue_number=number,
        moved=moved,
        provenance=provenance,
        reason=f"canonical GitHub issue is {state}",
    )


def format_json(result: PreflightResult) -> str:
    return json.dumps(
        asdict(result),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _markdown_scalar(value: object) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.splitlines())
    for char in ("\\", "`", "*", "_", "[", "]", "<", ">", "#", "|"):
        text = text.replace(char, f"\\{char}")
    return text


def format_markdown(result: PreflightResult) -> str:
    p = result.provenance
    rows = (
        ("authority", result.authority_state),
        ("canonical", result.canonical_issue_url or "unresolved"),
        ("moved", "yes" if result.moved else "no"),
        ("source", p.source_url),
        ("source timestamp", p.source_timestamp),
        (
            "advertised bounty",
            p.bounty_amount if p.bounty_amount is not None else "unknown",
        ),
        (
            "advertised solvers",
            p.solver_count if p.solver_count is not None else "unknown",
        ),
        ("reason", result.reason),
    )
    return "\n".join(
        f"- **{label}:** {_markdown_scalar(value)}" for label, value in rows
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_preflight",
        description=(
            "Fail closed unless an advertised bounty resolves to a currently "
            "open canonical GitHub issue."
        ),
    )
    parser.add_argument("issue_url")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-timestamp", required=True)
    parser.add_argument("--amount")
    parser.add_argument("--solver-count", type=int)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)

    result = preflight_bounty_listing(
        args.issue_url,
        LeadProvenance(
            source_url=args.source_url,
            source_timestamp=args.source_timestamp,
            bounty_amount=args.amount,
            solver_count=args.solver_count,
        ),
    )
    output = format_json(result) if args.format == "json" else format_markdown(result)
    print(output, end="" if args.format == "markdown" else "\n")
    return 0 if result.authority_state == OPEN else 2


if __name__ == "__main__":
    raise SystemExit(main())
