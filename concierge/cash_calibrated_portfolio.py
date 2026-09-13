# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlsplit

from concierge.opportunity_ranker import OpportunityRankInputError, rank_opportunities
from concierge.portfolio_allocator import PortfolioInputError, allocate_portfolio


class CashCalibrationInputError(ValueError):
    """Raised when realized-cash calibration evidence is not reliable enough to use."""


_SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_HISTORY_ITEMS = 10_000
_MAX_TERMINAL_SAMPLES = 10_000
_MAX_ACTIVE_MINUTES = 525_600
_RATE_SCALE = 1_000_000
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ALLOWED_STATES = frozenset({"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"})
_ALLOWED_CASH_STATUS = frozenset(
    {"verified_paid", "partially_verified", "not_inferred"}
)
_TOP_KEYS = frozenset(
    {
        "schema_version",
        "wallet",
        "history_source",
        "scope_sha256",
        "summary",
        "ranking",
        "items",
        "receipt_sha256",
    }
)
_SUMMARY_KEYS = frozenset(
    {
        "currency",
        "verified_cash_total",
        "active_minutes_total",
        "realized_rtc_per_hour_estimate",
        "fully_paid_items",
        "partially_paid_items",
        "zero_verified_cash_items",
        "item_count",
        "scope_complete",
        "cash_basis",
        "effort_basis",
        "fx_conversion",
        "accounting_revenue_claim",
        "tax_claim",
        "payout_or_transfer_authority",
    }
)
_ITEM_KEYS = frozenset(
    {
        "repo",
        "pr",
        "state",
        "cash_status",
        "verified_cash_rtc",
        "active_minutes",
        "realized_rtc_per_hour_estimate",
        "payment_evidence_sha256s",
    }
)
_RANKING_KEYS = frozenset(
    {"rank", "repo", "pr", "realized_rtc_per_hour_estimate"}
)


def _canonical_json(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CashCalibrationInputError("value is not canonical JSON") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise CashCalibrationInputError("canonical JSON value is too large")
    return encoded


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise CashCalibrationInputError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing " + ",".join(missing))
        if extra:
            detail.append("unknown " + ",".join(extra))
        raise CashCalibrationInputError(
            f"{label} has invalid fields" + (": " + "; ".join(detail) if detail else "")
        )
    return value


def _decimal_text(value: Any, label: str, *, nonnegative: bool = True) -> Decimal:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise CashCalibrationInputError(f"{label} must be a bounded decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CashCalibrationInputError(f"{label} must be a finite decimal string") from exc
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 30:
        raise CashCalibrationInputError(f"{label} must be a bounded finite decimal")
    exponent = parsed.as_tuple().exponent
    if not isinstance(exponent, int) or abs(exponent) > 18:
        raise CashCalibrationInputError(f"{label} must be a bounded finite decimal")
    if nonnegative and parsed < 0:
        raise CashCalibrationInputError(f"{label} must not be negative")
    return parsed


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _ratio_text(cash: Decimal, minutes: int) -> str:
    with localcontext() as context:
        context.prec = 36
        value = (cash * Decimal(60)) / Decimal(minutes)
    return _amount_text(value)


def _history_cmp(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_cross = left["_verified"] * Decimal(right["active_minutes"])
    right_cross = right["_verified"] * Decimal(left["active_minutes"])
    if left_cross > right_cross:
        return -1
    if left_cross < right_cross:
        return 1
    left_key = (left["repo"].casefold(), left["pr"])
    right_key = (right["repo"].casefold(), right["pr"])
    return (left_key > right_key) - (left_key < right_key)


def _validate_economics_receipt(economics: Any) -> tuple[str, list[dict[str, Any]]]:
    packet = _exact_keys(economics, _TOP_KEYS, "realized economics receipt")
    if packet.get("schema_version") != _SCHEMA_VERSION:
        raise CashCalibrationInputError("realized economics schema_version must be 1")
    receipt = packet.get("receipt_sha256")
    if type(receipt) is not str or not _HEX64_RE.fullmatch(receipt):
        raise CashCalibrationInputError("realized economics receipt_sha256 is invalid")
    unsigned = {key: value for key, value in packet.items() if key != "receipt_sha256"}
    expected_receipt = _sha256(unsigned)
    if not hmac.compare_digest(receipt, expected_receipt):
        raise CashCalibrationInputError("realized economics receipt integrity check failed")

    wallet = packet.get("wallet")
    if (
        type(wallet) is not str
        or not wallet
        or wallet != wallet.strip()
        or any(char.isspace() or not char.isprintable() for char in wallet)
    ):
        raise CashCalibrationInputError("realized economics wallet is invalid")
    if packet.get("history_source") not in {"queried_wallet", "captured_wallet"}:
        raise CashCalibrationInputError("realized economics history_source is invalid")
    scope_sha = packet.get("scope_sha256")
    if type(scope_sha) is not str or not _HEX64_RE.fullmatch(scope_sha):
        raise CashCalibrationInputError("realized economics scope_sha256 is invalid")

    summary = _exact_keys(packet.get("summary"), _SUMMARY_KEYS, "realized economics summary")
    if (
        summary.get("currency") != "RTC"
        or summary.get("scope_complete") is not True
        or summary.get("cash_basis") != "revenue_settlement_wallet_evidence"
        or summary.get("effort_basis") != "operator_active_minutes"
        or summary.get("fx_conversion") is not False
        or summary.get("accounting_revenue_claim") is not False
        or summary.get("tax_claim") is not False
        or summary.get("payout_or_transfer_authority") is not False
    ):
        raise CashCalibrationInputError("realized economics authority summary is incompatible")

    items = packet.get("items")
    if (
        type(items) is not list
        or not items
        or len(items) > _MAX_HISTORY_ITEMS
    ):
        raise CashCalibrationInputError("realized economics items must be a non-empty bounded list")

    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, int]] = set()
    evidence_seen: set[str] = set()
    total_cash = Decimal("0")
    total_minutes = 0
    full = partial = zero_cash = 0

    for index, raw in enumerate(items):
        row = _exact_keys(raw, _ITEM_KEYS, f"realized economics items[{index}]")
        repo = row.get("repo")
        pr = row.get("pr")
        if type(repo) is not str or not _REPO_RE.fullmatch(repo):
            raise CashCalibrationInputError(f"realized economics items[{index}].repo is invalid")
        owner, name = repo.split("/", 1)
        if owner in {".", ".."} or name in {".", ".."}:
            raise CashCalibrationInputError(f"realized economics items[{index}].repo is invalid")
        if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
            raise CashCalibrationInputError(f"realized economics items[{index}].pr is invalid")
        identity = (repo.casefold(), pr)
        if identity in identities:
            raise CashCalibrationInputError(
                f"duplicate realized economics identity: {repo}#{pr}"
            )
        identities.add(identity)

        state = row.get("state")
        status = row.get("cash_status")
        if state not in _ALLOWED_STATES or status not in _ALLOWED_CASH_STATUS:
            raise CashCalibrationInputError(
                f"realized economics items[{index}] has invalid state/status"
            )

        verified = _decimal_text(
            row.get("verified_cash_rtc"),
            f"realized economics items[{index}].verified_cash_rtc",
        )
        minutes = row.get("active_minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes <= 0
            or minutes > _MAX_ACTIVE_MINUTES
        ):
            raise CashCalibrationInputError(
                f"realized economics items[{index}].active_minutes is invalid"
            )
        evidence = row.get("payment_evidence_sha256s")
        if type(evidence) is not list:
            raise CashCalibrationInputError(
                f"realized economics items[{index}].payment_evidence_sha256s is invalid"
            )
        for fingerprint in evidence:
            if type(fingerprint) is not str or not _HEX64_RE.fullmatch(fingerprint):
                raise CashCalibrationInputError(
                    f"realized economics items[{index}] has invalid payment evidence"
                )
            if fingerprint in evidence_seen:
                raise CashCalibrationInputError(
                    "payment evidence cannot support multiple realized economics items"
                )
            evidence_seen.add(fingerprint)

        if status == "verified_paid":
            if state != "MERGED" or verified <= 0 or not evidence:
                raise CashCalibrationInputError(
                    f"realized economics items[{index}] verified_paid is inconsistent"
                )
            full += 1
        elif status == "partially_verified":
            if state != "MERGED" or verified <= 0 or not evidence:
                raise CashCalibrationInputError(
                    f"realized economics items[{index}] partially_verified is inconsistent"
                )
            partial += 1
        else:
            if verified != 0 or evidence:
                raise CashCalibrationInputError(
                    f"realized economics items[{index}] not_inferred is inconsistent"
                )
            zero_cash += 1

        ratio = row.get("realized_rtc_per_hour_estimate")
        if type(ratio) is not str or ratio != _ratio_text(verified, minutes):
            raise CashCalibrationInputError(
                f"realized economics items[{index}] RTC/hour estimate is inconsistent"
            )
        total_cash += verified
        total_minutes += minutes
        normalized.append(
            {
                "repo": repo,
                "pr": pr,
                "state": state,
                "cash_status": status,
                "verified_cash_rtc": _amount_text(verified),
                "active_minutes": minutes,
                "realized_rtc_per_hour_estimate": ratio,
                "payment_evidence_sha256s": list(evidence),
                "_verified": verified,
            }
        )

    keys = [(row["repo"].casefold(), row["pr"]) for row in normalized]
    if keys != sorted(keys):
        raise CashCalibrationInputError("realized economics items are not canonically ordered")

    normalized_scope = {
        "cash": [
            {
                "repo": row["repo"],
                "pr": row["pr"],
                "state": row["state"],
                "cash_status": row["cash_status"],
                "verified_cash_rtc": row["verified_cash_rtc"],
                "payment_evidence_sha256s": row["payment_evidence_sha256s"],
            }
            for row in normalized
        ],
        "effort": [
            {
                "repo": row["repo"],
                "pr": row["pr"],
                "active_minutes": row["active_minutes"],
            }
            for row in normalized
        ],
    }
    if not hmac.compare_digest(scope_sha, _sha256(normalized_scope)):
        raise CashCalibrationInputError("realized economics scope integrity check failed")

    if (
        summary.get("item_count") != len(normalized)
        or summary.get("fully_paid_items") != full
        or summary.get("partially_paid_items") != partial
        or summary.get("zero_verified_cash_items") != zero_cash
        or summary.get("active_minutes_total") != total_minutes
        or summary.get("verified_cash_total") != _amount_text(total_cash)
        or summary.get("realized_rtc_per_hour_estimate")
        != _ratio_text(total_cash, total_minutes)
    ):
        raise CashCalibrationInputError("realized economics summary disagrees with item evidence")

    ranking = packet.get("ranking")
    if type(ranking) is not list or len(ranking) != len(normalized):
        raise CashCalibrationInputError("realized economics ranking is invalid")
    expected_ranked = sorted(normalized, key=cmp_to_key(_history_cmp))
    for rank, (actual, source) in enumerate(zip(ranking, expected_ranked), start=1):
        actual_row = _exact_keys(
            actual, _RANKING_KEYS, f"realized economics ranking[{rank - 1}]"
        )
        expected = {
            "rank": rank,
            "repo": source["repo"],
            "pr": source["pr"],
            "realized_rtc_per_hour_estimate": source[
                "realized_rtc_per_hour_estimate"
            ],
        }
        if actual_row != expected:
            raise CashCalibrationInputError(
                "realized economics ranking disagrees with item evidence"
            )

    return receipt, normalized


def _github_issue_repo(source: Any) -> Optional[tuple[str, str]]:
    if type(source) is not str or not source or source != source.strip():
        return None
    try:
        parsed = urlsplit(source)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    pieces = parsed.path.split("/")
    if len(pieces) != 5 or pieces[0] != "" or pieces[3] != "issues":
        return None
    owner, name, issue = pieces[1], pieces[2], pieces[4]
    if (
        not _SEGMENT_RE.fullmatch(owner)
        or not _SEGMENT_RE.fullmatch(name)
        or owner in {".", ".."}
        or name in {".", ".."}
        or not issue.isascii()
        or not issue.isdigit()
        or issue.startswith("0")
    ):
        return None
    number = int(issue)
    if number <= 0:
        return None
    display = f"{owner}/{name}"
    return display, display.casefold()


def _history_stats(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for row in items:
        key = row["repo"].casefold()
        entry = stats.setdefault(
            key,
            {
                "repo": row["repo"],
                "cash_observed_terminal": 0,
                "closed_unmerged_zero_cash_terminal": 0,
                "unresolved": 0,
            },
        )
        if row["cash_status"] in {"verified_paid", "partially_verified"}:
            entry["cash_observed_terminal"] += 1
        elif (
            row["state"] == "CLOSED_UNMERGED"
            and row["cash_status"] == "not_inferred"
        ):
            entry["closed_unmerged_zero_cash_terminal"] += 1
        else:
            entry["unresolved"] += 1
    return stats


def _rate_floor(positive: int, total: int) -> tuple[Decimal, str]:
    scaled = (positive * _RATE_SCALE) // total
    value = Decimal(scaled) / Decimal(_RATE_SCALE)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return value, text or "0"


def _probability(value: Any, label: str) -> Decimal:
    parsed = _decimal_text(value, label)
    if parsed < 0 or parsed > 1:
        raise CashCalibrationInputError(f"{label} must be between 0 and 1")
    return parsed


def _probability_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def allocate_cash_calibrated_portfolio(
    candidates: list[dict[str, Any]],
    skills: list[str],
    capacity_hours: Union[str, int, Decimal],
    economics_receipt: dict[str, Any],
    *,
    saturation_threshold: int = 4,
    minimum_terminal_samples: int = 2,
) -> dict[str, Any]:
    """Dampen operator win estimates with settled-cash history, then allocate exactly.

    The realized RTC amounts are never combined with USD rewards.  History contributes
    only a dimensionless same-repository terminal cash-observation rate.  That rate
    may cap an operator probability; it can never increase one.
    """
    if (
        isinstance(minimum_terminal_samples, bool)
        or not isinstance(minimum_terminal_samples, int)
        or minimum_terminal_samples <= 0
        or minimum_terminal_samples > _MAX_TERMINAL_SAMPLES
    ):
        raise CashCalibrationInputError(
            f"minimum_terminal_samples must be an integer in 1..{_MAX_TERMINAL_SAMPLES}"
        )
    receipt_sha, history_items = _validate_economics_receipt(economics_receipt)
    history = _history_stats(history_items)

    try:
        initial = rank_opportunities(
            candidates,
            skills,
            saturation_threshold=saturation_threshold,
        )
    except OpportunityRankInputError as exc:
        raise CashCalibrationInputError(str(exc)) from exc
    ranked = initial.get("ranked")
    excluded = initial.get("excluded")
    if type(ranked) is not list or type(excluded) is not list:
        raise CashCalibrationInputError("canonical opportunity ranker returned invalid output")

    adjusted = copy.deepcopy(candidates)
    calibration: list[dict[str, Any]] = []
    dampened = 0

    for row in ranked:
        if type(row) is not dict:
            raise CashCalibrationInputError("canonical opportunity ranker returned malformed row")
        index = row.get("input_index")
        source = row.get("canonical_source_url")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= len(adjusted)
        ):
            raise CashCalibrationInputError("canonical opportunity ranker returned invalid index")
        if type(adjusted[index]) is not dict:
            raise CashCalibrationInputError("ranked candidate is not an object")
        original_text = row.get("estimated_win_probability")
        original = _probability(
            original_text, "ranked estimated_win_probability"
        )
        source_repo = _github_issue_repo(source)

        audit: dict[str, Any] = {
            "input_index": index,
            "canonical_source_url": source,
            "repo": source_repo[0] if source_repo else None,
            "original_estimated_win_probability": _probability_text(original),
            "calibrated_estimated_win_probability": _probability_text(original),
            "minimum_terminal_samples": minimum_terminal_samples,
            "terminal_sample_count": 0,
            "cash_observed_terminal_count": 0,
            "closed_unmerged_zero_cash_terminal_count": 0,
            "unresolved_history_count": 0,
            "empirical_cash_observation_rate": None,
            "disposition": "NO_CANONICAL_GITHUB_ISSUE_REPO",
        }
        if source_repo is None:
            calibration.append(audit)
            continue

        stats = history.get(source_repo[1])
        if stats is None:
            audit["disposition"] = "NO_MATCHING_HISTORY"
            calibration.append(audit)
            continue
        positive = stats["cash_observed_terminal"]
        negative = stats["closed_unmerged_zero_cash_terminal"]
        unresolved = stats["unresolved"]
        terminal = positive + negative
        audit.update(
            {
                "terminal_sample_count": terminal,
                "cash_observed_terminal_count": positive,
                "closed_unmerged_zero_cash_terminal_count": negative,
                "unresolved_history_count": unresolved,
            }
        )
        if terminal < minimum_terminal_samples:
            audit["disposition"] = "INSUFFICIENT_TERMINAL_HISTORY"
            calibration.append(audit)
            continue

        cap, cap_text = _rate_floor(positive, terminal)
        calibrated = min(original, cap)
        calibrated_text = _probability_text(calibrated)
        audit["empirical_cash_observation_rate"] = cap_text
        audit["calibrated_estimated_win_probability"] = calibrated_text
        if calibrated < original:
            adjusted[index]["estimated_win_probability"] = calibrated_text
            audit["disposition"] = "PROBABILITY_DAMPENED"
            dampened += 1
        else:
            audit["disposition"] = "HISTORY_CAP_NOT_BINDING"
        calibration.append(audit)

    try:
        portfolio = allocate_portfolio(
            adjusted,
            skills,
            capacity_hours,
            saturation_threshold=saturation_threshold,
        )
    except PortfolioInputError:
        raise

    payload: dict[str, Any] = {
        "schema": "cash-calibrated-opportunity-portfolio/v1",
        "economics_receipt_sha256": receipt_sha,
        "minimum_terminal_samples": minimum_terminal_samples,
        "calibrated_candidate_count": len(calibration),
        "dampened_candidate_count": dampened,
        "calibration": calibration,
        "portfolio": portfolio,
        "authority": {
            "eligibility": "canonical_opportunity_ranker",
            "cash_history": "realized_unit_economics_schema_and_self_integrity_only",
            "cash_evidence_authority": "inherited_not_reacquired",
            "calibration": "same_repo_terminal_cash_observation_probability_cap_only",
            "currency_conversion": False,
            "rtc_amount_used_in_usd_math": False,
            "probability_can_only_decrease": True,
            "selection": "canonical_exact_portfolio_allocator",
            "claim_or_submission_authority": False,
            "cash_or_settlement_claim": False,
            "accounting_or_tax_claim": False,
        },
    }
    payload["calibration_receipt_sha256"] = _sha256(payload)
    return payload


def format_summary(result: dict[str, Any]) -> str:
    portfolio = result.get("portfolio", {})
    return (
        f"selected={portfolio.get('selected_count', 0)} "
        f"calibrated={result.get('calibrated_candidate_count', 0)} "
        f"dampened={result.get('dampened_candidate_count', 0)} "
        f"estimated_portfolio_ev_usd={portfolio.get('estimated_portfolio_expected_value_usd', '0')} "
        "cash_claim=false currency_conversion=false"
    )


def _load_json(path: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CashCalibrationInputError(
                    f"{path} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    if path == "-":
        import sys
        text = sys.stdin.read(_MAX_JSON_BYTES + 1)
    else:
        source = Path(path)
        if source.stat().st_size > _MAX_JSON_BYTES:
            raise CashCalibrationInputError(f"{path} is too large")
        text = source.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > _MAX_JSON_BYTES:
        raise CashCalibrationInputError(f"{path} is too large")
    try:
        return json.loads(text, object_pairs_hook=unique_object)
    except CashCalibrationInputError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CashCalibrationInputError(f"{path} is not valid bounded UTF-8 JSON") from exc


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.cash_calibrated_portfolio",
        description=(
            "Dampen operator win estimates with evidence-bound terminal cash history "
            "without crossing RTC/USD currencies, then run the canonical exact allocator."
        ),
    )
    parser.add_argument("request", help="portfolio request JSON path, or - for stdin")
    parser.add_argument("economics", help="realized-unit-economics JSON path")
    parser.add_argument(
        "--minimum-terminal-samples",
        type=int,
        default=2,
        help="minimum same-repo terminal outcomes before a cash-history cap applies",
    )
    parser.add_argument(
        "--saturation-threshold",
        type=int,
        default=4,
        help="forwarded to canonical opportunity ranking",
    )
    parser.add_argument("--summary", action="store_true", help="emit one-line summary")
    args = parser.parse_args(argv)

    if args.request == "-" and args.economics == "-":
        parser.error("request and economics cannot both read from stdin")
    try:
        request = _load_json(args.request)
        economics = _load_json(args.economics)
        if type(request) is not dict or type(economics) is not dict:
            raise CashCalibrationInputError("request and economics JSON must be objects")
        result = allocate_cash_calibrated_portfolio(
            request.get("candidates"),
            request.get("skills", []),
            request.get("capacity_hours"),
            economics,
            saturation_threshold=args.saturation_threshold,
            minimum_terminal_samples=args.minimum_terminal_samples,
        )
    except (
        OSError,
        CashCalibrationInputError,
        OpportunityRankInputError,
        PortfolioInputError,
    ) as exc:
        parser.error(str(exc))

    if args.summary:
        print(format_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["portfolio"]["selected_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
