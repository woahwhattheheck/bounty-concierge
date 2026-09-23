# SPDX-License-Identifier: MIT
"""Publish the cached bounty-discovery section in README.md.

The existing bounty_index_sync workflow supplies data/bounty_index.json and
opts into freshness enforcement. A fresh cache is not sponsor acceptance,
claim availability, per-claim compensation, or evidence of our payment route.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import stat
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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


def _format_int(n: int | float | Decimal | None) -> str:
    """Keep the indexed numeric value; never turn unknown into zero."""
    if n is None:
        return "unknown"
    value = n if isinstance(n, Decimal) else Decimal(str(n))
    # Scientific notation avoids expanding extreme exponents into huge cells.
    if value and abs(value.adjusted()) > 32:
        return str(value)
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _single_line_text(value) -> str:
    """Render text without raw line or control characters."""
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
    """Keep indexed text literal inside a Markdown table cell."""
    escaped: list[str] = []
    for char in _single_line_text(value):
        if char in "\\|[]*_`":
            escaped.append("\\" + char)
        elif char == "&":
            escaped.append("&amp;")
        elif char == "<":
            escaped.append("&lt;")
        elif char == ">":
            escaped.append("&gt;")
        else:
            escaped.append(char)
    return "".join(escaped)


def _markdown_link_target(value) -> str:
    """Percent-encode characters that can escape an inline link target."""
    return quote(str(value), safe=_MARKDOWN_LINK_SAFE)


def _validate_top_n(top_n: int) -> None:
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 0:
        raise ValueError("top_n must be a non-negative integer")


def _validated_bounty_rows(bounties: Iterable[dict]) -> list[dict]:
    """Return renderer-safe rows without replacing absent values with zero."""
    try:
        rows = list(bounties)
    except TypeError as exc:
        raise ValueError("bounties must be an iterable of objects") from exc
    identities: set[tuple[str, int]] = set()
    for index, bounty in enumerate(rows):
        if not isinstance(bounty, dict):
            raise ValueError(f"bounties[{index}] must be an object")
        reward = bounty.get("reward_rtc")
        if reward is not None and (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float, Decimal))
            or (isinstance(reward, float) and not math.isfinite(reward))
            or (isinstance(reward, Decimal) and not reward.is_finite())
        ):
            raise ValueError(f"bounties[{index}].reward_rtc must be a finite number")
        if reward is not None and reward < 0:
            raise ValueError(f"bounties[{index}].reward_rtc must not be negative")
        number = bounty.get("number")
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int) or number <= 0
        ):
            raise ValueError(f"bounties[{index}].number must be a positive integer")
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
        repo = bounty.get("repo")
        if repo and number is not None:
            identity = (repo.casefold(), number)
            if identity in identities:
                raise ValueError(f"bounties[{index}] repeats issue {repo}#{number}")
            identities.add(identity)
    return rows


def render_table(bounties: Iterable[dict], top_n: int = DEFAULT_TOP_N) -> str:
    """Render a stable discovery table; indexed rewards are not promised pay."""
    _validate_top_n(top_n)
    validated = _validated_bounty_rows(bounties)
    # Stable two-pass sorting needs no Decimal arithmetic or context rounding.
    ordered = sorted(
        validated,
        key=lambda b: ((b.get("repo") or "").casefold(), b.get("number") or 0,
                       b.get("title") or "", b.get("url") or ""),
    )
    ordered.sort(
        key=lambda b: (b.get("reward_rtc") is not None,
                       b.get("reward_rtc") if b.get("reward_rtc") is not None else 0),
        reverse=True,
    )
    lines = [
        "| Repo | Issue | Title | Indexed RTC | Difficulty | Skills |",
        "|------|-------|-------|-------------|------------|--------|",
    ]
    for bounty in ordered[:top_n]:
        repo = bounty.get("repo") or ""
        repo_short = _markdown_cell(repo.split("/")[-1] or "unknown")
        number = bounty.get("number")
        issue_label = _markdown_cell(number if number is not None else "?")
        url = bounty.get("url")
        if not url and repo and number is not None:
            url = f"https://github.com/{repo}/issues/{number}"
        issue = f"[#{issue_label}]({_markdown_link_target(url)})" if url else f"#{issue_label}"
        # Truncate source text first. Slicing escaped markup can split an escape.
        title = _single_line_text((bounty.get("title") or "").strip())
        if len(title) > 60:
            title = title[:57] + "..."
        title = _markdown_cell(title)
        reward = _format_int(bounty.get("reward_rtc"))
        difficulty = _markdown_cell(bounty.get("difficulty") or "unknown")
        skills = _markdown_cell(", ".join(bounty.get("skills") or []) or "-")
        lines.append(f"| {repo_short} | {issue} | {title} | {reward} | {difficulty} | {skills} |")
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


def _index_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate bounty-index JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str):
    raise ValueError(f"non-finite bounty-index JSON value: {value}")


def build_section(
    top_n: int = DEFAULT_TOP_N,
    *,
    require_fresh: bool = False,
    now: datetime | None = None,
) -> str:
    """Build cached discovery content, optionally enforcing cache freshness.

    The existing workflow opts in; library callers retain their explicit choice.
    No cache timestamp proves that a campaign is unexpired or pays this claimant.
    """
    _validate_top_n(top_n)
    payload = json.loads(
        INDEX_PATH.read_text(encoding="utf-8"),
        parse_float=Decimal,
        parse_constant=_invalid_constant,
        object_pairs_hook=_index_object,
    )
    if not isinstance(payload, dict):
        raise ValueError("bounty index root must be an object")
    bounties = payload.get("bounties")
    if not isinstance(bounties, list):
        raise ValueError("bounty index must contain a 'bounties' list (empty is allowed)")
    bounties = _validated_bounty_rows(bounties)
    if "total_count" in payload:
        count = payload["total_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count != len(bounties):
            raise ValueError("bounty index 'total_count' must match its complete row list")
    updated_raw = payload.get("updated_at", "unknown")
    updated = _markdown_cell(updated_raw)
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
    header = (
        f"_Showing {min(top_n, len(bounties))} of {len(bounties)} cached issue candidates, "
        f"sorted by indexed RTC amount (unknown amounts last). Index rebuilt {updated}._"
    )
    notice = (
        "Indexed amounts may be campaign pools, caps or estimates, not per-claim rewards. "
        "A recent rebuild does not confirm an unexpired offer, available assignment, "
        "our eligibility or payment setup. Confirm live sponsor terms and our collection "
        "route before starting work; this table does not authorize a claim or submission."
    )
    return f"{header}\n\n{render_table(bounties, top_n=top_n)}\n\n{notice}"


def update_readme(readme_text: str, section: str) -> str:
    """Replace the contents between the unique sentinels with section."""
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
    pattern = re.compile(re.escape(START_MARKER) + r"(.*?)" + re.escape(END_MARKER), re.DOTALL)
    new_block = f"{START_MARKER}\n{section}\n{END_MARKER}"
    return pattern.sub(lambda _match: new_block, readme_text, count=1)


def _publish_readme(original: bytes, updated: str) -> None:
    """Stage a complete UTF-8 replacement before changing the existing README.

    The pre-replace comparison detects intervening edits but is not a filesystem
    compare-and-swap; concurrent publishers still need external serialization.
    """
    metadata = README_PATH.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("README must be an ordinary file, not a link or special file")
    fd, temporary = tempfile.mkstemp(prefix=".readme-bounties-", dir=README_PATH.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(updated.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        current = README_PATH.lstat()
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(current) != identity(metadata) or README_PATH.read_bytes() != original:
            raise ValueError("README changed during publication; keep it and retry from current inputs")
        os.replace(temporary, README_PATH)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="maximum candidate rows")
    parser.add_argument("--require-fresh", action="store_true", help="enforce existing cache-age limits")
    parser.add_argument("--stdout", action="store_true", help="print the generated section without writing README")
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        _validate_top_n(args.top)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        section = build_section(top_n=args.top, require_fresh=args.require_fresh)
        if args.stdout:
            print(section)
            return 0
        original = README_PATH.read_bytes()
        updated = update_readme(original.decode("utf-8"), section)
        if updated.encode("utf-8") != original:
            _publish_readme(original, updated)
            print(f"updated {README_PATH}")
        else:
            print(f"{README_PATH} already current")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
