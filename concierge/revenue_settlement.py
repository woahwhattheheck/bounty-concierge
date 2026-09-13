# SPDX-License-Identifier: MIT
"""Evidence-bound cash reconciliation with identity and temporal attribution.

The established settlement implementation lives in ``_revenue_settlement_base``.
This public module preserves that API while adding independent authority fences:

* full wallet-bound row hashes select evidence;
* explicit transaction IDs stop one transfer from being represented by multiple
  non-identical rows and counted more than once; and
* any selected cash-bearing row must be temporally attributable to the merged
  work: ``merged_at <= transfer_time <= verifier-owned current UTC``.

The verifier clock is internal. Callers cannot supply an ``as_of`` value to make
old or future transfers appear eligible.
"""

from __future__ import annotations

from typing import Any

from concierge import _revenue_settlement_base as _base


# Preserve the complete public and test-facing API, including the intentionally
# exercised private helpers. New names below replace only the guarded seams.
for _export_name in dir(_base):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_base, _export_name)

# Keep these implementation imports after the compatibility export above so
# similarly named base-module globals cannot replace the hardened helpers.
from datetime import datetime as _DateTime, timezone as _timezone
import re as _re


_original_bound_history_row = _base._bound_history_row
_original_reconcile_cash = _base.reconcile_cash
_original_query_canonical_history = _base._query_canonical_history

_UTC_TIMESTAMP_RE = _re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_MAX_EPOCH_SECONDS = 253402300799


def _verification_now() -> _DateTime:
    """Return verifier-owned current UTC for settlement temporal authority."""
    return _DateTime.now(_timezone.utc)


def _canonical_utc_timestamp(value: Any, *, field: str) -> _DateTime:
    if type(value) is not str or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise RevenueSettlementEvidenceError(
            f"{field} must be canonical UTC with at most microsecond precision"
        )
    try:
        parsed = _DateTime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RevenueSettlementEvidenceError(
            f"{field} must be a valid canonical UTC timestamp"
        ) from exc
    return parsed.astimezone(_timezone.utc)


def _history_timestamp_value(value: Any) -> _DateTime:
    if isinstance(value, bool):
        raise RevenueSettlementEvidenceError(
            "wallet history timestamp must be canonical UTC text or Unix seconds"
        )
    if isinstance(value, int):
        if value < 0 or value > _MAX_EPOCH_SECONDS:
            raise RevenueSettlementEvidenceError(
                "wallet history timestamp Unix seconds are out of range"
            )
        try:
            return _DateTime.fromtimestamp(value, tz=_timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise RevenueSettlementEvidenceError(
                "wallet history timestamp Unix seconds are out of range"
            ) from exc
    return _canonical_utc_timestamp(value, field="wallet history timestamp")


def _history_timestamp(row: dict[str, Any], *, required: bool) -> _DateTime | None:
    values = [row[key] for key in ("timestamp", "created_at") if key in row]
    if not values:
        if required:
            raise RevenueSettlementEvidenceError(
                "bound incoming transfer omitted transfer timestamp"
            )
        return None
    parsed = [_history_timestamp_value(value) for value in values]
    if len(set(parsed)) != 1:
        raise RevenueSettlementEvidenceError(
            "wallet history row contains conflicting transfer timestamps"
        )
    return parsed[0]


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
    """Fingerprint rows and expose only temporally usable incoming evidence."""
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
            # Inventory has no closeout merge time, but a row that cannot
            # establish its own transfer time can never be safely selected.
            _history_timestamp(row, required=True)
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


def _validate_selected_temporal_attribution(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    *,
    history_wallet: str,
) -> None:
    """Fail closed unless selected evidence follows merge and precedes verification."""
    parsed_items = _closeout_items(closeout_items)
    bound = _binding_map(bindings)
    history_by_hash = _history_index(history, wallet=history_wallet)

    verification_time = _verification_now()
    if (
        not isinstance(verification_time, _DateTime)
        or verification_time.tzinfo is None
        or verification_time.utcoffset() is None
    ):
        raise RevenueSettlementEvidenceError(
            "verifier clock did not provide timezone-aware current UTC"
        )
    verification_time = verification_time.astimezone(_timezone.utc)

    for item, raw_closeout in zip(parsed_items, closeout_items):
        selected = bound.get(item["key"], [])
        if not selected:
            continue

        if not isinstance(raw_closeout, dict):
            # _closeout_items already owns this shape validation.
            raise RevenueSettlementInputError("closeout item must be an object")

        if "merged_at" not in raw_closeout or raw_closeout.get("merged_at") is None:
            raise RevenueSettlementEvidenceError(
                f"{item['repo']}#{item['pr']} bound settlement requires closeout merged_at"
            )
        merged_at = _canonical_utc_timestamp(
            raw_closeout.get("merged_at"),
            field=f"{item['repo']}#{item['pr']} merged_at",
        )
        if merged_at > verification_time:
            raise RevenueSettlementEvidenceError(
                f"{item['repo']}#{item['pr']} merge timestamp is in the future"
            )

        for fingerprint in selected:
            raw = history_by_hash.get(fingerprint)
            if raw is None:
                # The established reconciler already emitted the authoritative
                # absent-evidence error before this post-validation runs.
                continue
            transfer_at = _history_timestamp(raw, required=True)
            if transfer_at is None:
                raise RevenueSettlementEvidenceError(
                    "selected transfer omitted required temporal evidence"
                )
            if transfer_at < merged_at:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound transfer predates the merged work"
                )
            if transfer_at > verification_time:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound transfer timestamp is in the future"
                )


def reconcile_cash(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    *,
    wallet: str,
    history_wallet: str,
    history_source: str,
) -> list[dict[str, Any]]:
    """Reconcile exact rows, then enforce identity and temporal attribution."""
    _base._bound_history_row = globals()["_bound_history_row"]
    results = _original_reconcile_cash(
        closeout_items,
        history,
        bindings,
        wallet=wallet,
        history_wallet=history_wallet,
        history_source=history_source,
    )

    # Preserve the existing transaction-identity fence and its established
    # error precedence before adding temporal authority.
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

    _validate_selected_temporal_attribution(
        closeout_items,
        history,
        bindings,
        history_wallet=history_wallet,
    )
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
