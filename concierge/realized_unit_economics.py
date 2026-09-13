# SPDX-License-Identifier: MIT
"""Realized unit economics from evidence-bound settlement plus complete effort scope.

This module deliberately does not infer cash from merge state or advertised rewards.
The cash numerator is produced by ``concierge.revenue_settlement`` from exact bound
wallet-history evidence. Operator effort remains operator-supplied decision-support
data; it is never upgraded to payroll, accounting, or tax authority.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, localcontext
from functools import cmp_to_key
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Optional

from concierge import revenue_settlement as settlement


_SCHEMA_VERSION = 1
_EFFORT_SOURCE = "operator_active_minutes"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_ACTIVE_MINUTES = 525_600
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ALLOWED_STATES = frozenset({"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"})
_ALLOWED_CASH_STATUS = frozenset(
    {"verified_paid", "partially_verified", "not_inferred"}
)


class RealizedUnitEconomicsInputError(ValueError):
    """Malformed, incomplete, or internally inconsistent economics input."""


def _canonical_json(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RealizedUnitEconomicsInputError(
            "value is not canonical JSON"
        ) from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise RealizedUnitEconomicsInputError(
            "canonical JSON value is too large"
        )
    return payload


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _identity(raw: Any) -> tuple[str, int]:
    if not isinstance(raw, dict):
        raise RealizedUnitEconomicsInputError("item must be an object")
    repo = raw.get("repo")
    pr = raw.get("pr")
    if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
        raise RealizedUnitEconomicsInputError(
            "repo must be in owner/name form"
        )
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise RealizedUnitEconomicsInputError(
            "repo must not contain dot path segments"
        )
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise RealizedUnitEconomicsInputError(
            "pr must be a positive integer"
        )
    return repo, pr


def _decimal(
    value: Any,
    *,
    field: str,
    positive: bool = False,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(
        value, (str, int, Decimal)
    ):
        raise RealizedUnitEconomicsInputError(
            f"{field} must be an exact decimal string or integer"
        )
    source = str(value)
    if len(source) > 64:
        raise RealizedUnitEconomicsInputError(
            f"{field} representation is too large"
        )
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise RealizedUnitEconomicsInputError(
            f"{field} must be a decimal"
        ) from exc
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or len(amount.as_tuple().digits) > 30
        or not isinstance(exponent, int)
        or abs(exponent) > 18
        or (positive and amount <= 0)
    ):
        qualifier = "positive " if positive else ""
        raise RealizedUnitEconomicsInputError(
            f"{field} must be a bounded {qualifier}decimal"
        )
    return amount


def _amount_text(amount: Decimal) -> str:
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _ratio_text(cash: Decimal, minutes: int) -> str:
    """Return a deterministic high-precision RTC/hour decision-support estimate."""
    with localcontext() as context:
        context.prec = 36
        value = (cash * Decimal(60)) / Decimal(minutes)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _strict_json(path: str) -> Any:
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_JSON_BYTES:
            raise RealizedUnitEconomicsInputError(
                f"{path} is too large"
            )

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise RealizedUnitEconomicsInputError(
                        f"{path} contains duplicate JSON key {key!r}"
                    )
                result[key] = value
            return result

        return json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_float=Decimal,
            parse_int=int,
        )
    except RealizedUnitEconomicsInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RealizedUnitEconomicsInputError(
            f"{path} is not valid bounded UTF-8 JSON"
        ) from exc


def _effort_items(payload: Any) -> dict[tuple[str, int], dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _SCHEMA_VERSION
        or payload.get("source") != _EFFORT_SOURCE
    ):
        raise RealizedUnitEconomicsInputError(
            "effort log must use schema_version 1 and "
            "source operator_active_minutes"
        )
    items = payload.get("items")
    if (
        not isinstance(items, list)
        or not items
        or len(items) > _MAX_ITEMS
    ):
        raise RealizedUnitEconomicsInputError(
            "effort items must be a non-empty bounded list"
        )
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in items:
        repo, pr = _identity(raw)
        key = (repo.casefold(), pr)
        if key in result:
            raise RealizedUnitEconomicsInputError(
                f"duplicate effort item: {repo}#{pr}"
            )
        minutes = raw.get("active_minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes <= 0
            or minutes > _MAX_ACTIVE_MINUTES
        ):
            raise RealizedUnitEconomicsInputError(
                "active_minutes must be an integer in 1..525600"
            )
        result[key] = {
            "repo": repo,
            "pr": pr,
            "active_minutes": minutes,
        }
    return result


def _validated_reconciliation(
    results: Any,
) -> dict[tuple[str, int], dict[str, Any]]:
    if (
        not isinstance(results, list)
        or not results
        or len(results) > _MAX_ITEMS
    ):
        raise RealizedUnitEconomicsInputError(
            "reconciliation results must be a non-empty bounded list"
        )
    normalized: dict[tuple[str, int], dict[str, Any]] = {}
    used_evidence: set[str] = set()
    for raw in results:
        repo, pr = _identity(raw)
        key = (repo.casefold(), pr)
        if key in normalized:
            raise RealizedUnitEconomicsInputError(
                f"duplicate reconciliation item: {repo}#{pr}"
            )
        state = raw.get("state")
        if state not in _ALLOWED_STATES:
            raise RealizedUnitEconomicsInputError(
                f"{repo}#{pr} has unsupported closeout state"
            )
        if raw.get("currency") != "RTC":
            raise RealizedUnitEconomicsInputError(
                f"{repo}#{pr} is not denominated in RTC"
            )
        advertised = _decimal(
            raw.get("advertised_amount"),
            field="advertised_amount",
            positive=True,
        )
        verified = _decimal(
            raw.get("verified_amount"),
            field="verified_amount",
        )
        if verified < 0:
            raise RealizedUnitEconomicsInputError(
                "verified_amount must not be negative"
            )
        status = raw.get("cash_status")
        if status not in _ALLOWED_CASH_STATUS:
            raise RealizedUnitEconomicsInputError(
                f"{repo}#{pr} has unsupported cash_status"
            )
        evidence = raw.get("payment_evidence")
        if not isinstance(evidence, list):
            raise RealizedUnitEconomicsInputError(
                f"{repo}#{pr} payment_evidence must be a list"
            )

        if status == "verified_paid":
            if state != "MERGED" or verified != advertised or not evidence:
                raise RealizedUnitEconomicsInputError(
                    f"{repo}#{pr} verified_paid state is inconsistent"
                )
        elif status == "partially_verified":
            if (
                state != "MERGED"
                or verified <= 0
                or verified >= advertised
                or not evidence
            ):
                raise RealizedUnitEconomicsInputError(
                    f"{repo}#{pr} partially_verified state is inconsistent"
                )
        else:
            if verified != 0 or evidence:
                raise RealizedUnitEconomicsInputError(
                    f"{repo}#{pr} not_inferred state contains cash evidence"
                )

        evidence_hashes: list[str] = []
        for item in evidence:
            if not isinstance(item, dict):
                raise RealizedUnitEconomicsInputError(
                    "payment evidence row must be an object"
                )
            fingerprint = item.get("history_sha256")
            if (
                type(fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            ):
                raise RealizedUnitEconomicsInputError(
                    "payment evidence fingerprint must be lowercase SHA-256"
                )
            if fingerprint in evidence_hashes:
                raise RealizedUnitEconomicsInputError(
                    f"{repo}#{pr} repeats payment evidence"
                )
            if fingerprint in used_evidence:
                raise RealizedUnitEconomicsInputError(
                    "one payment evidence row cannot support multiple "
                    "economics items"
                )
            evidence_hashes.append(fingerprint)
            used_evidence.add(fingerprint)

        normalized[key] = {
            "repo": repo,
            "pr": pr,
            "state": state,
            "cash_status": status,
            "advertised_amount": advertised,
            "verified_amount": verified,
            "payment_evidence_sha256s": evidence_hashes,
        }
    return normalized


def _ranking_cmp(left: dict[str, Any], right: dict[str, Any]) -> int:
    """Compare exact cash/minute ratios without decimal-division rounding."""
    left_cross = left["_verified"] * Decimal(right["active_minutes"])
    right_cross = right["_verified"] * Decimal(left["active_minutes"])
    if left_cross > right_cross:
        return -1
    if left_cross < right_cross:
        return 1
    left_key = (left["repo"].casefold(), left["pr"])
    right_key = (right["repo"].casefold(), right["pr"])
    return (left_key > right_key) - (left_key < right_key)


def _economics_from_reconciled(
    results: Any,
    effort_log: Any,
    *,
    wallet: str,
    history_source: str,
    settlement_summary: Optional[Any] = None,
) -> dict[str, Any]:
    """Compile a scope-complete profitability receipt from reconciled evidence.

    This function validates internal consistency but does not itself establish
    wallet-history authenticity. Production callers should use
    :func:`compile_realized_unit_economics`, which invokes revenue_settlement
    directly before entering this boundary.
    """
    if (
        type(wallet) is not str
        or not wallet
        or wallet != wallet.strip()
        or any(char.isspace() or not char.isprintable() for char in wallet)
    ):
        raise RealizedUnitEconomicsInputError(
            "wallet must be one printable identifier"
        )
    if history_source not in {"queried_wallet", "captured_wallet"}:
        raise RealizedUnitEconomicsInputError(
            "history_source is invalid"
        )

    reconciled = _validated_reconciliation(results)
    effort = _effort_items(effort_log)
    if set(reconciled) != set(effort):
        missing = sorted(set(reconciled) - set(effort))
        extra = sorted(set(effort) - set(reconciled))
        detail = []
        if missing:
            detail.append(f"missing effort for {missing[0][0]}#{missing[0][1]}")
        if extra:
            detail.append(f"extra effort for {extra[0][0]}#{extra[0][1]}")
        raise RealizedUnitEconomicsInputError(
            "effort scope must exactly cover reconciliation scope"
            + (": " + "; ".join(detail) if detail else "")
        )

    rows: list[dict[str, Any]] = []
    with localcontext() as context:
        context.prec = 128
        total_cash = Decimal("0")
        total_minutes = 0
        paid = partial = zero_cash = 0

        for key in sorted(reconciled):
            cash = reconciled[key]
            work = effort[key]
            verified = cash["verified_amount"]
            total_cash += verified
            total_minutes += work["active_minutes"]
            if cash["cash_status"] == "verified_paid":
                paid += 1
            elif cash["cash_status"] == "partially_verified":
                partial += 1
            else:
                zero_cash += 1

            rows.append(
                {
                    "repo": cash["repo"],
                    "pr": cash["pr"],
                    "state": cash["state"],
                    "cash_status": cash["cash_status"],
                    "verified_cash_rtc": _amount_text(verified),
                    "active_minutes": work["active_minutes"],
                    "realized_rtc_per_hour_estimate": _ratio_text(
                        verified, work["active_minutes"]
                    ),
                    "payment_evidence_sha256s": cash[
                        "payment_evidence_sha256s"
                    ],
                    "_verified": verified,
                }
            )

    if settlement_summary is not None:
        if not isinstance(settlement_summary, dict):
            raise RealizedUnitEconomicsInputError(
                "settlement_summary must be an object"
            )
        claimed_total = _decimal(
            settlement_summary.get("verified_cash_total"),
            field="settlement verified_cash_total",
        )
        if claimed_total != total_cash:
            raise RealizedUnitEconomicsInputError(
                "settlement summary cash total disagrees with item evidence"
            )
        if settlement_summary.get("currency") != "RTC":
            raise RealizedUnitEconomicsInputError(
                "settlement summary currency must be RTC"
            )

    ranked = sorted(rows, key=cmp_to_key(_ranking_cmp))
    ranking = [
        {
            "rank": index,
            "repo": row["repo"],
            "pr": row["pr"],
            "realized_rtc_per_hour_estimate": row[
                "realized_rtc_per_hour_estimate"
            ],
        }
        for index, row in enumerate(ranked, 1)
    ]

    public_rows = [
        {key: value for key, value in row.items() if key != "_verified"}
        for row in rows
    ]
    normalized_scope = {
        "cash": [
            {
                "repo": row["repo"],
                "pr": row["pr"],
                "state": row["state"],
                "cash_status": row["cash_status"],
                "verified_cash_rtc": row["verified_cash_rtc"],
                "payment_evidence_sha256s": row[
                    "payment_evidence_sha256s"
                ],
            }
            for row in public_rows
        ],
        "effort": [
            {
                "repo": effort[key]["repo"],
                "pr": effort[key]["pr"],
                "active_minutes": effort[key]["active_minutes"],
            }
            for key in sorted(effort)
        ],
    }

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "wallet": wallet,
        "history_source": history_source,
        "scope_sha256": _sha256(normalized_scope),
        "summary": {
            "currency": "RTC",
            "verified_cash_total": _amount_text(total_cash),
            "active_minutes_total": total_minutes,
            "realized_rtc_per_hour_estimate": _ratio_text(
                total_cash, total_minutes
            ),
            "fully_paid_items": paid,
            "partially_paid_items": partial,
            "zero_verified_cash_items": zero_cash,
            "item_count": len(public_rows),
            "scope_complete": True,
            "cash_basis": "revenue_settlement_wallet_evidence",
            "effort_basis": _EFFORT_SOURCE,
            "fx_conversion": False,
            "accounting_revenue_claim": False,
            "tax_claim": False,
            "payout_or_transfer_authority": False,
        },
        "ranking": ranking,
        "items": public_rows,
    }
    payload["receipt_sha256"] = _sha256(payload)
    return payload


def compile_realized_unit_economics(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    effort_log: Any,
    *,
    wallet: str,
    history_wallet: str,
    history_source: str,
) -> dict[str, Any]:
    """Reconcile cash at the settlement boundary, then compute unit economics."""
    results = settlement.reconcile_cash(
        closeout_items,
        history,
        bindings,
        wallet=wallet,
        history_wallet=history_wallet,
        history_source=history_source,
    )
    summary = settlement.summarize_cash(results)
    return _economics_from_reconciled(
        results,
        effort_log,
        wallet=wallet,
        history_source=history_source,
        settlement_summary=summary,
    )


def verify_receipt(receipt: Any) -> bool:
    """Verify receipt self-integrity; this does not re-query wallet evidence."""
    if not isinstance(receipt, dict):
        return False
    received = receipt.get("receipt_sha256")
    if (
        type(received) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", received)
    ):
        return False
    candidate = dict(receipt)
    candidate.pop("receipt_sha256", None)
    return _sha256(candidate) == received


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.realized_unit_economics",
        description=(
            "Compute realized RTC/hour from evidence-bound settlement plus a "
            "scope-complete operator active-minute log."
        ),
    )
    parser.add_argument("closeout", help="revenue_closeout JSON")
    parser.add_argument("bindings", help="payment binding JSON")
    parser.add_argument("effort", help="operator effort JSON")
    parser.add_argument("--wallet", required=True, help="recipient wallet")
    parser.add_argument(
        "--history",
        help="captured wallet history; omit to query the configured node",
    )
    args = parser.parse_args(argv)

    try:
        closeout_payload = _strict_json(args.closeout)
        bindings_payload = _strict_json(args.bindings)
        effort_payload = _strict_json(args.effort)
        closeout_items = settlement._schema_items(
            closeout_payload, name="closeout"
        )
        binding_items = settlement._schema_items(
            bindings_payload, name="bindings"
        )
        if args.history:
            history_payload = _strict_json(args.history)
            history_items, history_wallet = settlement._history_capture(
                history_payload, wallet=args.wallet
            )
            history_source = "captured_wallet"
        else:
            history_items, history_wallet = (
                settlement._query_canonical_history(args.wallet)
            )
            history_source = "queried_wallet"

        payload = compile_realized_unit_economics(
            closeout_items,
            history_items,
            binding_items,
            effort_payload,
            wallet=args.wallet,
            history_wallet=history_wallet,
            history_source=history_source,
        )
    except (
        OSError,
        settlement.PayoutLookupError,
        settlement.RevenueSettlementInputError,
        settlement.RevenueSettlementEvidenceError,
        RealizedUnitEconomicsInputError,
    ) as exc:
        parser.error(str(exc))

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
