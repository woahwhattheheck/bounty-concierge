# SPDX-License-Identifier: MIT
"""Evidence-bound cash reconciliation for the paid-work closeout queue.

This module composes with :mod:`concierge.revenue_closeout`. A merge, an
advertised bounty amount, or a settlement follow-up URL is never treated as
proof of payment. Cash becomes ``verified_paid`` only when the operator binds
one or more exact wallet-history rows to one merged closeout item and those
rows independently validate against the expected wallet and amount.

The binding uses a SHA-256 fingerprint of the complete canonical history row,
not heuristics such as "same amount near the merge time". That makes the
operator's evidence selection explicit and replayable while keeping this
module read-only with respect to payment providers.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from concierge.payout_tracker import PayoutLookupError, check_history


_SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_DECIMAL_CHARS = 64
_MAX_DECIMAL_DIGITS = 30
_MAX_DECIMAL_EXPONENT = 18
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STATUSES = frozenset({"confirmed"})
_IN_FLIGHT_STATUSES = frozenset({"pending", "confirming"})
_FAILED_STATUSES = frozenset({"failed"})


class RevenueSettlementInputError(ValueError):
    """Raised when reconciliation input is malformed or ambiguous."""


class RevenueSettlementEvidenceError(RuntimeError):
    """Raised when bound payout evidence does not prove the claimed cash state."""


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
        raise RevenueSettlementInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise RevenueSettlementInputError("canonical JSON value is too large")
    return payload


def history_row_sha256(row: dict[str, Any]) -> str:
    """Return the evidence identity for one exact wallet-history row."""
    if not isinstance(row, dict):
        raise RevenueSettlementInputError("wallet history row must be an object")
    return hashlib.sha256(_canonical_json(row)).hexdigest()


def _decimal(value: Any, *, field: str, positive: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal, float)):
        raise RevenueSettlementInputError(f"{field} must be a decimal")
    source = str(value)
    if len(source) > _MAX_DECIMAL_CHARS:
        raise RevenueSettlementInputError(f"{field} representation is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise RevenueSettlementInputError(f"{field} must be a decimal") from exc
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or len(amount.as_tuple().digits) > _MAX_DECIMAL_DIGITS
        or not isinstance(exponent, int)
        or abs(exponent) > _MAX_DECIMAL_EXPONENT
        or (positive and amount <= 0)
    ):
        raise RevenueSettlementInputError(f"{field} must be a bounded positive decimal")
    return amount


def _amount_text(amount: Decimal) -> str:
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _repo_and_pr(item: Any) -> tuple[str, int]:
    if not isinstance(item, dict):
        raise RevenueSettlementInputError("closeout item must be an object")
    repo = item.get("repo")
    pr = item.get("pr")
    if (
        not isinstance(repo, str)
        or not _REPO_RE.fullmatch(repo)
        or repo.split("/", 1)[0] in {".", ".."}
        or repo.split("/", 1)[1] in {".", ".."}
    ):
        raise RevenueSettlementInputError("repo must be in owner/name form")
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise RevenueSettlementInputError("pr must be a positive integer")
    return repo, pr


def _wallet(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(char.isspace() or not char.isprintable() for char in value)
    ):
        raise RevenueSettlementInputError("wallet must be one printable identifier")
    return value


def _history_amount(row: dict[str, Any]) -> Decimal:
    if "amount_rtc" in row and "amount" in row:
        legacy = _decimal(row["amount_rtc"], field="history amount_rtc")
        canonical = _decimal(row["amount"], field="history amount")
        if legacy != canonical:
            raise RevenueSettlementEvidenceError(
                "wallet history row contains conflicting amount fields"
            )
        return canonical
    if "amount_rtc" in row:
        return _decimal(row["amount_rtc"], field="history amount_rtc")
    if "amount" in row:
        return _decimal(row["amount"], field="history amount")
    raise RevenueSettlementEvidenceError("wallet history row omitted amount")


def _history_recipient(row: dict[str, Any]) -> str:
    values = []
    for key in ("to", "to_addr", "recipient", "wallet"):
        if key not in row:
            continue
        value = row[key]
        if type(value) is not str or not value:
            raise RevenueSettlementEvidenceError(
                "wallet history row contains malformed recipient"
            )
        values.append(value)
    if not values:
        raise RevenueSettlementEvidenceError(
            "wallet history row omitted recipient identity"
        )
    if len(set(values)) != 1:
        raise RevenueSettlementEvidenceError(
            "wallet history row contains conflicting recipient identities"
        )
    return values[0]


def _history_status(row: dict[str, Any]) -> str:
    """Return a conservative settlement state for one provider history row."""
    if "status" not in row:
        # Current RustChain confirmed transfer rows omit status; in-flight rows
        # carry status. This contract is already enforced by payout_tracker.
        return "confirmed"
    status = row["status"]
    if type(status) is not str:
        raise RevenueSettlementEvidenceError(
            "wallet history row contains malformed status"
        )
    normalized = status.strip().lower()
    if normalized in _TERMINAL_STATUSES:
        return "confirmed"
    if normalized in _IN_FLIGHT_STATUSES:
        return normalized
    if normalized in _FAILED_STATUSES:
        return "failed"
    raise RevenueSettlementEvidenceError(
        "wallet history row contains unknown settlement status"
    )


def inventory_history(history: Any, wallet: str) -> list[dict[str, Any]]:
    """Validate and fingerprint a captured wallet history without inferring awards."""
    wallet = _wallet(wallet)
    if not isinstance(history, list) or len(history) > _MAX_ITEMS:
        raise RevenueSettlementInputError("wallet history must be a bounded list")

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in history:
        if not isinstance(row, dict):
            raise RevenueSettlementInputError("wallet history row must be an object")
        fingerprint = history_row_sha256(row)
        if fingerprint in seen:
            raise RevenueSettlementEvidenceError(
                "wallet history contains duplicate indistinguishable rows"
            )
        seen.add(fingerprint)
        amount = _history_amount(row)
        recipient = _history_recipient(row)
        status = _history_status(row)
        result.append(
            {
                "history_sha256": fingerprint,
                "amount_rtc": _amount_text(amount),
                "recipient_matches_wallet": recipient == wallet,
                "status": status,
                # Timestamp is informational only; it is never used to guess a match.
                "timestamp": row.get("timestamp", row.get("created_at")),
            }
        )
    return result


def _validate_closeout_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        raise RevenueSettlementInputError("closeout items must be a bounded list")
    seen: set[tuple[str, int]] = set()
    normalized: list[dict[str, Any]] = []
    for raw in items:
        repo, pr = _repo_and_pr(raw)
        identity = (repo.casefold(), pr)
        if identity in seen:
            raise RevenueSettlementInputError(
                f"duplicate closeout item: {repo}#{pr}"
            )
        seen.add(identity)
        state = raw.get("state")
        if state not in {"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"}:
            raise RevenueSettlementInputError(
                f"{repo}#{pr} has unsupported closeout state"
            )
        currency = raw.get("currency")
        if type(currency) is not str or not currency:
            raise RevenueSettlementInputError(f"{repo}#{pr} omitted currency")
        amount = _decimal(raw.get("advertised_amount"), field="advertised_amount")
        if raw.get("cash_status") != "not_inferred":
            raise RevenueSettlementInputError(
                f"{repo}#{pr} cash_status must be not_inferred before reconciliation"
            )
        normalized.append(
            {
                "raw": raw,
                "repo": repo,
                "pr": pr,
                "identity": identity,
                "state": state,
                "currency": currency,
                "amount": amount,
            }
        )
    return normalized


def _validate_bindings(bindings: Any) -> dict[tuple[str, int], list[str]]:
    if not isinstance(bindings, list) or len(bindings) > _MAX_ITEMS:
        raise RevenueSettlementInputError("bindings must be a bounded list")
    by_item: dict[tuple[str, int], list[str]] = {}
    globally_used: set[str] = set()
    for raw in bindings:
        repo, pr = _repo_and_pr(raw)
        identity = (repo.casefold(), pr)
        if identity in by_item:
            raise RevenueSettlementInputError(
                f"duplicate payment binding: {repo}#{pr}"
            )
        fingerprints = raw.get("history_sha256s")
        if (
            not isinstance(fingerprints, list)
            or not fingerprints
            or len(fingerprints) > 100
        ):
            raise RevenueSettlementInputError(
                "history_sha256s must be a non-empty bounded list"
            )
        local: list[str] = []
        for value in fingerprints:
            if type(value) is not str or not _SHA256_RE.fullmatch(value):
                raise RevenueSettlementInputError(
                    "history_sha256s entries must be lowercase SHA-256 values"
                )
            if value in local:
                raise RevenueSettlementInputError(
                    "payment binding repeats one history row"
                )
            if value in globally_used:
                raise RevenueSettlementInputError(
                    "one wallet history row cannot settle multiple closeout items"
                )
            local.append(value)
            globally_used.add(value)
        by_item[identity] = local
    return by_item


def reconcile_cash(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    *,
    wallet: str,
) -> list[dict[str, Any]]:
    """Reconcile explicitly bound wallet evidence against closeout items.

    Unbound items remain ``not_inferred``. A bound RTC item becomes
    ``verified_paid`` only when every selected history row is terminal,
    addressed to ``wallet``, and the exact sum equals the advertised amount.
    A lower exact sum is reported as ``partially_verified``; overpayment is
    rejected as ambiguous rather than silently counted as revenue.
    """
    wallet = _wallet(wallet)
    items = _validate_closeout_items(closeout_items)
    binding_map = _validate_bindings(bindings)
    inventory = inventory_history(history, wallet)
    history_by_hash = {row["history_sha256"]: row for row in inventory}
    item_identities = {item["identity"] for item in items}

    unknown_bindings = set(binding_map) - item_identities
    if unknown_bindings:
        repo_cf, pr = sorted(unknown_bindings)[0]
        raise RevenueSettlementInputError(
            f"payment binding references unknown closeout item: {repo_cf}#{pr}"
        )

    results: list[dict[str, Any]] = []
    for item in items:
        identity = item["identity"]
        fingerprints = binding_map.get(identity)
        base = {
            "repo": item["repo"],
            "pr": item["pr"],
            "currency": item["currency"],
            "advertised_amount": _amount_text(item["amount"]),
            "state": item["state"],
            "cash_status": "not_inferred",
            "verified_amount": "0",
            "payment_evidence": [],
        }

        if fingerprints is None:
            base["reason"] = "no_payment_evidence_bound"
            results.append(base)
            continue

        if item["state"] != "MERGED":
            raise RevenueSettlementEvidenceError(
                f"{item['repo']}#{item['pr']} has payment evidence but is not merged"
            )
        if item["currency"] != "RTC":
            raise RevenueSettlementEvidenceError(
                f"{item['repo']}#{item['pr']} is not denominated in RTC"
            )

        verified = Decimal("0")
        evidence = []
        for fingerprint in fingerprints:
            row = history_by_hash.get(fingerprint)
            if row is None:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row is absent"
                )
            if row["status"] != "confirmed":
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row is not confirmed"
                )
            if row["recipient_matches_wallet"] is not True:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row targets another wallet"
                )
            amount = _decimal(row["amount_rtc"], field="verified history amount")
            verified += amount
            evidence.append(
                {
                    "history_sha256": fingerprint,
                    "amount_rtc": row["amount_rtc"],
                    "status": "confirmed",
                }
            )

        if verified > item["amount"]:
            raise RevenueSettlementEvidenceError(
                f"{item['repo']}#{item['pr']} bound payments exceed advertised amount"
            )
        base["verified_amount"] = _amount_text(verified)
        base["payment_evidence"] = evidence
        if verified == item["amount"]:
            base["cash_status"] = "verified_paid"
            base["reason"] = "bound_confirmed_wallet_evidence_matches_advertised_amount"
        else:
            base["cash_status"] = "partially_verified"
            base["reason"] = "bound_confirmed_wallet_evidence_below_advertised_amount"
        results.append(base)
    return results


def summarize_cash(results: Any) -> dict[str, Any]:
    """Aggregate verified RTC cash without mixing non-RTC or inferred amounts."""
    if not isinstance(results, list):
        raise RevenueSettlementInputError("reconciliation results must be a list")
    verified = Decimal("0")
    partial = Decimal("0")
    fully_paid = 0
    partially_paid = 0
    unverified = 0
    for row in results:
        if not isinstance(row, dict):
            raise RevenueSettlementInputError("reconciliation result must be an object")
        status = row.get("cash_status")
        currency = row.get("currency")
        amount = _decimal(
            row.get("verified_amount", "0"),
            field="verified_amount",
            positive=False,
        )
        if amount < 0:
            raise RevenueSettlementInputError("verified_amount must not be negative")
        if currency == "RTC":
            verified += amount
        if status == "verified_paid":
            fully_paid += 1
        elif status == "partially_verified":
            partially_paid += 1
            if currency == "RTC":
                partial += amount
        elif status == "not_inferred":
            unverified += 1
        else:
            raise RevenueSettlementInputError(
                "unknown cash_status in reconciliation result"
            )
    return {
        "currency": "RTC",
        "verified_cash_total": _amount_text(verified),
        "partial_cash_total": _amount_text(partial),
        "fully_paid_items": fully_paid,
        "partially_paid_items": partially_paid,
        "unverified_items": unverified,
        "cash_claim": "wallet_history_evidence_only",
    }


def _load_json(path: str) -> Any:
    if path == "-":
        raise RevenueSettlementInputError(
            "stdin is not accepted here because two independent inputs are required"
        )
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_JSON_BYTES:
            raise RevenueSettlementInputError(f"{path} is too large")
        return json.loads(source.read_text(encoding="utf-8"))
    except OSError:
        raise
    except json.JSONDecodeError as exc:
        raise RevenueSettlementInputError(f"{path} is not valid JSON") from exc


def _schema_items(payload: Any, *, name: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise RevenueSettlementInputError(f"{name} schema_version must be 1")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RevenueSettlementInputError(f"{name} items must be a list")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_settlement",
        description=(
            "Bind exact RustChain wallet-history evidence to merged paid-work "
            "closeout items without inferring payment from merge state."
        ),
    )
    parser.add_argument("closeout", help="JSON output from revenue_closeout --json")
    parser.add_argument("bindings", help="operator-authored payment binding JSON")
    parser.add_argument("--wallet", required=True, help="recipient RustChain wallet")
    parser.add_argument(
        "--history",
        help="captured wallet history JSON; omit to query the configured RustChain node",
    )
    parser.add_argument("--json", action="store_true", help="emit full reconciliation JSON")
    args = parser.parse_args(argv)

    try:
        closeout_payload = _load_json(args.closeout)
        closeout_items = _schema_items(closeout_payload, name="closeout")
        binding_payload = _load_json(args.bindings)
        binding_items = _schema_items(binding_payload, name="bindings")
        if args.history:
            history_payload = _load_json(args.history)
            history_items = _schema_items(history_payload, name="history")
        else:
            history_items = check_history(_wallet(args.wallet))
        results = reconcile_cash(
            closeout_items,
            history_items,
            binding_items,
            wallet=args.wallet,
        )
        summary = summarize_cash(results)
    except (
        OSError,
        PayoutLookupError,
        RevenueSettlementInputError,
        RevenueSettlementEvidenceError,
    ) as exc:
        parser.error(str(exc))

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "wallet": args.wallet,
        "summary": summary,
        "items": results,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "verified RTC cash: "
            f"{summary['verified_cash_total']} "
            f"({summary['fully_paid_items']} paid, "
            f"{summary['partially_paid_items']} partial, "
            f"{summary['unverified_items']} unverified)"
        )
        for row in results:
            print(
                f"{row['repo']}#{row['pr']} {row['currency']} "
                f"{row['advertised_amount']} -> {row['cash_status']} "
                f"verified={row['verified_amount']} ({row['reason']})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
