# SPDX-License-Identifier: MIT
"""Offline admission gate for BountyHub *new* bounty builds.

This evaluates operator-retained first-party snapshots, never marketplace
cards alone. It does not fetch pages, authorize claims, reserve assignments,
send messages, or establish receipt of money.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SCHEMA = "bountyhub-canonical-admission/v1"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_ISSUE = re.compile(r"^/([^/]+)/([^/]+)/issues/([1-9][0-9]*)$")
_GITHUB_PR = re.compile(r"^/([^/]+)/([^/]+)/pull/([1-9][0-9]*)$")
_FIRST_PARTY_PAYERS = {"opencollective.com", "algora.io", "console.algora.io"}
MAX_RECEIPT_AGE = timedelta(hours=24)
MAX_MAINTAINER_IDLE = timedelta(days=90)


class AdmissionError(ValueError):
    """Malformed or unsupported evidence packet."""


def _object(value, field):
    if type(value) is not dict:
        raise AdmissionError(f"{field} must be an object")
    return value


def _nonempty(value, field, maximum=500):
    if type(value) is not str or not value or value != value.strip() or len(value) > maximum:
        raise AdmissionError(f"{field} must be bounded nonempty text")
    return value


def _boolean(value, field):
    if type(value) is not bool:
        raise AdmissionError(f"{field} must be a boolean")
    return value


def _instant(value, field):
    value = _nonempty(value, field, 48)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise AdmissionError(f"{field} requires ISO-8601 date/time") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdmissionError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _url(value, field, *, host=None):
    value = _nonempty(value, field, 2048)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.port or parsed.query or parsed.fragment
            or parsed.netloc != parsed.hostname):
        raise AdmissionError(f"{field} requires a plain HTTPS URL without query/fragment")
    if host and parsed.hostname != host:
        raise AdmissionError(f"{field} must use {host}")
    return parsed


def _github_ref(value, field, pattern):
    parsed = _url(value, field, host="github.com")
    match = pattern.fullmatch(parsed.path)
    if match is None:
        raise AdmissionError(f"{field} is not a canonical GitHub issue/PR URL")
    slug = f"{match.group(1)}/{match.group(2)}"
    if not _REPO.fullmatch(slug):
        raise AdmissionError(f"{field} has an invalid repo")
    return slug.casefold(), int(match.group(3))


def _list(value, field):
    if type(value) is not list or len(value) > 150:
        raise AdmissionError(f"{field} must be a bounded list")
    return value


def _amount(value):
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,2})?", value) is None:
        raise AdmissionError("listing.advertised_usd must be a literal USD amount with <=2 decimals")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise AdmissionError("listing.advertised_usd is invalid") from error


def _known_dead_repository(repo):
    """Fail closed on unavailable policy; preserve existing claims and PRs."""
    policy_path = Path(__file__).resolve().parents[1] / "policies" / "repo_targeting_v1.json"
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise AdmissionError("repo-targeting policy unavailable") from error
    if type(policy) is not dict or policy.get("schema") != "repo-targeting-policy/v1":
        raise AdmissionError("unsupported repo-targeting policy schema")
    dead = policy.get("dead_repo")
    if type(dead) is not dict or type(dead.get("known_dead")) is not list:
        raise AdmissionError("repo-targeting policy missing known_dead list")
    known = set()
    for record in dead["known_dead"]:
        if type(record) is not dict or type(record.get("repo")) is not str:
            raise AdmissionError("invalid known_dead repository record")
        slug = record["repo"]
        if not _REPO.fullmatch(slug):
            raise AdmissionError("invalid known_dead repository slug")
        known.add(slug.casefold())
    return repo.casefold() in known



def _known_nonpayable_program(repo):
    """Fail closed for first-party confirmed symbolic/test/discontinued programs.

    This is an independent program-level exclusion, not an accusation of
    deception, and never touches the historical claims or PRs.
    """
    path = Path(__file__).resolve().parents[1] / "policies" / "repo_targeting_v1.json"
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise AdmissionError("repo-targeting policy unavailable") from error
    if type(policy) is not dict or policy.get("schema") != "repo-targeting-policy/v1":
        raise AdmissionError("unsupported repo-targeting policy schema")
    section = policy.get("program_exclusions")
    if (type(section) is not dict or
            section.get("schema") != "first-party-nonpayable-programs/v1" or
            section.get("new_paid_builds") != "HOLD" or
            type(section.get("preserve_existing_claims")) is not bool or
            section.get("preserve_existing_claims") is not True):
        raise AdmissionError("nonpayable-program exclusion policy unavailable")
    records = section.get("repositories")
    if type(records) is not list or len(records) > 200:
        raise AdmissionError("invalid nonpayable-program list")
    blocked = set()
    for record in records:
        if type(record) is not dict or type(record.get("repo")) is not str:
            raise AdmissionError("invalid nonpayable-program record")
        slug = record["repo"]
        if not _REPO.fullmatch(slug):
            raise AdmissionError("invalid nonpayable-program repository slug")
        if type(record.get("evidence_url")) is not str:
            raise AdmissionError("missing first-party exclusion evidence URL")
        evidence = _url(record["evidence_url"], "program_exclusions.evidence_url", host="github.com")
        if not evidence.path.startswith("/" + slug + "/"):
            raise AdmissionError("first-party evidence URL does not match excluded repository")
        if type(record.get("classification")) is not str or not record["classification"]:
            raise AdmissionError("missing nonpayable-program classification")
        blocked.add(slug.casefold())
    return repo.casefold() in blocked


def assess(packet):
    packet = _object(packet, "packet")
    if packet.get("schema") != SCHEMA:
        raise AdmissionError("unknown packet schema")
    listing = _object(packet.get("listing"), "listing")
    github = _object(packet.get("github"), "github")
    payer = _object(packet.get("payer"), "payer")
    now = _instant(packet.get("as_of"), "as_of")
    observed = _instant(github.get("observed_at"), "github.observed_at")
    if observed > now:
        raise AdmissionError("github snapshot cannot be in the future")

    listing_id = _nonempty(listing.get("id"), "listing.id", 128)
    listing_ref = _github_ref(listing.get("issue_url"), "listing.issue_url", _GITHUB_ISSUE)
    source_ref = _github_ref(github.get("issue_url"), "github.issue_url", _GITHUB_ISSUE)
    funding = _nonempty(listing.get("funding_type"), "listing.funding_type", 20)
    if funding not in {"escrowed", "promised", "unknown"}:
        raise AdmissionError("unsupported funding_type")
    amount = _amount(listing.get("advertised_usd"))
    state = _nonempty(github.get("state"), "github.state", 20)
    if state not in {"open", "closed"}:
        raise AdmissionError("github.state must be open or closed")
    archived = _boolean(github.get("repository_archived"), "github.repository_archived")
    census = _boolean(github.get("linked_pr_census_complete"), "github.linked_pr_census_complete")
    assigned = _list(github.get("assignees"), "github.assignees")
    for item in assigned:
        _nonempty(item, "github.assignees[]", 100)
    matched_prs = _list(github.get("linked_open_pr_urls"), "github.linked_open_pr_urls")
    parsed_prs = [_github_ref(item, "github.linked_open_pr_urls[]", _GITHUB_PR) for item in matched_prs]
    last_action = _instant(github.get("last_maintainer_action_at"), "github.last_maintainer_action_at")
    if last_action > now:
        raise AdmissionError("maintainer action cannot be in the future")

    sponsor = _nonempty(payer.get("sponsor_name"), "payer.sponsor_name", 180)
    proof_sponsor = _nonempty(payer.get("proof_sponsor_name"), "payer.proof_sponsor_name", 180)
    paid = _boolean(payer.get("completed_paid_merge_verified"), "payer.completed_paid_merge_verified")
    paid_proof = payer.get("receipt_url")
    if paid_proof is not None:
        parsed = _url(paid_proof, "payer.receipt_url")
        if parsed.hostname not in _FIRST_PARTY_PAYERS:
            raise AdmissionError("payer.receipt_url must be a supported first-party receipt")
    reasons = []
    if listing_ref != source_ref:
        reasons.append("CANONICAL_ISSUE_MISMATCH")
    if _known_dead_repository(source_ref[0]):
        reasons.append("REPO_KNOWN_DEAD")
    if _known_nonpayable_program(source_ref[0]):
        reasons.append("REPO_PROGRAM_NONPAYABLE")
    if amount < Decimal("15"):
        reasons.append("BELOW_OWNER_USD_15_FLOOR")
    if funding == "unknown":
        reasons.append("FUNDING_KIND_UNVERIFIED")
    if state != "open":
        reasons.append("CANONICAL_ISSUE_CLOSED")
    if archived:
        reasons.append("REPOSITORY_ARCHIVED")
    if now - observed > MAX_RECEIPT_AGE:
        reasons.append("CANONICAL_SOURCE_STALE")
    if now - last_action > MAX_MAINTAINER_IDLE:
        reasons.append("NO_RECENT_MAINTAINER_ACTION")
    if assigned:
        reasons.append("ASSIGNED_TO_EXISTING_CONTRIBUTOR")
    if not census:
        reasons.append("LINKED_PR_CENSUS_INCOMPLETE")
    if parsed_prs:
        reasons.append("EXISTING_OPEN_IMPLEMENTATION")
    if any(repo != source_ref[0] for repo, _ in parsed_prs):
        reasons.append("PR_REPOSITORY_MISMATCH")
    if not paid or not paid_proof or sponsor.casefold() != proof_sponsor.casefold():
        reasons.append("SAME_SPONSOR_COMPLETED_PAID_MERGE_UNVERIFIED")

    return {
        "schema": SCHEMA, "listing_id": listing_id,
        "issue_url": github["issue_url"],
        "advertised_usd": str(amount),
        "funding_type": funding, "funding_is_escrowed": funding == "escrowed",
        "decision": "READY_FOR_NEW_BUILD" if not reasons else "HOLD",
        "reason_codes": sorted(set(reasons)),
        "open_linked_pr_count": len(parsed_prs),
        "existing_assignee_count": len(assigned),
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "source_observed_at": observed.isoformat().replace("+00:00", "Z"),
        "qualification_is_offline": True,
        "paid_to_original_claimant": False,
        "claim_or_submission_performed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="JSON with listing, canonical GitHub and payer receipts")
    parser.add_argument("--output", type=Path, help="Write machine-readable decision here")
    args = parser.parse_args()
    result = assess(json.loads(args.packet.read_text(encoding="utf-8")))
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
