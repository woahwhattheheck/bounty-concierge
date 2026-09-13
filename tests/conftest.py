# SPDX-License-Identifier: MIT
"""Repository-wide pytest compatibility fixtures.

Settlement chronology became part of the cash-evidence contract after the
original settlement and transaction-identity suites were written. Their local
``closeout()`` helpers intentionally model merged work but predate ``merged_at``.
This fixture upgrades only those historical test helpers; production code still
fails closed when a cash-bound merged item omits provider merge time.
"""
from __future__ import annotations

import pytest


_LEGACY_SETTLEMENT_MODULES = {
    "test_revenue_settlement",
    "test_transaction_identity",
}
_TEST_MERGED_AT = "1970-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _upgrade_legacy_settlement_closeout_fixture(request, monkeypatch):
    module = request.module
    if module.__name__.rsplit(".", 1)[-1] not in _LEGACY_SETTLEMENT_MODULES:
        return
    original = getattr(module, "closeout", None)
    if not callable(original):
        return

    def closeout_with_merge_time(*args, **kwargs):
        item = original(*args, **kwargs)
        if item.get("state") == "MERGED":
            item.setdefault("merged_at", _TEST_MERGED_AT)
        return item

    monkeypatch.setattr(module, "closeout", closeout_with_merge_time)
