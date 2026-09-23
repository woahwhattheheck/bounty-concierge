# SPDX-License-Identifier: MIT
"""Local RTC-text evidence, not sponsor terms or a payment decision.

The legacy float remains available. Display/range consumers use the explicit
observation instead, so no match is distinguishable from an observed zero.
"""
from __future__ import annotations

import math
import re

_RTC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.,])"
    r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"[ \t]*RTC\b",
    re.IGNORECASE,
)
MAX_RETAINED_MENTIONS = 8


def parse_reward_evidence(title, body):
    """Keep first-match compatibility and up to eight ordered source excerpts.

    All finite mentions are counted, including repeated amounts. Text may refer
    to a pool, cap, treasury, bonus or per-task offer; this parser decides none
    of those meanings. Title precedes body, matching the original API.
    """
    mentions = []
    count = 0
    for source, text in (("title", title), ("body", body)):
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise ValueError(f"reward {source} must be text")
        for match in _RTC_PATTERN.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            if not math.isfinite(value):
                continue
            count += 1
            if len(mentions) < MAX_RETAINED_MENTIONS:
                start, end = match.span()
                mentions.append({
                    "source": source, "start": start, "end": end,
                    "amount_text": raw, "value_rtc": value,
                    "matched_text": match.group(0),
                    "excerpt": text[max(0, start - 96):min(len(text), end + 96)],
                })
    return {
        "status": "RTC_TEXT_MENTION_UNCONFIRMED" if count else "NO_RTC_MENTION",
        "selected": mentions[0] if mentions else None,
        "mentions": mentions, "mention_count": count,
        "mentions_truncated": count > len(mentions),
        "per_claim_compensation_confirmed": False,
    }


def parse_reward(title, body):
    """Legacy API: first finite RTC-text value, or 0.0 when no match exists."""
    selected = parse_reward_evidence(title, body)["selected"]
    return selected["value_rtc"] if selected is not None else 0.0


def reward_evidence_for(bounty):
    """Read explicit evidence, deriving from retained text for older indexes.

    Old rows that discarded body text cannot establish absence of a reward.
    They retain an explicitly unqualified legacy value instead of gaining a
    fabricated title/body excerpt or a zero-pay interpretation.
    """
    evidence = bounty.get("reward_evidence")
    if evidence is not None:
        if not isinstance(evidence, dict):
            raise ValueError("reward_evidence must be an object")
        status = evidence.get("status")
        selected = evidence.get("selected")
        count = evidence.get("mention_count")
        if type(count) is not int or count < 0:
            raise ValueError("reward_evidence.mention_count must be non-negative")
        if status == "NO_RTC_MENTION" and selected is None and count == 0:
            return evidence
        if status != "RTC_TEXT_MENTION_UNCONFIRMED" or not isinstance(selected, dict) or count < 1:
            raise ValueError("unsupported reward evidence state")
        raw = selected.get("amount_text")
        if (not isinstance(raw, str) or re.fullmatch(r"\d+(?:\.\d+)?", raw) is None
                or selected.get("source") not in ("title", "body")
                or not isinstance(selected.get("excerpt"), str)):
            raise ValueError("reward evidence needs amount text and a source excerpt")
        if not math.isfinite(float(raw)):
            raise ValueError("reward evidence amount must be finite")
        return evidence
    title = bounty.get("title") or ""
    body = bounty.get("body")
    derived = parse_reward_evidence(title, body)
    if "body" in bounty or derived["selected"] is not None:
        # Missing body is not described as a complete-text search.
        if "body" not in bounty:
            derived["source_coverage"] = "TITLE_ONLY"
        return derived
    return {"status": "LEGACY_NO_TEXT_EVIDENCE", "selected": None,
            "mention_count": 0, "per_claim_compensation_confirmed": False}


def reward_filter_value(bounty):
    """Optional first observed RTC mention for numerical discovery filters.

    None means no observed amount, not zero. A returned amount is still not
    confirmed per-claim compensation and must not drive a payment decision.
    """
    evidence = reward_evidence_for(bounty)
    selected = evidence.get("selected")
    return float(selected["amount_text"]) if selected is not None else None


def reward_summary(bounty, include_excerpt=False):
    """Return literal, single-line reward text for a consumer to escape."""
    evidence = reward_evidence_for(bounty)
    status = evidence["status"]
    if status == "NO_RTC_MENTION":
        return "unknown (no RTC mention)"
    if status == "LEGACY_NO_TEXT_EVIDENCE":
        value = bounty.get("reward_rtc", bounty.get("rtc"))
        return "unknown (text evidence absent)" if value is None else (
            f"legacy indexed {value} RTC (text evidence absent; pay unconfirmed)"
        )
    selected = evidence["selected"]
    text = f"{selected['amount_text']} RTC (text mention; pay unconfirmed)"
    count = evidence["mention_count"]
    if count > 1:
        text += f"; {count} mentions"
    if evidence.get("mentions_truncated"):
        text += f", first {MAX_RETAINED_MENTIONS} retained"
    if evidence.get("source_coverage") == "TITLE_ONLY":
        text += "; title only"
    if include_excerpt:
        text += f"; {selected['source']}: {selected['excerpt']}"
    # Avoid terminal control sequences or hidden line breaks from source text.
    return "".join(char if char.isprintable() else f"\\u{ord(char):04x}" for char in text)
