# SPDX-License-Identifier: MIT
"""Authority-bound active bounty custody.

Only :func:`compile_live_active_claim_portfolio` can authorize a new internal
claim. Live mode owns the clock and refreshes qualification plus availability
from independent canonical GitHub readers. Historical replay is intentionally
non-dispatch.

This module controls internal work custody only. It never posts a GitHub claim,
contacts a sponsor, submits upstream work, mutates payment state, or recognizes
cash/revenue.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from concierge import _active_claim_portfolio_core as _core

SCHEMA = "active-claim-portfolio/v2"
_LIVE_REQUIRED = frozenset(
    {"repo", "number", "sponsor_key", "worker_id", "reward_currency", "reward_minor"}
)
_LIVE_OPTIONAL = frozenset({"listing_url"})
_REPLAY_REQUIRED = frozenset(
    {
        "repo", "number", "sponsor_key", "worker_id", "observed_at",
        "reward_currency", "reward_minor", "qualification", "availability",
    }
)
_EVENT_KEYS = frozenset(
    {
        "event_id", "repo", "number", "sponsor_key", "worker_id",
        "opportunity_digest", "state", "event_at", "predecessor_event_id",
        "predecessor_event_digest", "evidence_ref", "evidence_sha256",
    }
)
_LIVE_REWARD_SIGNAL_KEYS = {
    "USD": ("advertised_reward_usd", "live_label_reward_usd"),
    "RTC": ("advertised_reward_rtc", "live_label_reward_rtc"),
}

ActiveClaimPortfolioError = _core.ActiveClaimPortfolioError
_MAX_SAFE_INT_DIGITS = len(str(_core._MAX_SAFE_INT))


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActiveClaimPortfolioError("value is not canonical JSON") from exc


def _rehash(receipt: Dict[str, Any]) -> Dict[str, Any]:
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    result = dict(body)
    result["receipt_sha256"] = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return result


def _utc_now() -> datetime:
    """Verifier-owned production clock; tests may patch this private helper."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ActiveClaimPortfolioError("verifier clock must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _canonical_repo(value: Any) -> str:
    # Reuse the core's structural validator, then canonicalize GitHub identity.
    return _core._repo(value).casefold()


def _safe_positive_int(value: Any, name: str) -> int:
    return _core._safe_int(value, name, positive=True)


def _validate_live_candidate(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ActiveClaimPortfolioError("candidate must be an object")
    keys = set(value)
    missing = set(_LIVE_REQUIRED) - keys
    extra = keys - set(_LIVE_REQUIRED) - set(_LIVE_OPTIONAL)
    if missing or extra:
        raise ActiveClaimPortfolioError(
            "live candidate has unexpected keys (missing=%s extra=%s)"
            % (sorted(missing), sorted(extra))
        )
    # Validate metadata through the hardened v1 core without trusting any receipt.
    repo = _canonical_repo(value["repo"])
    number = _safe_positive_int(value["number"], "candidate.number")
    sponsor = _core._identifier(value["sponsor_key"], "candidate.sponsor_key")
    worker = _core._identifier(value["worker_id"], "candidate.worker_id")
    currency = value["reward_currency"]
    if not isinstance(currency, str) or _core._CURRENCY_RE.fullmatch(currency) is None:
        raise ActiveClaimPortfolioError("candidate.reward_currency is invalid")
    reward = _core._safe_int(value["reward_minor"], "candidate.reward_minor")
    listing = value.get("listing_url")
    if listing is not None:
        if not isinstance(listing, str) or not listing or len(listing) > 2048:
            raise ActiveClaimPortfolioError("candidate.listing_url is invalid")
        if _core._SECRET_RE.search(listing):
            raise ActiveClaimPortfolioError("candidate.listing_url appears to contain secret material")
    return {
        "repo": repo,
        "number": number,
        "sponsor_key": sponsor,
        "worker_id": worker,
        "reward_currency": currency,
        "reward_minor": reward,
        "listing_url": listing,
    }


def _qualify_live_authority(
    repo: str, number: int, listing_url: Optional[str], max_pages: int
) -> Dict[str, Any]:
    from concierge.revenue_intake import qualify_live_revenue_intake

    return qualify_live_revenue_intake(
        repo, number, listing_url=listing_url, max_pages=max_pages
    )


def _inspect_live_availability(repo: str, number: int, max_pages: int) -> Dict[str, Any]:
    from concierge.bounty_availability import inspect_bounty_availability

    return inspect_bounty_availability(repo, number, max_pages=max_pages)


def _qualification_hold(code: str, *, canonical: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "disposition": "HOLD",
        "dispatch": False,
        "reason_codes": [code],
        "signals": {},
    }
    if canonical is not None:
        # revenue_intake results are explicitly safe-to-log normalized receipts;
        # retain the exact verifier-owned bytes inside the hold generation.
        result["canonical_qualification"] = canonical
    return result


def _availability_hold(repo: str, number: int, code: str) -> Dict[str, Any]:
    return {
        "schema": "bounty-availability/v1",
        "repo": repo,
        "number": number,
        "disposition": "HOLD",
        "dispatch": False,
        "reason_code": code,
        "issue_state": None,
        "signal_codes": [],
        "evidence": [],
        "authority": {
            "effect": "new_work_dispatch_only",
            "terminal_signal_is_payout_proof": False,
            "terminal_signal_is_revenue_proof": False,
            "raw_comment_text_retained": False,
            "user_identity_retained": False,
        },
    }


def _signal_amounts(signals: Dict[str, Any], key: str) -> List[Decimal]:
    raw = signals.get(key, [])
    if not isinstance(raw, list):
        raise ActiveClaimPortfolioError("live qualification reward signals are malformed")
    result: List[Decimal] = []
    for value in raw:
        if type(value) is not str or not value or len(value) > 96 or value != value.strip():
            raise ActiveClaimPortfolioError("live qualification reward signals are malformed")
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ActiveClaimPortfolioError(
                "live qualification reward signals are malformed"
            ) from exc
        if not amount.is_finite() or amount < 0:
            raise ActiveClaimPortfolioError("live qualification reward signals are malformed")
        result.append(amount)
    return result


def _exact_native_units(amount: Decimal, decimal_places: int) -> Optional[int]:
    """Convert one non-negative Decimal to exact bounded integer units.

    Decimal construction and tuple inspection are context-free. This avoids the
    process-wide mutable precision and rounding mode entirely, while bounding
    exponent work before multiplication so hostile scientific notation cannot
    manufacture a huge intermediate integer.
    """
    sign, raw_digits, exponent = amount.as_tuple()
    first_nonzero = next(
        (index for index, digit in enumerate(raw_digits) if digit), None
    )
    if first_nonzero is None:
        return 0
    if sign:
        return None

    digits = raw_digits[first_nonzero:]
    shift = exponent + decimal_places
    if shift < 0:
        fractional_digits = -shift
        if fractional_digits >= len(digits):
            return None
        if any(digits[-fractional_digits:]):
            return None
        digits = digits[:-fractional_digits]
        shift = 0

    if len(digits) + shift > _MAX_SAFE_INT_DIGITS:
        return None
    units = 0
    for digit in digits:
        units = units * 10 + digit
    if shift:
        units *= 10 ** shift
    if units > _core._MAX_SAFE_INT:
        return None
    return units


def _canonical_live_reward_minor(
    qualification: Dict[str, Any]
) -> Tuple[Optional[Tuple[str, int]], Optional[str]]:
    """Extract one exact native reward from the fresh qualification receipt.

    Live revenue intake nests the sponsor-authoritative reward signals under its
    ``qualification`` gate. USD is represented in cents by ``reward_minor``;
    RTC is represented in whole RTC because the custody schema is integer-only.
    Fractional RTC therefore fails closed instead of being rounded.
    """
    if (
        qualification.get("disposition") != "ACTIONABLE"
        or qualification.get("dispatch") is not True
    ):
        return None, None

    gate = qualification.get("qualification")
    if not isinstance(gate, dict):
        return None, "LIVE_REWARD_BINDING_UNAVAILABLE"
    signals = gate.get("signals")
    if not isinstance(signals, dict):
        return None, "LIVE_REWARD_BINDING_UNAVAILABLE"

    native: Dict[str, Decimal] = {}
    try:
        for currency, keys in _LIVE_REWARD_SIGNAL_KEYS.items():
            values: List[Decimal] = []
            for key in keys:
                values.extend(_signal_amounts(signals, key))
            unique = sorted(set(values))
            if len(unique) > 1:
                return None, "LIVE_REWARD_AMOUNT_AMBIGUOUS"
            if unique:
                native[currency] = unique[0]
    except ActiveClaimPortfolioError:
        return None, "LIVE_REWARD_BINDING_UNAVAILABLE"

    if not native:
        return None, "LIVE_REWARD_BINDING_UNAVAILABLE"
    if len(native) != 1:
        return None, "LIVE_REWARD_CURRENCY_AMBIGUOUS"

    currency, amount = next(iter(native.items()))
    minor = _exact_native_units(amount, 2 if currency == "USD" else 0)
    if minor is None:
        return None, "LIVE_REWARD_AMOUNT_UNREPRESENTABLE"
    return (currency, minor), None


def _bind_live_reward(
    candidate: Dict[str, Any], qualification: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    canonical, code = _canonical_live_reward_minor(qualification)
    if code is not None:
        return _qualification_hold(code, canonical=qualification), [code]
    if canonical is None:
        return qualification, []

    currency, reward_minor = canonical
    if candidate["reward_currency"] != currency:
        code = "LIVE_REWARD_CURRENCY_MISMATCH"
        return _qualification_hold(code, canonical=qualification), [code]
    if candidate["reward_minor"] != reward_minor:
        code = "LIVE_REWARD_AMOUNT_MISMATCH"
        return _qualification_hold(code, canonical=qualification), [code]
    return qualification, []


def _canonicalize_events(events: Any) -> List[Dict[str, Any]]:
    if not isinstance(events, list):
        raise ActiveClaimPortfolioError("events must be an array")
    result: List[Dict[str, Any]] = []
    for raw in events:
        if not isinstance(raw, dict) or set(raw) != set(_EVENT_KEYS):
            raise ActiveClaimPortfolioError("event has unexpected keys")
        row = dict(raw)
        row["repo"] = _canonical_repo(row["repo"])
        result.append(row)
    return result


def _canonicalize_replay_candidates(candidates: Any) -> List[Dict[str, Any]]:
    if not isinstance(candidates, list):
        raise ActiveClaimPortfolioError("candidates must be an array")
    result: List[Dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict) or set(raw) != set(_REPLAY_REQUIRED):
            raise ActiveClaimPortfolioError("replay candidate has unexpected keys")
        row = dict(raw)
        row["repo"] = _canonical_repo(row["repo"])
        availability = row.get("availability")
        if isinstance(availability, dict) and "repo" in availability:
            availability = dict(availability)
            availability["repo"] = _canonical_repo(availability["repo"])
            row["availability"] = availability
        result.append(row)
    return result


def _decorate(
    receipt: Dict[str, Any], *, mode: str, verifier_as_of: str,
    live_failure_codes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    result = dict(receipt)
    result["schema"] = SCHEMA
    result["mode"] = mode
    result["verifier_as_of"] = verifier_as_of
    result["authority"] = dict(result.get("authority") or {})
    result["authority"].update(
        {
            "effect": "internal_work_custody_only",
            "live_authority_refreshed": mode == "live",
            "caller_clock_authoritative": False,
            "caller_receipts_authoritative": False,
            "caller_reward_metadata_authoritative": False,
            "historical_replay_authorizes_new_work": False,
            "external_github_claim": False,
            "sponsor_or_maintainer_contact": False,
            "upstream_submission": False,
            "wallet_or_payment_mutation": False,
            "provider_mutation": False,
            "acceptance_or_payout_assertion": False,
            "cash_assertion": False,
            "revenue_recognition": False,
        }
    )
    decorated_rows: List[Dict[str, Any]] = []
    failures = live_failure_codes or {}
    for source in result.get("results", []):
        row = dict(source)
        codes = set(row.get("reason_codes") or [])
        codes.update(failures.get(str(row.get("opportunity_id")), []))
        if mode != "live" and row.get("disposition") == "READY_FOR_INTERNAL_CLAIM":
            row["disposition"] = "REPLAY_ONLY_HOLD"
            codes.add("HISTORICAL_REPLAY_NON_DISPATCH")
        row["reason_codes"] = sorted(codes)
        decorated_rows.append(row)
    result["results"] = decorated_rows
    return _rehash(result)


def compile_live_active_claim_portfolio(
    candidates: Any, events: Any, policy: Any, *, max_pages: int = 10
) -> Dict[str, Any]:
    """Refresh current authority and compile a live internal custody decision.

    No ``as_of`` or receipt argument exists on this path. Only this public
    function can produce ``READY_FOR_INTERNAL_CLAIM``.
    """
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 100:
        raise ActiveClaimPortfolioError("max_pages must be an integer between 1 and 100")
    if not isinstance(candidates, list):
        raise ActiveClaimPortfolioError("candidates must be an array")
    if len(candidates) > _core._MAX_ITEMS:
        raise ActiveClaimPortfolioError("too many candidates")

    validated = [_validate_live_candidate(row) for row in candidates]
    canonical_events = _canonicalize_events(events)
    as_of = _format_utc(_utc_now())
    cache: Dict[Tuple[str, int, Optional[str]], Tuple[Dict[str, Any], Dict[str, Any], List[str]]] = {}
    core_candidates: List[Dict[str, Any]] = []
    failure_codes: Dict[str, List[str]] = {}

    for item in validated:
        key = (item["repo"], item["number"], item["listing_url"])
        if key not in cache:
            codes: List[str] = []
            try:
                qualification = _qualify_live_authority(*key, max_pages)
                if not isinstance(qualification, dict):
                    raise TypeError("qualification authority returned non-object")
            except Exception:
                qualification = _qualification_hold("LIVE_QUALIFICATION_READ_FAILED")
                codes.append("LIVE_QUALIFICATION_READ_FAILED")
            try:
                availability = _inspect_live_availability(item["repo"], item["number"], max_pages)
                if not isinstance(availability, dict):
                    raise TypeError("availability authority returned non-object")
            except Exception:
                availability = _availability_hold(
                    item["repo"], item["number"], "LIVE_AVAILABILITY_READ_FAILED"
                )
                codes.append("LIVE_AVAILABILITY_READ_FAILED")
            cache[key] = (qualification, availability, codes)
        qualification, availability, codes = cache[key]
        bound_qualification, reward_codes = _bind_live_reward(item, qualification)
        opportunity_id = "%s#%d" % (item["repo"], item["number"])
        if codes or reward_codes:
            failure_codes.setdefault(opportunity_id, []).extend(codes + reward_codes)
        # v1 core needs observed_at in its generation. Use a fixed neutral value:
        # current authority bytes, not wall time, define the live generation.
        core_candidates.append(
            {
                "repo": item["repo"],
                "number": item["number"],
                "sponsor_key": item["sponsor_key"],
                "worker_id": item["worker_id"],
                "observed_at": "1970-01-01T00:00:00Z",
                "reward_currency": item["reward_currency"],
                "reward_minor": item["reward_minor"],
                "qualification": bound_qualification,
                "availability": availability,
            }
        )

    receipt = _core.compile_active_claim_portfolio(
        core_candidates, canonical_events, policy, as_of=as_of
    )
    return _decorate(
        receipt, mode="live", verifier_as_of=as_of, live_failure_codes=failure_codes
    )


def compile_replay_active_claim_portfolio(
    candidates: Any, events: Any, policy: Any, *, as_of: str
) -> Dict[str, Any]:
    """Reconstruct historical custody. Clean unclaimed rows are always HOLD."""
    canonical_candidates = _canonicalize_replay_candidates(candidates)
    canonical_events = _canonicalize_events(events)
    receipt = _core.compile_active_claim_portfolio(
        canonical_candidates, canonical_events, policy, as_of=as_of
    )
    return _decorate(receipt, mode="replay", verifier_as_of=as_of)


def compile_active_claim_portfolio(
    candidates: Any, events: Any, policy: Any, *, as_of: str
) -> Dict[str, Any]:
    """Legacy alias retained as replay-only, never as a live authority path."""
    return compile_replay_active_claim_portfolio(candidates, events, policy, as_of=as_of)


def verify_receipt_integrity(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    supplied = receipt.get("receipt_sha256")
    if not isinstance(supplied, str) or len(supplied) != 64:
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    try:
        return hashlib.sha256(_canonical_bytes(body)).hexdigest() == supplied
    except ActiveClaimPortfolioError:
        return False


def verify_active_claim_portfolio_receipt(
    receipt: Any, candidates: Any, events: Any, policy: Any, *, as_of: str
) -> bool:
    if not verify_receipt_integrity(receipt):
        return False
    try:
        expected = compile_replay_active_claim_portfolio(
            candidates, events, policy, as_of=as_of
        )
        return _canonical_bytes(receipt) == _canonical_bytes(expected)
    except ActiveClaimPortfolioError:
        return False


def _load(path: str) -> Any:
    # Delegate strict duplicate-key, size, regular-file, UTF-8 and JSON checks.
    return _core._load_json_file(path)


def _write(path: str, value: Dict[str, Any]) -> None:
    _core._exclusive_write_text(
        path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def _summary(receipt: Dict[str, Any]) -> str:
    counts: Dict[str, int] = {}
    for row in receipt.get("results", []):
        disposition = str(row.get("disposition"))
        counts[disposition] = counts.get(disposition, 0) + 1
    rendered = ",".join("%s=%d" % (key, counts[key]) for key in sorted(counts)) or "none"
    return "schema=%s mode=%s receipt=%s %s" % (
        receipt["schema"], receipt["mode"], receipt["receipt_sha256"], rendered
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.active_claim_portfolio",
        description="Authority-bound internal bounty claim custody.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_files(p: argparse.ArgumentParser) -> None:
        p.add_argument("--candidates", required=True)
        p.add_argument("--events", required=True)
        p.add_argument("--policy", required=True)

    live = sub.add_parser("live", help="refresh canonical authority; may emit READY")
    add_files(live)
    live.add_argument("--max-pages", type=int, default=10)
    live.add_argument("--output", required=True)

    for name in ("replay", "compile"):
        replay = sub.add_parser(name, help="historical audit only; never emits READY")
        add_files(replay)
        replay.add_argument("--as-of", required=True)
        replay.add_argument("--output", required=True)

    verify = sub.add_parser("verify-replay")
    add_files(verify)
    verify.add_argument("--as-of", required=True)
    verify.add_argument("--receipt", required=True)

    args = parser.parse_args(argv)
    try:
        candidates, events, policy = _load(args.candidates), _load(args.events), _load(args.policy)
        if args.command == "live":
            receipt = compile_live_active_claim_portfolio(
                candidates, events, policy, max_pages=args.max_pages
            )
            _write(args.output, receipt)
            print(_summary(receipt))
            return 0
        if args.command in {"replay", "compile"}:
            receipt = compile_replay_active_claim_portfolio(
                candidates, events, policy, as_of=args.as_of
            )
            _write(args.output, receipt)
            print(_summary(receipt))
            return 0
        receipt = _load(args.receipt)
        valid = verify_active_claim_portfolio_receipt(
            receipt, candidates, events, policy, as_of=args.as_of
        )
        print("valid=%s" % str(valid).lower())
        return 0 if valid else 2
    except ActiveClaimPortfolioError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
