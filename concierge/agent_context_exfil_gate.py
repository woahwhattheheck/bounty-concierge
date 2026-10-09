# SPDX-License-Identifier: MIT
"""Offline safety preflight for untrusted public bounty acceptance criteria.

Scan only the canonical public issue text. Never pass platform/system context,
credentials, private instructions, or conversation history to this program.
This is one security screen, NOT a payment/maintainer eligibility determination.

    python concierge/agent_context_exfil_gate.py --issue-file issue.txt --source-url URL

Exit codes: 0 = no sensitive instruction-request detected; 2 = suppress; 3 = input error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

MAX_INPUT_BYTES = 256 * 1024

_TOTAL = re.compile(r"\b(?:entire|complete|full|all|everything|verbatim|unmodified)\b", re.I)
_REQUEST = re.compile(
    r"\b(?:paste|include|provide|submit|commit|publish|disclose|copy|add|"
    r"containing|contains|must|mandatory|required|require(?:d|ment|s)?|"
    r"review|auto[- ]reject(?:ed)?)\b", re.I
)
_RULES = {
    "SYSTEM_OR_DEVELOPER_PROMPT": re.compile(
        r"\b(?:system|developer)\s+(?:prompt|instructions?|message|directives?)\b", re.I
    ),
    "PLATFORM_INITIALIZATION": re.compile(
        r"\b(?:platform|session|runtime)\s+(?:initialization|startup|boot)\s+"
        r"(?:text|instructions?|context|block|config(?:uration)?)\b", re.I
    ),
    "PRE_USER_CONTEXT": re.compile(
        r"\b(?:before\s+(?:the\s+)?(?:first|any)\s+(?:human|user)\s+messages?|"
        r"pre[- ](?:human|user|conversation|task|session)\s+(?:instructions?|"
        r"context|initialization|rules|directives?))\b", re.I
    ),
    "PLATFORM_PRE_TASK_DUMP": re.compile(
        r"\b(?:platform\s+provided\s+before\s+(?:the|this)\s+task\s+started|"
        r"context\s+before\s+(?:the\s+)?first\s+human\s+message|"
        r"instructions?\s+provided\s+before\s+(?:the\s+)?task)\b", re.I
    ),
}
_WARNING = re.compile(
    r"^\s*(?:[-*>]\s*)?(?:do\s+not|don't|never|must\s+not|"
    r"should\s+not|forbidden\s+to|prohibited\s+to)\s+"
    r"(?:paste|include|submit|share|provide|disclose|commit|publish|copy)\b", re.I
)


def inspect_issue(issue_text: str, source_url: str = "") -> dict:
    """Return a metadata-only screen verdict; never echo issue body to outputs.

    One sentence may wrap onto neighboring lines. Match only a total-context
    demand connected with submission language, not mentions of prompts alone.
    A negative prohibition is not itself a request to disclose instructions.
    """
    if not isinstance(issue_text, str) or not issue_text.strip():
        raise ValueError("canonical public issue text must be nonempty")
    raw = issue_text.encode("utf-8")
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("canonical public issue text exceeds 256 KiB")
    lines = issue_text.splitlines()
    hits: list[dict] = []
    for line_number, line in enumerate(lines, 1):
        if _WARNING.match(line):
            continue
        # A requirement may introduce a JSON metadata field on the next line.
        prior = lines[line_number - 2] if line_number >= 2 else ""
        after = lines[line_number] if line_number < len(lines) else ""
        nearby = " ".join((prior, line, after))
        if not _TOTAL.search(line) or not _REQUEST.search(nearby):
            continue
        for rule_id, expr in _RULES.items():
            if expr.search(line):
                hits.append({"line": line_number, "rule": rule_id})
        # Widely used field names alone are not sufficient; the demand for a
        # complete private context is what makes the clause disqualifying.
    seen = set()
    hits = [h for h in hits if not ((h["line"], h["rule"]) in seen or seen.add((h["line"], h["rule"]))) ]
    return {
        "schema": "bounty-public-issue-context-screen/v1",
        "source_url": source_url,
        "issue_sha256": hashlib.sha256(raw).hexdigest(),
        "verdict": "SUPPRESS_EXFILTRATION" if hits else "CLEAR_THIS_SECURITY_SCREEN_ONLY",
        "matches": hits,
        "payment_eligibility_verified": False,
        "claim_eligible": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--issue-file", type=Path, help="UTF-8 PUBLIC canonical issue body")
    source.add_argument("--stdin", action="store_true", help="read PUBLIC issue text from stdin")
    parser.add_argument("--source-url", default="", help="provenance URL (not fetched)")
    args = parser.parse_args(argv)
    try:
        if args.stdin:
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with args.issue_file.open("rb") as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("canonical public issue text exceeds 256 KiB")
        verdict = inspect_issue(raw.decode("utf-8"), args.source_url)
    except (OSError, UnicodeError, ValueError) as exc:
        # Do not echo the path, input content, or exception-provided file data.
        print(json.dumps({"verdict": "HOLD_INPUT_INVALID", "error_type": type(exc).__name__}))
        return 3
    print(json.dumps(verdict, sort_keys=True))
    return 2 if verdict["verdict"] == "SUPPRESS_EXFILTRATION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
