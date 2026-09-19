# SPDX-License-Identifier: MIT
"""Deterministic cash-floor routing for bounty intake.

This module answers one narrow question before expensive source work begins:
which queue should a currently observed bounty enter?  It does not replace the
repository's paid-work/claim economics gates and grants no external authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

SCHEMA = "bounty-cash-admission/v1"
RECEIPT_SCHEMA = "bounty-cash-admission-receipt/v1"
POLICY_SCHEMA = "bounty-cash-admission-policy/v1"

_POLICY = {
    "schema": POLICY_SCHEMA,
    "active_floor": "50",
    "save_up_floor": "10",
    "supported_nominal_currencies": ["USD", "USDC"],
    "max_evidence_age_seconds": 86400,
    "active_channel": "#bug-bounty",
    "save_up_channel": "#bounty-pile-10-49",
}

_DISPOSITIONS = frozenset({"ACTIVE", "MAYBE_SAVE_UP", "PRUNE", "HOLD"})
_AVAILABILITY_STATES = frozenset({
    "OPEN_UNASSIGNED",
    "OPEN_ASSIGNED_TO_US",
    "OPEN_ASSIGNED_OTHER",
    "CLOSED",
    "UNKNOWN",
})
_EVIDENCE_SCOPES = frozenset({"ISSUE", "PROGRAM"})
_AUTHORITIES = frozenset({"FIRST_PARTY", "PROVIDER", "OTHER"})
_KINDS = frozenset({"EXPLICIT_AMOUNT", "UNPRICED", "MAYBE_REWARDED"})
_SCOPE_RANK = {"PROGRAM": 1, "ISSUE": 2}
_AUTHORITY_RANK = {"OTHER": 1, "PROVIDER": 2, "FIRST_PARTY": 3}
_TRUSTED_AUTHORITIES = frozenset({"FIRST_PARTY", "PROVIDER"})
_AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class BountyCashAdmissionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _error(code: str, message: str) -> BountyCashAdmissionError:
    return BountyCashAdmissionError(code, message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("INVALID_JSON", "value is not canonical finite JSON") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_object(value: Any, *, required: set, optional: set, field: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise _error("INVALID_SCHEMA", "%s must be an object" % field)
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise _error("INVALID_SCHEMA", "%s has missing or unsupported fields" % field)
    return value


def _identifier(value: Any, field: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise _error("INVALID_IDENTITY", "%s must be a bounded identifier" % field)
    return value


def _https_url(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 2048:
        raise _error("INVALID_SOURCE", "%s must be bounded trimmed https URL" % field)
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value) or "\\" in value:
        raise _error("INVALID_SOURCE", "%s contains forbidden characters" % field)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _error("INVALID_SOURCE", "%s must be a valid URL" % field) from exc
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise _error("INVALID_SOURCE", "%s must be a plain https source URL" % field)
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if type(value) is not str or _TS_RE.fullmatch(value) is None:
        raise _error("INVALID_TIME", "%s must be exact UTC seconds" % field)
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _error("INVALID_TIME", "%s is invalid" % field) from exc
    if dt.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise _error("INVALID_TIME", "%s is not canonical" % field)
    return dt


def _age(observed_at: Any, *, as_of: datetime, field: str) -> Tuple[str, int, bool]:
    observed = _timestamp(observed_at, field)
    seconds = int((as_of - observed).total_seconds())
    if seconds < 0:
        raise _error("FUTURE_EVIDENCE", "%s must not be in the future" % field)
    return observed_at, seconds, seconds <= int(_POLICY["max_evidence_age_seconds"])


def _amount(value: Any, field: str) -> Tuple[str, Decimal]:
    if type(value) is not str or _AMOUNT_RE.fullmatch(value) is None:
        raise _error("INVALID_AMOUNT", "%s must be a nonnegative decimal string" % field)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise _error("INVALID_AMOUNT", "%s is invalid" % field) from exc
    if not parsed.is_finite() or parsed < 0:
        raise _error("INVALID_AMOUNT", "%s must be finite and nonnegative" % field)
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0", parsed


def _normalize_availability(
    value: Any, *, as_of: datetime, canonical_source_url: str
) -> Dict[str, Any]:
    row = _exact_object(
        value,
        required={"state", "observed_at", "source_url"},
        optional=set(),
        field="candidate.availability",
    )
    state = row["state"]
    if state not in _AVAILABILITY_STATES:
        raise _error("INVALID_AVAILABILITY", "unsupported availability state")
    observed_at, age_seconds, fresh = _age(
        row["observed_at"], as_of=as_of, field="candidate.availability.observed_at"
    )
    source_url = _https_url(
        row["source_url"], "candidate.availability.source_url"
    )
    if source_url != canonical_source_url:
        raise _error(
            "AVAILABILITY_TARGET_MISMATCH",
            "candidate.availability.source_url must match candidate canonical source",
        )
    return {
        "state": state,
        "observed_at": observed_at,
        "age_seconds": age_seconds,
        "fresh": fresh,
        "source_url": source_url,
    }


def _normalize_evidence(
    value: Any, index: int, *, as_of: datetime, canonical_source_url: str
) -> Dict[str, Any]:
    field = "candidate.reward_evidence[%d]" % index
    row = _exact_object(
        value,
        required={
            "scope", "authority", "kind", "guaranteed_for_candidate",
            "observed_at", "source_url", "target_url",
        },
        optional={"amount", "currency"},
        field=field,
    )
    scope = row["scope"]
    authority = row["authority"]
    kind = row["kind"]
    guaranteed = row["guaranteed_for_candidate"]
    if scope not in _EVIDENCE_SCOPES:
        raise _error("INVALID_REWARD_EVIDENCE", "%s scope is unsupported" % field)
    if authority not in _AUTHORITIES:
        raise _error("INVALID_REWARD_EVIDENCE", "%s authority is unsupported" % field)
    if kind not in _KINDS:
        raise _error("INVALID_REWARD_EVIDENCE", "%s kind is unsupported" % field)
    if type(guaranteed) is not bool:
        raise _error("INVALID_REWARD_EVIDENCE", "%s guarantee must be bool" % field)
    observed_at, age_seconds, fresh = _age(
        row["observed_at"], as_of=as_of, field=field + ".observed_at"
    )
    target_url = _https_url(row["target_url"], field + ".target_url")
    if target_url != canonical_source_url:
        raise _error(
            "REWARD_TARGET_MISMATCH",
            "%s target_url does not match candidate canonical source" % field,
        )
    normalized: Dict[str, Any] = {
        "scope": scope,
        "authority": authority,
        "kind": kind,
        "guaranteed_for_candidate": guaranteed,
        "observed_at": observed_at,
        "age_seconds": age_seconds,
        "fresh": fresh,
        "source_url": _https_url(row["source_url"], field + ".source_url"),
        "target_url": target_url,
    }
    if kind == "EXPLICIT_AMOUNT":
        if "amount" not in row or "currency" not in row:
            raise _error("INVALID_REWARD_EVIDENCE", "%s explicit amount is incomplete" % field)
        amount_text, _ = _amount(row["amount"], field + ".amount")
        currency = row["currency"]
        if type(currency) is not str or not re.fullmatch(r"[A-Z][A-Z0-9]{2,11}", currency):
            raise _error("INVALID_REWARD_EVIDENCE", "%s currency is invalid" % field)
        normalized["amount"] = amount_text
        normalized["currency"] = currency
    elif "amount" in row or "currency" in row or guaranteed:
        raise _error(
            "INVALID_REWARD_EVIDENCE",
            "%s unpriced/maybe evidence must not assert amount, currency, or guarantee" % field,
        )
    return normalized


def _semantic_reward_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row["kind"], row.get("amount"), row.get("currency"), row["guaranteed_for_candidate"]
    )


def _select_reward_evidence(rows: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    trusted = [row for row in rows if row["authority"] in _TRUSTED_AUTHORITIES]
    if not trusted:
        return None, ["NO_TRUSTED_REWARD_AUTHORITY"]
    best_rank = max((_SCOPE_RANK[row["scope"]], _AUTHORITY_RANK[row["authority"]]) for row in trusted)
    strongest = [
        row for row in trusted
        if (_SCOPE_RANK[row["scope"]], _AUTHORITY_RANK[row["authority"]]) == best_rank
    ]
    semantics = {_semantic_reward_key(row) for row in strongest}
    if len(semantics) != 1:
        return None, ["CONFLICTING_TOP_PRIORITY_REWARD_EVIDENCE"]
    strongest.sort(key=lambda row: (row["source_url"], row["observed_at"]))
    return strongest[-1], []


def compile_bounty_cash_admission(request: Dict[str, Any]) -> Dict[str, Any]:
    top = _exact_object(
        request,
        required={"schema", "as_of", "candidate"},
        optional=set(),
        field="request",
    )
    if top["schema"] != SCHEMA:
        raise _error("INVALID_SCHEMA", "unsupported request schema")
    as_of = _timestamp(top["as_of"], "as_of")
    candidate = _exact_object(
        top["candidate"],
        required={"work_id", "canonical_source_url", "availability", "reward_evidence"},
        optional=set(),
        field="candidate",
    )
    work_id = _identifier(candidate["work_id"], "candidate.work_id")
    canonical_source_url = _https_url(candidate["canonical_source_url"], "candidate.canonical_source_url")
    availability = _normalize_availability(
        candidate["availability"],
        as_of=as_of,
        canonical_source_url=canonical_source_url,
    )
    raw_evidence = candidate["reward_evidence"]
    if type(raw_evidence) is not list or not 1 <= len(raw_evidence) <= 32:
        raise _error("INVALID_REWARD_EVIDENCE", "reward_evidence must contain 1..32 rows")
    evidence = [
        _normalize_evidence(
            row, index, as_of=as_of, canonical_source_url=canonical_source_url
        )
        for index, row in enumerate(raw_evidence)
    ]
    selected, selection_reasons = _select_reward_evidence(evidence)

    disposition = "HOLD"
    reasons: List[str] = []
    target_channel: Optional[str] = None
    selected_value: Optional[Dict[str, Any]] = None

    if not availability["fresh"]:
        reasons = ["AVAILABILITY_EVIDENCE_STALE"]
    elif availability["state"] == "CLOSED":
        disposition = "PRUNE"
        reasons = ["TERMINAL_CLOSED"]
    elif availability["state"] == "OPEN_ASSIGNED_OTHER":
        reasons = ["ASSIGNED_TO_OTHER"]
    elif availability["state"] == "UNKNOWN":
        reasons = ["AVAILABILITY_UNKNOWN"]
    elif selection_reasons:
        reasons = selection_reasons
    elif selected is None:
        reasons = ["REWARD_VALUE_UNCONFIRMED"]
    elif not selected["fresh"]:
        reasons = ["REWARD_EVIDENCE_STALE"]
    elif selected["kind"] in {"UNPRICED", "MAYBE_REWARDED"}:
        reasons = ["REWARD_VALUE_UNCONFIRMED"]
    elif selected["kind"] != "EXPLICIT_AMOUNT":
        reasons = ["REWARD_VALUE_UNCONFIRMED"]
    elif not selected["guaranteed_for_candidate"]:
        reasons = ["REWARD_NOT_BOUND_TO_CANDIDATE"]
    elif selected.get("currency") not in _POLICY["supported_nominal_currencies"]:
        reasons = ["NON_USD_DENOMINATION_NO_CONVERSION"]
    else:
        amount_text, amount_value = _amount(selected["amount"], "selected_reward.amount")
        selected_value = {
            "amount": amount_text,
            "currency": selected["currency"],
            "scope": selected["scope"],
            "authority": selected["authority"],
            "source_url": selected["source_url"],
            "observed_at": selected["observed_at"],
        }
        active_floor = Decimal(_POLICY["active_floor"])
        save_floor = Decimal(_POLICY["save_up_floor"])
        if amount_value >= active_floor:
            disposition = "ACTIVE"
            reasons = ["VERIFIED_REWARD_MEETS_ACTIVE_FLOOR"]
            target_channel = _POLICY["active_channel"]
        elif amount_value >= save_floor:
            disposition = "MAYBE_SAVE_UP"
            reasons = ["VERIFIED_REWARD_IN_SAVE_UP_BAND"]
            target_channel = _POLICY["save_up_channel"]
        else:
            disposition = "PRUNE"
            reasons = ["VERIFIED_REWARD_BELOW_SAVE_UP_FLOOR"]

    ignored_lower_priority = []
    if selected is not None:
        selected_rank = (_SCOPE_RANK[selected["scope"]], _AUTHORITY_RANK[selected["authority"]])
        for row in evidence:
            rank = (_SCOPE_RANK[row["scope"]], _AUTHORITY_RANK[row["authority"]])
            if rank < selected_rank:
                ignored_lower_priority.append({
                    "scope": row["scope"],
                    "authority": row["authority"],
                    "kind": row["kind"],
                    "source_url": row["source_url"],
                })

    body = {
        "schema": RECEIPT_SCHEMA,
        "request_sha256": _sha(request),
        "policy": dict(_POLICY),
        "policy_sha256": _sha(_POLICY),
        "work_id": work_id,
        "canonical_source_url": canonical_source_url,
        "as_of": top["as_of"],
        "availability": availability,
        "disposition": disposition,
        "reason_codes": reasons,
        "target_channel": target_channel,
        "selected_reward": selected_value,
        "selected_reward_evidence": selected,
        "ignored_lower_priority_evidence": ignored_lower_priority,
        "authority": {
            "queue_routing_only": True,
            "active_queue_is_not_claim_authority": True,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "implementation_authority": False,
            "payment_cash_or_revenue_authority": False,
            "fx_conversion": False,
            "arbitrary_token_valuation": False,
            "downstream_claim_economics_still_required": True,
        },
    }
    return {**body, "receipt_sha256": _sha(body)}


def verify_receipt(receipt: Dict[str, Any]) -> bool:
    if type(receipt) is not dict or receipt.get("schema") != RECEIPT_SCHEMA:
        return False
    if receipt.get("disposition") not in _DISPOSITIONS:
        return False
    if receipt.get("policy") != _POLICY or receipt.get("policy_sha256") != _sha(_POLICY):
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or _SHA_RE.fullmatch(digest) is None:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    try:
        return _sha(body) == digest
    except BountyCashAdmissionError:
        return False


def _reject_float(raw: str) -> Any:
    raise BountyCashAdmissionError("INVALID_JSON", "floating-point JSON numbers are forbidden")


def _reject_constant(raw: str) -> Any:
    raise BountyCashAdmissionError("INVALID_JSON", "non-finite JSON constants are forbidden")


def _load_request(path: str) -> Dict[str, Any]:
    raw = Path(path).read_bytes() if path != "-" else __import__("sys").stdin.buffer.read()
    if not raw or len(raw) > 1024 * 1024:
        raise BountyCashAdmissionError("INVALID_JSON", "request byte length is invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise BountyCashAdmissionError("INVALID_JSON", "request must be strict UTF-8") from exc

    def unique(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise BountyCashAdmissionError("INVALID_JSON", "duplicate JSON key")
            out[key] = value
        return out

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except BountyCashAdmissionError:
        raise
    except (ValueError, json.JSONDecodeError) as exc:
        raise BountyCashAdmissionError("INVALID_JSON", "request is invalid JSON") from exc
    if type(value) is not dict:
        raise BountyCashAdmissionError("INVALID_JSON", "request must be an object")
    return value


def format_summary(receipt: Dict[str, Any]) -> str:
    return (
        "disposition=%s work_id=%s target=%s reasons=%s receipt_sha256=%s"
        % (
            receipt["disposition"],
            receipt["work_id"],
            receipt["target_channel"] or "-",
            ",".join(receipt["reason_codes"]),
            receipt["receipt_sha256"],
        )
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_cash_admission",
        description="Route bounty intake by verified cash floor without granting claim authority.",
    )
    parser.add_argument("request")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = compile_bounty_cash_admission(_load_request(args.request))
    except (OSError, BountyCashAdmissionError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, sort_keys=True, indent=2))
    else:
        print(format_summary(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
