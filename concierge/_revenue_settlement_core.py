# SPDX-License-Identifier: MIT
"""Authoritative offline-history completeness gate for settlement.

The established settlement implementation is preserved byte-for-byte in the
adjacent non-importable ``_revenue_settlement_core_impl.source`` file. This
canonical module executes those bytes in its own namespace, then narrows the
offline-history boundary: each caller payload is frozen into one bounded JSON
generation and a directly saved provider response is accepted only when it is
complete.

Public, historical-base, direct-core import, and direct-core CLI paths therefore
share the same authority boundary without exposing a second weaker Python
module import path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Keep the mature implementation bytes exact while making this canonical module
# their only Python import/CLI entry point. During ``python -m`` execution the
# outer module is named ``__main__``; force the embedded source to see its
# canonical name so its own terminal CLI guard cannot run before this gate is
# installed.
_execution_name = __name__
_source_path = Path(__file__).with_name("_revenue_settlement_core_impl.source")
try:
    _source_text = _source_path.read_text(encoding="utf-8")
except OSError as exc:
    raise ImportError("authoritative settlement core source is unavailable") from exc
_source_code = compile(_source_text, str(_source_path), "exec")
try:
    globals()["__name__"] = "concierge._revenue_settlement_core"
    exec(_source_code, globals(), globals())
finally:
    globals()["__name__"] = _execution_name


_original_history_capture = _history_capture


def _inert_json_snapshot(payload: Any) -> Any:
    """Freeze exactly one bounded JSON generation into built-in containers."""
    try:
        return json.loads(_canonical_json(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevenueSettlementInputError(
            "history capture was not canonical JSON"
        ) from exc


def _history_capture(
    payload: Any,
    *,
    wallet: str,
) -> tuple[list[dict[str, Any]], str]:
    """Load one inert snapshot and reject incomplete canonical captures."""
    wallet = _wallet(wallet)
    frozen = _inert_json_snapshot(payload)
    if not isinstance(frozen, dict):
        raise RevenueSettlementInputError("history capture must be an object")

    # A saved canonical provider envelope is offline authority only when this
    # single frozen generation contains the complete bounded snapshot it names.
    if "transactions" in frozen:
        items = frozen.get("transactions")
        capture_wallet = frozen.get("miner_id")
        total = frozen.get("total")
        if (
            frozen.get("ok") is not True
            or type(capture_wallet) is not str
            or capture_wallet != wallet
            or not isinstance(items, list)
            or any(type(item) is not dict for item in items)
            or type(total) is not int
            or total < 0
            or total > _MAX_ITEMS
            or total != len(items)
        ):
            raise RevenueSettlementInputError(
                "canonical history capture wallet metadata was invalid"
            )
        return items, capture_wallet

    # Normalized operator captures retain their established contract, but are
    # interpreted from the same inert generation rather than from mutable caller
    # containers that could change between validation and settlement.
    return _original_history_capture(frozen, wallet=wallet)


_run_as_main = _execution_name == "__main__"
del _source_text, _source_code, _source_path, _execution_name

if _run_as_main:
    raise SystemExit(main())
del _run_as_main
