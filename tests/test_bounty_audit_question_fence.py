# SPDX-License-Identifier: MIT
"""Regression coverage for interrogative maintainer expiry comments."""

from concierge import bounty_audit


def _comment(body: str) -> dict[str, str]:
    return {"author_association": "OWNER", "body": body}


def test_direct_and_tagged_expiry_questions_are_not_evidence():
    questions = [
        "This bounty is expired?",
        "The bounty has expired, right?",
        "This bounty was cancelled — correct?",
        "The bounty is no longer active?",
        "This bounty is no longer offered; can someone confirm?",
    ]

    for body in questions:
        assert bounty_audit._is_explicit_maintainer_expiry(_comment(body)) is False


def test_declarative_expiry_before_later_question_remains_evidence():
    statements = [
        "This bounty is expired.",
        "This bounty is expired. Why is the issue still open?",
        "The bounty was cancelled! Should we close the issue too?",
        "The bounty is no longer offered. Any questions can go in a new issue?",
    ]

    for body in statements:
        assert bounty_audit._is_explicit_maintainer_expiry(_comment(body)) is True
