# SPDX-License-Identifier: MIT
"""Offline, non-authoritative BountyHub original-contributor claim reconciliation.

No HTTP, provider write, credential, or account access occurs in this module.
The inputs are independently captured first-party snapshots, NOT authority to
assert a payment has been received. All monetary amounts here are USD claims/
pledges as reported by the source, not wallet/bank settlement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = "bountyhub-claim-receipt/v1"
OUT = "bountyhub-claim-assessment/v1"
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
GH = re.compile(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(issues|pull)/([1-9][0-9]*)/?\Z")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MONEY = re.compile(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?\Z")
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
SOURCE_HOSTS = {"www.bountyhub.dev", "bountyhub.dev"}

class InvalidReceipt(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidReceipt(message)


def obj(value: object, fields: set[str], name: str) -> dict:
    require(type(value) is dict and set(value) == fields, f"{name}: exact object fields required")
    return value


def txt(value: object, name: str, max_len: int = 2048) -> str:
    require(type(value) is str and 0 < len(value) <= max_len and value == value.strip(),
            f"{name}: invalid text")
    require(all(32 <= ord(c) != 127 for c in value), f"{name}: control characters")
    return value


def login(value: object, name: str) -> str:
    s = txt(value, name, 39)
    require(LOGIN.fullmatch(s) is not None, f"{name}: not a GitHub login")
    return s.casefold()


def utc(value: object, name: str) -> datetime:
    s = txt(value, name, 64)
    require(UTC.fullmatch(s) is not None, f"{name}: strict UTC required")
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00")
    except ValueError as exc:
        raise InvalidReceipt(f"{name}: invalid datetime") from exc
    return d


def url_parts(value: object, name: str):
    s = txt(value, name)
    require(not any(c.isspace() for c in s) and "%" not in s and "\\" not in s,
            f"{name}: noncanonical URL")
    try:
        p = urlsplit(s)
        port = p.port
    except ValueError as exc:
        raise InvalidReceipt(f"{name}: bad URL") from exc
    require(p.scheme == "https" and p.hostname and port is None and not p.username and
            not p.password and not p.query and not p.fragment, f"{name}: HTTPS canonical required")
    return s, p.hostname.casefold(), p.path


def github(value: object, name: str, kind: str):
    original, host, path = url_parts(value, name)
    require(host == "github.com", f"{name}: github.com required")
    m = GH.fullmatch(path)
    require(m is not None and m.group(3) == kind, f"{name}: wrong canonical GitHub URL")
    return original, (m.group(1).casefold(), m.group(2).casefold(), int(m.group(4)))


def amount(value: object, name: str) -> Decimal:
    s = txt(value, name, 16)
    require(MONEY.fullmatch(s) is not None, f"{name}: invalid amount")
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise InvalidReceipt(f"{name}: invalid decimal") from exc


def cash(value: Decimal) -> str:
    return format(value, ".2f")


def assessment(payload: object, now: datetime | None = None) -> dict:
    x = obj(payload, {"schema", "actor_login", "listing", "funding", "claim", "pull_request",
                      "claim_request", "max_snapshot_age_seconds"}, "request")
    require(x["schema"] == SCHEMA, "schema mismatch")
    actor = login(x["actor_login"], "actor_login")
    limit = x["max_snapshot_age_seconds"]
    require(type(limit) is int and 60 <= limit <= 86400, "invalid age limit")
    reference = now or datetime.now(timezone.utc)
    require(reference.tzinfo is not None, "now must be timezone-aware")
    reference = reference.astimezone(timezone.utc)

    listing = obj(x["listing"], {"url", "issue_url", "issue_state", "observed_at"}, "listing")
    listing_url, host, p = url_parts(listing["url"], "listing.url")
    require(host in SOURCE_HOSTS, "listing must be from BountyHub")
    segments = p.strip("/").split("/")
    require(len(segments) in (4, 5) and segments[:3] == ["en", "bounty", "view"]
            and UUID.fullmatch(segments[3]) is not None, "listing path does not identify a BountyHub bounty")
    issue_url, issue = github(listing["issue_url"], "listing.issue_url", "issues")
    issue_state = txt(listing["issue_state"], "listing.issue_state", 16).upper()
    require(issue_state in {"OPEN", "CLOSED", "UNKNOWN"}, "invalid canonical issue state")

    funding = obj(x["funding"], {"pledges", "advertised_total_usd", "observed_at"}, "funding")
    rows = funding["pledges"]
    require(type(rows) is list and len(rows) <= 1000, "too many pledges")
    dollars = {"PAID": Decimal(0), "PROMISED": Decimal(0), "OTHER": Decimal(0)}
    seen = set()
    for i, raw in enumerate(rows):
        row = obj(raw, {"pledge_id", "amount_usd", "payment_status", "retracted"}, f"pledge {i}")
        ident = txt(row["pledge_id"], "pledge_id", 128)
        require(ident not in seen, "duplicate pledge identity")
        seen.add(ident)
        status = txt(row["payment_status"], "payment_status", 40).upper()
        require(type(row["retracted"]) is bool, "pledge.retracted must be bool")
        val = amount(row["amount_usd"], "amount_usd")
        if not row["retracted"]:
            dollars[status if status in {"PAID", "PROMISED"} else "OTHER"] += val
    advertised = amount(funding["advertised_total_usd"], "advertised_total_usd")
    require(sum(dollars.values()) == advertised, "pledges differ from advertised total")

    pr = obj(x["pull_request"], {"url", "author_login", "head_sha", "state", "observed_at"}, "pull_request")
    pr_url, pr_id = github(pr["url"], "pull_request.url", "pull")
    sha = txt(pr["head_sha"], "pull_request.head_sha", 40)
    require(SHA.fullmatch(sha) is not None, "head_sha must be full lower-case Git commit SHA")
    pr_state = txt(pr["state"], "pull_request.state", 16).upper()
    require(pr_state in {"OPEN", "CLOSED", "MERGED"}, "invalid PR state")
    pr_author = login(pr["author_login"], "pull_request.author_login")
    require(pr_id[:2] == issue[:2], "claim's PR and issue must be from the same sponsor repository")

    claim = x["claim"]
    if claim is None:
        claim_info = None
    else:
        claim_info = obj(claim, {"claim_id", "claimant_login", "source_pr_url", "status",
                                 "provider_marked_paid", "observed_at"}, "claim")
        txt(claim_info["claim_id"], "claim_id", 128)
        _, claim_pr = github(claim_info["source_pr_url"], "claim.source_pr_url", "pull")
        require(claim_pr == pr_id, "portal claim points to a different original PR")
        claim_info["status"] = txt(claim_info["status"], "claim.status", 32).upper()
        require(claim_info["status"] in {"PENDING", "APPROVED", "REJECTED", "PAID", "UNKNOWN"},
                "invalid provider claim status")
        require(type(claim_info["provider_marked_paid"]) is bool, "provider_marked_paid must be bool")

    claim_request = obj(x["claim_request"], {"evidence_url", "affirmative", "waived"}, "claim_request")
    evidence_url = claim_request["evidence_url"]
    if evidence_url is not None:
        evurl, evid = github_comment_or_pull(evidence_url)
        require(evid == pr_id or evid == issue, "claim request evidence points to unrelated issue/PR")
    require(type(claim_request["affirmative"]) is bool and type(claim_request["waived"]) is bool,
            "claim request flags must be booleans")

    times = {"listing": listing["observed_at"], "funding": funding["observed_at"],
             "pull_request": pr["observed_at"]}
    if claim_info is not None:
        times["claim"] = claim_info["observed_at"]
    stale = []
    for scope, value in times.items():
        age = (reference - utc(value, f"{scope}.observed_at")).total_seconds()
        if age < -60 or age > limit:
            stale.append(scope)
    reason = []
    if stale:
        reason.append("PROVIDER_OR_PR_SNAPSHOT_STALE")
    if issue_state == "UNKNOWN":
        reason.append("CANONICAL_ISSUE_STATE_UNKNOWN")
    if pr_author != actor:
        reason.append("ORIGINAL_PR_AUTHOR_MISMATCH")
    if claim_info is not None and login(claim_info["claimant_login"], "claim.claimant_login") != actor:
        reason.append("PORTAL_CLAIMANT_MISMATCH")
    if claim_info is None:
        reason.append("NO_ORIGINAL_PORTAL_CLAIM")
    if not claim_request["affirmative"] or claim_request["waived"] or evidence_url is None:
        reason.append("AFFIRMATIVE_ORIGINAL_COMPENSATION_REQUEST_NOT_EVIDENCED")
    if claim_info is not None and claim_info["status"] == "REJECTED":
        reason.append("PORTAL_CLAIM_REJECTED")
    if claim_info is not None and claim_info["provider_marked_paid"] and claim_info["status"] not in {"PAID", "APPROVED"}:
        reason.append("INCONSISTENT_PAID_MARKER")
    if claim_info is not None and claim_info["status"] == "PAID" and not claim_info["provider_marked_paid"]:
        reason.append("PAID_STATUS_WITHOUT_PROVIDER_PAID_FLAG")

    if reason:
        disposition = "HOLD"
    elif claim_info["provider_marked_paid"]:
        disposition = "PROVIDER_PAID_MARKED_VERIFY_ACTUAL_SETTLEMENT"
    elif claim_info["status"] == "APPROVED":
        disposition = "CLAIM_APPROVED_AWAIT_PROVIDER_PAYOUT"
    elif claim_info["status"] == "PENDING":
        disposition = "CLAIM_REGISTERED_AWAIT_REVIEW"
    else:
        disposition = "CLAIM_STATE_UNKNOWN_REQUEST_PROVIDER_REVIEW"

    normalized = json.loads(json.dumps(x, sort_keys=True))
    if normalized["claim"] is not None:
        normalized["claim"]["status"] = claim_info["status"]
    payload_digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                              ensure_ascii=True).encode()).hexdigest()
    receipt = {"schema": OUT, "source_schema": SCHEMA, "listing_id": segments[3],
               "canonical_issue": f"{issue[0]}/{issue[1]}#{issue[2]}", "original_pr": pr_url,
               "original_pr_head": sha, "original_contributor": actor,
               "disposition": disposition, "reason_codes": reason, "stale_scopes": stale,
               "amounts_usd_reported": {"advertised": cash(advertised),
                                        "platform_paid_pledges": cash(dollars["PAID"]),
                                        "promised_unfunded": cash(dollars["PROMISED"]),
                                        "other_or_unknown": cash(dollars["OTHER"]),
                                        "cash_received_by_contributor": "UNVERIFIED"},
               "provider_claim_status": claim_info["status"] if claim_info else "NONE",
               "source_sha256": payload_digest,
               "observed_at_utc": {key: value for key, value in times.items()},
               "authority": {"can_claim": False, "can_submit": False,
                             "can_assert_contributor_paid": False, "read_only": True}}
    receipt["receipt_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True,
                                          separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return receipt


def github_comment_or_pull(value: object):
    """Accept a PR body URL or an anchored, unique PR/issue comment as evidence."""
    s = txt(value, "claim_request.evidence_url")
    p = urlsplit(s)
    require(p.scheme == "https" and p.hostname == "github.com" and not p.query
            and not p.username and not p.password and p.port is None,
            "compensation evidence must be from github.com")
    if p.fragment:
        require(re.fullmatch(r"issuecomment-[1-9][0-9]*", p.fragment) is not None,
                "unsupported evidence URL fragment")
    m = GH.fullmatch(p.path)
    require(m is not None, "invalid compensation evidence URL")
    return s, (m.group(1).casefold(), m.group(2).casefold(), int(m.group(4)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON filename or '-' for stdin")
    parser.add_argument("--as-of-utc", help="strict UTC timestamp, ONLY for historical replay")
    options = parser.parse_args(argv)
    try:
        source = sys.stdin.read(1024 * 1024 + 1) if options.snapshot == "-" else Path(options.snapshot).read_bytes()
        require(len(source) <= 1024 * 1024, "snapshot is too large")
        if isinstance(source, bytes):
            source = source.decode("utf-8")
        snapshot = json.loads(source, parse_constant=lambda _: (_ for _ in ()).throw(InvalidReceipt("nonfinite JSON")))
        result = assessment(snapshot, utc(options.as_of_utc, "as_of_utc") if options.as_of_utc else None)
    except (OSError, UnicodeError, InvalidReceipt, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["disposition"] != "HOLD" else 2

if __name__ == "__main__":
    raise SystemExit(main())
