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
from concierge.reward_evidence import extract_reward_evidence


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


class BountyFetchIncompleteError(RuntimeError):
    """A live read is incomplete; ``report`` retains explicit partial results."""

    def __init__(self, report):
        self.report = report
        failed = [row for row in report["repositories"] if row["status"] != "COMPLETE"]
        detail = "; ".join(f"{row['repo']}: {row['status']}" for row in failed[:6])
        if len(failed) > 6:
            detail += f"; {len(failed) - 6} more sources incomplete"
        super().__init__(
            f"Bounty sources incomplete ({len(report['bounties'])} partial rows, "
            f"not a complete or empty queue). {detail}. "
            "Use fetch_bounties_report() to inspect partial results."
        )


def _header_integer(headers, name):
    value = str(headers.get(name, ""))
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 12 else None


def fetch_bounties_report(repos=None, token=None, *, max_pages=100):
    """Read live sources once, retaining completion and safe error information.

    No retries or sleeps are performed. A rate-limit response, or exhausted
    successful-response quota, defers all remaining requests. Ordinary source
    failures do not hide successful reads from other repositories. ``complete``
    means every requested source traversed its returned pagination; it is not
    an atomic GitHub snapshot, bounty eligibility, or payment evidence.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 1000:
        raise ValueError("max_pages must be an integer between 1 and 1000")
    configured = REPOS if repos is None else repos
    if isinstance(configured, (str, bytes)):
        raise ValueError("repos must be a collection of owner/repo strings")
    sources = []
    seen_repos = set()
    for repo in configured:
        if (not isinstance(repo, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+", repo) is None
                or repo.split("/")[1] in (".", "..")):
            raise ValueError("each source must be an owner/repo name")
        if repo.casefold() not in seen_repos:
            seen_repos.add(repo.casefold())
            sources.append({"repo": repo, "status": "NOT_ATTEMPTED", "pages_fetched": 0,
                            "bounty_count": 0, "http_status": None})
    if not sources:
        raise ValueError("at least one bounty source is required")

    headers = {"Accept": "application/vnd.github+json"}
    token = token or GITHUB_TOKEN
    if token:
        headers["Authorization"] = f"Bearer {token}"
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "complete": False,
              "rate_limited": False, "retry_after_seconds": None,
              "rate_limit_reset_at": None, "repositories": sources, "bounties": []}

    for source in sources:
        if report["rate_limited"]:
            source["status"] = "NOT_ATTEMPTED_RATE_LIMIT"
            continue
        repo = source["repo"]
        seen_numbers = set()
        api_url = f"https://api.github.com/repos/{repo}/issues"
        for page in range(1, max_pages + 1):
            params = {"labels": "bounty", "state": "open", "per_page": 100, "page": page}
            try:
                response = requests.get(api_url, headers=headers, params=params, timeout=15)
            except requests.RequestException as exc:
                source["status"] = "TRANSPORT_ERROR"
                # Exception text may contain request details. Retain only its type.
                source["error_type"] = type(exc).__name__
                break

            try:
                status = response.status_code
                source["http_status"] = status
                remaining = _header_integer(response.headers, "X-RateLimit-Remaining")
                retry_after = _header_integer(response.headers, "Retry-After")
                reset_at = _header_integer(response.headers, "X-RateLimit-Reset")
                throttled = status == 429 or (status == 403 and
                            (remaining == 0 or retry_after is not None))
                if status == 403 and not throttled:
                    try:
                        error = response.json()
                    except ValueError:
                        error = {}
                    message = error.get("message", "") if isinstance(error, dict) else ""
                    throttled = isinstance(message, str) and "rate limit" in message.casefold()
                if throttled:
                    report.update(rate_limited=True, retry_after_seconds=retry_after,
                                  rate_limit_reset_at=reset_at)
                    source["status"] = "RATE_LIMITED"
                    break
                if status != 200:
                    source["status"] = "HTTP_ERROR"
                    break
                try:
                    issues = response.json()
                except ValueError:
                    source["status"] = "INVALID_JSON"
                    break
                if not isinstance(issues, list):
                    source["status"] = "INVALID_PAYLOAD"
                    break
                source["pages_fetched"] += 1
                source["status"] = "READING"
                for item_index, issue in enumerate(issues):
                    if isinstance(issue, dict) and "pull_request" in issue:
                        continue
                    normalized = _normalize_issue_row(issue)
                    if normalized is None:
                        source.update(status="INVALID_ISSUE", failed_item=item_index)
                        break
                    number = normalized["number"]
                    if number in seen_numbers:
                        source.update(status="REPEATED_ISSUE", failed_item=item_index)
                        break
                    seen_numbers.add(number)
                    title, body, labels = normalized["title"], normalized["body"], normalized["labels"]
                    reward = parse_reward(title, body)
                    reward_evidence = extract_reward_evidence(title, body)
                    report["bounties"].append({
                        "repo": repo, **normalized, "reward_rtc": reward,
                        "difficulty": estimate_difficulty(title, labels, reward),
                        "skills": tag_skills(title, body),
                        "reward_evidence": reward_evidence,
                    })
                    source["bounty_count"] += 1
                if remaining == 0:
                    report.update(rate_limited=True, retry_after_seconds=retry_after,
                                  rate_limit_reset_at=reset_at)
                if source["status"] != "READING":
                    break
                if not response.links.get("next"):
                    source["status"] = "COMPLETE"
                    break
                if report["rate_limited"]:
                    source["status"] = "RATE_LIMITED_BEFORE_NEXT_PAGE"
                    break
                if page == max_pages:
                    source["status"] = "PAGE_LIMIT"
            finally:
                response.close()

    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    report["complete"] = all(row["status"] == "COMPLETE" for row in sources)
    report["total_count"] = len(report["bounties"])
    return report


def fetch_bounties(repos=None, token=None):
    """Return the familiar bounty list only after all sources finish.

    Incomplete reads raise BountyFetchIncompleteError rather than impersonating
    an empty/full queue. The existing browse CLI handles this as a nonzero exit.
    Call fetch_bounties_report() for deliberately partial diagnostic results.
    """
    report = fetch_bounties_report(repos=repos, token=token)
    if not report["complete"]:
        raise BountyFetchIncompleteError(report)
    return report["bounties"]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_RTC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.,])"
    r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"[ \t]*RTC\b",
    re.IGNORECASE,
)


def parse_reward(title, body):
    """Extract the first finite RTC reward amount from title or body text.

    Looks for patterns like '150 RTC', '1,000 RTC', '1,000,000 RTC',
    '1,234.5 RTC', and '0.5 RTC'.  Amount and RTC must remain on the same
    logical line; horizontal spaces/tabs are accepted between them.  Commas are
    accepted only as canonical three-digit thousands separators.  Returns the amount as a finite float,
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
    """Return a complete sorted index, with explicit source traversal metadata."""
    report = fetch_bounties_report(repos=repos, token=token)
    if not report["complete"]:
        raise BountyFetchIncompleteError(report)
    report["bounties"].sort(key=lambda b: b["reward_rtc"], reverse=True)
    return report


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


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        data = aggregate()
    except (BountyFetchIncompleteError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(data, indent=2, default=str))
