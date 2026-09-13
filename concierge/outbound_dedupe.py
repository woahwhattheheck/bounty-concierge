# SPDX-License-Identifier: MIT
"""Fail-closed provider-truth gate for outbound revenue outreach.

This module is offline: callers collect minimal provider-side search receipts,
then this gate decides whether another outreach send is safe.  It does not
connect to providers, send messages, mutate mailboxes, or handle credentials.

Dispatch authority is deliberately narrow. A CLEAR decision requires:
* an out-of-band trusted inventory of every send-capable provider,
* exactly one query receipt for every required provider,
* each query bound to the exact canonical recipient + offer key,
* every required query complete and fresh,
* no matching provider-side ``sent`` observation.

Any known matching sent receipt is DNR even when sibling provider evidence is
missing, incomplete, or stale. Unknown or incomplete authority never becomes
permission to send.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


class OutboundDedupeInputError(ValueError):
    """Raised when input cannot safely participate in a dispatch decision."""


_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OFFER_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+=-]{0,255}$")
_ALLOWED_OBSERVATION_STATES = frozenset({"sent", "draft", "received"})
_REQUEST_KEYS = {"recipient", "offer_key", "provider_queries"}
_QUERY_KEYS = {"provider", "recipient", "offer_key", "complete", "completed_at", "observations"}
_OBSERVATION_KEYS = {"state", "message_id", "recipient", "offer_key", "observed_at"}
_MIN_MAX_QUERY_AGE = 30
_MAX_MAX_QUERY_AGE = 3600


def _exact_dict(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise OutboundDedupeInputError(f"{context} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise OutboundDedupeInputError(
            f"{context} fields are not exact (missing={missing}, extra={extra})"
        )
    return value


def _canonical_email(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise OutboundDedupeInputError(f"{field} must be an email address string")
    address = value.strip()
    if address != value or not address or not _EMAIL_RE.fullmatch(address):
        raise OutboundDedupeInputError(f"{field} must be a canonical addr-spec email")
    local, domain = address.rsplit("@", 1)
    if not local or not domain or local.startswith(".") or local.endswith(".") or ".." in local:
        raise OutboundDedupeInputError(f"{field} must be a valid addr-spec email")
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
    canonical = f"{local.casefold()}@{canonical_domain}"
    # Case-folding the local part is an explicit operational identity choice for
    # dedupe, not an SMTP-equivalence claim.
    return canonical


def _provider(value: Any, *, field: str = "provider") -> str:
    if type(value) is not str:
        raise OutboundDedupeInputError(f"{field} must be a string")
    provider = value.strip()
    if provider != value or provider != provider.casefold() or not _PROVIDER_RE.fullmatch(provider):
        raise OutboundDedupeInputError(
            f"{field} must use lowercase letters, digits, dot, underscore, or hyphen"
        )
    return provider


def _offer_key(value: Any, *, field: str = "offer_key") -> str:
    if type(value) is not str:
        raise OutboundDedupeInputError(f"{field} must be a string")
    key = value.strip()
    if key != value or not _OFFER_KEY_RE.fullmatch(key):
        raise OutboundDedupeInputError(
            f"{field} must be a lowercase stable key using letters, digits, . _ : / -"
        )
    return key


def _message_id(value: Any) -> str:
    if type(value) is not str:
        raise OutboundDedupeInputError("message_id must be a string")
    message_id = value.strip()
    if message_id != value or not _MESSAGE_ID_RE.fullmatch(message_id):
        raise OutboundDedupeInputError("message_id must be a compact opaque provider id")
    return message_id


def _normalized_now(now: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime):
        raise OutboundDedupeInputError("now must be a datetime")
    if current.tzinfo is None or current.utcoffset() is None:
        raise OutboundDedupeInputError("now must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: Any, *, field: str, now: datetime) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        raise OutboundDedupeInputError(f"{field} must be a canonical ISO-8601 timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise OutboundDedupeInputError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OutboundDedupeInputError(f"{field} must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now:
        raise OutboundDedupeInputError(f"{field} must not be in the future")
    return parsed


def _required_provider_set(required_providers: Iterable[str]) -> tuple[str, ...]:
    if isinstance(required_providers, (str, bytes)):
        raise OutboundDedupeInputError("required_providers must be an iterable of provider ids")
    try:
        raw = list(required_providers)
    except TypeError as exc:
        raise OutboundDedupeInputError(
            "required_providers must be an iterable of provider ids"
        ) from exc
    if not raw:
        raise OutboundDedupeInputError("required_providers must not be empty")
    if len(raw) > 32:
        raise OutboundDedupeInputError("required_providers exceeds supported inventory")
    normalized = [_provider(item, field="required_provider") for item in raw]
    if len(normalized) != len(set(normalized)):
        raise OutboundDedupeInputError("required_providers must be unique")
    return tuple(sorted(normalized))


def _query_age_policy(value: Any) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise OutboundDedupeInputError("max_query_age_seconds must be an integer")
    if not _MIN_MAX_QUERY_AGE <= value <= _MAX_MAX_QUERY_AGE:
        raise OutboundDedupeInputError(
            f"max_query_age_seconds must be between {_MIN_MAX_QUERY_AGE} and {_MAX_MAX_QUERY_AGE}"
        )
    return value


def _evidence_id(provider: str, message_id: str) -> str:
    return hashlib.sha256(f"{provider}\0{message_id}".encode("utf-8")).hexdigest()[:20]


def _recipient_id(recipient: str) -> str:
    return hashlib.sha256(recipient.encode("utf-8")).hexdigest()[:20]


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _provider_evidence_digest(
    *,
    recipient: str,
    offer_key: str,
    required: tuple[str, ...],
    queries: dict[str, dict[str, Any]],
) -> str:
    safe_queries: list[dict[str, Any]] = []
    for provider in sorted(queries):
        query = queries[provider]
        safe_observations = [
            {
                "state": observation["state"],
                "message_id_sha256": hashlib.sha256(
                    observation["message_id"].encode("utf-8")
                ).hexdigest(),
                "observed_at": _utc_text(observation["observed_at"]),
            }
            for observation in query["observations"]
        ]
        safe_observations.sort(
            key=lambda row: (
                row["observed_at"],
                row["state"],
                row["message_id_sha256"],
            )
        )
        safe_queries.append(
            {
                "provider": provider,
                "complete": query["complete"],
                "completed_at": _utc_text(query["completed_at"]),
                "observations": safe_observations,
            }
        )
    material = {
        "recipient_id": _recipient_id(recipient),
        "offer_key": offer_key,
        "required_providers": list(required),
        "queries": safe_queries,
    }
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_observation(
    raw: Any,
    *,
    provider: str,
    target_recipient: str,
    target_offer_key: str,
    now: datetime,
) -> dict[str, Any]:
    row = _exact_dict(raw, _OBSERVATION_KEYS, "provider observation")
    state = row["state"]
    if state not in _ALLOWED_OBSERVATION_STATES:
        raise OutboundDedupeInputError(
            "observation state must be sent, draft, or received"
        )
    message_id = _message_id(row["message_id"])
    recipient = _canonical_email(row["recipient"], field="observation recipient")
    offer_key = _offer_key(row["offer_key"], field="observation offer_key")
    observed_at = _timestamp(row["observed_at"], field="observed_at", now=now)
    # Provider searches are target-bound. Unrelated observations indicate the
    # normalizer did not produce the promised exact target query.
    if recipient != target_recipient or offer_key != target_offer_key:
        raise OutboundDedupeInputError(
            "provider observation does not match the query recipient/offer target"
        )
    return {
        "provider": provider,
        "state": state,
        "message_id": message_id,
        "recipient": recipient,
        "offer_key": offer_key,
        "observed_at": observed_at,
    }


def _validate_query(
    raw: Any,
    *,
    target_recipient: str,
    target_offer_key: str,
    now: datetime,
) -> dict[str, Any]:
    row = _exact_dict(raw, _QUERY_KEYS, "provider query")
    provider = _provider(row["provider"])
    query_recipient = _canonical_email(row["recipient"], field="query recipient")
    query_offer_key = _offer_key(row["offer_key"], field="query offer_key")
    if query_recipient != target_recipient or query_offer_key != target_offer_key:
        raise OutboundDedupeInputError(
            "provider query target does not match request recipient/offer"
        )
    complete = row["complete"]
    if type(complete) is not bool:
        raise OutboundDedupeInputError("provider query complete must be boolean")
    completed_at = _timestamp(row["completed_at"], field="completed_at", now=now)
    observations = row["observations"]
    if type(observations) is not list or len(observations) > 10_000:
        raise OutboundDedupeInputError(
            "provider query observations must be a bounded list"
        )
    by_id: dict[str, dict[str, Any]] = {}
    normalized: list[dict[str, Any]] = []
    for item in observations:
        observation = _validate_observation(
            item,
            provider=provider,
            target_recipient=target_recipient,
            target_offer_key=target_offer_key,
            now=now,
        )
        if observation["observed_at"] > completed_at:
            raise OutboundDedupeInputError(
                "provider observation must not occur after query completed_at"
            )
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
        "completed_at": completed_at,
        "observations": normalized,
    }


def evaluate_outbound_dedupe(
    request: dict[str, Any],
    *,
    required_providers: Iterable[str],
    now: datetime | None = None,
    max_query_age_seconds: int = 300,
) -> dict[str, Any]:
    """Evaluate provider-side duplicate authority without performing a send."""
    row = _exact_dict(request, _REQUEST_KEYS, "request")
    current = _normalized_now(now)
    max_age = _query_age_policy(max_query_age_seconds)
    required = _required_provider_set(required_providers)
    recipient = _canonical_email(row["recipient"], field="recipient")
    offer_key = _offer_key(row["offer_key"])

    queries_raw = row["provider_queries"]
    if type(queries_raw) is not list or len(queries_raw) > 32:
        raise OutboundDedupeInputError("provider_queries must be a bounded list")

    queries: dict[str, dict[str, Any]] = {}
    for raw_query in queries_raw:
        query = _validate_query(
            raw_query,
            target_recipient=recipient,
            target_offer_key=offer_key,
            now=current,
        )
        provider = query["provider"]
        if provider in queries:
            raise OutboundDedupeInputError(
                f"provider {provider} must appear at most once per request"
            )
        if provider not in required:
            raise OutboundDedupeInputError(
                f"provider {provider} is not in the trusted required provider inventory"
            )
        queries[provider] = query

    missing = sorted(set(required) - set(queries))
    incomplete = sorted(
        provider for provider, query in queries.items() if not query["complete"]
    )
    stale = sorted(
        provider
        for provider, query in queries.items()
        if (current - query["completed_at"]).total_seconds() > max_age
    )

    matches: list[dict[str, str]] = []
    observation_count = 0
    for provider, query in queries.items():
        for observation in query["observations"]:
            observation_count += 1
            if observation["state"] == "sent":
                matches.append(
                    {
                        "provider": provider,
                        "evidence_id": _evidence_id(provider, observation["message_id"]),
                        "observed_at": observation["observed_at"]
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                )
    matches.sort(
        key=lambda item: (item["observed_at"], item["provider"], item["evidence_id"])
    )

    if matches:
        disposition = "DNR"
        dispatch = False
        reason_codes = ["PROVIDER_SENT_MATCH"]
    else:
        hold_reasons: list[str] = []
        if missing:
            hold_reasons.append("REQUIRED_PROVIDER_MISSING")
        if incomplete:
            hold_reasons.append("PROVIDER_QUERY_INCOMPLETE")
        if stale:
            hold_reasons.append("PROVIDER_QUERY_STALE")
        if hold_reasons:
            disposition = "HOLD"
            dispatch = False
            reason_codes = hold_reasons
        else:
            disposition = "CLEAR"
            dispatch = True
            reason_codes = ["NO_PROVIDER_SENT_MATCH"]

    receipt_core = {
        "schema": "outbound-dedupe/v2",
        "disposition": disposition,
        "dispatch": dispatch,
        "recipient_id": _recipient_id(recipient),
        "offer_key": offer_key,
        "provider_evidence_sha256": _provider_evidence_digest(
            recipient=recipient,
            offer_key=offer_key,
            required=required,
            queries=queries,
        ),
        "reason_codes": reason_codes,
        "signals": {
            "required_providers": list(required),
            "queried_providers": sorted(queries),
            "missing_providers": missing,
            "incomplete_providers": incomplete,
            "stale_providers": stale,
            "provider_observation_count": observation_count,
            "matching_sent_count": len(matches),
            "matching_sent_evidence": matches,
            "max_query_age_seconds": max_age,
        },
        "authority": {
            "effect": "pre_send_duplicate_gate_only",
            "required_provider_inventory_bound": True,
            "query_target_bound": True,
            "freshness_bound": True,
            "external_send_performed": False,
            "provider_mutation_performed": False,
            "raw_correspondence_retained": False,
            "provider_message_ids_retained": False,
            "integrity_digest_is_external_authenticity": False,
        },
    }
    canonical_receipt = json.dumps(
        receipt_core,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        **receipt_core,
        "receipt_sha256": hashlib.sha256(canonical_receipt).hexdigest(),
    }


def format_summary(result: dict[str, Any]) -> str:
    codes = ",".join(result.get("reason_codes", [])) or "none"
    signals = result.get("signals", {})
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"recipient_id={result['recipient_id']} "
        f"offer={result['offer_key']} "
        f"matching_sent={signals.get('matching_sent_count', 0)} "
        f"reasons={codes}"
    )


def _strict_json_loads(text: str) -> Any:
    if type(text) is not str:
        raise OutboundDedupeInputError("request JSON must be text")

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise OutboundDedupeInputError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=pairs_hook)
    except OutboundDedupeInputError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise OutboundDedupeInputError("request JSON is invalid") from exc


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    payload = _strict_json_loads(raw)
    if type(payload) is not dict:
        raise OutboundDedupeInputError("request JSON must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.outbound_dedupe",
        description="Fail closed on duplicate outreach using trusted provider inventory.",
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument(
        "--required-provider",
        action="append",
        dest="required_providers",
        required=True,
        help="trusted send-capable provider id; repeat for every provider",
    )
    parser.add_argument(
        "--max-query-age-seconds",
        type=int,
        default=300,
        help="maximum age of a complete provider query before HOLD (30..3600)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = _load_request(args.request)
        result = evaluate_outbound_dedupe(
            request,
            required_providers=args.required_providers,
            max_query_age_seconds=args.max_query_age_seconds,
        )
    except (OSError, OutboundDedupeInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if result["dispatch"]:
        return 0
    return 3 if result["disposition"] == "DNR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
