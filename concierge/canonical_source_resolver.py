# SPDX-License-Identifier: MIT
"""Fail-closed canonical GitHub issue resolution for paid-work discovery rows.

Discovery feeds and mirror listings are useful for finding work, but the rest of
Bounty Concierge's live revenue gates require an already-known ``repo#issue``.
This module closes that gap without treating mirror text as authority: it only
extracts normalized GitHub issue identities, refuses ambiguous candidates, and
never copies listing text into its result.

Resolution is deliberately separate from authorization. A RESOLVED result must
still pass ``revenue_intake.qualify_live_revenue_intake`` before dispatch.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import SplitResult, urlsplit


class CanonicalSourceInputError(ValueError):
    """Raised when a discovery listing is structurally unreliable."""


_ISSUE_PATH_RE = re.compile(
    r"\A/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?\Z"
)
# Free-text extraction must admit only complete canonical-looking tokens, never
# a valid-looking prefix/suffix embedded inside attacker-controlled mirror text.
# The left edge rejects every token continuation. On the right edge a lone '.'
# may terminate prose, but a dot followed by another token character is still
# contamination (for example ``17.evil``, ``17..evil``, or ``17.#fragment``).
_TEXT_REF_TOKEN_CHARS = r"A-Za-z0-9_./?#%=&+~-"
_TEXT_REF_NON_DOT_TOKEN_CHARS = r"A-Za-z0-9_/?#%=&+~-"
_TEXT_REF_PREFIX_GUARD = rf"(?<![{_TEXT_REF_TOKEN_CHARS}])"
_TEXT_REF_SUFFIX_GUARD = rf"(?![{_TEXT_REF_NON_DOT_TOKEN_CHARS}]|\.[{_TEXT_REF_TOKEN_CHARS}])"
_FULL_ISSUE_URL_RE = re.compile(
    _TEXT_REF_PREFIX_GUARD
    + r"(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*"
    + r"(?:#issuecomment-[1-9][0-9]*)?)"
    + _TEXT_REF_SUFFIX_GUARD,
    re.IGNORECASE,
)
_QUALIFIED_REF_RE = re.compile(
    _TEXT_REF_PREFIX_GUARD
    + r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([1-9][0-9]*)"
    + _TEXT_REF_SUFFIX_GUARD
)
_MAX_TEXT_CHARS = 250_000
_MAX_SOURCE_URLS = 100


def _exact_string(value: Any, name: str, *, nonempty: bool = True) -> str:
    if type(value) is not str:
        raise CanonicalSourceInputError(f"{name} must be a string")
    if nonempty and not value.strip():
        raise CanonicalSourceInputError(f"{name} must be non-empty")
    if len(value) > _MAX_TEXT_CHARS:
        raise CanonicalSourceInputError(f"{name} exceeds the supported size")
    return value


def _https_url(value: Any, name: str) -> SplitResult:
    raw = _exact_string(value, name)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise CanonicalSourceInputError(f"{name} is not a valid URL") from exc
    if parsed.scheme.casefold() != "https":
        raise CanonicalSourceInputError(f"{name} must use https")
    if not parsed.hostname:
        raise CanonicalSourceInputError(f"{name} must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise CanonicalSourceInputError(f"{name} must not include credentials")
    if port not in (None, 443):
        raise CanonicalSourceInputError(f"{name} must not use a non-default port")
    return parsed


def _identity(owner: str, repo: str, number_text: str) -> tuple[str, int, str]:
    """Return GitHub's case-insensitive repo identity in one stable form."""
    number = int(number_text)
    canonical_repo = f"{owner.casefold()}/{repo.casefold()}"
    return (
        canonical_repo,
        number,
        f"https://github.com/{canonical_repo}/issues/{number}",
    )


def _github_issue_identity(parsed: SplitResult) -> tuple[str, int, str] | None:
    if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
        return None
    if parsed.query:
        return None
    if parsed.fragment and not re.fullmatch(
        r"issuecomment-[1-9][0-9]*", parsed.fragment, re.IGNORECASE
    ):
        return None
    match = _ISSUE_PATH_RE.fullmatch(parsed.path)
    if match is None:
        return None
    return _identity(match.group(1), match.group(2), match.group(3))


def _identities_from_text(text: str) -> set[tuple[str, int, str]]:
    identities: set[tuple[str, int, str]] = set()
    for match in _FULL_ISSUE_URL_RE.finditer(text):
        # Reuse the exact structural URL validator used for direct listings and
        # explicit sources instead of trusting regex captures as authority.
        parsed = _https_url(match.group(1), "text_issue_url")
        identity = _github_issue_identity(parsed)
        if identity is not None:
            identities.add(identity)
    for match in _QUALIFIED_REF_RE.finditer(text):
        identities.add(_identity(match.group(1), match.group(2), match.group(3)))
    return identities


def _source_url_identities(raw: Any) -> tuple[set[tuple[str, int, str]], int]:
    if raw is None:
        return set(), 0
    if type(raw) is not list:
        raise CanonicalSourceInputError("source_urls must be a list")
    if len(raw) > _MAX_SOURCE_URLS:
        raise CanonicalSourceInputError("source_urls exceeds the supported count")

    identities: set[tuple[str, int, str]] = set()
    ignored = 0
    for index, value in enumerate(raw):
        parsed = _https_url(value, f"source_urls[{index}]")
        identity = _github_issue_identity(parsed)
        if identity is None:
            ignored += 1
        else:
            identities.add(identity)
    return identities, ignored


def _resolved(
    identity: tuple[str, int, str],
    *,
    basis: str,
    listing_is_canonical: bool,
    candidate_count: int,
    ignored_source_url_count: int,
) -> dict[str, Any]:
    repo, number, url = identity
    return {
        "disposition": "RESOLVED",
        "resolved": True,
        "reason_codes": [],
        "canonical_repo": repo,
        "issue_number": number,
        "canonical_issue_url": url,
        "signals": {
            "resolution_basis": basis,
            "listing_is_canonical": listing_is_canonical,
            "candidate_count": candidate_count,
            "ignored_source_url_count": ignored_source_url_count,
        },
    }


def _hold(
    code: str,
    *,
    basis: str,
    candidate_count: int,
    ignored_source_url_count: int,
) -> dict[str, Any]:
    return {
        "disposition": "HOLD",
        "resolved": False,
        "reason_codes": [code],
        "canonical_repo": None,
        "issue_number": None,
        "canonical_issue_url": None,
        "signals": {
            "resolution_basis": basis,
            "listing_is_canonical": False,
            "candidate_count": candidate_count,
            "ignored_source_url_count": ignored_source_url_count,
        },
    }


def resolve_canonical_source(listing: dict[str, Any]) -> dict[str, Any]:
    """Resolve one discovery row to exactly one canonical GitHub issue.

    Precedence is intentionally authority-aware:

    1. A clean ``github.com/<owner>/<repo>/issues/<n>`` listing URL resolves to
       itself. References in that issue's title/body cannot redirect it.
    2. For mirrors, explicit ``source_urls`` are considered before free text.
       Exactly one distinct GitHub issue must be present.
    3. Without explicit source URLs, title/body references may resolve the row,
       but multiple distinct candidates HOLD rather than guessing.

    The returned object contains only normalized metadata and counts. Raw title,
    body, mirror URL paths, and source URL text are never echoed.
    """
    if type(listing) is not dict:
        raise CanonicalSourceInputError("listing must be an object")

    listing_parts = _https_url(listing.get("listing_url"), "listing_url")
    direct = _github_issue_identity(listing_parts)
    if direct is not None:
        return _resolved(
            direct,
            basis="LISTING_URL",
            listing_is_canonical=True,
            candidate_count=1,
            ignored_source_url_count=0,
        )

    source_identities, ignored_source_urls = _source_url_identities(
        listing.get("source_urls")
    )
    if len(source_identities) == 1:
        return _resolved(
            next(iter(source_identities)),
            basis="EXPLICIT_SOURCE_URL",
            listing_is_canonical=False,
            candidate_count=1,
            ignored_source_url_count=ignored_source_urls,
        )
    if len(source_identities) > 1:
        return _hold(
            "CANONICAL_SOURCE_AMBIGUOUS",
            basis="EXPLICIT_SOURCE_URL",
            candidate_count=len(source_identities),
            ignored_source_url_count=ignored_source_urls,
        )

    title = listing.get("title", "")
    body = listing.get("body", "")
    if title is None:
        title = ""
    if body is None:
        body = ""
    title = _exact_string(title, "title", nonempty=False)
    body = _exact_string(body, "body", nonempty=False)

    text_identities = _identities_from_text(f"{title}\n{body}")
    if len(text_identities) == 1:
        return _resolved(
            next(iter(text_identities)),
            basis="LISTING_TEXT",
            listing_is_canonical=False,
            candidate_count=1,
            ignored_source_url_count=ignored_source_urls,
        )
    if len(text_identities) > 1:
        return _hold(
            "CANONICAL_SOURCE_AMBIGUOUS",
            basis="LISTING_TEXT",
            candidate_count=len(text_identities),
            ignored_source_url_count=ignored_source_urls,
        )
    return _hold(
        "CANONICAL_SOURCE_MISSING",
        basis="NONE",
        candidate_count=0,
        ignored_source_url_count=ignored_source_urls,
    )


def format_summary(result: dict[str, Any]) -> str:
    """Format a compact operator-safe resolution receipt."""
    source = result.get("canonical_issue_url") or "none"
    codes = ",".join(result.get("reason_codes", [])) or "none"
    basis = result.get("signals", {}).get("resolution_basis", "NONE")
    return (
        f"disposition={result['disposition']} "
        f"resolved={str(result['resolved']).lower()} "
        f"basis={basis} source={source} reasons={codes}"
    )
