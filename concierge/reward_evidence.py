# SPDX-License-Identifier: MIT
"""Reward evidence extraction and formatting utilities.

Preserves exact reward mentions, title/body excerpts, and classification
(matched, unconfirmed_text, no_match) from bounty issues without declaring
campaign pools or token figures to be guaranteed per-claim compensation.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

# Standard RTC regex pattern matching finite numeric amounts
_RTC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.,])"
    r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"[ \t]*RTC\b",
    re.IGNORECASE,
)

# Generic currency and reward indicator patterns for unconfirmed mentions
_GENERAL_AMOUNT_PATTERN = re.compile(
    r"(?:\$\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:USD|USDT|USDC|EUR|GBP|SOL|ETH|SATS|POINTS|CREDITS)\b|"
    r"\b(?:bounty|reward|prize|grant|pool)\s*[:=]?\s*(?:of\s*)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\b)",
    re.IGNORECASE,
)

_MAX_EXCERPT_LENGTH = 120
_MAX_MENTIONS = 5


def _sanitize_single_line(text: str) -> str:
    """Flatten whitespace and linebreaks into clean single-line text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _make_excerpt(text: str, match_start: int, match_end: int, max_len: int = _MAX_EXCERPT_LENGTH) -> str:
    """Extract a contextual window around a regex match bounded to max_len characters."""
    raw_snippet = text[max(0, match_start - 30):min(len(text), match_end + 50)]
    clean = _sanitize_single_line(raw_snippet)
    if len(clean) > max_len:
        return clean[:max_len - 3] + "..."
    return clean


def extract_reward_evidence(title: str, body: str) -> Dict[str, Any]:
    """Extract reward evidence, classifications, and exact text from title and body.

    Returns a dictionary with:
      - status: 'matched' (valid finite RTC amount), 'unconfirmed_text' (other amounts/currencies), or 'no_match'
      - amount_rtc: float value if matched, else None
      - exact_text: the exact string matched (e.g. '150 RTC', '$50', or '')
      - excerpt: bounded single-line context excerpt around the primary match
      - mentions: list of additional amount/token mentions found (bounded to 5)
      - evidence_kind: 'rtc_exact', 'unconfirmed_text', or 'no_match'
    """
    safe_title = title or ""
    safe_body = body or ""
    
    # Pass 1: Look for primary RTC pattern in title first, then body
    rtc_matches: List[Dict[str, Any]] = []
    for source_name, text in [("title", safe_title), ("body", safe_body)]:
        for m in _RTC_PATTERN.finditer(text):
            raw_num = m.group(1).replace(",", "")
            try:
                val = float(raw_num)
            except ValueError:
                continue
            if math.isfinite(val) and val >= 0.0:
                rtc_matches.append({
                    "val": val,
                    "exact": m.group(0).strip(),
                    "start": m.start(),
                    "end": m.end(),
                    "text": text,
                    "source": source_name,
                })

    if rtc_matches:
        primary = rtc_matches[0]
        additional_mentions = [
            m["exact"] for m in rtc_matches[1:1 + _MAX_MENTIONS]
        ]
        
        # Also collect other non-RTC currency mentions if room permits
        if len(additional_mentions) < _MAX_MENTIONS:
            combined = f"{safe_title} {safe_body}"
            for gm in _GENERAL_AMOUNT_PATTERN.finditer(combined):
                g_text = gm.group(0).strip()
                if g_text not in additional_mentions and g_text != primary["exact"]:
                    additional_mentions.append(g_text)
                    if len(additional_mentions) >= _MAX_MENTIONS:
                        break

        excerpt = _make_excerpt(primary["text"], primary["start"], primary["end"])
        return {
            "status": "matched",
            "amount_rtc": primary["val"],
            "exact_text": primary["exact"],
            "excerpt": excerpt,
            "mentions": additional_mentions,
            "evidence_kind": "rtc_exact",
        }

    # Pass 2: Look for unconfirmed amounts, dollar amounts, or general rewards
    general_matches: List[str] = []
    first_general_match: Optional[re.Match] = None
    first_general_text: str = ""

    for text in (safe_title, safe_body):
        for gm in _GENERAL_AMOUNT_PATTERN.finditer(text):
            m_text = gm.group(0).strip()
            if m_text not in general_matches:
                if not first_general_match:
                    first_general_match = gm
                    first_general_text = text
                general_matches.append(m_text)
                if len(general_matches) >= _MAX_MENTIONS:
                    break
        if len(general_matches) >= _MAX_MENTIONS:
            break

    if general_matches and first_general_match:
        excerpt = _make_excerpt(first_general_text, first_general_match.start(), first_general_match.end())
        return {
            "status": "unconfirmed_text",
            "amount_rtc": None,
            "exact_text": general_matches[0],
            "excerpt": excerpt,
            "mentions": general_matches[1:],
            "evidence_kind": "unconfirmed_text",
        }

    # Pass 3: No match
    fallback_excerpt = _sanitize_single_line(safe_title)[:_MAX_EXCERPT_LENGTH]
    return {
        "status": "no_match",
        "amount_rtc": None,
        "exact_text": "",
        "excerpt": fallback_excerpt,
        "mentions": [],
        "evidence_kind": "no_match",
    }


def reward_summary(row: Dict[str, Any]) -> str:
    """Format a human-readable reward summary string for CLI and table displays.

    Handles explicit RTC amounts, unconfirmed mentions, and unknown/no-match cases.
    """
    if not isinstance(row, dict):
        return "unknown"

    evidence = row.get("reward_evidence")
    if isinstance(evidence, dict):
        status = evidence.get("status")
        if status == "matched":
            amt = evidence.get("amount_rtc")
            if amt is not None and isinstance(amt, (int, float)) and math.isfinite(amt):
                return f"{amt:,.1f} RTC" if amt != int(amt) else f"{int(amt):,} RTC"
            exact = evidence.get("exact_text")
            return str(exact) if exact else "RTC"
        elif status == "unconfirmed_text":
            exact = evidence.get("exact_text") or "unconfirmed"
            return f"unconfirmed ({exact})"
        elif status == "no_match":
            return "no reward listed"

    # Fallback to legacy reward_rtc field
    reward_rtc = row.get("reward_rtc")
    if reward_rtc is not None and isinstance(reward_rtc, (int, float)) and math.isfinite(reward_rtc) and reward_rtc > 0:
        return f"{reward_rtc:,.1f} RTC" if reward_rtc != int(reward_rtc) else f"{int(reward_rtc):,} RTC"

    return "unknown"


def reward_filter_value(row: Dict[str, Any]) -> Optional[float]:
    """Extract a numeric float for reward filtering (--min-rtc/--max-rtc), or None."""
    if not isinstance(row, dict):
        return None

    evidence = row.get("reward_evidence")
    if isinstance(evidence, dict):
        if evidence.get("status") == "matched":
            amt = evidence.get("amount_rtc")
            if amt is not None and isinstance(amt, (int, float)) and math.isfinite(amt):
                return float(amt)
        return None

    reward_rtc = row.get("reward_rtc")
    if reward_rtc is not None and isinstance(reward_rtc, (int, float)) and math.isfinite(reward_rtc) and reward_rtc > 0:
        return float(reward_rtc)

    return None
