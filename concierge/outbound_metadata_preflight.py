# SPDX-License-Identifier: MIT
"""Offline preflight for outbound GitHub metadata hygiene."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, TextIO


MAX_INPUT_BYTES = 256 * 1024
MAX_FIELD_BYTES = 128 * 1024
ALLOWED_FIELDS = ("title", "body", "commit_message", "comment", "review")

RULES = (
    ("COAUTHOR_TRAILER", re.compile(r"(?im)^\\s*co-authored-by\\s*:")),
    (
        "GENERATED_ATTRIBUTION",
        re.compile(r"(?im)^\\s*(?:generated|prepared|written|created)\\s+(?:with|by)\\s+(?:an?\\s+)?ai\\b"),
    ),
    (
        "BYLINE_HEADER",
        re.compile(r"(?im)^\\s*(?:attribution|model|agent|owner/source|owner/finalizer|source/test/finalizer|prepared\\s+by)\\s*:"),
    ),
    (
        "HARNESS_BYLINE",
        re.compile(r"(?i)\\b(?:cloud|connector)\\s+harness\\b|\\bcloud\\s+seat\\b"),
    ),
    (
        "INTERNAL_LANE",
        re.compile(r"(?i)(?<![a-z0-9])(?:sol-[a-z0-9][a-z0-9_-]*|astra-[a-z0-9][a-z0-9_-]*|sedge-[a-z0-9][a-z0-9_-]*)\\b"),
    ),
    (
        "INTERNAL_BRANCH_PREFIX",
        re.compile(r"(?i)(?:^|[/:])(?:sol56|solglasswing|sentinel)(?:[/-][a-z0-9._-]+)"),
    ),
    (
        "CREDIT_BYLINE",
        re.compile(r"(?im)^\\s*(?:(?:[a-z0-9_-]+\\s+)?claimant\\b|implementation\\s+carrier\\s+published\\b)"),
    ),
)


class MetadataPreflightError(ValueError):
    """The local preflight input is malformed or exceeds its bounded surface."""


@dataclass(frozen=True)
class MetadataViolation:
    field: str
    line: int
    code: str


def inspect_outbound_metadata(metadata: Mapping[str, Any]) -> list[MetadataViolation]:
    """Return policy-marker locations without provider I/O or source excerpts."""
    if not isinstance(metadata, Mapping):
        raise MetadataPreflightError("metadata must be an object")
    if set(metadata) - set(ALLOWED_FIELDS):
        raise MetadataPreflightError("unsupported metadata field")

    violations = []
    for field in ALLOWED_FIELDS:
        value = metadata.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise MetadataPreflightError("metadata fields must be strings")
        if len(value.encode("utf-8")) > MAX_FIELD_BYTES:
            raise MetadataPreflightError("metadata field exceeds size bound")
        for code, pattern in RULES:
            for match in pattern.finditer(value):
                violations.append(
                    MetadataViolation(
                        field=field,
                        line=value.count("\n", 0, match.start()) + 1,
                        code=code,
                    )
                )
    return sorted(
        violations,
        key=lambda item: (ALLOWED_FIELDS.index(item.field), item.line, item.code),
    )


def _read_payload(path: str, stdin: TextIO) -> Mapping[str, Any]:
    if path == "-":
        raw = stdin.read(MAX_INPUT_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
            raise MetadataPreflightError("input exceeds size bound")
    else:
        source = Path(path)
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise MetadataPreflightError("input exceeds size bound")
        raw = source.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise MetadataPreflightError("input must be UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise MetadataPreflightError("input must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="JSON metadata file, or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        violations = inspect_outbound_metadata(_read_payload(args.input, sys.stdin))
    except (OSError, MetadataPreflightError):
        print("outbound-metadata-preflight: invalid bounded input", file=sys.stderr)
        return 1

    result = {
        "schema": "outbound-metadata-preflight/v1",
        "status": "BLOCK" if violations else "CLEAR",
        "provider_requests": 0,
        "violations": [asdict(item) for item in violations],
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif violations:
        for item in violations:
            print(f"{item.field}:{item.line} {item.code}")
    else:
        print("CLEAR: outbound metadata contains no known attribution markers")
    return 2 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
