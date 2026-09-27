# SPDX-License-Identifier: MIT
"""Reward evidence extraction and formatting utilities.

Preserves exact reward mentions, title/body excerpts, and classification
(matched, unconfirmed_text, no_match) from bounty issues without declaring
campaign pools or token figures to be guaranteed per-claim compensation.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
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
    """Flatten whitespace and make other control characters visible."""
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", str(text)).strip()
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in clean)


def _make_excerpt(text: str, match_start: int, match_end: int, max_len: int = _MAX_EXCERPT_LENGTH) -> str:
    """Extract a contextual window around a regex match bounded to max_len characters."""
    raw_snippet = text[max(0, match_start - 30):min(len(text), match_end + 50)]
    clean = _sanitize_single_line(raw_snippet)
    if len(clean) > max_len:
        return clean[:max_len - 3] + "..."
    return clean


def extract_reward_evidence(title: str, body: str) -> Dict[str, Any]:
    """Extract text mentions, not confirmed per-claim compensation.

    ``matched`` describes a finite RTC text match only. ``amount_rtc`` keeps
    the existing float API; ``exact_text`` retains the original precision.
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


def _finite_amount(value: Any) -> Optional[Decimal]:
    """Accept numeric JSON values, including the README's Decimal reader."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _amount_label(amount: Decimal) -> str:
    """Format without rounding or expanding extreme exponents."""
    if amount and abs(amount.adjusted()) > 32:
        return str(amount)
    text = format(amount, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def reward_summary(row: Dict[str, Any]) -> str:
    """Describe a source mention without making it sound like promised pay.

    Exact RTC text takes precedence over float formatting. A pattern miss is
    not evidence that the sponsor offers nothing. Legacy zero is ambiguous.
    """
    if not isinstance(row, dict):
        return "unknown"

    evidence = row.get("reward_evidence")
    if isinstance(evidence, dict):
        status = evidence.get("status")
        if status == "matched":
            amount = _finite_amount(evidence.get("amount_rtc"))
            if amount is None:
                return "unknown (invalid RTC evidence)"
            exact = evidence.get("exact_text")
            if isinstance(exact, str) and _RTC_PATTERN.fullmatch(exact.strip()):
                return "unconfirmed " + _sanitize_single_line(exact)
            return f"unconfirmed {_amount_label(amount)} RTC"
        if status == "unconfirmed_text":
            exact = evidence.get("exact_text")
            return "unconfirmed " + (_sanitize_single_line(exact) if isinstance(exact, str) and exact else "amount")
        if status == "no_match":
            return "unknown (no amount match)"
        return "unknown (unrecognized evidence)"
    if evidence is not None:
        return "unknown (invalid evidence)"

    amount = _finite_amount(row.get("reward_rtc"))
    if amount is not None and amount > 0:
        return f"indexed {_amount_label(amount)} RTC (unconfirmed)"
    return "unknown (legacy amount)"


def reward_filter_value(row: Dict[str, Any]) -> Optional[float]:
    """Return a finite RTC mention for numeric filters, never a payment claim.

    Keep the existing float return type. Explicit matched zero is filterable;
    legacy zero, invalid evidence and non-RTC/no-match evidence are unknown.
    Presence of evidence prevents falling back to a misleading legacy number.
    """
    if not isinstance(row, dict):
        return None
    evidence = row.get("reward_evidence")
    if evidence is not None:
        if not isinstance(evidence, dict) or evidence.get("status") != "matched":
            return None
        amount = _finite_amount(evidence.get("amount_rtc"))
    else:
        amount = _finite_amount(row.get("reward_rtc"))
        if amount is not None and amount == 0:
            return None
    if amount is None:
        return None
    try:
        value = float(amount)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def reward_sort_key(row: Dict[str, Any]) -> tuple:
    """Stable descending numeric-mention ordering, with unknown rows last."""
    value = reward_filter_value(row)
    return (value is not None, value if value is not None else 0.0)


def reward_context(row: Dict[str, Any]) -> str:
    """Bounded single-line source context for the existing browse command."""
    evidence = row.get("reward_evidence") if isinstance(row, dict) else None
    if not isinstance(evidence, dict):
        return "No retained reward excerpt; legacy numeric data is not payout evidence."
    parts = []
    excerpt = evidence.get("excerpt")
    if isinstance(excerpt, str) and excerpt:
        parts.append("Excerpt: " + _sanitize_single_line(excerpt)[:_MAX_EXCERPT_LENGTH])
    mentions = evidence.get("mentions")
    if isinstance(mentions, list):
        shown = [_sanitize_single_line(item)[:_MAX_EXCERPT_LENGTH]
                 for item in mentions[:_MAX_MENTIONS] if isinstance(item, str)]
        if shown:
            parts.append("Other mentions: " + "; ".join(shown))
    return " | ".join(parts) if parts else "No retained excerpt. Inspect the source issue."
