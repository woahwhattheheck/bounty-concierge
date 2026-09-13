# SPDX-License-Identifier: MIT
"""Evidence-bound cash reconciliation with transaction-identity reuse fencing.

The established settlement implementation lives in ``_revenue_settlement_base``.
This public module preserves that API while adding an independent identity fence:
full wallet-bound row hashes select evidence, while explicit transaction IDs stop
one transfer from being represented by multiple non-identical rows and counted
more than once.
"""

from __future__ import annotations

from typing import Any

from concierge import _revenue_settlement_base as _base


# Preserve the complete public and test-facing API, including the intentionally
# exercised private helpers. New names below replace only the guarded seams.
for _export_name in dir(_base):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_base, _export_name)


_original_bound_history_row = _base._bound_history_row
_original_reconcile_cash = _base.reconcile_cash
_original_query_canonical_history = _base._query_canonical_history


def _transaction_identity(
    row: dict[str, Any],
    keys: tuple[str, ...],
    *,
    required: bool,
) -> str | None:
    values: list[str] = []
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or any(char.isspace() or not char.isprintable() for char in value)
        ):
            raise RevenueSettlementEvidenceError(
                "wallet history row contains malformed transaction identity"
            )
        values.append(value)
    if not values:
        if required:
            raise RevenueSettlementEvidenceError(
                "wallet history row omitted transaction identity"
            )
        return None
    if len(set(values)) != 1:
        raise RevenueSettlementEvidenceError(
            "wallet history row contains conflicting transaction identities"
        )
    return values[0]


def _canonical_incoming_transaction_id(row: dict[str, Any]) -> str | None:
    """Return the canonical cash-bearing transaction identity when applicable."""
    if row.get("type") != "transfer_in":
        return None
    return _transaction_identity(row, ("tx_hash",), required=True)


def _bound_history_row(
    row: dict[str, Any],
    *,
    wallet: str,
    history_source: str,
) -> dict[str, Any]:
    parsed = _original_bound_history_row(
        row,
        wallet=wallet,
        history_source=history_source,
    )
    if row.get("type") == "transfer_in":
        transaction_id = _canonical_incoming_transaction_id(row)
    else:
        transaction_id = _transaction_identity(
            row,
            ("tx_hash", "tx_id"),
            required=False,
        )
    return {**parsed, "transaction_id": transaction_id}


def inventory_history(
    history: Any,
    wallet: str,
    *,
    history_source: str,
) -> list[dict[str, Any]]:
    """Fingerprint every row and mark repeated explicit transactions unbindable."""
    wallet = _wallet(wallet)
    history_source = _history_source(history_source)
    result: list[dict[str, Any]] = []
    seen_transaction_ids: set[str] = set()
    for fingerprint, row in _history_index(history, wallet=wallet).items():
        entry: dict[str, Any] = {
            "history_sha256": fingerprint,
            "history_wallet": wallet,
            "history_source": history_source,
            "timestamp": row.get("timestamp", row.get("created_at")),
        }
        try:
            parsed = _bound_history_row(
                row,
                wallet=wallet,
                history_source=history_source,
            )
        except (RevenueSettlementEvidenceError, RevenueSettlementInputError) as exc:
            entry.update(bindable=False, reason=str(exc))
        else:
            transaction_id = parsed["transaction_id"]
            if transaction_id is not None and transaction_id in seen_transaction_ids:
                entry.update(
                    bindable=False,
                    reason="wallet transaction identity is duplicated in history",
                )
            else:
                if transaction_id is not None:
                    seen_transaction_ids.add(transaction_id)
                entry.update(
                    bindable=True,
                    amount_rtc=_amount_text(parsed["amount"]),
                    recipient_matches_wallet=parsed["recipient_matches_wallet"],
                    status=parsed["status"],
                    evidence_kind=parsed["evidence_kind"],
                )
        result.append(entry)
    return result


def _selected_transaction_ids(
    history: Any,
    bindings: Any,
    *,
    history_wallet: str,
    history_source: str,
) -> list[str]:
    history_by_hash = _history_index(history, wallet=history_wallet)
    bound = _binding_map(bindings)
    identities: list[str] = []
    for selected in bound.values():
        for fingerprint in selected:
            raw = history_by_hash.get(fingerprint)
            if raw is None:
                # The canonical reconciler emits the authoritative absent-row error.
                continue
            parsed = _bound_history_row(
                raw,
                wallet=history_wallet,
                history_source=history_source,
            )
            transaction_id = parsed["transaction_id"]
            if transaction_id is not None:
                identities.append(transaction_id)
    return identities


def reconcile_cash(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    *,
    wallet: str,
    history_wallet: str,
    history_source: str,
) -> list[dict[str, Any]]:
    """Reconcile exact rows, then reject reuse of one explicit transaction ID."""
    _base._bound_history_row = globals()["_bound_history_row"]
    results = _original_reconcile_cash(
        closeout_items,
        history,
        bindings,
        wallet=wallet,
        history_wallet=history_wallet,
        history_source=history_source,
    )
    seen: set[str] = set()
    for transaction_id in _selected_transaction_ids(
        history,
        bindings,
        history_wallet=history_wallet,
        history_source=history_source,
    ):
        if transaction_id in seen:
            raise RevenueSettlementEvidenceError(
                "one wallet transaction identity cannot be counted more than once"
            )
        seen.add(transaction_id)
    return results


def _query_canonical_history(wallet: str) -> tuple[list[dict[str, Any]], str]:
    """Read canonical history and reject repeated incoming transaction identity."""
    # Preserve monkeypatch/test seams exposed by the historical public module.
    _base._node_request_settings = globals()["_node_request_settings"]
    _base.requests = globals()["requests"]
    _base._MAX_ITEMS = globals()["_MAX_ITEMS"]
    rows, history_wallet = _original_query_canonical_history(wallet)
    seen: set[str] = set()
    for row in rows:
        try:
            transaction_id = _canonical_incoming_transaction_id(row)
        except RevenueSettlementEvidenceError as exc:
            raise PayoutLookupError(
                "history payout response contained malformed incoming "
                "transaction identity"
            ) from exc
        if transaction_id is None:
            continue
        if transaction_id in seen:
            raise PayoutLookupError(
                "history payout pagination contained duplicate canonical "
                "incoming transaction identity"
            )
        seen.add(transaction_id)
    return rows, history_wallet


def main(argv: list[str] | None = None) -> int:
    """Run the established CLI through the guarded public-module seams."""
    _base._query_canonical_history = globals()["_query_canonical_history"]
    _base._history_capture = globals()["_history_capture"]
    _base.reconcile_cash = globals()["reconcile_cash"]
    _base.summarize_cash = globals()["summarize_cash"]
    _base._node_request_settings = globals()["_node_request_settings"]
    _base.requests = globals()["requests"]
    _base._MAX_ITEMS = globals()["_MAX_ITEMS"]
    return _base.main(argv)


# Base functions resolve collaborators through their defining module. Patch the
# guarded seams once for normal use; main() refreshes monkeypatchable aliases.
_base._bound_history_row = _bound_history_row
_base.inventory_history = inventory_history
_base.reconcile_cash = reconcile_cash
_base._query_canonical_history = _query_canonical_history


if __name__ == "__main__":
    raise SystemExit(main())
