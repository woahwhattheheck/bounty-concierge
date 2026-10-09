# SPDX-License-Identifier: MIT
"""Quota-bounded live GitHub issue checks for BountyHub funding shortlists.

The BountyHub listing alone is not a live canonical issue, claim, or payment.
Historical owner claims are never removed or withdrawn by this read-only tool.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

import requests

SCHEMA = "bountyhub-canonical-probe/v1"
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _targets(selected):
    if type(selected) is not dict or type(selected.get("targets")) is not list:
        raise ValueError("input must be a BountyHub funded targets shortlist")
    if not 1 <= len(selected["targets"]) <= 100:
        raise ValueError("select between 1 and 100 canonical targets")
    found, rows = set(), []
    for row in selected["targets"]:
        if type(row) is not dict:
            raise ValueError("target must be an object")
        repo, number = row.get("repo"), row.get("number")
        if type(repo) is not str or REPO.fullmatch(repo) is None or type(number) is not int or number <= 0:
            raise ValueError("invalid canonical issue identity")
        key = repo.casefold(), number
        if key in found:
            raise ValueError("duplicate GitHub issue in BountyHub shortlist")
        found.add(key)
        rows.append({"repo": repo, "number": number,
                     "issue_url": f"https://github.com/{repo}/issues/{number}"})
    return rows


def _probe(session, row, github_token):
    url = f"https://api.github.com/repos/{row['repo']}/issues/{row['number']}"
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if github_token:
        headers["Authorization"] = "Bearer " + github_token
    result = {"source_url": url, "state": None, "http_status": None,
              "disposition": "HOLD", "reason": "SOURCE_NOT_VERIFIED",
              "rate_limited": False, "rate_remaining": None}
    try:
        response = session.get(url, headers=headers, timeout=12, allow_redirects=False)
    except requests.RequestException:
        result["reason"] = "GITHUB_TRANSPORT_ERROR"
        return result
    try:
        status = response.status_code
        result["http_status"] = status
        remaining = response.headers.get("X-RateLimit-Remaining")
        if isinstance(remaining, str) and remaining.isascii() and remaining.isdigit():
            result["rate_remaining"] = int(remaining)
        if status == 200:
            try:
                if len(response.content) > 1000000:
                    raise ValueError("oversized response")
                body = response.json()
                if type(body) is not dict or type(body.get("number")) is not int:
                    raise ValueError("bad issue")
                if body["number"] != row["number"] or type(body.get("html_url")) is not str:
                    raise ValueError("identity mismatch")
                if body["html_url"].rstrip("/").casefold() != row["issue_url"].casefold():
                    result["reason"] = "CANONICAL_ISSUE_MOVED"
                elif "pull_request" in body:
                    result["reason"] = "SOURCE_IS_PULL_REQUEST"
                elif body.get("state") == "closed":
                    result.update(state="closed", disposition="PRUNE_NEW_BUILD",
                                  reason="CANONICAL_ISSUE_CLOSED")
                elif body.get("state") == "open":
                    result.update(state="open", disposition="PROCEED_TO_PREFLIGHT",
                                  reason="CANONICAL_ISSUE_OPEN")
                else:
                    result["reason"] = "CANONICAL_STATE_UNKNOWN"
            except (ValueError, TypeError):
                result["reason"] = "CANONICAL_PAYLOAD_INVALID"
        elif status in (404, 410):
            result["reason"] = "CANONICAL_MISSING_OR_INACCESSIBLE"
        elif status in (301, 302, 307, 308):
            result["reason"] = "CANONICAL_ISSUE_MOVED"
        elif status in (403, 429):
            result["reason"] = "GITHUB_QUOTA_OR_PERMISSION_BLOCK"
            result["rate_limited"] = status == 429 or result["rate_remaining"] == 0 or bool(response.headers.get("Retry-After"))
        else:
            result["reason"] = "GITHUB_HTTP_UNAVAILABLE"
        return result
    finally:
        try:
            response.close()
        except Exception:
            pass


def check_shortlist(selected, *, max_requests=5, session=None, github_token=None):
    """Fail closed. OPEN merely permits further eligibility/claim/award preflight."""
    if type(max_requests) is not int or not 0 <= max_requests <= 100:
        raise ValueError("max_requests must be 0..100")
    if github_token is not None and (type(github_token) is not str or not github_token or any(
        c in github_token for c in "\r\n"
    )):
        raise ValueError("invalid GitHub token")
    rows = _targets(selected)
    report = {"schema": SCHEMA, "observed_at": _now(), "request_limit": max_requests,
              "request_count": 0, "rate_limited": False, "stopped_early": False,
              "records": [], "cash_proof": False, "claim_authority": False,
              "build_authority": False}
    with requests.Session() if session is None and max_requests else nullcontext(session) as client:
        stopped = False
        for row in rows:
            result = {**row, "checked_at": None, "state": None, "http_status": None,
                      "disposition": "HOLD", "reason": "NOT_CHECKED"}
            if not stopped and report["request_count"] < max_requests:
                report["request_count"] += 1
                result["checked_at"] = _now()
                fetched = _probe(client, row, github_token)
                result.update(fetched)
                if fetched["reason"] in {"GITHUB_QUOTA_OR_PERMISSION_BLOCK", "GITHUB_TRANSPORT_ERROR"}:
                    report["rate_limited"] = bool(fetched["rate_limited"])
                    report["stopped_early"] = True
                    stopped = True
            report["records"].append(result)
    report["open_count"] = sum(r["state"] == "open" for r in report["records"])
    report["closed_count"] = sum(r["state"] == "closed" for r in report["records"])
    report["unresolved_count"] = sum(r["disposition"] == "HOLD" for r in report["records"])
    report["receipt_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("shortlist", type=Path)
    parser.add_argument("--max-requests", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.shortlist.read_text(encoding="utf-8"))
        result = check_shortlist(data, max_requests=args.max_requests,
                                 github_token=os.environ.get("GITHUB_TOKEN") or None)
        output = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (OSError, ValueError) as exc:
        parser.exit(2, "HOLD: " + str(exc) + "\n")
    return 2 if result["rate_limited"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
