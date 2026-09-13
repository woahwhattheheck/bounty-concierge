# SPDX-License-Identifier: MIT
"""Evidence-bound cash reconciliation for the paid-work closeout queue.

Cash authority is deliberately narrower than "the PR merged" or "the wallet
history contains something with the same amount."  A payment is recognized
only from a canonical RustChain ``/wallet/history`` envelope whose ``miner_id``
matches the expected recipient and from an explicitly bound, confirmed
``transfer_in`` transaction in that envelope.

Captured/offline evidence must preserve the provider envelope.  Bare arrays,
legacy wrapper shapes, rewards, generic ledger rows, outgoing transfers, and
caller-invented recipient aliases are never cash authority.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from concierge import payout_tracker as _payout_tracker

PayoutLookupError = _payout_tracker.PayoutLookupError

_SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 10_000
_MAX_HISTORY_PAGE = 200
_MAX_DECIMAL_CHARS = 64
_MAX_DECIMAL_DIGITS = 30
_MAX_DECIMAL_EXPONENT = 18
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_HISTORY_TYPES = frozenset({"transfer_in", "transfer_out", "reward", "ledger"})
_TRANSFER_STATUSES = frozenset({"pending", "confirming", "confirmed", "failed"})
_COMMON_HISTORY_KEYS = frozenset({"type", "amount", "epoch", "timestamp", "tx_hash"})


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
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise RevenueSettlementInputError("value is not canonical JSON") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise RevenueSettlementInputError("canonical JSON value is too large")
    return payload


def _wallet(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(char.isspace() or not char.isprintable() for char in value)
    ):
        raise RevenueSettlementInputError("wallet must be one printable identifier")
    return value


def history_row_sha256(row: dict[str, Any], wallet: str | None = None) -> str:
    """Return a wallet-bound identity for one exact provider transaction.

    A row alone is insufficient authority because canonical ``transfer_in``
    entries identify the recipient through the enclosing ``miner_id``.
    """
    if wallet is None:
        raise RevenueSettlementInputError(
            "wallet provenance is required when fingerprinting history"
        )
    if not isinstance(row, dict):
        raise RevenueSettlementInputError("wallet history row must be an object")
    wallet = _wallet(wallet)
    bound = {"miner_id": wallet, "transaction": row}
    return hashlib.sha256(_canonical_json(bound)).hexdigest()


def history_envelope_sha256(history: Any) -> str:
    """Hash the exact canonical provider envelope used for reconciliation."""
    return hashlib.sha256(_canonical_json(history)).hexdigest()


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
        qualifier = "bounded positive" if positive else "bounded finite"
        raise RevenueSettlementInputError(f"{field} must be a {qualifier} decimal")
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


def _printable_text(value: Any, *, field: str, maximum: int = 256) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(char.isspace() or not char.isprintable() for char in value)
    ):
        raise RevenueSettlementEvidenceError(
            f"wallet history row contains malformed {field}"
        )
    return value


def _validate_history_envelope(history: Any, wallet: str) -> tuple[list[dict[str, Any]], str]:
    wallet = _wallet(wallet)
    if not isinstance(history, dict):
        raise RevenueSettlementEvidenceError(
            "cash evidence requires the canonical RustChain history envelope"
        )
    expected = {"ok", "miner_id", "transactions", "total"}
    if set(history) != expected:
        raise RevenueSettlementEvidenceError(
            "RustChain history envelope fields are not canonical"
        )
    if history.get("ok") is not True:
        raise RevenueSettlementEvidenceError("RustChain history envelope was not successful")
    response_wallet = history.get("miner_id")
    if type(response_wallet) is not str:
        raise RevenueSettlementEvidenceError(
            "RustChain history envelope omitted wallet identity"
        )
    try:
        response_wallet = _wallet(response_wallet)
    except RevenueSettlementInputError as exc:
        raise RevenueSettlementEvidenceError(
            "RustChain history envelope contains malformed wallet identity"
        ) from exc
    if response_wallet != wallet:
        raise RevenueSettlementEvidenceError(
            "RustChain history envelope targets another wallet"
        )
    rows = history.get("transactions")
    total = history.get("total")
    if (
        not isinstance(rows, list)
        or len(rows) > _MAX_HISTORY_PAGE
        or any(not isinstance(row, dict) for row in rows)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < len(rows)
        or total > _MAX_ITEMS
    ):
        raise RevenueSettlementEvidenceError(
            "RustChain history envelope pagination is malformed"
        )
    if total != len(rows):
        raise RevenueSettlementEvidenceError(
            "RustChain history envelope is incomplete; cash evidence requires one complete snapshot"
        )
    return rows, history_envelope_sha256(history)


def _validate_common_history_row(row: dict[str, Any]) -> tuple[str, Decimal]:
    row_type = row.get("type")
    if type(row_type) is not str or row_type not in _ALLOWED_HISTORY_TYPES:
        raise RevenueSettlementEvidenceError(
            "wallet history row has unsupported transaction type"
        )
    amount = _decimal(row.get("amount"), field="history amount", positive=False)
    if amount < 0:
        raise RevenueSettlementEvidenceError("wallet history amount must not be negative")
    epoch = row.get("epoch")
    if epoch is not None and (
        isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0
    ):
        raise RevenueSettlementEvidenceError("wallet history row contains malformed epoch")
    timestamp = row.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise RevenueSettlementEvidenceError(
            "wallet history row contains malformed timestamp"
        )
    tx_hash = row.get("tx_hash")
    if tx_hash is not None:
        _printable_text(tx_hash, field="tx_hash")
    return row_type, amount


def _inventory_row(
    row: dict[str, Any],
    *,
    wallet: str,
    envelope_sha256: str,
) -> dict[str, Any]:
    row_type, amount = _validate_common_history_row(row)
    keys = set(row)

    if row_type == "transfer_in":
        if keys != _COMMON_HISTORY_KEYS | {"from"}:
            raise RevenueSettlementEvidenceError(
                "incoming transfer row fields are not canonical"
            )
        if row["epoch"] is None:
            raise RevenueSettlementEvidenceError(
                "confirmed incoming transfer omitted epoch"
            )
        if row["tx_hash"] is None:
            raise RevenueSettlementEvidenceError(
                "confirmed incoming transfer omitted transaction identity"
            )
        if amount <= 0:
            raise RevenueSettlementEvidenceError(
                "incoming transfer amount must be positive"
            )
        _printable_text(row["from"], field="sender")
        cash_eligible = True
        status = "confirmed"
    elif row_type == "transfer_out":
        allowed = _COMMON_HISTORY_KEYS | {"to"}
        if "status" in row:
            allowed = allowed | {"status"}
        if keys != allowed:
            raise RevenueSettlementEvidenceError(
                "outgoing transfer row fields are not canonical"
            )
        _printable_text(row["to"], field="recipient")
        if row.get("tx_hash") is None:
            raise RevenueSettlementEvidenceError(
                "outgoing transfer omitted transaction identity"
            )
        status_value = row.get("status")
        if status_value is not None and (
            type(status_value) is not str or status_value not in _TRANSFER_STATUSES
        ):
            raise RevenueSettlementEvidenceError(
                "outgoing transfer contains malformed status"
            )
        cash_eligible = False
        status = status_value or "confirmed"
    elif row_type == "reward":
        if keys != _COMMON_HISTORY_KEYS or row["tx_hash"] is not None:
            raise RevenueSettlementEvidenceError("reward row fields are not canonical")
        cash_eligible = False
        status = "non_cash"
    else:
        if keys != _COMMON_HISTORY_KEYS | {"reason"} or row["tx_hash"] is not None:
            raise RevenueSettlementEvidenceError("ledger row fields are not canonical")
        reason = row.get("reason")
        if reason is not None:
            _printable_text(reason, field="ledger reason", maximum=512)
        cash_eligible = False
        status = "non_cash"

    return {
        "history_sha256": history_row_sha256(row, wallet),
        "history_envelope_sha256": envelope_sha256,
        "amount_rtc": _amount_text(amount),
        "recipient_matches_wallet": cash_eligible,
        "cash_eligible": cash_eligible,
        "type": row_type,
        "tx_hash": row.get("tx_hash"),
        "status": status,
        "timestamp": row.get("timestamp"),
    }


def inventory_history(history: Any, wallet: str) -> list[dict[str, Any]]:
    """Validate a canonical RustChain snapshot and inventory exact row evidence."""
    wallet = _wallet(wallet)
    rows, envelope_sha256 = _validate_history_envelope(history, wallet)
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = _inventory_row(
            row,
            wallet=wallet,
            envelope_sha256=envelope_sha256,
        )
        fingerprint = item["history_sha256"]
        if fingerprint in seen:
            raise RevenueSettlementEvidenceError(
                "wallet history contains duplicate indistinguishable rows"
            )
        seen.add(fingerprint)
        result.append(item)
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
            raise RevenueSettlementInputErropˆf"duplicate closeout item: {repo}#{pr}")
        seen.add(identity)
        state = raw.get("state")
        if state not in {"OPEN", "CLOSED_UNMERGED", "MERGED", "HEAD_MOVED"}:
            raise RevenueSettlementInputError(f"{repo}#{pr} has unsupported closeout state")
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
            raise RevenueSettlementInputErropˆf"duplicate payment binding: {repo}#{pr}")
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
    """Reconcile wallet-bound incoming-transfer evidence against closeout items."""
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
        evidence: list[dict[str, Any]] = []
        for fingerprint in fingerprints:
            row = history_by_hash.get(fingerprint)
            if row is None:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row is absent"
                )
            if row["cash_eligible"] is not True or row["type"] != "transfer_in":
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound history row is not canonical incoming transfer evidence"
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
                    "history_envelope_sha256": row["history_envelope_sha256"],
                    "amount_rtc": row["amount_rtc"],
                    "type": "transfer_in",
                    "tx_hash": row["tx_hash"],
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
            base["reason"] = (
                "bound_confirmed_incoming_wallet_evidence_matches_advertised_amount"
            )
        else:
            base["cash_status"] = "partially_verified"
            base["reason"] = (
                "bound_confirmed_incoming_wallet_evidence_below_advertised_amount"
            )
        results.append(base)
    return results


def summarize_cash(results: Any) -> dict[str, Any]:
    """Aggregate evidence-backed RTC cash without inferring unbound revenue."""
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
            raise RevenueSettlementInputError("unknown cash_status in reconciliation result")
    return {
        "currency": "RTC",
        "verified_cash_total": _amount_text(verified),
        "partial_cash_total": _amount_text(partial),
        "fully_paid_items": fully_paid,
        "partially_paid_items": partially_paid,
        "unverified_items": unverified,
        "cash_claim": "wallet_history_evidence_only",
    }


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RevenueSettlementInputError("JSON contains duplicate object key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise RevenueSettlementInputError("JSON contains non-finite number")


def _load_json(path: str) -> Any:
    if path == "-":
        raise RevenueSettlementInputError(
            "stdin is not accepted here because two independent inputs are required"
        )
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(_MAX_JSON_BYTES + 1)
    except OSError:
        raise
    if len(raw) > _MAX_JSON_BYTES:
        raise RevenueSettlementInputErropˆf"{path} is too large")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise RevenueSettlementInputError(f"{path} is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except RevenueSettlementInputError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise RevenueSettlementInputError(f"{path} is not valid JSON") from exc


def _schema_items(payload: Any, *, name: str) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "items"}
        or payload.get("schema_version") != _SCHEMA_VERSION
    ):
        raise RevenueSettlementInputError(
            f"{name} must be the canonical schema_version 1 wrapper"
        )
    items = payload.get("items")
    if not isinstance(items, list):
        raise RevenueSettlementInputError(f"{name} items must be a list")
    return items


def _fetch_canonical_history(wallet: str, node_url: str | None = None) -> dict[str, Any]:
    """Fetch one current RustChain history envelope without legacy coercion."""
    try:
        wallet = _payout_tracker._validate_wallet_id(wallet)
        base, verify = _payout_tracker._node_request_settings(node_url)
        response = _payout_tracker.requests.get(
            f"{base}/wallet/history",
            params={"miner_id": wallet, "limit": _MAX_HISTORY_PAGE},
            timeout=15,
            verify=verify,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise PayoutLookupError(
                "history payout response was not valid JSON"
            ) from exc
    except PayoutLookupError:
        raise
    except (_payout_tracker.requests.RequestException, OSError) as exc:
        raise PayoutLookupError("history payout request failed") from exc

    # Validation here binds the transport/query result before it reaches the
    # generic reconciliation API.  inventory_history validates it again.
    try:
        _validate_history_envelope(payload, wallet)
    except (RevenueSettlementInputError, RevenueSettlementEvidenceError) as exc:
        raise PayoutLookupError("canonical history payout response was malformed") from exc
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_settlement",
        description=(
            "Bind canonical RustChain incoming-transfer evidence to merged "
            "paid-work closeout items without inferring payment from merge state."
        ),
    )
    parser.add_argument("closeout", help="JSON output from revenue_closeout --json")
    parser.add_argument("bindings", help="operator-authored payment binding JSON")
    parser.add_argument("--wallet", required=True, help="recipient RustChain wallet")
    parser.add_argument(
        "--history",
        help=(
            "raw captured canonical RustChain /wallet/history envelope; "
            "omit to query the configured node"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit full reconciliation JSON")
    args = parser.parse_args(argv)

    try:
        closeout_payload = _load_json(args.closeout)
        closeout_items = _schema_items(closeout_payload, name="closeout")
        binding_payload = _load_json(args.bindings)
        binding_items = _schema_items(binding_payload, name="bindings")
        wallet = _wallet(args.wallet)
        history_payload = (
            _load_json(args.history)
            if args.history
            else _fetch_canonical_history(wallet)
        )
        results = reconcile_cash(
            closeout_items,
            history_payload,
            binding_items,
            wallet=wallet,
        )
        summary = summarize_cash(results)
        snapshot_sha256 = history_envelope_sha256(history_payload)
    except (
        OSError,
        PayoutLookupError,
        RevenueSettlementInputError,
        RevenueSettlementEvidenceError
    ) as exc:
        parser.error(str(exc))

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "wallet": wallet,
        "history_envelope_sha256": snapshot_sha256,
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
