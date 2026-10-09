# SPDX-License-Identifier: MIT
"""Narrow claim-copy admission; not a payout, eligibility or award verifier."""
from __future__ import annotations

import re
from typing import Optional

_WAIVER = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:I|we)\s+(?:(?:am|are)\s+)?not\s+"
    r"(?:claim(?:ing)?|request(?:ing)?|seek(?:ing)?|pursu(?:e|ing))\s+"
    r"(?:(?:this|the|any|a|an)\s+)?"
    r"(?:bounty|reward|payment|payout|compensation|prize)\b",
    r"\bthis\s+is\s+not\s+(?:an?\s+)?(?:bounty\s+)?claim\b",
    r"\b(?:I|we)\s+(?:(?:hereby|explicitly)\s+)?"
    r"(?:waive|forfeit|decline)\s+(?:(?:my|our|this|the|any)\s+)?"
    r"(?:bounty|reward|compensation|payment|payout|prize)\b",
    r"\bno\s+(?:bounty|reward|payment|payout|compensation)\s+"
    r"is\s+(?:requested|claimed|sought)\b",
))

_AFFIRMATIVE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:I|we)\s+(?:(?:am|are|hereby|affirmatively)\s+){0,3}"
    r"(?:claim(?:ing)?|request(?:ing)?|seek(?:ing)?)\s+"
    r"(?:(?:the|this|my|our|advertised|published|eligible|conditional|stated)\s+){0,4}"
    r"(?:bounty|reward|payment|payout|compensation|prize)\b",
    r"\b(?:the|this|eligible|advertised)\s+"
    r"(?:bounty|reward|payment|payout|compensation|prize)\s+"
    r"(?:is|remains)\s+(?:(?:hereby|affirmatively)\s+)?"
    r"(?:claimed|requested|sought)\b",
    r"\b(?:bounty|reward|payment|payout|compensation)\s+"
    r"(?:is|remains)\s+(?:(?:hereby|affirmatively)\s+)?requested\b",
))


def audit_bounty_claim_text(
    body: Optional[str], *, require_affirmative: bool = True
) -> tuple[str, ...]:
    """Return stable codes only. Do not rewrite original contributor statements.

    This examines proposed publication copy, not existing claim ownership.
    """
    if not isinstance(body, str) or not body.strip():
        return ("BOUNTY_CLAIM_BODY_MISSING",)
    if len(body.encode("utf-8")) > 65536:
        return ("BOUNTY_CLAIM_BODY_TOO_LARGE",)
    findings: list[str] = []
    if any(rule.search(body) for rule in _WAIVER):
        findings.append("BOUNTY_COMPENSATION_WAIVER")
    if require_affirmative and not any(rule.search(body) for rule in _AFFIRMATIVE):
        findings.append("BOUNTY_AFFIRMATIVE_REQUEST_MISSING")
    return tuple(findings)
