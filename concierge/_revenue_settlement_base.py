# SPDX-License-Identifier: MIT
"""Complete-snapshot guard for offline settlement history.

The established settlement implementation is preserved byte-for-byte in
``_revenue_settlement_core``.  This import shim narrows only the offline
canonical-history boundary: a saved provider envelope is authoritative only
when it contains the complete bounded snapshot named by ``total``.
"""

from __future__ import annotations

import sys
from typing import Any

from concierge import _revenue_settlement_core as _core


_original_history_capture = _core._history_capture


def _history_capture(
    payload: Any,
    *,
    wallet: str,
) -> tuple[list[dict[str, Any]], str]:
    """Reject incomplete canonical pages before offline cash reconciliation."""
    if isinstance(payload, dict) and "transactions" in payload:
        items = payload.get("transactions")
        total = payload.get("total")
        if (
            not isinstance(items, list)
            or type(total) is not int
            or total < 0
            or total != len(items)
            or total > _core._MAX_ITEMS
        ):
            raise _core.RevenueSettlementInputError(
                "canonical history capture wallet metadata was invalid"
            )
    return _original_history_capture(payload, wallet=wallet)


# Base functions resolve collaborators in their defining module. Patch that
# module, then expose the exact core module under the historical import name so
# later public-module monkeypatches retain their existing behavior.
_core._history_capture = _history_capture
sys.modules[__name__] = _core
