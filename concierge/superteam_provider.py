# SPDX-License-Identifier: MIT
"""Read-only Superteam Earn agent opportunity provider.

The official Superteam agent API is a first-party discovery source for listings
that explicitly allow agent submissions. This module only discovers and
normalizes work. It never registers an agent, submits work, posts comments,
claims a payout, performs KYC/wallet actions, or recognizes revenue.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import requests


BASE_URL = "https://superteam.fun"
LIVE_LISTINGS_URL = f"{BASE_URL}/api/agents/listings/live"
DETAILS_URL_PREFIX = f"{BASE_URL}/api/agents/listings/details"
LISTING_URL_PREFIX = f"{BASE_URL}/earn/listing"
API_KEY_ENV = "SUPERTEAM_EARN_API_KEY"
MAX_RESPONSE_BYTES = 2_000_000
STREAM_CHUNK_BYTES = 64 * 1024
MAX_DECIMAL_SIGNIFICANT_DIGITS = 128
MAX_DECIMAL_TEXT_CHARS = 256
MAX_BATCHES = 5
MAX_TOTAL_ROWS = 250
_ALLOWED_TYPES = frozenset({"bounty", "project", "hackathon"})
_ALLOWED_ACCESS = frozenset({"AGENT_ALLOWED", "AGENT_ONLY"})
_ALLOWED_COMPENSATION = frozenset({"fixed", "range", "variable"})
_ALLOWED_ELIGIBILITY_TYPES = frozenset({"text", "link"})
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SuperteamProviderError(ValueError):
    """Raised when first-party provider evidence cannot be normalized safely."""


def _text(
    value: Any,
    name: str,
    *,
    max_chars: int = 500,
    allow_none: bool = False,
    allow_newlines: bool = False,
) -> str | None:
    if value is None and allow_none:
        return None
    if type(value) is not str or not value.strip():
        raise SuperteamProviderError(f"{name} must be a non-empty string")
    if len(value) > max_chars:
        raise SuperteamProviderError(f"{name} is outside the supported text boundary")
    if allow_newlines:
        if "\x00" in value or any(
            ord(char) < 0x20 and char not in "\t\n\r" for char in value
        ):
            raise SuperteamProviderError(
                f"{name} is outside the supported text boundary"
            )
    elif _CONTROL_RE.search(value):
        raise SuperteamProviderError(f"{name} is outside the supported text boundary")
    return value


def _optional_text(value: Any, name: str, *, max_chars: int = 500) -> str | None:
    if value is None:
        return None
    return _text(value, name, max_chars=max_chars)


def _optional_bool(value: Any, name: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise SuperteamProviderError(f"{name} must be boolean or null")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SuperteamProviderError(f"{name} must be a non-negative integer")
    return value


def _decimal_fixed_text_size(value: Decimal) -> int:
    """Return the maximum fixed-point characters before trailing-zero stripping."""
    digits = len(value.as_tuple().digits)
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        return MAX_DECIMAL_TEXT_CHARS + 1
    if value.is_zero() and exponent >= 0:
        return 1
    if exponent >= 0:
        return digits + exponent
    if digits + exponent > 0:
        return digits + 1
    return 2 - exponent


def _validate_decimal_boundary(value: Decimal, name: str) -> None:
    digits = len(value.as_tuple().digits)
    if (
        digits > MAX_DECIMAL_SIGNIFICANT_DIGITS
        or _decimal_fixed_text_size(value) > MAX_DECIMAL_TEXT_CHARS
    ):
        raise SuperteamProviderError(f"{name} is outside the supported amount boundary")


def _decimal(value: Any, name: str, *, allow_none: bool = True) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise SuperteamProviderError(
            f"{name} must preserve exact JSON numeric semantics"
        )
    if not isinstance(value, (Decimal, int, str)):
        raise SuperteamProviderError(f"{name} must be an exact decimal")
    if isinstance(value, str) and len(value) > MAX_DECIMAL_TEXT_CHARS:
        raise SuperteamProviderError(f"{name} is outside the supported amount boundary")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise SuperteamProviderError(f"{name} must be an exact decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise SuperteamProviderError(
            f"{name} must be a non-negative finite decimal"
        )
    _validate_decimal_boundary(parsed, name)
    return parsed


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    _validate_decimal_boundary(value, "decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _iso_datetime(value: Any, name: str) -> str:
    raw = _text(value, name, max_chars=64)
    assert raw is not None
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError as exc:
        raise SuperteamProviderError(f"{name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SuperteamProviderError(f"{name} must include an offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _deadline_floor(value: str | None, *, as_of: datetime) -> str:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SuperteamProviderError("as_of must be timezone-aware")
    if value is None:
        return as_of.astimezone(timezone.utc).date().isoformat()
    if type(value) is not str or value != value.strip():
        raise SuperteamProviderError("deadline must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SuperteamProviderError("deadline must be YYYY-MM-DD") from exc
    return parsed.isoformat()


def _api_key(value: str | None) -> str:
    if value is None:
        value = os.environ.get(API_KEY_ENV)
    if type(value) is not str or not value or value != value.strip():
        raise SuperteamProviderError(f"{API_KEY_ENV} is required")
    if (
        len(value) > 512
        or _CONTROL_RE.search(value)
        or any(char.isspace() for char in value)
    ):
        raise SuperteamProviderError(f"{API_KEY_ENV} is malformed")
    return value


def _declared_response_length(response: Any, *, name: str) -> None:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return
    raw_length = headers.get("Content-Length")
    if raw_length is None:
        return
    try:
        header_length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise SuperteamProviderError(f"{name} returned invalid Content-Length") from exc
    if header_length < 0 or header_length > MAX_RESPONSE_BYTES:
        raise SuperteamProviderError(f"{name} response exceeds size limit")


def _bounded_response_bytes(response: Any, *, name: str) -> bytes:
    _declared_response_length(response, name=name)
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        out = bytearray()
        for chunk in iterator(chunk_size=STREAM_CHUNK_BYTES):
            if chunk in (b"", None):
                continue
            if not isinstance(chunk, (bytes, bytearray)):
                raise SuperteamProviderError(f"{name} returned no readable body")
            if len(out) + len(chunk) > MAX_RESPONSE_BYTES:
                raise SuperteamProviderError(f"{name} response exceeds size limit")
            out.extend(chunk)
        return bytes(out)

    # Compatibility for injected test doubles. Production ``requests``
    # responses always take the streamed iterator path above.
    raw = getattr(response, "content", None)
    if not isinstance(raw, (bytes, bytearray)):
        text = getattr(response, "text", None)
        if type(text) is not str:
            raise SuperteamProviderError(f"{name} returned no readable body")
        try:
            raw = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SuperteamProviderError(f"{name} response is not UTF-8") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SuperteamProviderError(f"{name} response exceeds size limit")
    return bytes(raw)


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous JSON objects instead of accepting the last duplicate key."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise SuperteamProviderError(
                f"Superteam response contains duplicate JSON object key {key!r}"
            )
        out[key] = value
    return out


def _response_json(response: Any, *, name: str) -> Any:
    close = getattr(response, "close", None)
    try:
        status = getattr(response, "status_code", None)
        if isinstance(status, bool) or not isinstance(status, int):
            raise SuperteamProviderError(f"{name} returned no HTTP status")
        if status == 401:
            raise SuperteamProviderError("Superteam agent API authentication failed")
        if status == 403:
            raise SuperteamProviderError("Superteam agent API denied this read")
        if status == 429:
            raise SuperteamProviderError("Superteam agent API rate limit reached")
        if status < 200 or status >= 300:
            raise SuperteamProviderError(f"{name} returned HTTP {status}")

        raw = _bounded_response_bytes(response, name=name)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SuperteamProviderError(f"{name} response is not UTF-8") from exc
        try:
            return json.loads(
                text,
                parse_float=Decimal,
                object_pairs_hook=_reject_duplicate_object_pairs,
            )
        except SuperteamProviderError:
            raise
        except (json.JSONDecodeError, InvalidOperation, ValueError) as exc:
            raise SuperteamProviderError(f"{name} returned invalid JSON") from exc
    finally:
        if callable(close):
            close()


def _request_json(
    session: Any,
    url: str,
    *,
    api_key: str,
    params: Any = None,
    name: str,
) -> Any:
    try:
        response = session.get(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "bounty-concierge-superteam-provider/1",
            },
            params=params,
            timeout=15,
            # Never forward the bearer token to a redirect target.
            allow_redirects=False,
            # Bound the body before Requests buffers it into ``content``.
            stream=True,
        )
        return _response_json(response, name=name)
    except requests.RequestException as exc:
        raise SuperteamProviderError(f"{name} request failed") from exc


def _count_block(value: Any) -> dict[str, int]:
    if value is None:
        return {"submissions": 0, "comments": 0}
    if type(value) is not dict:
        raise SuperteamProviderError("_count must be an object")
    return {
        "submissions": _nonnegative_int(
            value.get("Submission", 0), "_count.Submission"
        ),
        "comments": _nonnegative_int(value.get("Comments", 0), "_count.Comments"),
    }


def _sponsor(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise SuperteamProviderError("sponsor must be an object")
    verified = value.get("isVerified")
    if type(verified) is not bool or not verified:
        raise SuperteamProviderError("sponsor must be verified")
    return {
        "name": _text(value.get("name"), "sponsor.name", max_chars=200),
        "slug": _optional_text(value.get("slug"), "sponsor.slug", max_chars=200),
        "verified": True,
        "caution": _optional_bool(value.get("isCaution"), "sponsor.isCaution"),
    }


def _rewards(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if type(value) is not dict:
        raise SuperteamProviderError("rewards must be an object or null")
    out: dict[str, str] = {}
    for raw_rank, raw_amount in value.items():
        if type(raw_rank) is not str or not raw_rank or len(raw_rank) > 32:
            raise SuperteamProviderError("reward rank must be a bounded string")
        amount = _decimal(raw_amount, f"rewards[{raw_rank}]", allow_none=False)
        assert amount is not None
        out[raw_rank] = _decimal_text(amount) or "0"
    return dict(sorted(out.items()))


def _skills(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if type(value) is not list or len(value) > 50:
        raise SuperteamProviderError("skills must be a bounded list")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if type(item) is not dict:
            raise SuperteamProviderError(f"skills[{index}] must be an object")
        parent = _text(item.get("skills"), f"skills[{index}].skills", max_chars=80)
        subs = item.get("subskills", [])
        if type(subs) is not list or len(subs) > 50:
            raise SuperteamProviderError(
                f"skills[{index}].subskills must be a bounded list"
            )
        normalized_subs: list[str] = []
        for sub_index, sub in enumerate(subs):
            normalized = _text(
                sub,
                f"skills[{index}].subskills[{sub_index}]",
                max_chars=80,
            )
            assert normalized is not None
            normalized_subs.append(normalized)
        out.append({"skill": parent, "subskills": normalized_subs})
    return out


def _eligibility(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if type(value) is not list or len(value) > 100:
        raise SuperteamProviderError("eligibility must be a bounded list")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if type(item) is not dict:
            raise SuperteamProviderError(f"eligibility[{index}] must be an object")
        order = _nonnegative_int(item.get("order"), f"eligibility[{index}].order")
        question = _text(
            item.get("question"),
            f"eligibility[{index}].question",
            max_chars=2_000,
            allow_newlines=True,
        )
        question_type = item.get("type")
        if question_type is not None:
            question_type = _text(
                question_type,
                f"eligibility[{index}].type",
                max_chars=16,
            )
            if question_type not in _ALLOWED_ELIGIBILITY_TYPES:
                raise SuperteamProviderError("unsupported eligibility question type")
        optional = item.get("optional", False)
        if type(optional) is not bool:
            raise SuperteamProviderError(
                f"eligibility[{index}].optional must be boolean"
            )
        is_link = item.get("isLink", False)
        if type(is_link) is not bool:
            raise SuperteamProviderError(
                f"eligibility[{index}].isLink must be boolean"
            )
        out.append(
            {
                "order": order,
                "question": question,
                "type": question_type,
                "optional": optional,
                "is_link": is_link,
            }
        )
    return sorted(out, key=lambda item: (item["order"], item["question"]))


def normalize_live_listing(row: Any) -> dict[str, Any]:
    """Normalize one official agent-feed row without copying arbitrary body text."""
    if type(row) is not dict:
        raise SuperteamProviderError("listing row must be an object")

    listing_id = _text(row.get("id"), "id", max_chars=128)
    slug = _text(row.get("slug"), "slug", max_chars=200)
    assert listing_id is not None and slug is not None
    if not _SLUG_RE.fullmatch(slug):
        raise SuperteamProviderError(
            "slug is outside the canonical Superteam format"
        )

    listing_type = _text(row.get("type"), "type", max_chars=32)
    if listing_type not in _ALLOWED_TYPES:
        raise SuperteamProviderError("unsupported listing type")
    access = _text(row.get("agentAccess"), "agentAccess", max_chars=32)
    if access not in _ALLOWED_ACCESS:
        raise SuperteamProviderError("listing is not agent eligible")
    status = _text(row.get("status"), "status", max_chars=32)
    if status != "OPEN":
        raise SuperteamProviderError("listing status must be OPEN")

    compensation_type = _optional_text(
        row.get("compensationType"), "compensationType", max_chars=32
    )
    if (
        compensation_type is not None
        and compensation_type not in _ALLOWED_COMPENSATION
    ):
        raise SuperteamProviderError("unsupported compensation type")

    token = _optional_text(row.get("token"), "token", max_chars=64)
    reward_amount = _decimal(row.get("rewardAmount"), "rewardAmount")
    min_ask = _decimal(row.get("minRewardAsk"), "minRewardAsk")
    max_ask = _decimal(row.get("maxRewardAsk"), "maxRewardAsk")
    if min_ask is not None and max_ask is not None and min_ask > max_ask:
        raise SuperteamProviderError("minRewardAsk exceeds maxRewardAsk")

    prize_breakdown = _rewards(row.get("rewards"))
    counts = _count_block(row.get("_count"))
    sponsor = _sponsor(row.get("sponsor"))
    deadline = _iso_datetime(row.get("deadline"), "deadline")
    winners_announced = row.get("isWinnersAnnounced", False)
    if type(winners_announced) is not bool:
        raise SuperteamProviderError("isWinnersAnnounced must be boolean")

    competitive = listing_type in {"bounty", "hackathon"}
    return {
        "provider": "superteam",
        "provider_contract": "earn-agent-skill/v0.2.0",
        "external_id": listing_id,
        "canonical_url": f"{LISTING_URL_PREFIX}/{slug}",
        "slug": slug,
        "title": _text(row.get("title"), "title", max_chars=500),
        "listing_type": listing_type,
        "status": status,
        "agent_access": access,
        "deadline": deadline,
        "sponsor": sponsor,
        "competition": counts,
        "compensation": {
            "type": compensation_type,
            "token": token,
            "advertised_amount": _decimal_text(reward_amount),
            "min_ask": _decimal_text(min_ask),
            "max_ask": _decimal_text(max_ask),
            "prize_breakdown": prize_breakdown,
            "competitive": competitive,
            # Advertisement is not acceptance, award, settlement, or payment.
            "guaranteed": False,
            "usd_equivalent_inferred": False,
        },
        "winners_announced": winners_announced,
        "dispatch_authorized": False,
        "submission_authorized": False,
        "payout_authorized": False,
        "cash_claim": False,
    }


def fetch_live_opportunities(
    *,
    api_key: str | None = None,
    listing_type: str | None = None,
    take: int = 20,
    deadline: str | None = None,
    max_batches: int = 1,
    as_of: datetime | None = None,
    session: Any = requests,
) -> dict[str, Any]:
    """Fetch agent-eligible listings from Superteam's first-party read endpoint."""
    key = _api_key(api_key)
    if listing_type is not None and listing_type not in _ALLOWED_TYPES:
        raise SuperteamProviderError(
            "listing_type must be bounty, project, or hackathon"
        )
    if isinstance(take, bool) or not isinstance(take, int) or take < 1 or take > 50:
        raise SuperteamProviderError("take must be an integer from 1 to 50")
    if (
        isinstance(max_batches, bool)
        or not isinstance(max_batches, int)
        or max_batches < 1
        or max_batches > MAX_BATCHES
    ):
        raise SuperteamProviderError(
            f"max_batches must be an integer from 1 to {MAX_BATCHES}"
        )
    if take * max_batches > MAX_TOTAL_ROWS:
        raise SuperteamProviderError(
            f"requested feed exceeds {MAX_TOTAL_ROWS} rows"
        )

    now = datetime.now(timezone.utc) if as_of is None else as_of
    floor = _deadline_floor(deadline, as_of=now)
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    last_full = False

    for _batch in range(max_batches):
        params: list[tuple[str, str]] = [
            ("take", str(take)),
            ("deadline", floor),
        ]
        if listing_type is not None:
            params.append(("type", listing_type))
        params.extend(
            ("excludeIds[]", listing_id) for listing_id in sorted(seen_ids)
        )

        payload = _request_json(
            session,
            LIVE_LISTINGS_URL,
            api_key=key,
            params=params,
            name="Superteam live listings",
        )
        if type(payload) is not list:
            raise SuperteamProviderError(
                "Superteam live listings payload must be a list"
            )
        if len(payload) > take:
            raise SuperteamProviderError(
                "Superteam live listings exceeded requested page size"
            )
        last_full = len(payload) == take
        if not payload:
            break

        for raw in payload:
            normalized = normalize_live_listing(raw)
            listing_id = normalized["external_id"]
            if listing_id in seen_ids:
                raise SuperteamProviderError(
                    "Superteam pagination repeated an excluded listing"
                )
            seen_ids.add(listing_id)
            rows.append(normalized)
        if len(payload) < take:
            break

    rows.sort(key=lambda row: (row["deadline"], row["canonical_url"]))
    return {
        "schema": "superteam-agent-opportunity-feed/v1",
        "provider": "superteam",
        "source": LIVE_LISTINGS_URL,
        "deadline_floor": floor,
        "listing_type": listing_type,
        "count": len(rows),
        "truncated": bool(last_full and len(rows) == take * max_batches),
        "opportunities": rows,
        "authority": {
            "discovery": "first_party_agent_feed",
            "agent_eligibility": "provider_enforced_and_revalidated",
            "sponsor_verification": "provider_enforced_and_revalidated",
            "dispatch": False,
            "submission": False,
            "payout": False,
            "cash_claim": False,
            "currency_conversion": False,
        },
    }


def _verify_current_live_identity(
    core: Mapping[str, Any],
    *,
    api_key: str,
    session: Any,
    as_of: datetime | None,
) -> dict[str, str]:
    """Bind details to an exact row in the bounded first-party live feed."""
    live = fetch_live_opportunities(
        api_key=api_key,
        listing_type=core["listing_type"],
        take=50,
        max_batches=MAX_BATCHES,
        as_of=as_of,
        session=session,
    )
    expected_id = core["external_id"]
    expected_slug = core["slug"]
    id_matches = [
        item for item in live["opportunities"] if item["external_id"] == expected_id
    ]
    slug_matches = [
        item for item in live["opportunities"] if item["slug"] == expected_slug
    ]
    exact = [
        item
        for item in id_matches
        if item["slug"] == expected_slug
    ]

    if len(id_matches) > 1 or len(slug_matches) > 1:
        raise SuperteamProviderError(
            "Superteam live feed returned ambiguous listing identity"
        )
    if id_matches and not exact:
        raise SuperteamProviderError(
            "Superteam live feed external id maps to a different slug"
        )
    if slug_matches and not exact:
        raise SuperteamProviderError(
            "Superteam live feed slug maps to a different external id"
        )
    if len(exact) == 1:
        live_row = exact[0]
        # Identity is exact; also require the live row's critical discovery
        # state to match the details row so details cannot widen stale scope.
        for key in ("listing_type", "status", "agent_access"):
            if live_row[key] != core[key]:
                raise SuperteamProviderError(
                    f"Superteam live/details {key} evidence conflicts"
                )
        return {
            "source": LIVE_LISTINGS_URL,
            "external_id": expected_id,
            "slug": expected_slug,
        }
    if live["truncated"]:
        raise SuperteamProviderError(
            "Superteam live feed was truncated before listing identity could be proven"
        )
    raise SuperteamProviderError(
        "Superteam listing is not present in the current live agent feed"
    )


def fetch_listing_details(
    slug: str,
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
    session: Any = requests,
) -> dict[str, Any]:
    """Fetch scope only after exact identity appears in the current live feed."""
    key = _api_key(api_key)
    slug = _text(slug, "slug", max_chars=200)
    assert slug is not None
    if not _SLUG_RE.fullmatch(slug):
        raise SuperteamProviderError(
            "slug is outside the canonical Superteam format"
        )
    payload = _request_json(
        session,
        f"{DETAILS_URL_PREFIX}/{quote(slug, safe='')}",
        api_key=key,
        name="Superteam listing details",
    )
    if type(payload) is not dict:
        raise SuperteamProviderError(
            "Superteam listing details payload must be an object"
        )

    returned_slug = _text(payload.get("slug"), "slug", max_chars=200)
    if returned_slug != slug:
        raise SuperteamProviderError(
            "Superteam listing details do not match the requested slug"
        )

    # The details endpoint is broader than the live-feed query. Reapply the
    # public/published boundary locally, and explicitly reject archived detail
    # evidence when the provider supplies that field.
    if payload.get("isPrivate") is not False or payload.get("isPublished") is not True:
        raise SuperteamProviderError(
            "Superteam listing details are not public and published"
        )
    if payload.get("isArchived") is True:
        raise SuperteamProviderError("Superteam listing details are archived")

    core = normalize_live_listing(
        {
            **payload,
            "_count": {"Submission": 0, "Comments": 0},
        }
    )
    description = _text(
        payload.get("description"),
        "description",
        max_chars=100_000,
        allow_newlines=True,
    )
    requirements = _text(
        payload.get("requirements"),
        "requirements",
        max_chars=100_000,
        allow_none=True,
        allow_newlines=True,
    )
    skills = _skills(payload.get("skills"))
    eligibility = _eligibility(payload.get("eligibility"))
    region = _optional_text(payload.get("region"), "region", max_chars=128)
    foundation_paying = _optional_bool(
        payload.get("isFndnPaying"), "isFndnPaying"
    )

    live_identity = _verify_current_live_identity(
        core,
        api_key=key,
        session=session,
        as_of=as_of,
    )

    return {
        "schema": "superteam-agent-listing-details/v1",
        "listing": core,
        "public_contract": {
            "skills": skills,
            "eligibility": eligibility,
            "region": region,
            "is_private": False,
            "is_published": True,
            "foundation_paying": foundation_paying,
            # Sponsor-authored text is data to evaluate, not instruction
            # authority for the local runtime.
            "untrusted_scope": {
                "description": description,
                "requirements": requirements,
            },
        },
        "authority": {
            "source": "first_party_agent_details",
            "live_identity": live_identity,
            "scope_text": "sponsor_supplied_untrusted_data",
            "dispatch": False,
            "submission": False,
            "payout": False,
            "cash_claim": False,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    """Return a compact receipt that never includes API keys or sponsor text."""
    if result.get("schema") == "superteam-agent-opportunity-feed/v1":
        return (
            f"provider=superteam count={result['count']} "
            f"truncated={str(result['truncated']).lower()} "
            "dispatch=false submission=false payout=false cash_claim=false"
        )
    listing = result.get("listing", {})
    return (
        f"provider=superteam slug={listing.get('slug', 'none')} "
        "dispatch=false submission=false payout=false cash_claim=false"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.superteam_provider",
        description="Read and normalize agent-eligible Superteam Earn opportunities.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="read agent-eligible live listings")
    list_parser.add_argument("--type", choices=sorted(_ALLOWED_TYPES))
    list_parser.add_argument("--take", type=int, default=20)
    list_parser.add_argument("--deadline")
    list_parser.add_argument("--max-batches", type=int, default=1)
    list_parser.add_argument("--json", action="store_true")

    detail_parser = sub.add_parser(
        "details", help="read one currently-live agent-eligible listing contract"
    )
    detail_parser.add_argument("slug")
    detail_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result = fetch_live_opportunities(
                listing_type=args.type,
                take=args.take,
                deadline=args.deadline,
                max_batches=args.max_batches,
            )
        else:
            result = fetch_listing_details(args.slug)
    except (SuperteamProviderError, requests.RequestException) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())