# SPDX-License-Identifier: MIT
"""Offline IssueHunt/BountyHub output-registration reconciliation.

This module consumes *captured* provider evidence. It cannot claim a bounty,
register a PR, talk to a sponsor, or verify a cash transfer over the network.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "bounty-portal-registration-audit/v1"
OUTPUT_SCHEMA = "bounty-portal-registration-result/v1"
_MAX_CASES = 2000
_MAX_REGISTERED = 500
_HEX40 = re.compile(r"[a-f0-9]{40}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")


class PortalRegistrationError(ValueError):
    """An input could falsely associate a PR, registration or payment."""


def _record(value: Any, field: str) -> dict:
    if type(value) is not dict:
        raise PortalRegistrationError(f"{field} must be an object")
    return value


def _exact(value: Any, keys: set[str], field: str) -> dict:
    obj = _record(value, field)
    if set(obj) != keys:
        raise PortalRegistrationError(f"{field} has unexpected/missing fields")
    return obj


def _canonical_name(value: Any, field: str) -> str:
    if type(value) is not str or len(value) > 100 or not _NAME.fullmatch(value):
        raise PortalRegistrationError(f"{field} is not a canonical name")
    if value in {".", ".."}:
        raise PortalRegistrationError(f"{field} is not a canonical name")
    return value.casefold()


def _repo(value: Any) -> str:
    if type(value) is not str or value.count("/") != 1:
        raise PortalRegistrationError("repo must be owner/name")
    return "/".join(_canonical_name(p, "repo") for p in value.split("/"))


def _issue(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise PortalRegistrationError("issue must be a positive integer")
    return value


def _clock(value: Any) -> datetime:
    if type(value) is not str or not _TIME.fullmatch(value):
        raise PortalRegistrationError("timestamps must be canonical UTC seconds")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PortalRegistrationError("invalid timestamp") from exc


def _fresh(value: Any, as_of: datetime, max_age_seconds: int) -> bool:
    delta = (as_of - _clock(value)).total_seconds()
    return 0 <= delta <= max_age_seconds


def _github_pr(value: Any) -> tuple[str, str, int]:
    if type(value) is not str or len(value) > 512:
        raise PortalRegistrationError("PR URL must be a canonical HTTPS GitHub URL")
    u = urlsplit(value)
    parts = u.path.split("/")
    if (u.scheme != "https" or u.netloc != "github.com" or u.query or u.fragment
            or len(parts) != 5 or parts[0] != "" or parts[3] != "pull"
            or not parts[4].isascii() or not parts[4].isdigit()
            or parts[4].startswith("0") or len(parts[4]) > 10
            or int(parts[4]) <= 0 or int(parts[4]) > 2_147_483_647):
        raise PortalRegistrationError("PR URL must be github.com/owner/repo/pull/number")
    repo = _repo(parts[1] + "/" + parts[2])
    num = int(parts[4])
    return f"https://github.com/{repo}/pull/{num}", repo, num


def _source_url(platform: str, source: Any, repo: str, issue: int) -> str:
    if type(source) is not str or len(source) > 800:
        raise PortalRegistrationError("portal source must be an HTTPS issue URL")
    u = urlsplit(source)
    if (u.scheme != "https" or u.query or u.fragment or u.username or u.password
            or u.port is not None):
        raise PortalRegistrationError("portal source must be credential-free HTTPS")
    if platform == "issuehunt":
        if u.hostname not in {"oss.issuehunt.io", "issuehunt.io"} or u.path != f"/r/{repo}/issues/{issue}":
            raise PortalRegistrationError("IssueHunt source does not match its issue")
    elif platform == "bountyhub":
        if u.hostname not in {"bountyhub.dev", "www.bountyhub.dev"} or not u.path.startswith("/en/bounty/"):
            raise PortalRegistrationError("BountyHub source must be a canonical bounty page")
    else:
        raise PortalRegistrationError("unsupported platform")
    return source


def _registered_pr(value: Any, platform: str) -> str:
    try:
        return _github_pr(value)[0]
    except (PortalRegistrationError, ValueError):
        pass
    if platform != "issuehunt" or type(value) is not str or len(value) > 512:
        raise PortalRegistrationError("roster includes an unsupported PR reference")
    u = urlsplit(value)
    parts = u.path.split("/")
    if (u.scheme != "https" or u.hostname not in {"oss.issuehunt.io", "issuehunt.io"}
            or u.port is not None or u.query or u.fragment or u.username or u.password
            or len(parts) != 6 or parts[0] != "" or parts[1] != "r"
            or parts[4] != "pull" or not parts[5].isascii() or not parts[5].isdigit()
            or parts[5].startswith("0") or len(parts[5]) > 10
            or int(parts[5]) <= 0 or int(parts[5]) > 2_147_483_647):
        raise PortalRegistrationError("unsupported IssueHunt PR reference")
    repo = _repo(parts[2] + "/" + parts[3])
    return f"https://github.com/{repo}/pull/{int(parts[5])}"


def _settlement(value: Any, identity: dict, as_of: datetime, max_age: int) -> str | None:
    if value is None:
        return None
    obj = _exact(value, {"status", "identity", "receipt_sha256", "verified_at"}, "settlement")
    if obj["status"] not in {"AWARDED", "PAID"}:
        raise PortalRegistrationError("settlement status unsupported")
    if obj["identity"] != identity:
        raise PortalRegistrationError("settlement evidence belongs to another PR")
    if type(obj["receipt_sha256"]) is not str or not _HEX64.fullmatch(obj["receipt_sha256"]):
        raise PortalRegistrationError("settlement needs an explicit receipt hash")
    # A stale/unknown signed-in provider result is not current payment verification.
    return obj["status"] if _fresh(obj["verified_at"], as_of, max_age) else None


def audit(input_data: Any, *, as_of: datetime | None = None, max_age_seconds: int = 86_400) -> dict:
    """Join source snapshots by original PR identity, not issue-level bounty alone.

    Portal listings must be COMPLETE snapshots (or the result stays UNKNOWN).
    Award/payment statuses require separate same-identity observed receipts.
    """
    obj = _exact(input_data, {"schema", "cases"}, "audit")
    if obj["schema"] != SCHEMA:
        raise PortalRegistrationError("unsupported schema")
    cases = obj["cases"]
    if type(cases) is not list or len(cases) > _MAX_CASES:
        raise PortalRegistrationError("cases must be a bounded list")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 604_800:
        raise PortalRegistrationError("max_age_seconds out of range")
    as_of = as_of or datetime.now(timezone.utc).replace(microsecond=0)
    if as_of.tzinfo is None:
        raise PortalRegistrationError("as_of must be timezone aware")
    as_of = as_of.astimezone(timezone.utc)
    if as_of.microsecond:
        raise PortalRegistrationError("as_of must be UTC second precision")
    outcomes, work_orders, seen = [], [], set()
    for raw in cases:
        case = _exact(raw, {"platform", "repo", "issue", "claimant", "github", "portal", "settlement"}, "case")
        platform = case["platform"]
        if platform not in {"issuehunt", "bountyhub"}:
            raise PortalRegistrationError("unsupported platform")
        repo = _repo(case["repo"])
        issue = _issue(case["issue"])
        claimant = _canonical_name(case["claimant"], "claimant")
        gh = _exact(case["github"], {"pr_url", "head_sha", "observed_at", "state", "author"}, "github")
        pr_url, pr_repo, pr_number = _github_pr(gh["pr_url"])
        if pr_repo != repo or _canonical_name(gh["author"], "PR author") != claimant:
            raise PortalRegistrationError("GitHub PR, issue and claimant identities disagree")
        if type(gh["head_sha"]) is not str or not _HEX40.fullmatch(gh["head_sha"]):
            raise PortalRegistrationError("GitHub head must be a 40-character commit SHA")
        if gh["state"] not in {"open", "closed"}:
            raise PortalRegistrationError("unsupported GitHub PR state")
        identity = {"platform": platform, "repo": repo, "issue": issue,
                    "claimant": claimant, "pr_url": pr_url}
        identity_key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        if identity_key in seen:
            raise PortalRegistrationError("duplicate original-author PR identity")
        seen.add(identity_key)
        portal = _exact(case["portal"], {"source_url", "observed_at", "complete", "registered_pr_urls"}, "portal")
        _source_url(platform, portal["source_url"], repo, issue)
        if type(portal["complete"]) is not bool:
            raise PortalRegistrationError("complete must be boolean")
        roster = portal["registered_pr_urls"]
        if type(roster) is not list or len(roster) > _MAX_REGISTERED:
            raise PortalRegistrationError("roster must be a bounded list")
        normalized_roster = {_registered_pr(u, platform) for u in roster}
        # Never interpret another contributor's same-issue PR as our registration.
        # Only an exactly matching PR link is affirmative output-registration evidence.
        github_fresh = _fresh(gh["observed_at"], as_of, max_age_seconds)
        portal_fresh = _fresh(portal["observed_at"], as_of, max_age_seconds)
        proof_status = _settlement(case["settlement"], identity, as_of, max_age_seconds)
        if proof_status:
            status = proof_status
        elif not github_fresh or not portal_fresh or not portal["complete"] or case["settlement"] is not None:
            # Stale positive award/payment evidence cannot be downgraded to
            # an affirmative unawarded stage merely because the portal is live.
            status = "UNKNOWN"
        elif pr_url in normalized_roster:
            status = "PORTAL_REGISTERED_UNAWARDED"
        else:
            status = "GITHUB_SUBMITTED_PORTAL_NOT_REGISTERED"
        key = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()
        outcome = {
            "identity": identity, "identity_sha256": key, "pr_number": pr_number,
            "github_head_sha": gh["head_sha"], "status": status,
            "github_fresh": github_fresh, "portal_fresh": portal_fresh,
            "portal_complete": portal["complete"],
            "other_registered_count": len(normalized_roster - {pr_url}),
            "portal_source": portal["source_url"],
        }
        outcomes.append(outcome)
        if status == "GITHUB_SUBMITTED_PORTAL_NOT_REGISTERED":
            work_orders.append({
                "operation_id": "portal-registration-" + key[:24],
                "action": "VERIFY_AND_REGISTER_EXISTING_PR_IN_PORTAL",
                "identity": identity,
                "github_head_sha": gh["head_sha"],
                "portal_source": portal["source_url"],
                "operator_only": True,
                "requires_provider_readback_before_submission": True,
                "requires_current_payout_eligibility_check": True,
                "payment_or_award_verified": False,
            })
    outcomes.sort(key=lambda v: v["identity_sha256"])
    work_orders.sort(key=lambda v: v["operation_id"])
    return {"schema": OUTPUT_SCHEMA, "as_of": as_of.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "cases": outcomes, "work_orders": work_orders,
            "summary": {status: sum(x["status"] == status for x in outcomes) for status in
                        ("GITHUB_SUBMITTED_PORTAL_NOT_REGISTERED", "PORTAL_REGISTERED_UNAWARDED",
                         "AWARDED", "PAID", "UNKNOWN")}}
