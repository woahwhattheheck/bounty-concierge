# SPDX-License-Identifier: MIT
"""Multi-repo bounty aggregator for RustChain.

Fetches open issues labelled 'bounty' from configured GitHub repositories,
parses reward amounts, estimates difficulty, and tags required skills.
"""

import json
import math
import re
import sys
from datetime import datetime, timezone

import requests

from concierge.config import GITHUB_TOKEN, REPOS


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


def fetch_bounties(repos=None, token=None):
    """Fetch open bounty issues from one or more GitHub repos.

    Args:
        repos:  List of 'owner/repo' strings.  Defaults to config.REPOS.
        token:  GitHub personal-access token.  Defaults to config.GITHUB_TOKEN.

    Returns:
        List of dicts, one per bounty issue, with keys:
            repo, number, title, body, url, labels, created_at, reward_rtc,
            difficulty, skills
    """
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
            try:
                resp = requests.get(api_url, headers=headers, params=params, timeout=15)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
            except requests.RequestException as exc:
                print(f"[warn] failed to fetch {repo}: {exc}", file=sys.stderr)
                break

            try:
                issues = resp.json()
            except ValueError as exc:
                print(f"[warn] failed to decode {repo}: {exc}", file=sys.stderr)
                break
            if not isinstance(issues, list):
                print(f"[warn] unsupported payload for {repo}: expected list", file=sys.stderr)
                break

            for issue in issues:
                normalized = _normalize_issue_row(issue)
                if normalized is None:
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
    """Estimate bounty difficulty tier from reward size and labels.

    Tiers:
        micro     --  < 10 RTC
        standard  --  10-50 RTC
        major     --  50-200 RTC
        critical  --  200+ RTC

    Labels named 'critical', 'major', 'micro', or 'standard' override the
    reward-based estimate.
    """
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
    """Return whether *keyword* occurs as a complete token or phrase.

    Word guards are applied only when the corresponding keyword edge is a word
    character.  That prevents short tags such as ``rust`` and ``node`` from
    matching unrelated words while preserving punctuation-leading file suffix
    keywords such as ``.py`` and punctuation-bearing labels such as ``CI/CD``.
    """
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
    """Return a list of skill tags relevant to this bounty.

    Scans title and body for complete keyword/phrase matches.
    """
    combined = f"{title} {body}"
    matched = []
    for skill, keywords in _SKILL_KEYWORDS.items():
        for kw in keywords:
            if _keyword_matches(combined, kw):
                matched.append(skill)
                break
    return sorted(matched)


# ---------------------------------------------------------------------------
# Aggregation & formatting
# ---------------------------------------------------------------------------

def aggregate(repos=None, token=None):
    """Fetch, enrich, sort, and return all bounties as a summary dict.

    Returns:
        {
            "updated_at": ISO-8601 timestamp,
            "total_count": int,
            "bounties": [sorted list, highest RTC first],
        }
    """
    bounties = fetch_bounties(repos, token)
    bounties.sort(key=lambda b: b["reward_rtc"], reverse=True)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(bounties),
        "bounties": bounties,
    }


def _markdown_cell(value):
    """Escape untrusted text so it cannot create Markdown table cells or rows."""
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return re.sub(r"[\r\n]+", " ", text)


def format_markdown(bounties):
    """Format a list of bounty dicts as a Markdown table.

    Columns: #, Repo, Title, RTC, Tier, Skills
    """
    lines = [
        "| # | Repo | Title | RTC | Tier | Skills |",
        "|---|------|-------|-----|------|--------|",
    ]
    for b in bounties:
        repo_short = b["repo"].split("/")[-1]
        skills = ", ".join(b["skills"]) if b["skills"] else "-"
        title_short = _markdown_cell(b["title"][:60])
        lines.append(
            f"| {b['number']} | {repo_short} | {title_short} | "
            f"{b['reward_rtc']:.1f} | {b['difficulty']} | {skills} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    data = aggregate()
    print(json.dumps(data, indent=2, default=str))
