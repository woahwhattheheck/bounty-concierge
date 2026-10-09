# SPDX-License-Identifier: MIT
"""Bind an explicit MOVA payment claim command to its canonical issue.

This is a local validation step, not a GitHub command executor or claim publisher.
Bare /claim and natural-language requests retain historical behavior; the existing
claim-intent gate remains authoritative for affirmative compensation semantics.
"""
from __future__ import annotations

import re

_COMMAND = re.compile(r"(?<!\S)/claim\b", re.IGNORECASE)
_EXPLICIT_REFERENCE = re.compile(
    r"^[ \t]+#?([1-9][0-9]*)(?=$|[ \t\r\n.,;:])"
)


class ClaimTargetMismatch(ValueError):
    """An explicit claim command targets a different issue or is malformed."""


def validate_claim_target(claim_text: str, issue_number: int) -> None:
    """Reject wrong issue numbers on every explicit /claim command.

    Keep historical bare `/claim` semantics, because some provider commands infer
    the target from the issue thread. Where the operator *does* write a numeric
    target, its value must equal the canonical target's issue number.
    """
    if not isinstance(claim_text, str):
        raise ClaimTargetMismatch("claim text must be a string")
    if type(issue_number) is not int or issue_number < 1:
        raise ClaimTargetMismatch("issue_number must be positive integer")

    for command in _COMMAND.finditer(claim_text):
        tail = claim_text[command.end():]
        first_line = tail.splitlines()[0] if tail else ""
        stripped = first_line.lstrip(" \t")
        if not stripped or not (stripped.startswith("#") or stripped[0].isdigit()):
            # An untargeted command is issued in its existing canonical thread;
            # do not retroactively break the accepted bare /claim form.
            continue
        matched = _EXPLICIT_REFERENCE.match(first_line)
        if matched is None or int(matched.group(1)) != issue_number:
            raise ClaimTargetMismatch(
                "explicit /claim issue number does not match canonical target"
            )
