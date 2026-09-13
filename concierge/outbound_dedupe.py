# SPDX-License-Identifier: MIT
"""Deterministic provider-truth gate for outbound revenue outreach.

The gate consumes caller-normalized provider query receipts and decides whether
an outbound offer may be dispatched.  It never connects to Gmail (or any other
provider) and deliberately rejects message bodies, subjects, headers, snippets,
and other raw correspondence fields.  The intent is to make stale-feed
duplicate suppression auditable without moving mailbox contents or credentials
into Bounty Concierge.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


class OutboundDedupeInputError(ValueError):
    """Raised when provider evidence is incomplete or structurally unreliable."""


_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OFFER_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+=-]{0,255}$")
_ALLOWED_OBSERVATION_STATES = frozenset({"sent", "draft", "received"})
_FORBIDDEN_RAW_FIELDS = frozenset(
    {
        "body",
        "body_html",
        "body_text",
        "headers",
        "html",
        "raw",
        "snippet",
        "subject",
    }
)


def _canonical_email(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise OutboundDedupeInputError(f"{field} must be an email address string")
    address = value.strip()
    if not address or not _EMAIL_RE.fullmatch(address):
        raise OutboundDedupeInputError(f"{field} must be a plain addr-spec email address")
    local, domain = address.rsplit("@", 1)
    if not local or not domain or local.startswith(".") or local.endswith(".") or ".." in local:
        raise OutboundDedupeInputError(f"{field} must be a valid plain addr-spec email address")
    try:
        canonical_domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise OutboundDedupeInputError(f"{field} domain is not valid IDNA") from exc
    if (
        not canonical_domain
        or canonical_domain.startswith(".")
        or canonical_domain.endswith(".")
        or ".." in canonical_domain
    ):
        raise OutboundDedupeInputError(f"{field} domain is invalid")
    return f"{local.casefold()}@{canonical_domain}"


def _offer_key(value: Any, *, field: str = "offer_key") -> str:
    if not isinstance(value, str):
        raise OutboundDedupeInputError(f"{field} must be a string")
    key = value.strip()
    if not _OFFER_KEY_RE.fullmatch(key):
        raise OutboundDedupeInputError(
            f"{field} must be a lowercase stable key using letters, digits, . _ : / -"
        )
    return key


def _provider(value: Any) -> str:
    if not isinstance(value, str):
        raise OutboundDedupeInputError("provider must be a string")
    provider = value.strip().casefold()
    if not _PROVIDER_RE.fullmatch(provider):
        raise OutboundDedupeInputError(
            "provider must use lowercase letters, digits, dot, underscore, or hyphen"
        )
    return provider


def _message_id(value: Any) -> str:
    if not isinstance(value, str):
        raise OutboundDedupeInputError("message_id must be a string")
    message_id = value.strip()
    if not _MESSAGE_ID_RE.fullmatch(message_id):
        raise OutboundDedupeInputError("message_id must be a compact opaque provider id")
    return message_id


def _timestamp(value: Any, *, field: str, now: datetime) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OutboundDedupeInputError(f"{field} must be an ISO-8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise OutboundDedupeInputError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OutboundDedupeInputError(f"{field} must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now + timedelta(minutes=5):
        raise OutboundDedupeInputError(f"{field} must not be in the future")
    return parsed


def _normalized_now(now: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime):
        raise OutboundDedupeInputError("now must be a datetime")
    if current.tzinfo is None or current.utcoffset() is None:
        raise OutboundDedupeInputError("now must include a timezone")
    return current.astimezone(timezone.utc)


def _evidence_id(provider: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{provider}\0{message_id}".encode("utf-8")).hexdigest()
    return digest[:20]


def _validate_observation(
    raw: Any,
    *,
    provider: str,
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise OutboundDedupeInputError("each provider observation must be an object")
    forbidden = sorted(_FORBIDDEN_RAW_FIELDS.intersection(raw))
    if forbidden:
        raise OutboundDedupeInputError(
            "provider observations must not contain raw correspondence fields: "
            + ",".join(forbidden)
        )
    state = raw.get("state")
    if state not in _ALLOWED_OBSERVATION_STATES:
        raise OutboundDedupeInputError(
            "observation state must be sent, draft, or received"
        )
    message_id = _message_id(raw.get("message_id"))
    recipient = _canonical_email(raw.get("recipient"), field="observation recipient")
    offer_key = _offer_key(raw.get("offer_key"), field="observation offer_key")
    observed_at = _timestamp(raw.get("observed_at"), field="observed_at", now=now)
    return {
        "provider": provider,
        "state": state,
        "message_id": message_id,
        "recipient": recipient,
        "offer_key": offer_key,
        "observed_at": observed_at,
    }


def _validate_query(raw: Any, *, now: datetime) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise OutboundDedupeInputError("each provider query must be an object")
    provider = _provider(raw.get("provider"))
    complete = raw.get("complete")
    if type(complete) is not bool:
        raise OutboundDedupeInputError("provider query complete must be boolean")
    observations = raw.get("observations")
    if not isinstance(observations, list):
        raise OutboundDedupeInputError("provider query observations must be a list")

    normalized: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in observations:
        observation = _validate_observation(item, provider=provider, now=now)
        previous = by_id.get(observation["message_id"])
        if previous is not None:
            if previous != observation:
                raise OutboundDedupeInputError(
                    f"provider {provider} returned conflicting observations for one message_id"
                )
            continue
        by_id[observation["message_id"]] = observation
        normalized.append(observation)

    return {
        "provider": provider,
        "complete": complete,
        "observations": normalized,
    }


def evaluate_outbound_dedupe(
    request: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a safe dispatch receipt from normalized provider observations.

    A matching provider observation blocks dispatch only when all three
    authoritative identifiers agree: state ``sent``, canonical recipient, and
    stable offer key.  Every declared provider query must be complete before an
    otherwise-clear request can dispatch.
    """
    if not isinstance(request, dict):
        raise OutboundDedupeInputError("request must be an object")
    current = _normalized_now(now)
    recipient = _canonical_email(request.get("recipient"), field="recipient")
    offer_key = _offer_key(request.get("offer_key"))

    queries_raw = request.get("provider_queries")
    if not isinstance(queries_raw, list) or not queries_raw:
        raise OutboundDedupeInputError("provider_queries must be a non-empty list")

    queries: list[dict[str, Any]] = []
    providers: set[str] = set()
    for raw_query in queries_raw:
        query = _validate_query(raw_query, now=current)
        provider = query["provider"]
        if provider in providers:
            raise OutboundDedupeInputError(
                f"provider {provider} must appear at most once per request"
            )
        providers.add(provider)
        queries.append(query)

    incomplete = sorted(query["provider"] for query in queries if not query["complete"])
    matches: list[dict[str, str]] = []
    observation_count = 0
    for query in queries:
        for observation in query["observations"]:
            observation_count += 1
            if (
                observation["state"] == "sent"
                and observation["recipient"] == recipient
                and observation["offer_key"] == offer_key
            ):
                matches.append(
                    {
                        "provider": observation["provider"],
                        "evidence_id": _evidence_id(
                            observation["provider"], observation["message_id"]
                        ),
                        "observed_at": observation["observed_at"]
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                )

    matches.sort(key=lambda item: (item["observed_at"], item["provider"], item["evidence_id"]))

    if matches:
        disposition = "DNR"
        dispatch = False
        reason_codes = ["PROVIDER_SENT_MATCH"]
    elif incomplete:
        disposition = "HOLD"
        dispatch = False
        reason_codes = ["PROVIDER_QUERY_INCOMPLETE"]
    else:
        disposition = "CLEAR"
        dispatch = True
        reason_codes = ["NO_PROVIDER_SENT_MATCH"]

    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "recipient": recipient,
        "offer_key": offer_key,
        "reason_codes": reason_codes,
        "signals": {
            "provider_count": len(queries),
            "provider_observation_count": observation_count,
            "incomplete_providers": incomplete,
            "matching_sent_count": len(matches),
            "matching_sent_evidence": matches,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    """Format a compact receipt without provider message ids or correspondence."""
    codes = ",".join(result.get("reason_codes", [])) or "none"
    signals = result.get("signals", {})
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"recipient={result['recipient']} "
        f"offer={result['offer_key']} "
        f"matching_sent={signals.get('matching_sent_count', 0)} "
        f"reasons={codes}"
    )


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise OutboundDedupeInputError("request JSON must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.outbound_dedupe",
        description=(
            "Fail closed on duplicate revenue outreach using normalized "
            "provider-side sent evidence."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full safe receipt JSON")
    args = parser.parse_args(argv)

    try:
        request = _load_request(args.request)
        result = evaluate_outbound_dedupe(request)
    except (OSError, json.JSONDecodeError, OutboundDedupeInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))

    if result["dispatch"]:
        return 0
    if result["disposition"] == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
