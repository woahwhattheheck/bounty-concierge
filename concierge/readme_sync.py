# SPDX-License-Identifier: MIT
"""Refresh the Open Bounties table in README.md from data/bounty_index.json.

The bounty_index_sync workflow writes ``data/bounty_index.json`` once a day.
This module rewrites the table from the JSON between sentinel markers. The
authoritative workflow enables the freshness gate so an inherited or stale
snapshot cannot be presented as current bounty inventory.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import quote

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
INDEX_PATH = REPO_ROOT / "data" / "bounty_index.json"

START_MARKER = "<!-- BOUNTY-TABLE-START -->"
END_MARKER = "<!-- BOUNTY-TABLE-END -->"

DEFAULT_TOP_N = 10
MAX_INDEX_AGE = timedelta(hours=36)
MAX_FUTURE_SKEW = timedelta(minutes=5)

_MARKDOWN_LINK_SAFE = ":/?#[]@!$&'*+,;=%-._~"


def _format_int(n: float) -> str:
    """Render an RTC reward without trailing .0."""
    if n == int(n):
        return str(int(n))
    return f"{n:.1f}"


def _single_line_text(value) -> str:
    """Render untrusted text without raw line or control characters."""
    escaped: list[str] = []
    for char in str(value):
        if char.isprintable():
            escaped.append(char)
            continue
        codepoint = ord(char)
        if codepoint <= 0xFF:
            escaped.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(f"\\U{codepoint:08x}")
    return "".join(escaped)


def _markdown_cell(value) -> str:
    """Escape untrusted text for one Markdown table cell."""
    text = _single_line_text(value)
    escaped: list[str] = []
    backslash_run = 0
    for char in text:
        if char == "|":
            escaped.append("\\" if backslash_run % 2 == 0 else "\\\\")
            escaped.append("|")
            backslash_run = 0
            continue
        escaped.append(char)
        if char == "\\":
            backslash_run += 1
        else:
            backslash_run = 0
    return "".join(escaped)


def _markdown_link_target(value) -> str:
    """Percent-encode characters that can escape an inline link target."""
    return quote(str(value), safe=_MARKDOWN_LINK_SAFE)


def _validated_bounty_rows(bounties: Iterable[dict]) -> list[dict]:
    """Return renderer-safe rows or fail closed on malformed index data."""
    try:
        rows = list(bounties)
    except TypeError as exc:
        raise ValueError("bounties must be an iterable of objects") from exc

    for index, bounty in enumerate(rows):
        if not isinstance(bounty, dict):
            raise ValueError(f"bounties[{index}] must be an object")

        reward = bounty.get("reward_rtc")
        if reward is not None and (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or (isinstance(reward, float) and not math.isfinite(reward))
        ):
            raise ValueError(f"bounties[{index}].reward_rtc must be a finite number")

        number = bounty.get("number")
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int)
        ):
            raise ValueError(f"bounties[{index}].number must be an integer")

        for field in ("repo", "title", "url", "difficulty"):
            value = bounty.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"bounties[{index}].{field} must be a string")

        skills = bounty.get("skills")
        if skills is not None and (
            not isinstance(skills, list)
            or any(not isinstance(skill, str) for skill in skills)
        ):
            raise ValueError(f"bounties[{index}].skills must be a list of strings")

    return rows


def render_table(bounties: Iterable[dict], top_n: int = DEFAULT_TOP_N) -> str:
    """Return a markdown table from a sequence of bounty dicts."""
    if top_n < 0:
        raise ValueError("top_n must be non-negative")
    validated = _validated_bounty_rows(bounties)
    sorted_bounties = sorted(
        validated,
        key=lambda b: (-(b.get("reward_rtc") or 0), b.get("number") or 0),
    )
    rows = sorted_bounties[:top_n]

    lines = [
        "| Repo | Issue | Title | RTC | Difficulty | Skills |",
        "|------|-------|-------|-----|------------|--------|",
    ]
    for b in rows:
        repo = b.get("repo") or ""
        repo_short = _markdown_cell(repo.split("/")[-1])
        issue_num = b.get("number", "?")
        issue_label = _markdown_cell(issue_num)
        url = b.get("url") or f"https://github.com/{repo}/issues/{issue_num}"
        url = _markdown_link_target(url)
        title = _markdown_cell((b.get("title") or "").strip())
        if len(title) > 60:
            title = title[:57] + "..."
        rtc = _format_int(b.get("reward_rtc") or 0)
        diff = _markdown_cell(b.get("difficulty") or "unknown")
        skills = _markdown_cell(", ".join(b.get("skills") or []) or "-")
        lines.append(
            f"| {repo_short} | "
            f"[#{issue_label}]({url}) | "
            f"{title} | "
            f"{rtc} | {diff} | {skills} |"
        )
    return "\n".join(lines)


def _parse_index_timestamp(value) -> datetime:
    """Parse an explicit UTC index timestamp or reject it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("bounty index 'updated_at' must be a non-empty UTC timestamp")
    raw = value.strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("bounty index 'updated_at' must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("bounty index 'updated_at' must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


def _normalize_now(now: datetime | None) -> datetime:
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("freshness reference time must be timezone-aware UTC")
    return now.astimezone(timezone.utc)


def build_section(
    top_n: int = DEFAULT_TOP_N,
    *,
    require_fresh: bool = False,
    now: datetime | None = None,
) -> str:
    """Build the generated README section.

    When ``require_fresh`` is true, malformed/future timestamps fail closed and
    snapshots older than ``MAX_INDEX_AGE`` render only a stale warning rather
    than bounty rows. Direct library callers retain the historical behavior
    unless they explicitly request freshness; the canonical workflow does.
    """
    if top_n < 0:
        raise ValueError("top_n must be non-negative")
    payload = json.loads(INDEX_PATH.read_text())
    if not isinstance(payload, dict):
        raise ValueError("bounty index root must be an object")
    bounties = payload.get("bounties")
    if bounties is None:
        bounties = []
    elif not isinstance(bounties, list):
        raise ValueError("bounty index 'bounties' must be a list")
    bounties = _validated_bounty_rows(bounties)

    updated_raw = payload.get("updated_at", "unknown")
    updated = _single_line_text(updated_raw)

    if require_fresh:
        updated_at = _parse_index_timestamp(updated_raw)
        reference = _normalize_now(now)
        if updated_at > reference + MAX_FUTURE_SKEW:
            raise ValueError("bounty index 'updated_at' is implausibly in the future")
        if reference - updated_at > MAX_INDEX_AGE:
            return (
                "**WARNING: Cached bounty index is stale and is not shown as current.** "
                f"Last successful rebuild: {updated}. "
                "Use `concierge browse` or the live bounty-board link above before "
                "selecting or claiming paid work."
            )

    table = render_table(bounties, top_n=top_n)
    header = (
        f"_Showing top {min(top_n, len(bounties))} open bounties, "
        f"sorted by RTC reward. Index rebuilt {updated}. "
        "For the live total, use the full bounty board link above._"
    )
    return f"{header}\n\n{table}"


def update_readme(readme_text: str, section: str) -> str:
    """Replace the contents between the unique sentinels with ``section``."""
    start_count = readme_text.count(START_MARKER)
    end_count = readme_text.count(END_MARKER)
    if start_count == 0 or end_count == 0:
        raise ValueError(
            f"README is missing the {START_MARKER} / {END_MARKER} sentinels. "
            "Add them around the Open Bounties table."
        )
    if start_count != 1 or end_count != 1:
        raise ValueError("README must contain exactly one Open Bounties sentinel pair.")
    if readme_text.index(START_MARKER) > readme_text.index(END_MARKER):
        raise ValueError("README Open Bounties sentinels are out of order.")
    if START_MARKER in section or END_MARKER in section:
        raise ValueError("Generated README section must not contain Open Bounties sentinels.")

    pattern = re.compile(
        re.escape(START_MARKER) + r"(.*?)" + re.escape(END_MARKER),
        re.DOTALL,
    )
    new_block = f"{START_MARKER}\n{section}\n{END_MARKER}"
    return pattern.sub(lambda _match: new_block, readme_text, count=1)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    top_n = DEFAULT_TOP_N
    if "--top" in argv:
        i = argv.index("--top")
        try:
            top_n = int(argv[i + 1])
            if top_n < 0:
                raise ValueError
        except (ValueError, IndexError):
            print("error: --top expects a non-negative integer", file=sys.stderr)
            return 2

    allowed = {"--require-fresh"}
    consumed = set()
    if "--top" in argv:
        i = argv.index("--top")
        consumed.update(argv[i:i + 2])
    consumed.update(arg for arg in argv if arg in allowed)
    unknown = [arg for arg in argv if arg not in consumed]
    if unknown:
        print(f"error: unrecognized argument: {unknown[0]}", file=sys.stderr)
        return 2

    if not INDEX_PATH.exists():
        print(f"error: {INDEX_PATH} not found", file=sys.stderr)
        return 1

    try:
        section = build_section(
            top_n=top_n,
            require_fresh="--require-fresh" in argv,
        )
    except (OSError, ValueError) as exc:
        print(f"error: failed to build section: {exc}", file=sys.stderr)
        return 1

    try:
        original = README_PATH.read_text()
        updated = update_readme(original, section)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if updated != original:
        README_PATH.write_text(updated)
        print(f"updated {README_PATH}")
    else:
        print(f"{README_PATH} already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
