# SPDX-License-Identifier: MIT
"""Cross-platform bounty candidate formatter and existing dispatcher.

Formatting is local and does not post, reserve work, or establish eligibility.
The existing dispatcher remains explicitly separate from preview generation.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

from concierge.readme_sync import (
    _format_int,
    _markdown_cell,
    _markdown_link_target,
    _single_line_text,
    _validated_bounty_rows,
)

SHORT_LIMIT = 280
_DISCOVERY_NOTICE = (
    "Indexed RTC figures may describe pools, caps or estimates, not per-claim pay. "
    "Confirm live sponsor terms, assignment, our eligibility and payment route "
    "before starting work. This preview does not confirm availability or a cash value."
)


def _normalized_bounties(bounties: List[dict]) -> List[dict]:
    """Accept both the existing CLI rtc shape and the index reward_rtc shape."""
    normalized = []
    for index, bounty in enumerate(bounties):
        if not isinstance(bounty, dict):
            raise ValueError(f"bounties[{index}] must be an object")
        if not isinstance(bounty.get("title"), str):
            raise ValueError(f"bounties[{index}].title must be a string")
        row = dict(bounty)
        if "reward_rtc" not in row:
            row["reward_rtc"] = row.get("rtc")
        # Use the same literal-text and numeric contract as the README, without
        # fetching or rewriting its index. Input order is deliberately retained.
        _validated_bounty_rows([row])
        if "rtc" in row and "reward_rtc" in bounty:
            legacy = dict(row, reward_rtc=row["rtc"])
            _validated_bounty_rows([legacy])
            left, right = row["rtc"], row["reward_rtc"]
            unequal = (left is None) != (right is None)
            if left is not None and right is not None:
                unequal = Decimal(str(left)) != Decimal(str(right))
            if unequal:
                raise ValueError(f"bounties[{index}] has conflicting rtc and reward_rtc values")
        normalized.append(row)
    return _validated_bounty_rows(normalized)


def _short_announcement(bounty: dict) -> str:
    """Budget the title around whole metadata; never slice a destination URL."""
    prefix = "RustChain candidate: "
    title = _single_line_text(bounty["title"]).strip()
    amount = f" | indexed {_format_int(bounty.get('reward_rtc'))} RTC"
    raw_url = bounty.get("url") or ""
    url = _markdown_link_target(raw_url) if raw_url else ""
    destination = f" | {url}" if url else " | source link not supplied"
    # Prefer preserving the complete source link over repeating an oversized
    # amount. The medium and long formats still contain the complete amount.
    if len(prefix + amount + destination) > SHORT_LIMIT:
        amount = ""
    if len(prefix + destination) > SHORT_LIMIT:
        destination = " | link exceeds short-format budget; use full preview"
    room = SHORT_LIMIT - len(prefix) - len(amount) - len(destination)
    if len(title) > room:
        title = (title[:room - 3] + "...") if room >= 3 else title[:room]
    return prefix + title + amount + destination


def format_announcement(bounties: List[dict]) -> Dict[str, str]:
    """Create local short, medium and long candidate previews.

    Input rows require title and may supply url, difficulty, labels and either
    rtc (the existing CLI contract) or reward_rtc (the index contract). Missing
    amounts remain unknown. Conflicting aliases reject instead of choosing one.
    Input order is preserved; callers own their selection/ranking policy.

    The short format is at most 280 Python characters, not a guarantee of any
    provider's weighted-length policy. Oversized links are explicitly omitted
    from that format, never silently truncated; complete links remain in long.
    """
    rows = _normalized_bounties(bounties)
    if not rows:
        return {"short": "", "medium": "", "long": ""}

    short = _short_announcement(rows[0])
    medium_lines = ["RustChain bounty candidates (supplied snapshot):\n"]
    for bounty in rows[:5]:
        raw_url = bounty.get("url") or ""
        url = _markdown_link_target(raw_url) if raw_url else "source link not supplied"
        medium_lines.append(
            f"- {_markdown_cell(bounty['title'])} | "
            f"indexed {_format_int(bounty.get('reward_rtc'))} RTC | {url}"
        )
    if len(rows) > 5:
        medium_lines.append(f"\n+{len(rows) - 5} supplied candidates in the full preview.")
    medium_lines.append("\n" + _DISCOVERY_NOTICE)

    long_lines = [
        "# RustChain Bounty Candidates\n",
        "| Title | Indexed RTC | Difficulty | Source |",
        "|-------|-------------|------------|--------|",
    ]
    for bounty in rows:
        raw_url = bounty.get("url") or ""
        link = f"[source]({_markdown_link_target(raw_url)})" if raw_url else "not supplied"
        long_lines.append(
            f"| {_markdown_cell(bounty['title'])} | {_format_int(bounty.get('reward_rtc'))} | "
            f"{_markdown_cell(bounty.get('difficulty') or 'unknown')} | {link} |"
        )
    long_lines.append("\n" + _DISCOVERY_NOTICE)
    return {"short": short, "medium": "\n".join(medium_lines), "long": "\n".join(long_lines)}


def post_announcement(platform: str, content: str, platform_config: dict) -> dict:
    """Post content through an explicitly requested existing platform handler.

    Returns {"ok": bool, "url": str, "error": str}. Formatting alone never
    invokes this function. The caller retains publication authorization.
    """
    try:
        handler = _PLATFORM_HANDLERS.get(platform)
        if handler is None:
            return {"ok": False, "url": "", "error": f"Unknown platform: {platform}"}
        return handler(content, platform_config)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": "", "error": str(exc)}


def _post_moltbook(content: str, cfg: dict) -> dict:
    import requests

    api_key = cfg.get("api_key", "")
    submolt = cfg.get("submolt", "rustchain")
    title = cfg.get("title", "Open RustChain Bounties")
    resp = requests.post(
        "https://www.moltbook.com/api/v1/posts",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"content": content, "title": title, "submolt_name": submolt},
        timeout=30,
    )
    data = resp.json() if resp.ok else {}
    url = data.get("url", "")
    return {"ok": resp.ok, "url": url, "error": "" if resp.ok else resp.text}


def _post_stub(content: str, cfg: dict) -> dict:
    """Placeholder for platforms not yet wired up."""
    return {"ok": False, "url": "", "error": "Platform handler not yet implemented."}


_PLATFORM_HANDLERS = {
    "moltbook": _post_moltbook,
    "4claw": _post_stub,
    "agentchan": _post_stub,
    "devto": _post_stub,
    "twitter": _post_stub,
}
