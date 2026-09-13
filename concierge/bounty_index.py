# SPDX-License-Identifier: MIT
"""Multi-repo bounty aggregator for RustChain.

Fetches open issues labelled 'bounty' from configured GitHub repositories,
parses reward amounts, estimates difficulty, and tags required skills.
"""

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone

import requests

from concierge.config import GITHUB_TOKEN, REPOS


class BountyIndexIncompleteError(RuntimeError):
    """Raised when an authoritative bounty-index build cannot prove completeness."""


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _normalize_issue_row(issue):
    """Return parser-safe issue fields, or None for an unsupported API row."""
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None

    number = issue.get("number")
    title = issue.get("title", "")
    body = issue.get("body", "")
    url = issue.get("html_url")
    labels = issue.get("labels", [])
    created_at = issue.get("created_at", "")

    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    if not isinstance(title, str):
        return None
    if body is None:
        body = ""
    elif not isinstance(body, str):
        return None
    if not isinstance(url, str) or not url:
        return None
    if not isinstance(labels, list):
        return None
    if created_at is None:
        created_at = ""
    elif not isinstance(created_at, str):
        return None

    label_names = []
    for label in labels:
        if not isinstance(label, dict):
            return None
        name = label.get("name")
        if not isinstance(name, str):
            return None
        label_names.append(name)

    return {
        "number": number,
        "title": title,
        "body": body,
        "url": url,
        "labels": label_names,
        "created_at": created_at,
    }


def _source_failure(message, *, strict, cause=None):
    """Warn in salvage mode; raise in authoritative mode."""
    if strict:
        if cause is None:
            raise BountyIndexIncompleteError(message)
        raise BountyIndexIncompleteError(message) from cause
    print(f"[warn] {message}", file=sys.stderr)


def _fetch_bounties(repos=None, token=None, *, strict=False):
    """Fetch bounties, optionally failing closed on any source incompleteness."""
    if repos is None:
        repos = REPOS
    token = token or GITHUB_TOKEN

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    bounties = []
    for repo in repos:
        api_url = f"https://api.github.com/repos/{repo}/issues"
        params = {"labels": "bounty", "state": "open", "per_page": 100, "page": 1}

        while True:
            page = params["page"]
            try:
                resp = requests.get(api_url, headers=headers, params=params, timeout=15)
                if resp.status_code == 404:
                    _source_failure(
                        f"configured bounty source {repo} page {page} returned 404",
                        strict=strict,
                    )
                    break
                resp.raise_for_status()
            except requests.RequestException as exc:
                _source_failure(
                    f"failed to fetch {repo} page {page}: {exc}",
                    strict=strict,
                    cause=exc,
                )
                break

            try:
                issues = resp.json()
            except ValueError as exc:
                _source_failure(
                    f"failed to decode {repo} page {page}: {exc}",
                    strict=strict,
                    cause=exc,
                )
                break
            if not isinstance(issues, list):
                _source_failure(
                    f"unsupported payload for {repo} page {page}: expected list",
                    strict=strict,
                )
                break

            for item_index, issue in enumerate(issues):
                if isinstance(issue, dict) and "pull_request" in issue:
                    continue
                normalized = _normalize_issue_row(issue)
                if normalized is None:
                    _source_failure(
                        f"unsupported bounty row for {repo} page {page} item {item_index}",
                        strict=strict,
                    )
                    continue

                title = normalized["title"]
                body = normalized["body"]
                label_names = normalized["labels"]

                reward = parse_reward(title, body)
                difficulty = estimate_difficulty(title, label_names, reward)
                skills = tag_skills(title, body)

                bounties.append({
                    "repo": repo,
                    "number": normalized["number"],
                    "title": title,
                    "body": body,
                    "url": normalized["url"],
                    "labels": label_names,
                    "created_at": normalized["created_at"],
                    "reward_rtc": reward,
                    "difficulty": difficulty,
                    "skills": skills,
                })

            if not getattr(resp, "links", {}).get("next"):
                break
            params["page"] += 1

    return bounties


def fetch_bounties(repos=None, token=None):
    """Best-effort bounty discovery for interactive/diagnostic callers.

    Source failures are warned and skipped so existing callers can salvage
    reachable repositories. Canonical publication uses :func:`aggregate`, which
    deliberately runs the same fetch in strict mode and refuses partial data.
    """
    return _fetch_bounties(repos, token, strict=False)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_RTC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.,])"
    r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*RTC\b",
    re.IGNORECASE,
)


def parse_reward(title, body):
    """Extract the first finite RTC reward amount from title or body text.

    Looks for patterns like '150 RTC', '1,000 RTC', '1,000,000 RTC',
    '1,234.5 RTC', and '0.5 RTC'.  Commas are accepted only as canonical
    three-digit thousands separators.  Returns the amount as a finite float,
    or 0.0 if nothing valid is found.
    """
    for text in (title, body):
        for match in _RTC_PATTERN.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            if math.isfinite(value):
                return value
    return 0.0


def estimate_difficulty(title, labels, reward):
    """Estimate bounty difficulty tier from reward size and labels."""
    label_lower = [lb.lower() for lb in labels]
    for tier in ("critical", "major", "standard", "micro"):
        if tier in label_lower:
            return tier

    if reward >= 200:
        return "critical"
    if reward >= 50:
        return "major"
    if reward >= 10:
        return "standard"
    return "micro"


_SKILL_KEYWORDS = {
    "python": ["python", ".py", "flask", "django", "pip"],
    "rust": ["rust", "cargo", "rustc", ".rs"],
    "javascript": ["javascript", "node", "npm", "react", "svelte", "typescript", ".js", ".ts"],
    "docker": ["docker", "container", "dockerfile", "compose"],
    "ci/cd": ["ci/cd", "github actions", "workflow", "pipeline", "ci cd"],
    "documentation": ["documentation", "docs", "readme", "write-up", "writeup"],
    "security": ["security", "audit", "vulnerability", "red team", "penetration"],
    "social-media": ["social", "twitter", "moltbook", "bottube", "youtube", "dev.to"],
    "translation": ["translation", "translate", "i18n", "localization"],
}


def _keyword_matches(text, keyword):
    """Return whether *keyword* occurs as a complete token or phrase."""
    if not isinstance(keyword, str) or not keyword.strip():
        return False

    keyword = keyword.casefold()
    pattern = re.escape(keyword)
    if re.match(r"\w", keyword[0]):
        pattern = rf"(?<!\w){pattern}"
    if re.match(r"\w", keyword[-1]):
        pattern = rf"{pattern}(?!\w)"
    return re.search(pattern, text.casefold()) is not None


def tag_skills(title, body):
    """Return a sorted list of skill tags relevant to this bounty."""
    combined = f"{title} {body}"
    matched = []
    for skill, keywords in _SKILL_KEYWORDS.items():
        for kw in keywords:
            if _keyword_matches(combined, kw):
                matched.append(skill)
                break
    return sorted(matched)


# ---------------------------------------------------------------------------
# Aggregation, publication & formatting
# ---------------------------------------------------------------------------

def aggregate(repos=None, token=None):
    """Build an authoritative complete bounty index.

    Unlike :func:`fetch_bounties`, canonical aggregation fails closed if any
    configured repository/page cannot be fetched or decoded, or if a non-PR
    issue row has an unsupported shape. An explicit ``repos=[]`` is therefore a
    complete empty inventory, while an unreachable configured source is not.
    """
    bounties = _fetch_bounties(repos, token, strict=True)
    bounties.sort(key=lambda b: b["reward_rtc"], reverse=True)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(bounties),
        "bounties": bounties,
    }


def render_index(data):
    """Serialize one authoritative index deterministically for publication."""
    return json.dumps(data, indent=2, default=str) + "\n"


def write_index_atomic(output_path, repos=None, token=None):
    """Build then atomically replace *output_path*, preserving prior-good data.

    All network/schema validation completes before a temporary file is opened.
    The temporary file is created beside the target, flushed and fsynced, then
    atomically replaced. Any failure before ``os.replace`` leaves the previous
    published index untouched and removes temporary residue.
    """
    data = aggregate(repos=repos, token=token)
    payload = render_index(data)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        replaced = True
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if not replaced:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
    return data


def _markdown_cell(value):
    """Escape untrusted text so it cannot create Markdown table cells or rows."""
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return re.sub(r"[\r\n]+", " ", text)


def format_markdown(bounties):
    """Format a list of bounty dicts as a Markdown table."""
    lines = [
        "| # | Repo | Title | RTC | Tier | Skills |",
        "|---|------|-------|-----|------|--------|",
    ]
    for b in bounties:
        repo_short = _markdown_cell(b["repo"].split("/")[-1])
        skill_text = ", ".join(b["skills"]) if b["skills"] else "-"
        skills = _markdown_cell(skill_text)
        difficulty = _markdown_cell(b["difficulty"])
        title_short = _markdown_cell(b["title"][:60])
        lines.append(
            f"| {b['number']} | {repo_short} | {title_short} | "
            f"{b['reward_rtc']:.1f} | {difficulty} | {skills} |"
        )
    return "\n".join(lines)


def main(argv=None):
    """CLI entry point. Stdout remains available; --output is atomic."""
    parser = argparse.ArgumentParser(description="Build the canonical bounty index")
    parser.add_argument(
        "--output",
        help="atomically replace this file only after a complete successful build",
    )
    args = parser.parse_args(argv)

    try:
        if args.output:
            write_index_atomic(args.output)
        else:
            print(render_index(aggregate()), end="")
    except BountyIndexIncompleteError as exc:
        print(f"[error] bounty index incomplete: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
