# SPDX-License-Identifier: MIT
"""Offline issue-text preflight: detect acceptance rules asking for agent secrets.

This is a narrow safety filter for NEW unpaid bounty work. An unflagged issue
is NOT certified funded, payable, safe, or approved. No network or writes.
Original claims and previously submitted work are unaffected.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "issue-instruction-leak-gate/v1"
MAX_TEXT = 250_000

# Inspect acceptance wording, not the contributor's real instructions.
_PROTECTED = re.compile(
    r"\b(?:system[_ -]?prompt|boot[_ -]?context|"
    r"runtime[_ -]?instructions|initial(?:ization)?[_ -]?instructions|"
    r"(?:configuration|startup|session)[_ -]?prompt|"
    r"pre[- ]user (?:context|instructions))\b",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"\b(?:paste|copy|provide|include|submit|attach|commit|upload|"
    r"disclose|reveal|publish|contains?|containing|require(?:s|d)?|"
    r"add|put|write|fill)\b",
    re.IGNORECASE,
)
_CONTEXT_LEAK = re.compile(
    r"\b(?:paste|copy|provide|include|submit|attach|commit|upload|"
    r"disclose|reveal|publish)\b.{0,180}\b(?:everything|entire|full|"
    r"verbatim|complete)\b.{0,160}\b(?:context|instructions|"
    r"configuration)\b.{0,120}\b(?:before|prior to|start of)\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"\b(?:do not|don't|never|must not|should not|avoid)\s+"
    r"(?:paste|copy|provide|include|submit|attach|commit|upload|"
    r"disclose|reveal|publish|add|put|write)\b",
    re.IGNORECASE,
)


class InvalidSnapshot(ValueError):
    pass


def assess(snapshot: dict) -> dict:
    """Return a metadata-only finding; never echo the source or secret text."""
    if type(snapshot) is not dict:
        raise InvalidSnapshot("snapshot must be a JSON object")
    body, title = snapshot.get("body"), snapshot.get("title", "")
    if type(body) is not str or type(title) is not str:
        raise InvalidSnapshot("body and title must be strings")
    if len(body) > MAX_TEXT or len(title) > 500:
        raise InvalidSnapshot("issue snapshot exceeds size budget")
    if not body.strip():
        raise InvalidSnapshot("issue body cannot be empty")

    matched_lines = []
    # Keep each line independent: a negative warning must not excuse an
    # unrelated disclosure demand elsewhere in the same issue.
    for line_no, raw in enumerate(body.splitlines(), 1):
        if len(raw) > 10_000:
            raise InvalidSnapshot("oversized issue line")
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or _NEGATED.search(line):
            continue
        if (_PROTECTED.search(line) and _REQUEST.search(line)
                or _CONTEXT_LEAK.search(line)):
            matched_lines.append(line_no)
    if matched_lines:
        return {
            "schema": SCHEMA,
            "decision": "BLOCK_UNSAFE_ACCEPTANCE",
            "reasons": ["PRIVATE_AGENT_CONTEXT_REQUEST"],
            "issue_lines": matched_lines[:50],
            "new_unpaid_build_allowed": False,
            "existing_claims_unaffected": True,
        }
    return {
        "schema": SCHEMA,
        "decision": "NO_SECRET_REQUEST_DETECTED",
        "reasons": [],
        "issue_lines": [],
        "new_unpaid_build_allowed": None,
        "existing_claims_unaffected": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="JSON object with issue title and body")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
        verdict = assess(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, InvalidSnapshot) as error:
        print(json.dumps({"schema": SCHEMA, "decision": "INVALID_SNAPSHOT",
                          "error": type(error).__name__, "new_unpaid_build_allowed": False}))
        return 2
    print(json.dumps(verdict, sort_keys=True))
    return 3 if verdict["decision"] == "BLOCK_UNSAFE_ACCEPTANCE" else 0


if __name__ == "__main__":
    sys.exit(main())
