# SPDX-License-Identifier: MIT
"""Fail-closed detection for credential-gated paid-work contribution terms.

This module never returns source text, URLs, or credential-shaped values.  It is
intended to consume only contribution terms that have already crossed an
authority boundary (for example, repository OWNER/MEMBER/COLLABORATOR prose).
"""

from __future__ import annotations

import re
from typing import Any, Iterable


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
    r"(?i)\b(?:your|runner|github|account|wallet|session)"
    r"(?:[-_\s]+\w+){0,2}[-_\s]*$"
)


def credential_gate_signal_types(texts: Iterable[str]) -> list[str]:
    """Return generic unsafe-credential signal types without echoing input text.

    The detector deliberately distinguishes ordinary login/OAuth product prose
    from contribution terms that ask a worker to disclose a credential or to
    verify/authenticate credential material at an external destination.
    """
    signals: set[str] = set()
    for text in texts:
        if not isinstance(text, str):
            raise TypeError("credential safety text must be a string")
        for match in _SENSITIVE_CREDENTIAL_RE.finditer(text):
            custody_prefix = text[max(0, match.start() - 80) : match.start()]
            if not _DIRECT_CUSTODY_PREFIX_RE.search(custody_prefix):
                continue

            start = max(0, match.start() - 140)
            end = min(len(text), match.end() + 220)
            window = text[start:end]
            if _DISCLOSURE_ACTION_RE.search(window):
                signals.add("credential_disclosure")
            if (
                _VERIFICATION_ACTION_RE.search(window)
                and (
                    _DOMAIN_RE.search(window)
                    or _EXTERNAL_DESTINATION_RE.search(window)
                )
            ):
                signals.add("external_credential_verification")
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
