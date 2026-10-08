# SPDX-License-Identifier: MIT
"""Strict, consistent GitHub PR-census evidence for acceptance liquidity."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

INPUT_SCHEMA = "sponsor-acceptance-liquidity-input/v1"
RECEIPT_SCHEMA = "sponsor-acceptance-liquidity-receipt/v1"
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


class SponsorLiquidityInputError(ValueError):
    pass


def _keys(value: Any, expected: set[str]) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise SponsorLiquidityInputError("invalid PR census fields")
    return value


def _login(value: Any) -> str:
    if type(value) is not str or _LOGIN.fullmatch(value) is None:
        raise SponsorLiquidityInputError("invalid GitHub login")
    return value.casefold()


def _time(value: Any) -> datetime:
    if type(value) is not str or len(value) > 40 or not value.endswith("Z"):
        raise SponsorLiquidityInputError("timestamp must be RFC3339 UTC")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SponsorLiquidityInputError("invalid timestamp") from exc
    if result.utcoffset() != timedelta(0):
        raise SponsorLiquidityInputError("timestamp must be UTC")
    return result


def _stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(value: Any) -> str:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise SponsorLiquidityInputError("invalid JSON receipt") from exc
    return hashlib.sha256(data).hexdigest()


def normalize(request: dict[str, Any]) -> tuple[dict, int]:
    """Return canonical evidence and its age; no external provenance asserted."""
    r = _keys(request, {
        "schema", "repository", "actor_login", "observed_at", "evaluated_at",
        "window_start_at", "max_age_seconds", "all_open_prs",
        "all_closed_prs_since_window", "open_prs", "closed_prs",
    })
    if r["schema"] != INPUT_SCHEMA:
        raise SponsorLiquidityInputError("unsupported input schema")
    repo = r["repository"]
    if type(repo) is not str or (m := _REPO.fullmatch(repo)) is None:
        raise SponsorLiquidityInputError("invalid repository")
    repo = _login(m.group(1)) + "/" + m.group(2).casefold()
    actor = _login(r["actor_login"])
    observed, evaluated, start = map(_time, (r["observed_at"], r["evaluated_at"], r["window_start_at"]))
    if not start <= observed <= evaluated:
        raise SponsorLiquidityInputError("observation times contradict")
    if not timedelta(days=7) <= evaluated - start <= timedelta(days=90):
        raise SponsorLiquidityInputError("closed PR window must cover 7..90 days")
    age = int((evaluated - observed).total_seconds())
    max_age = r["max_age_seconds"]
    if type(max_age) is not int or not 60 <= max_age <= 3600:
        raise SponsorLiquidityInputError("max_age_seconds must be 60..3600")
    if type(r["all_open_prs"]) is not bool or type(r["all_closed_prs_since_window"]) is not bool:
        raise SponsorLiquidityInputError("invalid completeness flags")
    seen: set[int] = set()
    prs: dict[str, list[dict]] = {}
    for group in ("open_prs", "closed_prs"):
        raw = r[group]
        if type(raw) is not list or len(raw) > 5000:
            raise SponsorLiquidityInputError("invalid PR inventory size")
        parsed = []
        for entry in raw:
            fields = {"number", "author_login", "created_at"}
            if group == "closed_prs":
                fields |= {"closed_at", "merged_at"}
            p = _keys(entry, fields)
            n = p["number"]
            if type(n) is not int or not 1 <= n < 2**53 or n in seen:
                raise SponsorLiquidityInputError("invalid or duplicate PR number")
            seen.add(n)
            created = _time(p["created_at"])
            if created > observed:
                raise SponsorLiquidityInputError("PR created after observation")
            row = {"number": n, "author_login": _login(p["author_login"]),
                   "created_at": _stamp(created)}
            if group == "closed_prs":
                closed = _time(p["closed_at"])
                merged = None if p["merged_at"] is None else _time(p["merged_at"])
                if not start <= closed <= observed or closed < created:
                    raise SponsorLiquidityInputError("closed PR outside window")
                if merged is not None and not created <= merged <= closed:
                    raise SponsorLiquidityInputError("merge timestamp contradicts closure")
                row.update({"closed_at": _stamp(closed),
                            "merged_at": None if merged is None else _stamp(merged)})
            parsed.append(row)
        prs[group] = sorted(parsed, key=lambda p: p["number"])
    return ({"schema": INPUT_SCHEMA, "repository": repo, "actor_login": actor,
             "observed_at": _stamp(observed), "evaluated_at": _stamp(evaluated),
             "window_start_at": _stamp(start), "max_age_seconds": max_age,
             "all_open_prs": r["all_open_prs"],
             "all_closed_prs_since_window": r["all_closed_prs_since_window"], **prs}, age)
