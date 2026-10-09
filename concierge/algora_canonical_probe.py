# SPDX-License-Identifier: MIT
"""Bounded live canonical GitHub preflight for Algora-listed issues.

Listing state is not permission to claim, build, submit or receive payment.
This adapter makes first-party reads and produces only an advisory receipt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

SCHEMA = "algora-canonical-probe/v1"
_ISSUE_PATH = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$")
_MAX_RESPONSE = 2_000_000
_MAX_PR_PAGES = 4
_PR_PAGE_SIZE = 100


class AlgoraProbeError(ValueError):
    """Malformed or incomplete first-party source evidence."""


def _url(value: Any, hostname: str) -> str:
    if (type(value) is not str or not value or len(value) > 512 or "%" in value
            or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value)):
        raise AlgoraProbeError("URLs must be bounded canonical HTTPS")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise AlgoraProbeError("URL syntax invalid") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != hostname
            or parsed.username is not None or parsed.password is not None
            or port is not None or parsed.query or parsed.fragment):
        raise AlgoraProbeError("unexpected HTTPS URL origin or parameters")
    return value


def _listing(raw: Any) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != {
        "listing_url", "canonical_issue_url", "listing_state", "advertised_usd"
    }:
        raise AlgoraProbeError("listing requires exact identity and advertised fields")
    source = _url(raw["listing_url"], "algora.io")
    canonical = _url(raw["canonical_issue_url"], "github.com")
    match = _ISSUE_PATH.fullmatch(urlsplit(canonical).path)
    if match is None:
        raise AlgoraProbeError("canonical_issue_url must identify a GitHub issue")
    if raw["listing_state"] not in ("OPEN", "CLOSED", "UNKNOWN"):
        raise AlgoraProbeError("listing_state invalid")
    amount = raw["advertised_usd"]
    if (amount is not None and
        (type(amount) is not str or len(amount) > 32 or
         re.fullmatch(r"[0-9]{1,15}(?:\.[0-9]{1,2})?", amount) is None)):
        raise AlgoraProbeError("advertised_usd must be a bounded nominal decimal or null")
    return {"listing_url": source, "canonical_issue_url": canonical,
            "listing_state": raw["listing_state"], "advertised_usd": amount,
            "owner": match.group(1), "repo": match.group(2), "number": int(match.group(3))}


class GitHubRestReader:
    """Sequential, size-bounded provider requests; no background retry/poll."""
    def __init__(self, token: str | None = None, timeout: float = 8.0):
        self.token = token
        self.timeout = timeout
        self.calls = 0

    def __call__(self, path: str) -> Any:
        if not path.startswith("/repos/"):
            raise AlgoraProbeError("unsupported API path")
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "bounty-concierge-algora/1",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request("https://api.github.com" + path, headers=headers)
        self.calls += 1
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read(_MAX_RESPONSE + 1)
                if len(payload) > _MAX_RESPONSE:
                    raise AlgoraProbeError("GitHub response exceeded 2 MB")
        except HTTPError as exc:
            remaining = exc.headers.get("X-RateLimit-Remaining", "unknown")
            retry = exc.headers.get("Retry-After", "unreported")
            raise AlgoraProbeError(
                f"GitHub HTTP {exc.code}; remaining={remaining}; retry_after={retry}"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise AlgoraProbeError("GitHub request unavailable") from None
        try:
            return json.loads(payload)
        except (UnicodeError, ValueError):
            raise AlgoraProbeError("GitHub response is not JSON") from None


def _related(pr: dict[str, Any], owner: str, repo: str, number: int) -> bool:
    body = str(pr.get("title") or "") + "\n" + str(pr.get("body") or "")
    short = re.compile(r"(?<![A-Za-z0-9])#" + str(number) + r"\b")
    full = f"github.com/{owner}/{repo}/issues/{number}"
    return bool(short.search(body) or full.casefold() in body.casefold())


def inspect_algora_listing(
    raw: dict[str, Any], *,
    reader: Callable[[str], Any] | None = None,
    checked_at: str | None = None,
    max_pr_pages: int = _MAX_PR_PAGES,
) -> dict[str, Any]:
    """Live provider evidence is necessary but never sufficient for a work lease."""
    listing = _listing(raw)
    if type(max_pr_pages) is not int or not 1 <= max_pr_pages <= _MAX_PR_PAGES:
        raise AlgoraProbeError("max_pr_pages invalid")
    reader = reader if reader is not None else GitHubRestReader(os.environ.get("GITHUB_TOKEN"))
    live = isinstance(reader, GitHubRestReader)
    owner, repo, number = listing["owner"], listing["repo"], listing["number"]
    base = "/repos/" + quote(owner, safe="") + "/" + quote(repo, safe="")
    reasons: list[str] = []
    evidence: dict[str, Any] = {
        "repository": None, "issue": None, "linked_prs": [],
        "pr_pages_scanned": 0, "pr_census_complete": False,
    }
    if listing["listing_state"] != "OPEN":
        reasons.append("MARKETPLACE_NOT_OPEN")

    def obj(value: Any, name: str) -> dict[str, Any]:
        if type(value) is not dict:
            raise AlgoraProbeError(f"GitHub {name} response was not an object")
        return value

    try:
        origin = obj(reader(base), "repo")
        if str(origin.get("full_name", "")).casefold() != f"{owner}/{repo}".casefold():
            raise AlgoraProbeError("GitHub repo identity mismatch")
        if type(origin.get("archived")) is not bool or type(origin.get("fork")) is not bool:
            raise AlgoraProbeError("GitHub archive/fork metadata missing")
        evidence["repository"] = {
            "full_name": origin["full_name"], "archived": origin["archived"],
            "fork": origin["fork"],
        }
        if origin["archived"]:
            reasons.append("REPOSITORY_ARCHIVED")
        if origin["fork"]:
            reasons.append("REPOSITORY_IS_FORK")
        issue = obj(reader(base + "/issues/" + str(number)), "issue")
        if type(issue.get("number")) is not int or issue["number"] != number or "pull_request" in issue:
            raise AlgoraProbeError("GitHub issue identity invalid")
        path = urlsplit(str(issue.get("html_url") or "")).path.rstrip("/").casefold()
        if (issue.get("state") not in ("open", "closed") or
                path != urlsplit(listing["canonical_issue_url"]).path.rstrip("/").casefold()):
            raise AlgoraProbeError("GitHub issue state or URL mismatch")
        assignees = issue.get("assignees", [])
        if type(assignees) is not list:
            raise AlgoraProbeError("GitHub assignees unavailable")
        evidence["issue"] = {
            "number": number, "state": issue["state"], "state_reason": issue.get("state_reason"),
            "assignees": [x.get("login") for x in assignees if type(x) is dict],
        }
        if issue["state"] == "closed":
            reasons.append("CANONICAL_ISSUE_CLOSED")
        if evidence["issue"]["assignees"]:
            reasons.append("EXISTING_ASSIGNEE_REVIEW")
        terminal = {"MARKETPLACE_NOT_OPEN", "REPOSITORY_ARCHIVED",
                    "REPOSITORY_IS_FORK", "CANONICAL_ISSUE_CLOSED"}
        if not terminal.intersection(reasons):
            for page in range(1, max_pr_pages + 1):
                url = base + f"/pulls?state=open&per_page={_PR_PAGE_SIZE}&page={page}"
                prs = reader(url)
                if type(prs) is not list or len(prs) > _PR_PAGE_SIZE:
                    raise AlgoraProbeError("GitHub open-PR page invalid")
                evidence["pr_pages_scanned"] += 1
                for pr in prs:
                    if type(pr) is not dict or type(pr.get("number")) is not int:
                        raise AlgoraProbeError("GitHub open-PR record invalid")
                    if _related(pr, owner, repo, number):
                        evidence["linked_prs"].append({
                            "number": pr["number"], "url": pr.get("html_url"),
                            "author": (pr.get("user") or {}).get("login"),
                        })
                if len(prs) < _PR_PAGE_SIZE:
                    evidence["pr_census_complete"] = True
                    break
            if not evidence["pr_census_complete"]:
                reasons.append("PR_CENSUS_TRUNCATED")
            if evidence["linked_prs"]:
                reasons.append("OPEN_PR_REFERENCE_PRESENT")
    except AlgoraProbeError as exc:
        reasons.append("CANONICAL_READ_FAILED")
        evidence["read_error"] = str(exc)

    if not live:
        reasons.append("FIXTURE_NOT_LIVE_PROVIDER")
    if {"MARKETPLACE_NOT_OPEN", "REPOSITORY_ARCHIVED", "REPOSITORY_IS_FORK",
        "CANONICAL_ISSUE_CLOSED"}.intersection(reasons):
        status = "PRUNE"
    elif reasons:
        status = "HOLD"
    else:
        status = "REVIEW"

    core = {
        "schema": SCHEMA,
        "listing": {key: listing[key] for key in (
            "listing_url", "canonical_issue_url", "listing_state", "advertised_usd"
        )},
        "checked_at": checked_at or datetime.now(timezone.utc).isoformat(),
        "transport": "LIVE_GITHUB_REST" if live else "TEST_INJECTION",
        "canonical": evidence, "reason_codes": sorted(set(reasons)),
        "disposition": status,
        "economics": {
            "marketplace_advertised_usd": listing["advertised_usd"],
            "verified_award_usd": None,
            "verified_received_usd": None,
            "payment_eligibility": "UNVERIFIED",
        },
        "authority": {"claim": False, "build": False, "publish": False, "payment": False},
        "next_action": "Fresh scope/claim/payment review before independent MOVA lease",
    }
    raw_hash = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**core, "receipt_sha256": hashlib.sha256(raw_hash.encode()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical GitHub preflight for one Algora listing")
    parser.add_argument("listing_json")
    args = parser.parse_args()
    try:
        with open(args.listing_json, encoding="utf-8") as fp:
            listing = json.load(fp)
        receipt = inspect_algora_listing(listing)
        print(json.dumps(receipt, sort_keys=True, indent=2))
        return 0 if receipt["disposition"] == "REVIEW" else 2
    except (AlgoraProbeError, OSError, ValueError) as exc:
        print("algora-canonical-probe: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
