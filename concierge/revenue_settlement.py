# SPDX-License-Identifier: MIT
"""Evidence-bound cash reconciliation for the paid-work closeout queue.

Merge state never proves payment. Cash is verified only when an operator binds
exact wallet-history rows (by SHA-256 of canonical JSON) to a merged closeout
item and those rows validate as incoming, confirmed RTC for the expected wallet.
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
_MAX_BOUND_ROWS = 100
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_STATES = frozenset({"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"})
_IN_FLIGHT = frozenset({"pending", "confirming"})
_TERMINAL = frozenset({"confirmed"})
_FAILED = frozenset({"failed"})


class RevenueSettlementInputError(ValueError):
    """Malformed or ambiguous operator/provider input."""


class RevenueSettlementEvidenceError(RuntimeError):
    """Bound evidence does not prove the claimed cash state."""


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
    if not isinstance(row, dict):
        raise RevenueSettlementInputError("wallet history row must be an object")
    return hashlib.sha256(_canonical_json(row)).hexdigest()


def _decimal(value: Any, *, field: str, positive: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise RevenueSettlementInputError(f"{field} must be a decimal")
    source = str(value)
    if len(source) > 64:
        raise RevenueSettlementInputError(f"{field} representation is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise RevenueSettlementInputError(f"{field} must be a decimal") from exc
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or len(amount.as_tuple().digits) > 30
        or not isinstance(exponent, int)
        or abs(exponent) > 18
        or (positive and amount <= 0)
    ):
        raise RevenueSettlementInputError(f"{field} must be a bounded positive decimal")
    return amount


def _amount_text(amount: Decimal) -> str:
    text = format(amount, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _wallet(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(char.isspace() or not char.isprintable() for char in value)
    ):
        raise RevenueSettlementInputError("wallet must be one printable identifier")
    return value


def _identity(raw: Any) -> tuple[str, int]:
    if not isinstance(raw, dict):
        raise RevenueSettlementInputError("item must be an object")
    repo = raw.get("repo")
    pr = raw.get("pr")
    if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
        raise RevenueSettlementInputError("repo must be in owner/name form")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise RevenueSettlementInputError("repo must not contain dot path segments")
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise RevenueSettlementInputError("pr must be a positive integer")
    return repo, pr


def _history_index(history: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(history, list) or len(history) > _MAX_ITEMS:
        raise RevenueSettlementInputError("wallet history must be a bounded list")
    indexed: dict[str, dict[str, Any]] = {}
    for row in history:
        if not isinstance(row, dict):
            raise RevenueSettlementInputError("wallet history row must be an object")
        fingerprint = history_row_sha256(row)
        if fingerprint in indexed:
            raise RevenueSettlementEvidenceError(
                "wallet history contains duplicate indistinguishable rows"
            )
        indexed[fingerprint] = row
    return indexed


def _history_amount(row: dict[str, Any]) -> Decimal:
    if "amount" in row and "amount_rtc" in row:
        left = _decimal(row["amount"], field="history amount")
        right = _decimal(row["amount_rtc"], field="history amount_rtc")
        if left != right:
            raise RevenueSettlementEvidenceError(
                "wallet history row contains conflicting amount fields"
            )
        return left
    if "amount" in row:
        return _decimal(row["amount"], field="history amount")
    if "amount_rtc" in row:
        return _decimal(row["amount_rtc"], field="history amount_rtc")
    raise RevenueSettlementEvidenceError("wallet history row omitted amount")


def _one_identity(
    row: dict[str, Any],
    keys: tuple[str, ...],
    *,
    kind: str,
    required: bool,
) -> str | None:
    values: list[str] = []
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if type(value) is not str or not value:
            raise RevenueSettlementEvidenceError(
                f"wallet history row contains malformed {kind}"
            )
        values.append(value)
    if not values:
        if required:
            raise RevenueSettlementEvidenceError(
                f"wallet history row omitted {kind} identity"
            )
        return None
    if len(set(values)) != 1:
        raise RevenueSettlementEvidenceError(
            f"wallet history row contains conflicting {kind} identities"
        )
    return values[0]


def _history_status(row: dict[str, Any]) -> str:
    if "status" not in row:
        # Existing payout_tracker contract: confirmed current rows omit status;
        # in-flight current rows carry pending/confirming.
        return "confirmed"
    status = row["status"]
    if type(status) is not str:
        raise RevenueSettlementEvidenceError(
            "wallet history row contains malformed status"
        )
    status = status.strip().lower()
    if status in _TERMINAL:
        return "confirmed"
    if status in _IN_FLIGHT:
        return status
    if status in _FAILED:
        return "failed"
    raise RevenueSettlementEvidenceError(
        "wallet history row contains unknown settlement status"
    )


def _bound_history_row(row: dict[str, Any], *, wallet: str) -> dict[str, Any]:
    row_type = row.get("type")
    if row_type is not None and type(row_type) is not str:
        raise RevenueSettlementEvidenceError(
            "wallet history row contains malformed type"
        )
    if isinstance(row_type, str) and row_type.strip().lower() == "transfer_out":
        raise RevenueSettlementEvidenceError(
            "wallet history row is an outgoing transfer"
        )
    recipient = _one_identity(
        row,
        ("to", "to_addr", "recipient", "wallet"),
        kind="recipient",
        required=True,
    )
    sender = _one_identity(
        row,
        ("from", "from_addr", "sender"),
        kind="sender",
        required=False,
    )
    if sender == wallet:
        raise RevenueSettlementEvidenceError(
            "wallet history row is self-funded or outgoing"
        )
    return {
        "amount": _history_amount(row),
        "recipient_matches_wallet": recipient == wallet,
        "status": _history_status(row),
    }


def inventory_history(history: Any, wallet: str) -> list[dict[str, Any]]:
    """Fingerprint every row; classify non-payment rows as unbindable."""
    wallet = _wallet(wallet)
    result: list[dict[str, Any]] = []
    for fingerprint, row in _history_index(history).items():
        entry: dict[str, Any] = {
            "history_sha256": fingerprint,
            "timestamp": row.get("timestamp", row.get("created_at")),
        }
        try:
            parsed = _bound_history_row(row, wallet=wallet)
        except RevenueSettlementEvidenceError as exc:
            entry.update(bindable=False, reason=str(exc))
        else:
            entry.update(
                bindable=True,
                amount_rtc=_amount_text(parsed["amount"]),
                recipient_matches_wallet=parsed["recipient_matches_wallet"],
                status=parsed["status"],
            )
        result.append(entry)
    return result


def _closeout_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        raise RevenueSettlementInputError("closeout items must be a bounded list")
    seen: set[tuple[str, int]] = set()
    result = []
    for raw in items:
        repo, pr = _identity(raw)
        key = (repo.casefold(), pr)
        if key in seen:
            raise RevenueSettlementInputError(f"duplicate closeout item: {repo}#{pr}")
        seen.add(key)
        state = raw.get("state")
        if state not in _ALLOWED_STATES:
            raise RevenueSettlementInputError(f"{repo}#{pr} has unsupported closeout state")
        currency = raw.get("currency")
        if type(currency) is not str or not currency:
            raise RevenueSettlementInputError(f"{repo}#{pr} omitted currency")
        if raw.get("cash_status") != "not_inferred":
            raise RevenueSettlementInputError(
                f"{repo}#{pr} cash_status must be not_inferred before reconciliation"
            )
        result.append(
            {
                "repo": repo,
                "pr": pr,
                "key": key,
                "state": state,
                "currency": currency,
                "amount": _decimal(raw.get("advertised_amount"), field="advertised_amount"),
            }
        )
    return result


def _binding_map(bindings: Any) -> dict[tuple[str, int], list[str]]:
    if not isinstance(bindings, list) or len(bindings) > _MAX_ITEMS:
        raise RevenueSettlementInputError("bindings must be a bounded list")
    result: dict[tuple[str, int], list[str]] = {}
    used_rows: set[str] = set()
    for raw in bindings:
        repo, pr = _identity(raw)
        key = (repo.casefold(), pr)
        if key in result:
            raise RevenueSettlementInputError(f"duplicate payment binding: {repo}#{pr}")
        hashes = raw.get("history_sha256s")
        if not isinstance(hashes, list) or not hashes or len(hashes) > _MAX_BOUND_ROWS:
            raise RevenueSettlementInputError(
                "history_sha256s must be a non-empty bounded list"
            )
        selected: list[str] = []
        for value in hashes:
            if type(value) is not str or not _SHA256_RE.fullmatch(value):
                raise RevenueSettlementInputError(
                    "history_sha256s entries must be lowercase SHA-256 values"
                )
            if value in selected:
                raise RevenueSettlementInputError(
                    "payment binding repeats one history row"
                )
            if value in used_rows:
                raise RevenueSettlementInputError(
                    "one wallet history row cannot settle multiple closeout items"
                )
            selected.append(value)
            used_rows.add(value)
        result[key] = selected
    return result


def reconcile_cash(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    *,
    wallet: str,
) -> list[dict[str, Any]]:
    """Return evidence-bound cash state; never guess a payment-to-PR match."""
    wallet = _wallet(wallet)
    items = _closeout_items(closeout_items)
    bound = _binding_map(bindings)
    history_by_hash = _history_index(history)
    known = {item["key"] for item in items}
    unknown = set(bound) - known
    if unknown:
        repo_cf, pr = sorted(unknown)[0]
        raise RevenueSettlementInputError(
            f"payment binding references unknown closeout item: {repo_cf}#{pr}"
        )

    results = []
    for item in items:
        selected = bound.get(item["key"])
        row = {
            "repo": item["repo"],
            "pr": item["pr"],
            "currency": item["currency"],
            "advertised_amount": _amount_text(item["amount"]),
            "state": item["state"],
            "cash_status": "not_inferred",
            "verified_amount": "0",
            "payment_evidence": [],
        }
        if selected is None:
            row["reason"] = "no_payment_evidence_bound"
            results.append(row)
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
        for fingerprint in selected:
            raw = history_by_hash.get(fingerprint)
            if raw is None:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row is absent"
                )
            parsed = _bound_history_row(raw, wallet=wallet)
            if parsed["status"] != "confirmed":
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row is not confirmed"
                )
            if parsed["recipient_matches_wallet"] is not True:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row targets another wallet"
                )
            verified += parsed["amount"]
            evidence.append(
                {
                    "history_sha256": fingerprint,
                    "amount_rtc": _amount_text(parsed["amount"]),
                    "status": "confirmed",
                }
            )

        if verified > item["amount"]:
            raise RevenueSettlementEvidenceError(
                f"{item['repo']}#{item['pr']} bound payments exceed advertised amount"
            )
        row["verified_amount"] = _amount_text(verified)
        row["payment_evidence"] = evidence
        if verified == item["amount"]:
            row["cash_status"] = "verified_paid"
            row["reason"] = (
                "bound_confirmed_wallet_evidence_matches_advertised_amount"
            )
        else:
            row["cash_status"] = "partially_verified"
            row["reason"] = (
                "bound_confirmed_wallet_evidence_below_advertised_amount"
            )
        results.append(row)
    return results


def summarize_cash(results: Any) -> dict[str, Any]:
    if not isinstance(results, list):
        raise RevenueSettlementInputError("reconciliation results must be a list")
    total = Decimal("0")
    partial = Decimal("0")
    paid_count = partial_count = unverified_count = 0
    for row in results:
        if not isinstance(row, dict):
            raise RevenueSettlementInputError("reconciliation result must be an object")
        amount = _decimal(
            row.get("verified_amount", "0"),
            field="verified_amount",
            positive=False,
        )
        if amount < 0:
            raise RevenueSettlementInputError("verified_amount must not be negative")
        if row.get("currency") == "RTC":
            total += amount
        status = row.get("cash_status")
        if status == "verified_paid":
            paid_count += 1
        elif status == "partially_verified":
            partial_count += 1
            if row.get("currency") == "RTC":
                partial += amount
        elif status == "not_inferred":
            unverified_count += 1
        else:
            raise RevenueSettlementInputError(
                "unknown cash_status in reconciliation result"
            )
    return {
        "currency": "RTC",
        "verified_cash_total": _amount_text(total),
        "partial_cash_total": _amount_text(partial),
        "fully_paid_items": paid_count,
        "partially_paid_items": partial_count,
        "unverified_items": unverified_count,
        "cash_claim": "wallet_history_evidence_only",
    }


def _load_json(path: str) -> Any:
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_JSON_BYTES:
            raise RevenueSettlementInputError(f"{path} is too large")
        return json.loads(source.read_text(encoding="utf-8"))
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
        closeout_items = _schema_items(_load_json(args.closeout), name="closeout")
        binding_items = _schema_items(_load_json(args.bindings), name="bindings")
        history_items = (
            _schema_items(_load_json(args.history), name="history")
            if args.history
            else check_history(_wallet(args.wallet))
        )
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
