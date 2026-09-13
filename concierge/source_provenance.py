# SPDX-License-Identifier: MIT
"""Fail-closed source-provenance gate for paid-work intake.

Third-party bounty listings can be useful discovery signals, but they are not
authority for scope, reward, issue state, or claimant competition. This module
binds a normalized listing to the canonical GitHub issue already established by
``bounty_audit`` and requires at least one reward-evidence URL on that exact
canonical issue before dispatch.

The result intentionally carries only normalized metadata. It never copies
listing, issue, or comment body text.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit


_COMMENT_FRAGMENT_RE = re.compile(r"issuecomment-[1-9][0-9]*\Z")


class ProvenanceInputError(ValueError):
    """Raised when a provenance snapshot is structurally unreliable."""


def _exact_string(value: Any, name: str, *, nonempty: bool = True) -> str:
    if type(value) is not str:
        raise ProvenanceInputError(f"{name} must be a string")
    if nonempty and not value.strip():
        raise ProvenanceInputError(f"{name} must be non-empty")
    return value


def _https_url(value: Any, name: str) -> SplitResult:
    raw = _exact_string(value, name)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ProvenanceInputError(f"{name} is not a valid URL") from exc
    if parsed.scheme.casefold() != "https":
        raise ProvenanceInputError(f"{name} must use https")
    if not parsed.hostname:
        raise ProvenanceInputError(f"{name} must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ProvenanceInputError(f"{name} must not include credentials")
    if port not in (None, 443):
        raise ProvenanceInputError(f"{name} must not use a non-default port")
    return parsed


def _repo_identity(value: Any) -> tuple[str, str, str]:
    repo = _exact_string(value, "canonical_audit.repo")
    if repo.count("/") != 1:
        raise ProvenanceInputError("canonical_audit.repo must be in owner/name form")
    owner, name = repo.split("/", 1)
    if not owner or not name or owner in {".", ".."} or name in {".", ".."}:
        raise ProvenanceInputError("canonical_audit.repo must be in owner/name form")
    if any(ch.isspace() for ch in repo):
        raise ProvenanceInputError("canonical_audit.repo must not contain whitespace")
    return repo, owner, name


def _positive_issue_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProvenanceInputError("canonical_audit.number must be a positive integer")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ProvenanceInputError(f"{name} must be boolean")
    return value


def _canonical_issue_url(audit: dict[str, Any]) -> tuple[str, SplitResult]:
    _repo, owner, name = _repo_identity(audit.get("repo"))
    number = _positive_issue_number(audit.get("number"))
    expected = f"https://github.com/{owner}/{name}/issues/{number}"

    issue_url = _https_url(audit.get("issue_url"), "canonical_audit.issue_url")
    if issue_url.hostname.casefold() != "github.com":
        raise ProvenanceInputError("canonical_audit.issue_url must be on github.com")
    if issue_url.query or issue_url.fragment:
        raise ProvenanceInputError(
            "canonical_audit.issue_url must not contain query or fragment data"
        )
    if issue_url.path.rstrip("/").casefold() != f"/{owner}/{name}/issues/{number}".casefold():
        raise ProvenanceInputError(
            "canonical_audit.issue_url does not match canonical_audit repo/number"
        )
    return expected, issue_url


def _same_issue(parsed: SplitResult, canonical: SplitResult, *, allow_comment: bool) -> bool:
    if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
        return False
    if parsed.query:
        return False
    if parsed.path.rstrip("/").casefold() != canonical.path.rstrip("/").casefold():
        return False
    if not parsed.fragment:
        return True
    return allow_comment and bool(_COMMENT_FRAGMENT_RE.fullmatch(parsed.fragment))


def _reward_evidence(snapshot: dict[str, Any]) -> list[SplitResult]:
    raw = snapshot.get("reward_evidence_urls")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProvenanceInputError("reward_evidence_urls must be a list")
    parsed: list[SplitResult] = []
    for index, value in enumerate(raw):
        parsed.append(_https_url(value, f"reward_evidence_urls[{index}]"))
    return parsed


def verify_source_provenance(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a safe dispatch decision bound to canonical GitHub provenance.

    Decision precedence is ``REJECT`` > ``HOLD`` > ``ACTIONABLE``. External
    mirrors may remain useful discovery inputs, but they can never become the
    dispatch URL or the sole reward evidence.
    """
    if not isinstance(snapshot, dict):
        raise ProvenanceInputError("snapshot must be an object")

    listing = _https_url(snapshot.get("listing_url"), "listing_url")
    audit_value = snapshot.get("canonical_audit")

    reasons: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        reasons.append({"code": code, "severity": severity, "message": message})

    if audit_value is None:
        add(
            "CANONICAL_SOURCE_MISSING",
            "HOLD",
            "A canonical GitHub issue audit is required before paid-work dispatch.",
        )
        return {
            "disposition": "HOLD",
            "dispatch": False,
            "reason_codes": [reason["code"] for reason in reasons],
            "reasons": reasons,
            "use_source_url": None,
            "signals": {
                "listing_relation": "UNVERIFIED",
                "canonical_source_verified": False,
                "canonical_repo": None,
                "issue_number": None,
                "canonical_reward_evidence_count": 0,
                "ignored_reward_evidence_count": 0,
                "stale_listing_signal": False,
                "search_truncated": False,
            },
        }
    if not isinstance(audit_value, dict):
        raise ProvenanceInputError("canonical_audit must be an object")

    audit = audit_value
    required_audit_fields = {
        "repo",
        "number",
        "issue_url",
        "issue_state",
        "stale_listing_signal",
        "search_truncated",
    }
    if not required_audit_fields.issubset(audit):
        add(
            "CANONICAL_AUDIT_INCOMPLETE",
            "HOLD",
            "Canonical GitHub audit is missing provenance-critical fields.",
        )
        return {
            "disposition": "HOLD",
            "dispatch": False,
            "reason_codes": [reason["code"] for reason in reasons],
            "reasons": reasons,
            "use_source_url": None,
            "signals": {
                "listing_relation": "UNVERIFIED",
                "canonical_source_verified": False,
                "canonical_repo": None,
                "issue_number": None,
                "canonical_reward_evidence_count": 0,
                "ignored_reward_evidence_count": 0,
                "stale_listing_signal": False,
                "search_truncated": False,
            },
        }

    canonical_url, canonical_parts = _canonical_issue_url(audit)
    repo, _owner, _name = _repo_identity(audit.get("repo"))
    number = _positive_issue_number(audit.get("number"))

    state = _exact_string(audit.get("issue_state"), "canonical_audit.issue_state")
    stale = _boolean(
        audit.get("stale_listing_signal"),
        "canonical_audit.stale_listing_signal",
    )
    truncated = _boolean(
        audit.get("search_truncated"),
        "canonical_audit.search_truncated",
    )

    listing_relation = (
        "FIRST_PARTY"
        if _same_issue(listing, canonical_parts, allow_comment=True)
        else "MIRROR_CANONICALIZED"
    )

    evidence = _reward_evidence(snapshot)
    canonical_evidence_count = sum(
        1 for item in evidence if _same_issue(item, canonical_parts, allow_comment=True)
    )
    ignored_evidence_count = len(evidence) - canonical_evidence_count

    if state.casefold() != "open":
        add(
            "CANONICAL_ISSUE_NOT_OPEN",
            "REJECT",
            "Canonical GitHub state says the issue is not open.",
        )
    if stale:
        add(
            "STALE_CANONICAL_SOURCE",
            "HOLD",
            "Canonical GitHub audit reports a stale-listing signal.",
        )
    if truncated:
        add(
            "CANONICAL_AUDIT_INCOMPLETE",
            "HOLD",
            "Canonical GitHub audit was truncated and cannot authorize dispatch.",
        )
    if canonical_evidence_count == 0:
        add(
            "CANONICAL_REWARD_EVIDENCE_MISSING",
            "HOLD",
            "At least one reward-evidence URL must point to the canonical issue or its comment thread.",
        )

    if any(reason["severity"] == "REJECT" for reason in reasons):
        disposition = "REJECT"
    elif reasons:
        disposition = "HOLD"
    else:
        disposition = "ACTIONABLE"

    return {
        "disposition": disposition,
        "dispatch": disposition == "ACTIONABLE",
        "reason_codes": [reason["code"] for reason in reasons],
        "reasons": reasons,
        "use_source_url": canonical_url,
        "signals": {
            "listing_relation": listing_relation,
            "canonical_source_verified": True,
            "canonical_repo": repo,
            "issue_number": number,
            "canonical_reward_evidence_count": canonical_evidence_count,
            "ignored_reward_evidence_count": ignored_evidence_count,
            "stale_listing_signal": stale,
            "search_truncated": truncated,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    """Format an operator-safe summary without source listing text."""
    codes = ",".join(result.get("reason_codes", [])) or "none"
    relation = result.get("signals", {}).get("listing_relation", "UNVERIFIED")
    source = result.get("use_source_url") or "none"
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"relation={relation} source={source} reasons={codes}"
    )


def _load_snapshot(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ProvenanceInputError("snapshot JSON must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.source_provenance",
        description=(
            "Bind a paid-work listing to canonical GitHub source and reward evidence "
            "before dispatch."
        ),
    )
    parser.add_argument("snapshot", help="JSON snapshot path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="Emit full result JSON")
    args = parser.parse_args(argv)

    try:
        snapshot = _load_snapshot(args.snapshot)
        result = verify_source_provenance(snapshot)
    except (OSError, json.JSONDecodeError, ProvenanceInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))

    if result["disposition"] == "ACTIONABLE":
        return 0
    if result["disposition"] == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
