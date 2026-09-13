# SPDX-License-Identifier: MIT
"""Repository-wide pytest compatibility fixtures.

Settlement chronology became part of the cash-evidence contract after the
original settlement suite was written. Its local ``closeout()`` helper models
merged work but predates ``merged_at``. This fixture upgrades only that
historical helper; production code still fails closed when a cash-bound merged
item omits provider merge time.
"""
from __future__ import annotations

import pytest


_LEGACY_SETTLEMENT_MODULES = {"test_revenue_settlement"}
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
