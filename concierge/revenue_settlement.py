# SPDX-License-Identifier: MIT
"""Evidence-bound cash reconciliation with identity and temporal authority fences.

The established settlement implementation lives in ``_revenue_settlement_base``.
This public module preserves that API while adding two independent authority
fences around production cash recognition:

* full wallet-bound row hashes select evidence, while explicit transaction IDs
  stop one transfer from being represented by multiple non-identical rows; and
* producer-shaped closeout evidence must prove that selected cash happened no
  earlier than the merge and no later than verifier-owned current UTC.

The production time boundary is deliberately internal. Callers cannot supply an
``as_of`` value to backdate verification. Historical low-level fixture/tool rows
that contain none of the ``revenue_closeout`` producer fields keep their legacy
shape; any producer marker (including ``merged_at`` itself) activates the strict
chronology contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
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

_TEMPORAL_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_MAX_EPOCH_SECONDS = 253402300799
# ``revenue_closeout.scan_paid_pr`` emits this richer shape. The low-level
# reconciliation API historically also accepted deliberately minimal rows in
# tests and tooling. Requiring chronology for any producer marker preserves
# that API while making the production closeout/economics path fail closed.
_PRODUCER_CLOSEOUT_FIELDS = frozenset(
    {
        "canonical_url",
        "head_sha",
        "merged_at",
        "next_action",
        "reason",
        "settlement_followup_url",
    }
)


def _trusted_utc_now() -> datetime:
    """Return verifier-owned current UTC; never supplied by request input."""
    return datetime.now(timezone.utc)


def _verified_now() -> datetime:
    current = _trusted_utc_now()
    if (
        not isinstance(current, datetime)
        or current.tzinfo is None
        or current.utcoffset() is None
    ):
        raise RevenueSettlementEvidenceError(
            "trusted verifier clock did not return timezone-aware UTC time"
        )
    return current.astimezone(timezone.utc)


def _parse_canonical_utc_text(value: Any, *, field: str) -> datetime:
    if type(value) is not str or not _TEMPORAL_UTC_RE.fullmatch(value):
        raise RevenueSettlementEvidenceError(
            f"{field} must be canonical UTC with at most microsecond precision"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RevenueSettlementEvidenceError(
            f"{field} must be a valid canonical UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RevenueSettlementEvidenceError(
            f"{field} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _parse_history_timestamp(value: Any, *, field: str) -> datetime:
    if isinstance(value, bool):
        raise RevenueSettlementEvidenceError(
            f"{field} must be canonical UTC text or exact Unix seconds"
        )
    if isinstance(value, int):
        if value < 0 or value > _MAX_EPOCH_SECONDS:
            raise RevenueSettlementEvidenceError(
                f"{field} Unix seconds are out of range"
            )
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise RevenueSettlementEvidenceError(
                f"{field} Unix seconds are out of range"
            ) from exc
    return _parse_canonical_utc_text(value, field=field)


def _history_transfer_time(row: dict[str, Any]) -> datetime:
    parsed: list[tuple[str, datetime]] = []
    for key in ("timestamp", "created_at"):
        if key in row:
            parsed.append(
                (
                    key,
                    _parse_history_timestamp(
                        row[key],
                        field=f"wallet history {key}",
                    ),
                )
            )
    if not parsed:
        raise RevenueSettlementEvidenceError(
            "bound wallet history row omitted transfer timestamp"
        )
    transfer_time = parsed[0][1]
    if any(value != transfer_time for _, value in parsed[1:]):
        raise RevenueSettlementEvidenceError(
            "wallet history row contains conflicting transfer timestamps"
        )
    return transfer_time


def _requires_temporal_attribution(raw: dict[str, Any]) -> bool:
    return any(field in raw for field in _PRODUCER_CLOSEOUT_FIELDS)


def _raw_closeout_index(closeout_items: Any) -> dict[tuple[str, int], dict[str, Any]]:
    # The canonical reconciler has already validated the container and identity
    # before this helper runs; repeat only the projection needed to retain
    # producer metadata that the historical base normalizer omits.
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in closeout_items:
        repo, pr = _identity(raw)
        indexed[(repo.casefold(), pr)] = raw
    return indexed


def _validate_temporal_attribution(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    *,
    history_wallet: str,
) -> None:
    """Fence selected production cash to merge <= transfer <= trusted now."""
    raw_closeout = _raw_closeout_index(closeout_items)
    bound = _binding_map(bindings)
    history_by_hash = _history_index(history, wallet=history_wallet)

    targets: list[tuple[list[str], dict[str, Any]]] = []
    for key, selected in bound.items():
        raw = raw_closeout.get(key)
        if raw is not None and _requires_temporal_attribution(raw):
            targets.append((selected, raw))
    if not targets:
        return

    # One verifier-owned sample per reconciliation prevents a moving time
    # boundary across rows/items and exposes no caller-controlled ``as_of``.
    trusted_now = _verified_now()
    for selected, raw in targets:
        repo = raw["repo"]
        pr = raw["pr"]
        if "merged_at" not in raw:
            raise RevenueSettlementEvidenceError(
                f"{repo}#{pr} bound production closeout omitted merged_at"
            )
        merged_at = _parse_canonical_utc_text(
            raw["merged_at"],
            field=f"{repo}#{pr} merged_at",
        )
        if merged_at > trusted_now:
            raise RevenueSettlementEvidenceError(
                f"{repo}#{pr} merge timestamp is in the future"
            )
        for fingerprint in selected:
            history_row = history_by_hash.get(fingerprint)
            if history_row is None:
                # The canonical reconciler already emits the authoritative
                # absent-row error before this temporal pass.
                continue
            transfer_at = _history_transfer_time(history_row)
            if transfer_at < merged_at:
                raise RevenueSettlementEvidenceError(
                    f"{repo}#{pr} bound transfer predates merge"
                )
            if transfer_at > trusted_now:
                raise RevenueSettlementEvidenceError(
                    f"{repo}#{pr} bound transfer timestamp is in the future"
                )


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
    """Reconcile exact rows, then reject identity reuse and temporal mismatch."""
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
    _validate_temporal_attribution(
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
