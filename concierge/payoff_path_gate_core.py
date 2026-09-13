# SPDX-License-Identifier: MIT
"""Continuity-safe payoff-path semantics for speculative/unpaid work.

This module replaces caller-authored cumulative spend snapshots with immutable effort
facts and hash-chained owner budget policy generations.  A successor document embeds
the prior gate receipt; compilation rejects omitted or rewritten effort history,
mutated same-generation caps, cross-work transplants, skipped policy generations, and
implicit STOP -> READY resets.  Only an explicit successor owner-cap generation may
reopen a previously exhausted work item, and that transition is surfaced in output.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

WORK_SCHEMA = "payoff-path-work/v2"
PACKET_SCHEMA = "payoff-path-gate/v2"
RECEIPT_SCHEMA = "payoff-path-gate-receipt/v2"
AUTHORITY = "OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION"

MECHANISMS = {
    "BOUNTY", "COMPETITION_PRIZE", "PAID_OFFER_OR_PILOT",
    "PRIME_SUBCONTRACT", "REFERRAL_COMMISSION", "SPONSOR_OR_GRANT",
}
EVENT_FOR_MECHANISM = {
    "BOUNTY": "SUBMIT_WORK",
    "COMPETITION_PRIZE": "ENTER_COMPETITION",
    "PAID_OFFER_OR_PILOT": "SEND_PAID_OFFER",
    "PRIME_SUBCONTRACT": "SECURE_TEAMING",
    "REFERRAL_COMMISSION": "COMPLETE_REFERRAL",
    "SPONSOR_OR_GRANT": "APPLY_FOR_GRANT",
}
VALUE_KINDS = {"FIXED", "POOL", "NEGOTIATED", "UNSPECIFIED_BY_SOURCE"}
STATES = {
    "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW",
    "HOLD_NO_PAYOFF_PATH",
    "HOLD_STALE_OR_INVALID",
    "STOP_UNPAID_WORK",
}

_MAX_INPUT_BYTES = 4_000_000
_MAX_ITEMS = 10_000
_MAX_EVENTS = 100_000
_MAX_ID = 128
_MAX_URL = 2048
_MAX_FREE_MINUTES = 10_000_000
_MAX_SOURCE_AGE_DAYS = 3650
_SAFE_INT = 9_007_199_254_740_991
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_SENSITIVE_REF = re.compile(
    r"(?:^|[-_./:])(email|phone|password|passwd|secret|token|bearer|api[-_]?key|private[-_]?key)(?:$|[-_./:])",
    re.IGNORECASE,
)


class PayoffPathError(ValueError):
    """Raised when payoff-path evidence is structurally unsafe or continuity-invalid."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _exact_keys(value: Any, expected: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PayoffPathError(f"{name} must be an object")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise PayoffPathError(f"{name} keys mismatch; missing={missing} extra={extra}")
    return value


def _text(value: Any, name: str, *, pattern: re.Pattern[str] | None = None, limit: int = _MAX_ID) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > limit:
        raise PayoffPathError(f"{name} must be a bounded non-empty canonical string")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise PayoffPathError(f"{name} contains control characters")
    if pattern is not None and not pattern.fullmatch(value):
        raise PayoffPathError(f"{name} has invalid format")
    return value


def _opaque_ref(value: Any, name: str) -> str:
    ref = _text(value, name, pattern=_ID)
    if "@" in ref or _SENSITIVE_REF.search(ref):
        raise PayoffPathError(f"{name} must not encode contact details or secrets")
    return ref


def _sha(value: Any, name: str) -> str:
    return _text(value, name, pattern=_HEX64, limit=64)


def _nullable_sha(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _sha(value, name)


def _int(value: Any, name: str, *, minimum: int = 0, maximum: int = _SAFE_INT) -> int:
    if type(value) is not int:
        raise PayoffPathError(f"{name} must be an integer (bool is not accepted)")
    if value < minimum or value > maximum:
        raise PayoffPathError(f"{name} must be between {minimum} and {maximum}")
    return value


def _render_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise PayoffPathError("trusted time must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name, limit=24)
    if not _UTC.fullmatch(text):
        raise PayoffPathError(f"{name} must be canonical millisecond UTC")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PayoffPathError(f"{name} is invalid") from exc
    if _render_timestamp(parsed) != text:
        raise PayoffPathError(f"{name} must be canonical millisecond UTC")
    return parsed


def _trusted_now(value: datetime | str | None) -> datetime:
    if value is None:
        value = datetime.now(timezone.utc)
    if type(value) is str:
        return _timestamp(value, "trusted_as_of")
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PayoffPathError("trusted_as_of must be timezone-aware datetime or canonical UTC string")
    return _timestamp(_render_timestamp(value), "trusted_as_of")


def _https_url(value: Any, name: str) -> str:
    text = _text(value, name, limit=_MAX_URL)
    if "`" in text or "<" in text or ">" in text:
        raise PayoffPathError(f"{name} contains unsafe markup characters")
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.fragment:
        raise PayoffPathError(f"{name} must be an HTTPS URL without credentials or fragment")
    if parts.hostname is None:
        raise PayoffPathError(f"{name} must have a hostname")
    return text


def _normalize_value(raw: Any, name: str) -> dict[str, Any]:
    value = _exact_keys(raw, {"kind", "currency", "amount_minor"}, name)
    kind = _text(value["kind"], f"{name}.kind")
    if kind not in VALUE_KINDS:
        raise PayoffPathError(f"{name}.kind is unsupported")
    currency = value["currency"]
    amount = value["amount_minor"]
    if kind in {"FIXED", "POOL"}:
        currency = _text(currency, f"{name}.currency", pattern=_CURRENCY, limit=3)
        amount = _int(amount, f"{name}.amount_minor", minimum=1)
    else:
        if currency is not None or amount is not None:
            raise PayoffPathError(f"{name} cannot attach invented numeric value to {kind}")
        currency = None
        amount = None
    return {"kind": kind, "currency": currency, "amount_minor": amount}


def _normalize_source(raw: Any, name: str) -> dict[str, Any]:
    source = _exact_keys(raw, {"canonical_url", "evidence_ref", "evidence_sha256", "observed_at_utc", "max_age_days"}, name)
    return {
        "canonical_url": _https_url(source["canonical_url"], f"{name}.canonical_url"),
        "evidence_ref": _opaque_ref(source["evidence_ref"], f"{name}.evidence_ref"),
        "evidence_sha256": _sha(source["evidence_sha256"], f"{name}.evidence_sha256"),
        "observed_at_utc": _render_timestamp(_timestamp(source["observed_at_utc"], f"{name}.observed_at_utc")),
        "max_age_days": _int(source["max_age_days"], f"{name}.max_age_days", minimum=1, maximum=_MAX_SOURCE_AGE_DAYS),
    }


def _normalize_conversion(raw: Any, name: str, mechanism: str) -> dict[str, Any]:
    conversion = _exact_keys(raw, {"event", "due_at_utc", "evidence_ref", "evidence_sha256"}, name)
    event = _text(conversion["event"], f"{name}.event")
    expected = EVENT_FOR_MECHANISM[mechanism]
    if event != expected:
        raise PayoffPathError(f"{name}.event {event!r} does not match {mechanism}; expected {expected!r}")
    return {
        "event": event,
        "due_at_utc": _render_timestamp(_timestamp(conversion["due_at_utc"], f"{name}.due_at_utc")),
        "evidence_ref": _opaque_ref(conversion["evidence_ref"], f"{name}.evidence_ref"),
        "evidence_sha256": _sha(conversion["evidence_sha256"], f"{name}.evidence_sha256"),
    }


def _normalize_path(raw: Any, name: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    path = _exact_keys(raw, {"mechanism", "value", "source", "conversion"}, name)
    mechanism = _text(path["mechanism"], f"{name}.mechanism")
    if mechanism not in MECHANISMS:
        raise PayoffPathError(f"{name}.mechanism is unsupported")
    return {
        "mechanism": mechanism,
        "value": _normalize_value(path["value"], f"{name}.value"),
        "source": _normalize_source(path["source"], f"{name}.source"),
        "conversion": _normalize_conversion(path["conversion"], f"{name}.conversion", mechanism),
    }


def _normalize_policy(raw: Any, name: str) -> dict[str, Any]:
    policy = _exact_keys(raw, {
        "policy_id", "generation", "cap_minutes", "observed_at_utc",
        "evidence_ref", "evidence_sha256", "predecessor_policy_sha256",
        "supersedes_receipt_sha256",
    }, name)
    return {
        "policy_id": _opaque_ref(policy["policy_id"], f"{name}.policy_id"),
        "generation": _int(policy["generation"], f"{name}.generation", minimum=1, maximum=_MAX_ITEMS),
        "cap_minutes": _int(policy["cap_minutes"], f"{name}.cap_minutes", minimum=1, maximum=_MAX_FREE_MINUTES),
        "observed_at_utc": _render_timestamp(_timestamp(policy["observed_at_utc"], f"{name}.observed_at_utc")),
        "evidence_ref": _opaque_ref(policy["evidence_ref"], f"{name}.evidence_ref"),
        "evidence_sha256": _sha(policy["evidence_sha256"], f"{name}.evidence_sha256"),
        "predecessor_policy_sha256": _nullable_sha(policy["predecessor_policy_sha256"], f"{name}.predecessor_policy_sha256"),
        "supersedes_receipt_sha256": _nullable_sha(policy["supersedes_receipt_sha256"], f"{name}.supersedes_receipt_sha256"),
    }


def _normalize_event(raw: Any, name: str, work_id: str, opportunity_id: str) -> dict[str, Any]:
    event = _exact_keys(raw, {"event_id", "work_id", "opportunity_id", "minutes", "observed_at_utc", "evidence_ref", "evidence_sha256"}, name)
    normalized = {
        "event_id": _opaque_ref(event["event_id"], f"{name}.event_id"),
        "work_id": _opaque_ref(event["work_id"], f"{name}.work_id"),
        "opportunity_id": _opaque_ref(event["opportunity_id"], f"{name}.opportunity_id"),
        "minutes": _int(event["minutes"], f"{name}.minutes", minimum=1, maximum=_MAX_FREE_MINUTES),
        "observed_at_utc": _render_timestamp(_timestamp(event["observed_at_utc"], f"{name}.observed_at_utc")),
        "evidence_ref": _opaque_ref(event["evidence_ref"], f"{name}.evidence_ref"),
        "evidence_sha256": _sha(event["evidence_sha256"], f"{name}.evidence_sha256"),
    }
    if normalized["work_id"] != work_id or normalized["opportunity_id"] != opportunity_id:
        raise PayoffPathError(f"{name} identity does not match owning work/opportunity")
    return normalized


def _normalize_continuity_row(raw: Any, name: str) -> dict[str, Any]:
    row = _exact_keys(raw, {
        "work_id", "opportunity_id", "policy_generation", "policy_sha256",
        "policy_history", "event_fingerprints", "spent_minutes", "terminal_stop",
    }, name)
    work_id = _opaque_ref(row["work_id"], f"{name}.work_id")
    opportunity_id = _opaque_ref(row["opportunity_id"], f"{name}.opportunity_id")

    history = row["policy_history"]
    if type(history) is not list or not history or len(history) > _MAX_ITEMS:
        raise PayoffPathError(f"{name}.policy_history must be a bounded non-empty array")
    normalized_history = []
    expected_generation = 1
    for i, raw_policy in enumerate(history):
        entry = _exact_keys(raw_policy, {"generation", "policy_sha256"}, f"{name}.policy_history[{i}]")
        generation = _int(entry["generation"], f"{name}.policy_history[{i}].generation", minimum=1, maximum=_MAX_ITEMS)
        if generation != expected_generation:
            raise PayoffPathError(f"{name}.policy_history must contain contiguous generations from 1")
        normalized_history.append({
            "generation": generation,
            "policy_sha256": _sha(entry["policy_sha256"], f"{name}.policy_history[{i}].policy_sha256"),
        })
        expected_generation += 1

    fps = row["event_fingerprints"]
    if type(fps) is not list or len(fps) > _MAX_EVENTS:
        raise PayoffPathError(f"{name}.event_fingerprints must be a bounded array")
    normalized_fps = []
    seen = set()
    for i, raw_fp in enumerate(fps):
        fp = _exact_keys(raw_fp, {"event_id", "event_sha256"}, f"{name}.event_fingerprints[{i}]")
        event_id = _opaque_ref(fp["event_id"], f"{name}.event_fingerprints[{i}].event_id")
        if event_id in seen:
            raise PayoffPathError(f"duplicate predecessor event_id: {event_id}")
        seen.add(event_id)
        normalized_fps.append({"event_id": event_id, "event_sha256": _sha(fp["event_sha256"], f"{name}.event_fingerprints[{i}].event_sha256")})
    normalized_fps.sort(key=lambda value: value["event_id"])
    if type(row["terminal_stop"]) is not bool:
        raise PayoffPathError(f"{name}.terminal_stop must be boolean")
    policy_generation = _int(row["policy_generation"], f"{name}.policy_generation", minimum=1, maximum=_MAX_ITEMS)
    policy_sha256 = _sha(row["policy_sha256"], f"{name}.policy_sha256")
    if normalized_history[-1] != {"generation": policy_generation, "policy_sha256": policy_sha256}:
        raise PayoffPathError(f"{name}.policy_history must end at the current policy generation/digest")
    return {
        "work_id": work_id,
        "opportunity_id": opportunity_id,
        "policy_generation": policy_generation,
        "policy_sha256": policy_sha256,
        "policy_history": normalized_history,
        "event_fingerprints": normalized_fps,
        "spent_minutes": _int(row["spent_minutes"], f"{name}.spent_minutes", minimum=0, maximum=_SAFE_INT),
        "terminal_stop": row["terminal_stop"],
    }


def _normalize_receipt(raw: Any, name: str = "predecessor_receipt") -> dict[str, Any]:
    receipt = _exact_keys(raw, {"schema", "evaluated_at_utc", "source_document_sha256", "packet_sha256", "markdown_sha256", "predecessor_receipt_sha256", "continuity"}, name)
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise PayoffPathError(f"{name}.schema must be {RECEIPT_SCHEMA}")
    rows = receipt["continuity"]
    if type(rows) is not list or len(rows) > _MAX_ITEMS:
        raise PayoffPathError(f"{name}.continuity must be a bounded array")
    normalized_rows = []
    seen = set()
    for i, raw_row in enumerate(rows):
        row = _normalize_continuity_row(raw_row, f"{name}.continuity[{i}]")
        if row["work_id"] in seen:
            raise PayoffPathError(f"duplicate predecessor work_id: {row['work_id']}")
        seen.add(row["work_id"])
        normalized_rows.append(row)
    normalized_rows.sort(key=lambda value: value["work_id"])
    return {
        "schema": RECEIPT_SCHEMA,
        "evaluated_at_utc": _render_timestamp(_timestamp(receipt["evaluated_at_utc"], f"{name}.evaluated_at_utc")),
        "source_document_sha256": _sha(receipt["source_document_sha256"], f"{name}.source_document_sha256"),
        "packet_sha256": _sha(receipt["packet_sha256"], f"{name}.packet_sha256"),
        "markdown_sha256": _sha(receipt["markdown_sha256"], f"{name}.markdown_sha256"),
        "predecessor_receipt_sha256": _nullable_sha(receipt["predecessor_receipt_sha256"], f"{name}.predecessor_receipt_sha256"),
        "continuity": normalized_rows,
    }


def _normalize_document(document: Any) -> dict[str, Any]:
    if type(document) is dict and document.get("schema") == "payoff-path-work/v1":
        raise PayoffPathError(
            f"document.schema must be {WORK_SCHEMA}; legacy caller-authored spend snapshots are not accepted"
        )
    root = _exact_keys(document, {"schema", "predecessor_receipt", "work_items"}, "document")
    if root["schema"] != WORK_SCHEMA:
        raise PayoffPathError(f"document.schema must be {WORK_SCHEMA}; legacy caller-authored spend snapshots are not accepted")
    predecessor = None if root["predecessor_receipt"] is None else _normalize_receipt(root["predecessor_receipt"])
    items = root["work_items"]
    if type(items) is not list or len(items) > _MAX_ITEMS:
        raise PayoffPathError("document.work_items must be a bounded array")
    normalized = []
    seen_work = set()
    for index, raw in enumerate(items):
        item = _exact_keys(raw, {"work_id", "opportunity_id", "started_at_utc", "budget_policy", "effort_events", "payoff_path"}, f"work_items[{index}]")
        work_id = _opaque_ref(item["work_id"], f"work_items[{index}].work_id")
        opportunity_id = _opaque_ref(item["opportunity_id"], f"work_items[{index}].opportunity_id")
        if work_id in seen_work:
            raise PayoffPathError(f"duplicate work_id: {work_id}")
        seen_work.add(work_id)
        events = item["effort_events"]
        if type(events) is not list or len(events) > _MAX_EVENTS:
            raise PayoffPathError(f"work_items[{index}].effort_events must be a bounded array")
        normalized_events = []
        seen_events = set()
        total = 0
        for event_index, raw_event in enumerate(events):
            event = _normalize_event(raw_event, f"work_items[{index}].effort_events[{event_index}]", work_id, opportunity_id)
            if event["event_id"] in seen_events:
                raise PayoffPathError(f"duplicate effort event_id in {work_id}: {event['event_id']}")
            seen_events.add(event["event_id"])
            total += event["minutes"]
            if total > _SAFE_INT:
                raise PayoffPathError(f"derived effort total too large for {work_id}")
            normalized_events.append(event)
        normalized_events.sort(key=lambda value: value["event_id"])
        normalized.append({
            "work_id": work_id,
            "opportunity_id": opportunity_id,
            "started_at_utc": _render_timestamp(_timestamp(item["started_at_utc"], f"work_items[{index}].started_at_utc")),
            "budget_policy": _normalize_policy(item["budget_policy"], f"work_items[{index}].budget_policy"),
            "effort_events": normalized_events,
            "payoff_path": _normalize_path(item["payoff_path"], f"work_items[{index}].payoff_path"),
        })
    normalized.sort(key=lambda value: value["work_id"])
    return {"schema": WORK_SCHEMA, "predecessor_receipt": predecessor, "work_items": normalized}


def _event_fingerprints(item: dict[str, Any]) -> list[dict[str, str]]:
    return [{"event_id": event["event_id"], "event_sha256": _digest(event)} for event in item["effort_events"]]


def _spent(item: dict[str, Any]) -> int:
    return sum(event["minutes"] for event in item["effort_events"])


def _enforce_continuity(document: dict[str, Any], as_of: datetime) -> set[str]:
    """Validate the current immutable ledger against the embedded predecessor receipt.

    Returns work IDs whose prior terminal STOP was explicitly superseded by a new
    owner cap generation and whose new cap now exceeds cumulative effort.
    """
    predecessor = document["predecessor_receipt"]
    items = {item["work_id"]: item for item in document["work_items"]}
    reopened = set()

    if predecessor is None:
        for item in items.values():
            policy = item["budget_policy"]
            if policy["generation"] != 1 or policy["predecessor_policy_sha256"] is not None or policy["supersedes_receipt_sha256"] is not None:
                raise PayoffPathError(f"initial work {item['work_id']} must start at policy generation 1 with null predecessor links")
        return reopened

    prior_time = _timestamp(predecessor["evaluated_at_utc"], "predecessor_receipt.evaluated_at_utc")
    if prior_time > as_of:
        raise PayoffPathError("predecessor receipt is from the future")
    predecessor_digest = _digest(predecessor)
    previous = {row["work_id"]: row for row in predecessor["continuity"]}
    missing = sorted(set(previous) - set(items))
    if missing:
        raise PayoffPathError(f"successor omitted prior work continuity: {missing}")

    for work_id, item in items.items():
        policy = item["budget_policy"]
        current_fps = {row["event_id"]: row["event_sha256"] for row in _event_fingerprints(item)}
        prior = previous.get(work_id)
        if prior is None:
            if policy["generation"] != 1 or policy["predecessor_policy_sha256"] is not None or policy["supersedes_receipt_sha256"] is not None:
                raise PayoffPathError(f"new work {work_id} must start at policy generation 1")
            continue
        if item["opportunity_id"] != prior["opportunity_id"]:
            raise PayoffPathError(f"cross-opportunity receipt transplant for {work_id}")

        for fingerprint in prior["event_fingerprints"]:
            event_id = fingerprint["event_id"]
            if event_id not in current_fps:
                raise PayoffPathError(f"successor omitted prior effort event {work_id}/{event_id}")
            if current_fps[event_id] != fingerprint["event_sha256"]:
                raise PayoffPathError(f"successor mutated prior effort event {work_id}/{event_id}")
        if _spent(item) < prior["spent_minutes"]:
            raise PayoffPathError(f"successor reduced cumulative effort for {work_id}")

        prior_ids = {row["event_id"] for row in prior["event_fingerprints"]}
        for event in item["effort_events"]:
            if event["event_id"] not in prior_ids and _timestamp(event["observed_at_utc"], "new effort observed_at_utc") <= prior_time:
                raise PayoffPathError(f"new effort event backdates predecessor receipt for {work_id}/{event['event_id']}")

        current_policy_digest = _digest(policy)
        if policy["generation"] == prior["policy_generation"]:
            if current_policy_digest != prior["policy_sha256"]:
                raise PayoffPathError(f"same-generation owner budget policy changed for {work_id}")
        elif policy["generation"] == prior["policy_generation"] + 1:
            if policy["predecessor_policy_sha256"] != prior["policy_sha256"]:
                raise PayoffPathError(f"owner budget policy predecessor mismatch for {work_id}")
            if policy["supersedes_receipt_sha256"] != predecessor_digest:
                raise PayoffPathError(f"owner budget policy does not supersede exact predecessor receipt for {work_id}")
            if _timestamp(policy["observed_at_utc"], "successor policy observed_at_utc") <= prior_time:
                raise PayoffPathError(f"successor owner budget policy predates predecessor receipt for {work_id}")
            if prior["terminal_stop"] and _spent(item) < policy["cap_minutes"]:
                reopened.add(work_id)
        else:
            raise PayoffPathError(f"owner budget policy generation must stay fixed or advance exactly once for {work_id}")
    return reopened


def _evaluate_item(item: dict[str, Any], as_of: datetime, reopened: bool = False) -> dict[str, Any]:
    policy = item["budget_policy"]
    budget = policy["cap_minutes"]
    spent = _spent(item)
    remaining = max(budget - spent, 0)
    reasons = []
    invalid = False

    if _timestamp(item["started_at_utc"], "normalized.started_at_utc") > as_of:
        reasons.append("WORK_STARTS_IN_FUTURE"); invalid = True
    if _timestamp(policy["observed_at_utc"], "normalized.policy.observed_at_utc") > as_of:
        reasons.append("OWNER_BUDGET_POLICY_FROM_FUTURE"); invalid = True
    for event in item["effort_events"]:
        if _timestamp(event["observed_at_utc"], "normalized.effort.observed_at_utc") > as_of:
            reasons.append("EFFORT_EVENT_FROM_FUTURE"); invalid = True; break

    path = item["payoff_path"]
    if spent >= budget:
        state = "STOP_UNPAID_WORK"
        reasons.append("FREE_WORK_BUDGET_EXHAUSTED")
    elif invalid:
        state = "HOLD_STALE_OR_INVALID"
    elif path is None:
        state = "HOLD_NO_PAYOFF_PATH"
        reasons.append("NO_EVIDENCE_BACKED_COMPENSATION_PATH")
    else:
        source = path["source"]
        conversion = path["conversion"]
        observed = _timestamp(source["observed_at_utc"], "normalized.source.observed_at_utc")
        due = _timestamp(conversion["due_at_utc"], "normalized.conversion.due_at_utc")
        if observed > as_of:
            reasons.append("SOURCE_EVIDENCE_FROM_FUTURE")
        elif as_of - observed > timedelta(days=source["max_age_days"]):
            reasons.append("SOURCE_EVIDENCE_STALE")
        if due <= as_of:
            reasons.append("CONVERSION_DEADLINE_EXPIRED")
        if due <= observed:
            reasons.append("CONVERSION_DEADLINE_NOT_AFTER_SOURCE_OBSERVATION")
        if reasons:
            state = "HOLD_STALE_OR_INVALID"
        else:
            state = "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW"
            reasons.append("PAYOFF_PATH_CURRENT_AND_DERIVED_EFFORT_WITHIN_OWNER_CAP")

    if reopened and "OWNER_CAP_GENERATION_EXPLICITLY_REOPENED_PREVIOUS_STOP" not in reasons:
        reasons.append("OWNER_CAP_GENERATION_EXPLICITLY_REOPENED_PREVIOUS_STOP")

    path_summary = None
    if path is not None:
        path_summary = {
            "mechanism": path["mechanism"], "value": path["value"],
            "canonical_source_url": path["source"]["canonical_url"],
            "source_evidence_ref": path["source"]["evidence_ref"],
            "source_evidence_sha256": path["source"]["evidence_sha256"],
            "source_observed_at_utc": path["source"]["observed_at_utc"],
            "conversion_event": path["conversion"]["event"],
            "conversion_due_at_utc": path["conversion"]["due_at_utc"],
            "conversion_evidence_ref": path["conversion"]["evidence_ref"],
            "conversion_evidence_sha256": path["conversion"]["evidence_sha256"],
        }
    return {
        "work_id": item["work_id"], "opportunity_id": item["opportunity_id"],
        "state": state, "reasons": reasons,
        "budget_policy_id": policy["policy_id"],
        "budget_policy_generation": policy["generation"],
        "budget_policy_sha256": _digest(policy),
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "free_work_remaining_minutes": remaining,
        "effort_event_count": len(item["effort_events"]),
        "effort_ledger_sha256": _digest(_event_fingerprints(item)),
        "payoff_path": path_summary,
    }


def _summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    result = {state: 0 for state in sorted(STATES)}
    total = 0
    for row in rows:
        total += 1; result[row["state"]] += 1
    return {"total_items": total, **result}


def render_markdown(packet: dict[str, Any]) -> str:
    if type(packet) is not dict or packet.get("schema") != PACKET_SCHEMA:
        raise PayoffPathError(f"packet must be {PACKET_SCHEMA}")
    lines = [
        "# Payoff Path Gate Review", "",
        f"Evaluated at: `{packet['evaluated_at_utc']}`",
        f"Source document SHA-256: `{packet['source_document_sha256']}`",
        f"Predecessor receipt SHA-256: `{packet['predecessor_receipt_sha256'] or 'GENESIS'}`", "",
        "> READY means owner review only. It does not authorize outreach, submission, spend, delivery, payment action, or a revenue claim.", "",
    ]
    if not packet["results"]:
        lines.append("No speculative work items were supplied.")
    for row in packet["results"]:
        lines.extend([
            f"## {row['work_id']}", "",
            f"- Opportunity: `{row['opportunity_id']}`",
            f"- State: **{row['state']}**",
            f"- Owner cap policy: `{row['budget_policy_id']}` generation `{row['budget_policy_generation']}` / `{row['budget_policy_sha256']}`",
            f"- Unpaid cap: `{row['free_work_budget_minutes']}` min; derived immutable effort: `{row['free_work_spent_minutes']}` min; remaining: `{row['free_work_remaining_minutes']}` min",
            f"- Effort ledger: `{row['effort_event_count']}` events / `{row['effort_ledger_sha256']}`",
            f"- Reasons: {', '.join('`' + reason + '`' for reason in row['reasons'])}",
        ])
        path = row["payoff_path"]
        if path is None:
            lines.append("- Payoff path: **none evidenced**")
        else:
            value = path["value"]
            value_text = (f"{value['kind']} {value['currency']} {value['amount_minor']} minor units" if value["kind"] in {"FIXED", "POOL"} else value["kind"])
            lines.extend([
                f"- Mechanism: `{path['mechanism']}`; source value state: `{value_text}`",
                f"- Source: `{path['canonical_source_url']}`",
                f"- Source evidence: `{path['source_evidence_ref']}` / `{path['source_evidence_sha256']}`",
                f"- Next conversion: `{path['conversion_event']}` by `{path['conversion_due_at_utc']}`",
            ])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compile_gate(document: Any, trusted_as_of: datetime | str | None = None) -> tuple[dict[str, Any], str, dict[str, Any]]:
    as_of = _trusted_now(trusted_as_of)
    normalized = _normalize_document(document)
    reopened = _enforce_continuity(normalized, as_of)
    rows = [_evaluate_item(item, as_of, item["work_id"] in reopened) for item in normalized["work_items"]]
    predecessor = normalized["predecessor_receipt"]
    predecessor_digest = None if predecessor is None else _digest(predecessor)
    packet = {
        "schema": PACKET_SCHEMA,
        "evaluated_at_utc": _render_timestamp(as_of),
        "source_document_sha256": _digest(normalized),
        "predecessor_receipt_sha256": predecessor_digest,
        "summary": _summary(rows), "results": rows, "authority": AUTHORITY,
    }
    markdown = render_markdown(packet)
    by_id = {item["work_id"]: item for item in normalized["work_items"]}
    prior_rows = {} if predecessor is None else {row["work_id"]: row for row in predecessor["continuity"]}
    continuity = []
    for row in rows:
        item = by_id[row["work_id"]]
        prior_row = prior_rows.get(row["work_id"])
        current_policy = {"generation": row["budget_policy_generation"], "policy_sha256": row["budget_policy_sha256"]}
        if prior_row is None:
            history = [current_policy]
        elif prior_row["policy_generation"] == row["budget_policy_generation"]:
            history = list(prior_row["policy_history"])
        else:
            history = [*prior_row["policy_history"], current_policy]
        continuity.append({
            "work_id": row["work_id"], "opportunity_id": row["opportunity_id"],
            "policy_generation": row["budget_policy_generation"],
            "policy_sha256": row["budget_policy_sha256"],
            "policy_history": history,
            "event_fingerprints": _event_fingerprints(item),
            "spent_minutes": row["free_work_spent_minutes"],
            "terminal_stop": row["state"] == "STOP_UNPAID_WORK",
        })
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "evaluated_at_utc": packet["evaluated_at_utc"],
        "source_document_sha256": packet["source_document_sha256"],
        "packet_sha256": _digest(packet),
        "markdown_sha256": _sha256_bytes(markdown.encode("utf-8")),
        "predecessor_receipt_sha256": predecessor_digest,
        "continuity": continuity,
    }
    return packet, markdown, receipt


def verify_gate(document: Any, packet: Any, markdown: Any, receipt: Any, trusted_now: datetime | str | None = None) -> bool:
    now = _trusted_now(trusted_now)
    normalized = _normalize_document(document)
    packet_obj = _exact_keys(packet, {"schema", "evaluated_at_utc", "source_document_sha256", "predecessor_receipt_sha256", "summary", "results", "authority"}, "packet")
    if packet_obj["schema"] != PACKET_SCHEMA or packet_obj["authority"] != AUTHORITY:
        raise PayoffPathError("packet schema or authority is invalid")
    evaluated = _timestamp(packet_obj["evaluated_at_utc"], "packet.evaluated_at_utc")
    if evaluated > now:
        raise PayoffPathError("packet evaluation time is in the future")
    expected_packet, expected_markdown, expected_receipt = compile_gate(normalized, evaluated)
    if packet_obj != expected_packet:
        raise PayoffPathError("packet content does not match continuity-safe recompilation")
    if type(markdown) is not str or markdown != expected_markdown:
        raise PayoffPathError("Markdown content does not match recompilation")
    receipt_obj = _normalize_receipt(receipt, "receipt")
    if receipt_obj != expected_receipt:
        raise PayoffPathError("receipt content does not match exact packet/Markdown/continuity")

    reopened = _enforce_continuity(normalized, now)
    current_rows = [_evaluate_item(item, now, item["work_id"] in reopened) for item in normalized["work_items"]]
    previous = {row["work_id"]: row for row in expected_packet["results"]}
    for row in current_rows:
        before = previous[row["work_id"]]
        if before["state"] == "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW" and row["state"] != before["state"]:
            raise PayoffPathError(f"previous READY item {row['work_id']} is no longer current at trusted verification time")
    return True


# Strict JSON + CLI/file boundary.  The public payoff_path_gate wrapper replaces the
# four pathname primitives below with descriptor-bound equivalents; keeping these
# hooks here preserves direct-library/CLI compatibility while semantics stay in this
# continuity-safe core.
def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PayoffPathError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_strict_json(text: str) -> Any:
    if type(text) is not str or len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
        raise PayoffPathError("JSON input is missing or too large")
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PayoffPathError(f"non-finite JSON constant: {value}")
            ),
        )
    except PayoffPathError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PayoffPathError("invalid JSON") from exc


def _read_regular(path):
    from pathlib import Path
    import stat
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise PayoffPathError(f"cannot stat input: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PayoffPathError(f"input must be a regular non-symlink file: {path}")
    if info.st_size > _MAX_INPUT_BYTES:
        raise PayoffPathError(f"input file too large: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PayoffPathError(f"cannot read UTF-8 input: {path}") from exc


def _preflight_output(path) -> None:
    from pathlib import Path
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PayoffPathError(f"cannot stat output: {path}") from exc
    import stat
    if stat.S_ISLNK(info.st_mode):
        raise PayoffPathError(f"refusing final symlink output: {path}")
    raise PayoffPathError(f"refusing to overwrite existing output: {path}")


def _exclusive_write(path, text: str) -> None:
    from pathlib import Path
    import os
    path = Path(path)
    _preflight_output(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise PayoffPathError(f"cannot create output exclusively: {path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _publish_bundle(outputs) -> None:
    from pathlib import Path
    import os
    destinations = [Path(path).resolve(strict=False) for path, _ in outputs]
    if len(set(destinations)) != len(destinations):
        raise PayoffPathError("output paths must be distinct")
    for path, _ in outputs:
        path = Path(path)
        _preflight_output(path)
        if not path.parent.exists() or not path.parent.is_dir():
            raise PayoffPathError(f"output parent must be an existing directory: {path.parent}")
    created = []
    try:
        for path, text in outputs:
            path = Path(path)
            _exclusive_write(path, text)
            created.append(path)
    except Exception:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _compile_command(args) -> int:
    from pathlib import Path
    document = load_strict_json(_read_regular(Path(args.input)))
    packet, markdown, receipt = compile_gate(document)
    _publish_bundle([
        (Path(args.packet), _canonical_json(packet) + "\n"),
        (Path(args.markdown), markdown),
        (Path(args.receipt), _canonical_json(receipt) + "\n"),
    ])
    print(_canonical_json(receipt))
    return 0


def _verify_command(args) -> int:
    from pathlib import Path
    document = load_strict_json(_read_regular(Path(args.input)))
    packet = load_strict_json(_read_regular(Path(args.packet)))
    markdown = _read_regular(Path(args.markdown))
    receipt = load_strict_json(_read_regular(Path(args.receipt)))
    verify_gate(document, packet, markdown, receipt)
    print(_canonical_json({"verified": True, "packet_sha256": receipt["packet_sha256"]}))
    return 0


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile", help="compile a continuity-safe owner-review payoff-path packet")
    compile_parser.add_argument("--input", required=True)
    compile_parser.add_argument("--packet", required=True)
    compile_parser.add_argument("--markdown", required=True)
    compile_parser.add_argument("--receipt", required=True)
    compile_parser.set_defaults(func=_compile_command)
    verify_parser = sub.add_parser("verify", help="verify exact inputs, continuity, packet, Markdown, receipt, and temporal validity")
    verify_parser.add_argument("--input", required=True)
    verify_parser.add_argument("--packet", required=True)
    verify_parser.add_argument("--markdown", required=True)
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.set_defaults(func=_verify_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except PayoffPathError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
