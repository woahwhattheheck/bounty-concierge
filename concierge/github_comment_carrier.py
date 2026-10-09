# SPDX-License-Identifier: MIT
"""Reuse a retained issue-comment/PR binding in the existing intake cache.

This command reads operator-provided GitHub snapshots, not live GitHub. Empty
searches are never evidence that no contribution exists. It records positive
carrier observations only; it does not acquire a claim or reserve work.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from .github_intake_cache import GitHubIntakeCache, IntakeCacheError, normalize_repo

_MAX_BYTES = 2 * 1024 * 1024
_ISSUE = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)\Z")
_PULL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)(?=$|[\s)>\],;!]|[.](?=\s|$))")
_SHA = re.compile(r"[0-9a-fA-F]{40,64}\Z")


def _prose(body: str) -> str:
    """Exclude fenced code and quotations from closing-reference authority."""
    lines = []
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        match = re.match(r"(`{3,}|~{3,})", stripped)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None and not stripped.startswith(">") and not line.startswith(("    ", "\t")):
            lines.append(line)
    return "\n".join(lines)


def resolve_comment_carrier(
    capture: dict[str, Any], *, ttl_seconds: int = 1800, now_epoch: float | None = None
) -> dict[str, Any] | None:
    """Return one positively linked open PR, or None; ambiguity is an error.

    Input uses normalized fields from GitHub issue comments and get_pr_info.
    The caller remains responsible for independently obtaining the snapshots.
    """
    if not isinstance(capture, dict):
        raise ValueError("capture must be an object")
    issue_url = capture.get("issue_url")
    match = _ISSUE.fullmatch(issue_url) if isinstance(issue_url, str) else None
    if match is None:
        raise ValueError("issue_url must be a canonical public GitHub issue URL")
    repo = normalize_repo(match.group(1))
    issue = int(match.group(2))
    now = time.time() if now_epoch is None else now_epoch
    observed = capture.get("observed_epoch")
    for value in (now, observed):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("observation and current time must be finite nonnegative numbers")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 60 <= ttl_seconds <= 604800:
        raise ValueError("ttl_seconds must be between 60 and 604800")
    if observed > now or now - observed >= ttl_seconds:
        raise ValueError("capture is future-dated or expired; obtain a fresh readback")
    comments = capture.get("comments")
    pulls = capture.get("pull_requests")
    if not isinstance(comments, list) or not isinstance(pulls, list) or len(comments) > 100 or len(pulls) > 128:
        raise ValueError("capture requires at most 100 comments and 128 PR readbacks")

    # A quoted example or '$70' in a body is not an issue-to-PR relationship.
    target = rf"(?:#{issue}|{re.escape(repo)}#{issue}|{re.escape(issue_url)})"
    closes = re.compile(
        rf"^\s*(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+{target}\s*[.!]?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    linked: dict[int, str] = {}
    for comment in comments:
        if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
            raise ValueError("comment must contain a string body")
        url = comment.get("url")
        if not isinstance(url, str) or re.fullmatch(re.escape(issue_url) + r"#issuecomment-[1-9][0-9]*", url) is None:
            raise ValueError("comment URL must belong to the captured issue")
        for ref in _PULL.finditer(_prose(comment["body"])):
            if normalize_repo(ref.group(1)) == repo:
                linked.setdefault(int(ref.group(2)), url)

    matches: dict[int, dict[str, Any]] = {}
    seen: dict[tuple[str, int], tuple[str, str, str]] = {}
    for pr in pulls:
        if not isinstance(pr, dict) or not isinstance(pr.get("body"), str):
            raise ValueError("PR readback must contain a string body")
        url = pr.get("url")
        ref = _PULL.fullmatch(url) if isinstance(url, str) else None
        number = pr.get("number")
        if ref is None or isinstance(number, bool) or not isinstance(number, int) or number != int(ref.group(2)):
            raise ValueError("PR number must match its canonical URL")
        sha = pr.get("head_sha")
        if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
            raise ValueError("PR head_sha must be a retained hexadecimal commit SHA")
        if pr.get("state") not in {"open", "closed"}:
            raise ValueError("PR readback must retain its open/closed state")
        key = (normalize_repo(ref.group(1)), number)
        snapshot = (pr["state"], sha.casefold(), pr["body"])
        if key in seen and seen[key] != snapshot:
            raise ValueError("conflicting readbacks for the same PR; refresh its state and head")
        seen[key] = snapshot
        if key[0] != repo or number not in linked or pr["state"] != "open":
            continue
        if closes.search(_prose(pr["body"])) is None:
            continue
        result = {
            "repo": repo, "issue": issue, "pr_number": number,
            "head_sha": sha.casefold(), "observed_epoch": float(observed),
            "comment_url": linked[number], "pr_url": url,
        }
        if number in matches and matches[number] != result:
            raise ValueError("conflicting readbacks for the same PR; refresh its head")
        matches[number] = result
    if len(matches) > 1:
        raise ValueError("multiple linked open PRs; inspect the carriers instead of selecting one silently")
    return next(iter(matches.values()), None)


def import_comment_carrier(
    cache: GitHubIntakeCache, capture: dict[str, Any], *, ttl_seconds: int = 1800,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    resolved = resolve_comment_carrier(capture, ttl_seconds=ttl_seconds, now_epoch=now_epoch)
    if resolved is None:
        return {"status": "NO_CONFIRMED_CARRIER", "cache_written": False, "absence_established": False}
    record = cache.put(
        resolved["repo"], resolved["issue"], disposition="CARRIER", source="github",
        reason_code="ISSUE_COMMENT_AND_CLOSING_REFERENCE", pr_number=resolved["pr_number"],
        head_sha=resolved["head_sha"], ttl_seconds=ttl_seconds,
        observed_epoch=resolved["observed_epoch"],
    )
    applied = (
        record.get("observed_epoch") == resolved["observed_epoch"]
        and record.get("pr_number") == resolved["pr_number"]
        and record.get("head_sha") == resolved["head_sha"]
    )
    return {
        "status": "CARRIER_OBSERVED" if applied else "NEWER_OBSERVATION_RETAINED",
        "cache_written": applied, "absence_established": False, "record": record,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="retained normalized GitHub JSON capture (at most 2 MiB)")
    parser.add_argument("--cache-file", default=os.environ.get("CONCIERGE_INTAKE_CACHE"))
    parser.add_argument("--ttl", type=int, default=1800)
    args = parser.parse_args(argv)
    if not args.cache_file:
        parser.error("--cache-file or CONCIERGE_INTAKE_CACHE is required")
    try:
        with args.capture.open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("capture exceeds 2 MiB")
        capture = json.loads(raw)
        result = import_comment_carrier(GitHubIntakeCache(args.cache_file), capture, ttl_seconds=args.ttl)
        print(json.dumps(result, sort_keys=True))
        return 0 if "record" in result else 2
    except (OSError, ValueError, TypeError, IntakeCacheError):
        # Never print comment bodies, provider text, local paths or input excerpts.
        print(json.dumps({"status": "CAPTURE_REJECTED", "cache_written": False, "absence_established": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
