# SPDX-License-Identifier: MIT
"""Reuse explicitly selected issue outcomes without querying a provider."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit


_ISSUE = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+)#([1-9][0-9]*)\Z")
_FIELDS = frozenset({"reason", "source_url", "observed_at"})


def normalize_exclusions(value: Any) -> dict[tuple[str, int], dict[str, str]]:
    """Validate a caller-supplied map, retaining its original evidence times."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("issue exclusions must be an owner/repo#number object")
    result: dict[tuple[str, int], dict[str, str]] = {}
    for issue, record in value.items():
        match = _ISSUE.fullmatch(issue) if isinstance(issue, str) else None
        if match is None or match[1].split("/")[1] in {".", ".."}:
            raise ValueError("invalid issue exclusion identity")
        key = match[1].casefold(), int(match[2])
        if key in result:
            raise ValueError("duplicate issue identity in exclusions")
        if not isinstance(record, dict) or set(record) != _FIELDS:
            raise ValueError("issue exclusions require reason, source_url and observed_at")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 2048
               for item in record.values()):
            raise ValueError("invalid issue exclusion evidence")
        source = urlsplit(record["source_url"])
        if source.scheme != "https" or not source.netloc or source.username or source.password:
            raise ValueError("issue exclusion source must be an HTTPS URL")
        observed = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("issue exclusion observation requires a timezone")
        result[key] = dict(record)
    return result


def unique_exclusion_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Do not silently replace repeated JSON records or evidence fields."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field in issue exclusions")
        result[key] = value
    return result
