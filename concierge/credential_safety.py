# SPDX-License-Identifier: MIT
"""Fail-closed detection for credential-gated paid-work contribution terms.

This module never returns source text, URLs, or credential-shaped values. It is
intended to consume only contribution terms that have already crossed an
authority boundary (for example, repository OWNER/MEMBER/COLLABORATOR prose).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Pattern


_SEGMENT_SPLIT_RE = re.compile(
    r"(?<=[.!?;])\s+|,\s+(?:and|but|then)\s+|\n+",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"(?i)(?<![\w.-])(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?"
)
_EXTERNAL_DESTINATION_RE = re.compile(
    r"(?i)\b(?:gateway|portal|website|site|form|third[- ]party(?:\s+service)?|"
    r"external\s+service)\b"
)
_SENSITIVE_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:"
    r"credentials?|passwords?|"
    r"(?:api|access|github|auth(?:entication)?|session)[-_\s]*(?:keys?|tokens?)|"
    r"runner[-_\s]*credentials?|session[-_\s]*(?:cookies?|ids?)|"
    r"private[-_\s]*keys?|seed[-_\s]*phrases?|"
    r"wallet[-_\s]*(?:secrets?|seeds?|private[-_\s]*keys?)"
    r")\b"
)
_DISCLOSURE_ACTION_RE = re.compile(
    r"(?i)\b(?:provide|submit|upload|send|share|post|paste|copy)\b"
)
_VERIFICATION_ACTION_RE = re.compile(
    r"(?i)\b(?:verify|authenticate|enter|sign\s+in|log\s+in)\b"
)
_DIRECT_CUSTODY_PREFIX_RE = re.compile(
    r"(?i)\b(?:your|runner|github|wallet|session)"
    r"(?:[-_\s]+\w+){0,2}[-_\s]*$"
)
_POST_CREDENTIAL_CUSTODY_RE = re.compile(
    r"(?i)^\s*(?:from|on|for|of)\s+(?:your\s+)?"
    r"(?:\w+\s+){0,3}(?:runner|github|account|wallet|session)\b"
)
_NEGATION_PREFIX_RE = re.compile(
    r"(?i)(?:"
    r"do\s+not|don't|never|must\s+not|should\s+not|shall\s+not|"
    r"cannot|can't|not\s+required\s+to|no\s+need\s+to|do\s+not\s+need\s+to"
    r")\s+(?:\w+\s+){0,4}$"
)
_CREDENTIAL_THEN_VERIFY_RE = re.compile(
    r"(?i)^\s*(?:to|then|and\s+then)\s*$"
)


def _unnegated_actions(pattern: Pattern[str], text: str) -> list[re.Match[str]]:
    actions: list[re.Match[str]] = []
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 80) : match.start()]
        if not _NEGATION_PREFIX_RE.search(prefix):
            actions.append(match)
    return actions


def _credential_has_direct_custody(segment: str, match: re.Match[str]) -> bool:
    prefix = segment[max(0, match.start() - 80) : match.start()]
    suffix = segment[match.end() : min(len(segment), match.end() + 100)]
    return bool(
        _DIRECT_CUSTODY_PREFIX_RE.search(prefix)
        or _POST_CREDENTIAL_CUSTODY_RE.search(suffix)
    )


def _action_before_credential(
    action: re.Match[str], credential: re.Match[str], *, max_gap: int
) -> bool:
    return (
        action.start() <= credential.start()
        and credential.start() - action.end() <= max_gap
    )


def _verification_relates_to_credential(
    segment: str,
    action: re.Match[str],
    credential: re.Match[str],
) -> bool:
    if _action_before_credential(action, credential, max_gap=120):
        return True
    if action.start() < credential.end() or action.start() - credential.end() > 80:
        return False
    between = segment[credential.end() : action.start()]
    return bool(_CREDENTIAL_THEN_VERIFY_RE.search(between))


def _relation_window(
    segment: str,
    action: re.Match[str],
    credential: re.Match[str],
) -> str:
    start = max(0, min(action.start(), credential.start()) - 80)
    end = min(len(segment), max(action.end(), credential.end()) + 160)
    return segment[start:end]


def credential_gate_signal_types(texts: Iterable[str]) -> list[str]:
    """Return generic unsafe-credential signal types without echoing input text.

    Each signal is bound within one local sentence/clause to an unnegated action,
    credential phrase, direct custody relation, and (for verification) external
    destination. This prevents unrelated prose from being combined into a
    terminal rejection while still catching pre- and post-nominal custody.
    """
    signals: set[str] = set()
    for text in texts:
        if not isinstance(text, str):
            raise TypeError("credential safety text must be a string")
        for segment in (part.strip() for part in _SEGMENT_SPLIT_RE.split(text)):
            if not segment:
                continue
            disclosure_actions = _unnegated_actions(_DISCLOSURE_ACTION_RE, segment)
            verification_actions = _unnegated_actions(
                _VERIFICATION_ACTION_RE, segment
            )
            for credential in _SENSITIVE_CREDENTIAL_RE.finditer(segment):
                if not _credential_has_direct_custody(segment, credential):
                    continue

                if any(
                    _action_before_credential(action, credential, max_gap=120)
                    for action in disclosure_actions
                ):
                    signals.add("credential_disclosure")

                for action in verification_actions:
                    if not _verification_relates_to_credential(
                        segment, action, credential
                    ):
                        continue
                    relation = _relation_window(segment, action, credential)
                    if (
                        _DOMAIN_RE.search(relation)
                        or _EXTERNAL_DESTINATION_RE.search(relation)
                    ):
                        signals.add("external_credential_verification")
                        break
    return sorted(signals)


def apply_credential_gate(
    qualification: dict[str, Any], signal_types: list[str]
) -> dict[str, Any]:
    """Overlay a terminal credential-safety rejection on a qualification result."""
    if not isinstance(qualification, dict):
        raise TypeError("qualification must be an object")
    if not isinstance(signal_types, list) or not all(
        isinstance(value, str) for value in signal_types
    ):
        raise TypeError("signal_types must be a list of strings")

    result = dict(qualification)
    signals = dict(result.get("signals") or {})
    signals["credential_gate_signal_types"] = sorted(set(signal_types))
    result["signals"] = signals

    if not signal_types:
        return result

    reasons = [dict(reason) for reason in (result.get("reasons") or [])]
    reason_codes = list(result.get("reason_codes") or [])
    if "EXTERNAL_CREDENTIAL_GATE" not in reason_codes:
        reasons.append(
            {
                "code": "EXTERNAL_CREDENTIAL_GATE",
                "severity": "REJECT",
                "message": (
                    "Authoritative contribution terms require unsafe credential "
                    "or secret handling outside the repository workflow."
                ),
            }
        )
        reason_codes.append("EXTERNAL_CREDENTIAL_GATE")

    result["reasons"] = reasons
    result["reason_codes"] = reason_codes
    result["disposition"] = "REJECT"
    result["dispatch"] = False
    return result
