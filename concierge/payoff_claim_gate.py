# SPDX-License-Identifier: MIT
"""Bind canonical payoff-path authority to one exact GitHub bounty claim.

This adapter does not create payoff semantics.  It reuses the installed payoff-path
verifier (including v3 policy/evidence continuity), then narrows verified READY
authority to exactly one GitHub issue before the live claim wrapper spends provider
reads.  It never claims, comments, submits work, contacts a sponsor, moves money, or
recognizes revenue.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from concierge.payoff_bundle_custody import PayoffBundleError, read_payoff_bundle
from concierge.payoff_path_gate import PayoffPathError, load_strict_json, verify_gate

CLAIM_PROOF_SCHEMA = "payoff-claim-proof/v2"
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ClaimPayoffError(ValueError):
    """Safe, source-text-free failure at the claim/payoff boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _repo(value: Any) -> str:
    if type(value) is not str or value != value.strip() or not _REPO.fullmatch(value):
        raise ClaimPayoffError(
            "INVALID_CLAIM_TARGET",
            "claim repository must be a canonical owner/name identifier",
        )
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ClaimPayoffError(
            "INVALID_CLAIM_TARGET",
            "claim repository must be a canonical owner/name identifier",
        )
    return value


def _issue(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ClaimPayoffError(
            "INVALID_CLAIM_TARGET",
            "claim issue number must be a positive integer",
        )
    return value


def _read_bundle(bundle: str | os.PathLike[str]) -> dict[str, str | None]:
    try:
        return read_payoff_bundle(bundle)
    except PayoffBundleError as exc:
        raise ClaimPayoffError(exc.code, str(exc)) from exc


def _github_issue_identity(value: Any) -> tuple[str, str, int] | None:
    if type(value) is not str:
        return None
    parts = urlsplit(value)
    if (
        parts.scheme.lower() != "https"
        or parts.netloc.casefold() != "github.com"
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        return None
    segments = parts.path.split("/")
    if (
        len(segments) != 5
        or segments[0] != ""
        or not segments[1]
        or not segments[2]
        or segments[3] != "issues"
        or not segments[4]
    ):
        return None
    try:
        number = int(segments[4])
    except ValueError:
        return None
    if number <= 0:
        return None
    return segments[1].casefold(), segments[2].casefold(), number


def _matching_rows(
    packet: dict[str, Any], repo: str, issue: int
) -> list[dict[str, Any]]:
    expected_owner, expected_repo = repo.split("/", 1)
    expected = (expected_owner.casefold(), expected_repo.casefold(), issue)
    results = packet.get("results")
    if type(results) is not list:
        raise ClaimPayoffError(
            "INVALID_GATE_BUNDLE",
            "verified payoff packet did not contain a results array",
        )

    matches: list[dict[str, Any]] = []
    for row in results:
        if type(row) is not dict:
            continue
        path = row.get("payoff_path")
        if type(path) is not dict:
            continue
        if _github_issue_identity(path.get("canonical_source_url")) == expected:
            matches.append(row)
    return matches


def _required_text(raw: dict[str, str | None], key: str) -> str:
    value = raw.get(key)
    if type(value) is not str:
        raise ClaimPayoffError(
            "INVALID_GATE_BUNDLE",
            f"payoff bundle member {key} was not text",
        )
    return value


def verify_claim_payoff_bundle(
    repo: str,
    issue: int,
    bundle: str | os.PathLike[str],
    *,
    trusted_now: datetime | str | None = None,
) -> dict[str, Any]:
    """Verify and bind one exact READY BOUNTY path to ``repo#issue``.

    Production callers omit ``trusted_now``.  Tests may supply an explicit trusted
    time.  Chained documents transparently use ``previous-receipt.json`` when the
    canonical verifier requires it; bootstrap documents fail if a predecessor is
    supplied contrary to their continuity contract.
    """

    canonical_repo = _repo(repo)
    canonical_issue = _issue(issue)
    raw = _read_bundle(bundle)
    try:
        document = load_strict_json(_required_text(raw, "document"))
        packet = load_strict_json(_required_text(raw, "packet"))
        receipt = load_strict_json(_required_text(raw, "receipt"))
        previous_text = raw.get("previous_receipt")
        previous_receipt = (
            load_strict_json(previous_text) if type(previous_text) is str else None
        )
        verified = verify_gate(
            document,
            packet,
            _required_text(raw, "markdown"),
            receipt,
            trusted_now=trusted_now,
            previous_receipt=previous_receipt,
        )
    except (PayoffPathError, ClaimPayoffError) as exc:
        if isinstance(exc, ClaimPayoffError):
            raise
        raise ClaimPayoffError(
            "INVALID_GATE_BUNDLE",
            "payoff bundle failed exact content, continuity, or temporal verification",
        ) from exc

    if verified is not True:
        raise ClaimPayoffError(
            "INVALID_GATE_BUNDLE",
            "payoff gate did not return exact verification authority",
        )
    if type(packet) is not dict or type(receipt) is not dict:
        raise ClaimPayoffError(
            "INVALID_GATE_BUNDLE",
            "verified payoff artifacts were not objects",
        )

    matches = _matching_rows(packet, canonical_repo, canonical_issue)
    if not matches:
        raise ClaimPayoffError(
            "CLAIM_TARGET_NOT_IN_BUNDLE",
            "payoff bundle has no item bound to the exact GitHub bounty target",
        )
    if len(matches) != 1:
        raise ClaimPayoffError(
            "AMBIGUOUS_CLAIM_TARGET",
            "payoff bundle must bind exactly one work item to the GitHub bounty target",
        )

    row = matches[0]
    path = row.get("payoff_path")
    if type(path) is not dict:
        raise ClaimPayoffError(
            "INVALID_GATE_BUNDLE",
            "matching payoff path was malformed",
        )
    if path.get("mechanism") != "BOUNTY" or path.get("conversion_event") != "SUBMIT_WORK":
        raise ClaimPayoffError(
            "WRONG_COMPENSATION_MECHANISM",
            "GitHub claim requires a BOUNTY path whose conversion event is SUBMIT_WORK",
        )
    if row.get("state") != "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW":
        raise ClaimPayoffError(
            "PAYOFF_PATH_NOT_READY",
            "matching payoff path is not current and READY for owner review",
        )
    remaining = row.get("free_work_remaining_minutes")
    if type(remaining) is not int or remaining <= 0:
        raise ClaimPayoffError(
            "FREE_WORK_BUDGET_EXHAUSTED",
            "matching payoff path has no remaining bounded speculative-work budget",
        )

    continuity = packet.get("continuity")
    generation = continuity.get("generation") if type(continuity) is dict else None
    return {
        "schema": CLAIM_PROOF_SCHEMA,
        "repo": canonical_repo,
        "issue": canonical_issue,
        "canonical_issue_url": f"https://github.com/{canonical_repo}/issues/{canonical_issue}",
        "work_id": row.get("work_id"),
        "opportunity_id": row.get("opportunity_id"),
        "state": row["state"],
        "free_work_remaining_minutes": remaining,
        "mechanism": path["mechanism"],
        "value": path.get("value"),
        "conversion_event": path["conversion_event"],
        "conversion_due_at_utc": path.get("conversion_due_at_utc"),
        "packet_schema": packet.get("schema"),
        "continuity_generation": generation,
        "packet_evaluated_at_utc": packet.get("evaluated_at_utc"),
        "source_document_sha256": packet.get("source_document_sha256"),
        "packet_sha256": receipt.get("packet_sha256"),
        "authority": "EXPLICIT_OWNER_CLI_PREREQUISITE_ONLY_NO_EXTERNAL_ACTION",
    }
