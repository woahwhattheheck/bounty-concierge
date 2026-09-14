# SPDX-License-Identifier: MIT
"""Cash-history calibration that reacquires live closeout and wallet authority.

This module never accepts a standalone realized-economics receipt as decision
input.  Production calibration first rebuilds closeout state from live GitHub,
queries canonical wallet history, and only then invokes the existing
``compile_realized_unit_economics`` authority path in-process.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlsplit

from concierge import realized_unit_economics as rue
from concierge import revenue_closeout as closeout
from concierge import revenue_settlement as settlement
from concierge.opportunity_ranker import OpportunityRankInputError, rank_opportunities
from concierge.portfolio_allocator import PortfolioInputError, allocate_portfolio


class CashCalibrationInputError(ValueError):
    """Raised when calibration inputs or provider-derived evidence are unreliable."""


_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_TERMINAL_SAMPLES = 10_000
_RATE_SCALE = 1_000_000
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_POSITIVE_CASH = frozenset({"verified_paid", "partially_verified"})


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


def _decimal_text(value: Any, label: str) -> Decimal:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise CashCalibrationInputError(f"{label} must be a bounded decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CashCalibrationInputError(f"{label} must be a finite decimal string") from exc
    exponent = parsed.as_tuple().exponent
    if (
        not parsed.is_finite()
        or len(parsed.as_tuple().digits) > 30
        or not isinstance(exponent, int)
        or abs(exponent) > 18
    ):
        raise CashCalibrationInputError(f"{label} must be a bounded finite decimal")
    return parsed


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


def _github_issue_repo(source: Any) -> Optional[tuple[str, str]]:
    """Return display + casefolded repo only for a canonical GitHub issue URL."""
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
    if int(issue) <= 0:
        return None
    display = f"{owner}/{name}"
    return display, display.casefold()


def _history_stats(items: Any) -> dict[str, dict[str, Any]]:
    """Project provider-reacquired economics into dimensionless terminal counts."""
    if type(items) is not list or not items or len(items) > _MAX_TERMINAL_SAMPLES:
        raise CashCalibrationInputError("compiled economics items are not a bounded list")
    stats: dict[str, dict[str, Any]] = {}
    identities: set[tuple[str, int]] = set()
    for index, row in enumerate(items):
        if type(row) is not dict:
            raise CashCalibrationInputError(f"compiled economics item {index} is malformed")
        repo = row.get("repo")
        pr = row.get("pr")
        if type(repo) is not str or "/" not in repo:
            raise CashCalibrationInputError(f"compiled economics item {index} repo is invalid")
        if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
            raise CashCalibrationInputError(f"compiled economics item {index} pr is invalid")
        identity = (repo.casefold(), pr)
        if identity in identities:
            raise CashCalibrationInputError("compiled economics contains duplicate identity")
        identities.add(identity)

        state = row.get("state")
        cash_status = row.get("cash_status")
        entry = stats.setdefault(
            repo.casefold(),
            {
                "repo": repo,
                "cash_observed_terminal": 0,
                "closed_unmerged_zero_cash_terminal": 0,
                "unresolved": 0,
            },
        )
        if cash_status in _POSITIVE_CASH:
            if state != "MERGED":
                raise CashCalibrationInputError(
                    "compiled economics cannot report cash-positive unmerged history"
                )
            entry["cash_observed_terminal"] += 1
        elif cash_status == "not_inferred" and state == "CLOSED_UNMERGED":
            entry["closed_unmerged_zero_cash_terminal"] += 1
        elif cash_status == "not_inferred" and state in {"OPEN", "HEAD_MOVED", "MERGED"}:
            entry["unresolved"] += 1
        else:
            raise CashCalibrationInputError(
                f"compiled economics item {index} has unsupported terminal semantics"
            )
    return stats


def _rate_floor(positive: int, total: int) -> tuple[Decimal, str]:
    scaled = (positive * _RATE_SCALE) // total
    value = Decimal(scaled) / Decimal(_RATE_SCALE)
    return value, _probability_text(value)


def allocate_cash_calibrated_portfolio(
    candidates: list[dict[str, Any]],
    skills: list[str],
    capacity_hours: Union[str, int, Decimal],
    closeout_manifest_items: list[dict[str, Any]],
    payment_bindings: list[dict[str, Any]],
    effort_log: dict[str, Any],
    *,
    wallet: str,
    saturation_threshold: int = 4,
    minimum_terminal_samples: int = 2,
    max_closeout_pages: int = 10,
) -> dict[str, Any]:
    """Reacquire live provider evidence, dampen win estimates, allocate exactly.

    Authority path, in order:
      1. ``revenue_closeout.build_closeout_queue`` re-reads current GitHub state.
      2. ``revenue_settlement._query_canonical_history`` re-reads wallet history.
      3. ``compile_realized_unit_economics`` reconciles those live observations.
      4. only the resulting same-repository terminal counts may lower probability.

    No standalone economics receipt/history/closeout result can be passed into this
    public function.  RTC amounts never enter USD reward arithmetic.
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
    if (
        isinstance(max_closeout_pages, bool)
        or not isinstance(max_closeout_pages, int)
        or max_closeout_pages <= 0
        or max_closeout_pages > 100
    ):
        raise CashCalibrationInputError("max_closeout_pages must be an integer in 1..100")

    # Critical trust boundary: both provider observations are reacquired here.
    # Callers supply scope/bindings/effort, never the observed PR lifecycle or
    # wallet-history result that can change calibration.
    live_closeout = closeout.build_closeout_queue(
        closeout_manifest_items,
        max_pages=max_closeout_pages,
    )
    live_history, history_wallet = settlement._query_canonical_history(wallet)
    economics = rue.compile_realized_unit_economics(
        live_closeout,
        live_history,
        payment_bindings,
        effort_log,
        wallet=wallet,
        history_wallet=history_wallet,
        history_source="queried_wallet",
    )
    if not rue.verify_receipt(economics):
        raise CashCalibrationInputError("in-process realized economics receipt failed integrity")
    summary = economics.get("summary")
    if (
        type(summary) is not dict
        or summary.get("currency") != "RTC"
        or summary.get("scope_complete") is not True
        or summary.get("cash_basis") != "revenue_settlement_wallet_evidence"
        or summary.get("effort_basis") != "operator_active_minutes"
        or summary.get("fx_conversion") is not False
        or summary.get("accounting_revenue_claim") is not False
        or summary.get("tax_claim") is not False
        or summary.get("payout_or_transfer_authority") is not False
    ):
        raise CashCalibrationInputError("in-process economics authority summary is incompatible")
    history = _history_stats(economics.get("items"))

    try:
        initial = rank_opportunities(
            candidates,
            skills,
            saturation_threshold=saturation_threshold,
        )
    except OpportunityRankInputError as exc:
        raise CashCalibrationInputError(str(exc)) from exc
    ranked = initial.get("ranked")
    if type(ranked) is not list or type(initial.get("excluded")) is not list:
        raise CashCalibrationInputError("canonical opportunity ranker returned invalid output")

    adjusted = copy.deepcopy(candidates)
    calibration: list[dict[str, Any]] = []
    dampened = 0
    for row in ranked:
        if type(row) is not dict:
            raise CashCalibrationInputError("canonical opportunity ranker returned malformed row")
        index = row.get("input_index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= len(adjusted)
            or type(adjusted[index]) is not dict
        ):
            raise CashCalibrationInputError("canonical opportunity ranker returned invalid index")
        original = _probability(
            row.get("estimated_win_probability"),
            "ranked estimated_win_probability",
        )
        source = row.get("canonical_source_url")
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
        terminal = positive + negative
        audit.update(
            terminal_sample_count=terminal,
            cash_observed_terminal_count=positive,
            closed_unmerged_zero_cash_terminal_count=negative,
            unresolved_history_count=stats["unresolved"],
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
        "schema": "cash-calibrated-opportunity-portfolio/v2",
        "economics_receipt_sha256": economics["receipt_sha256"],
        "minimum_terminal_samples": minimum_terminal_samples,
        "calibrated_candidate_count": len(calibration),
        "dampened_candidate_count": dampened,
        "calibration": calibration,
        "portfolio": portfolio,
        "authority": {
            "eligibility": "canonical_opportunity_ranker",
            "closeout_state": "live_github_reacquired_in_process",
            "wallet_history": "canonical_provider_reacquired_in_process",
            "economics": "compiled_in_process_from_live_provider_observations",
            "standalone_economics_receipt_accepted": False,
            "captured_wallet_history_accepted": False,
            "cash_evidence_authority": "reacquired_not_inherited",
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
        "live_closeout=true live_wallet=true cash_claim=false currency_conversion=false"
    )


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CashCalibrationInputError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_bounded(path: str) -> bytes:
    if path == "-":
        data = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            raise CashCalibrationInputError("stdin JSON is too large")
        return data

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CashCalibrationInputError(f"{path} must be a regular file")
        if info.st_size > _MAX_JSON_BYTES:
            raise CashCalibrationInputError(f"{path} is too large")
        chunks: list[bytes] = []
        remaining = _MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > _MAX_JSON_BYTES:
            raise CashCalibrationInputError(f"{path} grew beyond the size limit")
        return data
    finally:
        os.close(fd)


def _load_json(path: str) -> Any:
    raw = _read_bounded(path)
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                CashCalibrationInputError(f"non-finite JSON value {value} is not allowed")
            ),
        )
    except CashCalibrationInputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CashCalibrationInputError(f"{path} is not valid strict UTF-8 JSON") from exc


def _schema_items(payload: Any, label: str) -> list[dict[str, Any]]:
    if type(payload) is not dict or payload.get("schema_version") != 1:
        raise CashCalibrationInputError(f"{label} must be a schema_version 1 object")
    items = payload.get("items")
    if type(items) is not list:
        raise CashCalibrationInputError(f"{label}.items must be a list")
    return items


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.cash_calibrated_portfolio",
        description=(
            "Reacquire live GitHub closeout + canonical wallet history, then only "
            "dampen operator win estimates before exact portfolio allocation."
        ),
    )
    parser.add_argument("request", help="portfolio request JSON path, or - for stdin")
    parser.add_argument("manifest", help="revenue-closeout manifest JSON path")
    parser.add_argument("bindings", help="payment bindings JSON path")
    parser.add_argument("effort", help="operator effort JSON path")
    parser.add_argument("--wallet", required=True, help="canonical recipient wallet")
    parser.add_argument("--minimum-terminal-samples", type=int, default=2)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--max-closeout-pages", type=int, default=10)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    if [args.request, args.manifest, args.bindings, args.effort].count("-") > 1:
        parser.error("at most one input may read from stdin")
    try:
        request = _load_json(args.request)
        manifest = _load_json(args.manifest)
        bindings = _load_json(args.bindings)
        effort = _load_json(args.effort)
        if type(request) is not dict:
            raise CashCalibrationInputError("request must be an object")
        result = allocate_cash_calibrated_portfolio(
            request.get("candidates"),
            request.get("skills", []),
            request.get("capacity_hours"),
            _schema_items(manifest, "manifest"),
            _schema_items(bindings, "bindings"),
            effort,
            wallet=args.wallet,
            saturation_threshold=args.saturation_threshold,
            minimum_terminal_samples=args.minimum_terminal_samples,
            max_closeout_pages=args.max_closeout_pages,
        )
    except (
        OSError,
        CashCalibrationInputError,
        closeout.RevenueCloseoutError,
        closeout.RevenueCloseoutInputError,
        settlement.PayoutLookupError,
        settlement.RevenueSettlementInputError,
        settlement.RevenueSettlementEvidenceError,
        rue.RealizedUnitEconomicsInputError,
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
