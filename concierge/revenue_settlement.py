# SPDX-License-Identifier: MIT
"""Evidence-bound cash reconciliation with transaction and temporal authority fences.

The established settlement implementation lives in ``_revenue_settlement_base``.
This public module preserves that API while adding two independent authority
fences around cash recognition:

* full wallet-bound row hashes select evidence, while explicit transaction IDs
  stop one transfer from being represented by multiple non-identical rows; and
* every bound cash-bearing transfer must carry canonical provider time and fall
  inside the verifier-owned interval from the GitHub merge through current UTC.

The current-time boundary is deliberately internal. Callers cannot supply an
``as_of`` value to backdate verification and make pre-merge or future evidence
look valid.
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

_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_MAX_EPOCH_SECONDS = 253402300799


def _trusted_utc_now() -> datetime:
    """Return verifier-owned current UTC for temporal cash authority."""
    return datetime.now(timezone.utc)


def _parse_utc_text(value: Any, *, field: str) -> datetime:
    if type(value) is not str or not _UTC_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be canonical UTC with at most microsecond precision"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_history_time(value: Any) -> datetime:
    if isinstance(value, bool):
        raise RevenueSettlementEvidenceError(
            "wallet history transfer timestamp must be canonical UTC text or Unix seconds"
        )
    if isinstance(value, int):
        if value < 0 or value > _MAX_EPOCH_SECONDS:
            raise RevenueSettlementEvidenceError(
                "wallet history transfer timestamp Unix seconds are out of range"
            )
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise RevenueSettlementEvidenceError(
                "wallet history transfer timestamp Unix seconds are out of range"
            ) from exc
    try:
        return _parse_utc_text(value, field="wallet history transfer timestamp")
    except ValueError as exc:
        raise RevenueSettlementEvidenceError(str(exc)) from exc


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="auto").replace(
        "+00:00", "Z"
    )


def _history_transfer_time(row: dict[str, Any]) -> datetime:
    values = [row[key] for key in ("timestamp", "created_at") if key in row]
    if not values:
        raise RevenueSettlementEvidenceError(
            "wallet history row omitted transfer timestamp"
        )
    parsed = [_parse_history_time(value) for value in values]
    if len(set(parsed)) != 1:
        raise RevenueSettlementEvidenceError(
            "wallet history row has conflicting transfer timestamps"
        )
    return parsed[0]


def _closeout_merge_time(raw: dict[str, Any], *, repo: str, pr: int) -> datetime:
    if "merged_at" not in raw:
        raise RevenueSettlementInputError(
            f"{repo}#{pr} payment evidence requires merged_at from revenue_closeout"
        )
    try:
        return _parse_utc_text(raw.get("merged_at"), field=f"{repo}#{pr} merged_at")
    except ValueError as exc:
        raise RevenueSettlementInputError(str(exc)) from exc


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
    # A bindable cash row without provider time can never prove that the cash
    # belongs to the merged work. This includes canonical rows whose provider
    # omitted status (the historical compatibility path treats them confirmed).
    transfer_at = _history_transfer_time(row)
    if row.get("type") == "transfer_in":
        transaction_id = _canonical_incoming_transaction_id(row)
    else:
        transaction_id = _transaction_identity(
            row,
            ("tx_hash", "tx_id"),
            required=False,
        )
    return {
        **parsed,
        "transaction_id": transaction_id,
        "transfer_at": _iso_utc(transfer_at),
    }


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
                    transfer_at=parsed["transfer_at"],
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


def _validate_temporal_authority(
    closeout_items: Any,
    history: Any,
    bindings: Any,
    *,
    history_wallet: str,
    history_source: str,
) -> None:
    """Require merge <= transfer <= verifier-owned current UTC for bound cash."""
    now = _trusted_utc_now()
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise RevenueSettlementInputError(
            "trusted current time must be timezone-aware"
        )
    now = now.astimezone(timezone.utc)

    items = _closeout_items(closeout_items)
    bound = _binding_map(bindings)
    history_by_hash = _history_index(history, wallet=history_wallet)

    raw_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    if not isinstance(closeout_items, list):
        # _closeout_items above owns the canonical shape error.
        raise RevenueSettlementInputError("closeout items must be a list")
    for raw, item in zip(closeout_items, items):
        if isinstance(raw, dict):
            raw_by_key[item["key"]] = raw

    for item in items:
        selected = bound.get(item["key"])
        if selected is None:
            continue
        # Preserve the established authoritative errors for unmerged/non-RTC
        # items. Temporal authority applies only once the row is otherwise
        # eligible to become cash.
        if item["state"] != "MERGED" or item["currency"] != "RTC":
            continue
        raw_item = raw_by_key[item["key"]]
        merged_at = _closeout_merge_time(
            raw_item,
            repo=item["repo"],
            pr=item["pr"],
        )
        if merged_at > now:
            raise RevenueSettlementEvidenceError(
                f"{item['repo']}#{item['pr']} merge timestamp is in the future"
            )
        for fingerprint in selected:
            raw = history_by_hash.get(fingerprint)
            if raw is None:
                # The canonical reconciler owns the absent-row error.
                continue
            parsed = _bound_history_row(
                raw,
                wallet=history_wallet,
                history_source=history_source,
            )
            transfer_at = _parse_history_time(parsed["transfer_at"])
            if transfer_at < merged_at:
                raise RevenueSettlementEvidenceError(
                    f"{item['repo']}#{item['pr']} bound transfer predates merged work"
                )
            if transfer_at > now:
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
    """Reconcile exact rows, then enforce temporal and transaction uniqueness."""
    _base._bound_history_row = globals()["_bound_history_row"]
    results = _original_reconcile_cash(
        closeout_items,
        history,
        bindings,
        wallet=wallet,
        history_wallet=history_wallet,
        history_source=history_source,
    )
    _validate_temporal_authority(
        closeout_items,
        history,
        bindings,
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
