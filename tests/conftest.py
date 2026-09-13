# SPDX-License-Identifier: MIT
"""Pytest-only migration adapter for the strict settlement time contract.

PR #122 intentionally made bound cash evidence fail closed unless a merged
closeout carries ``merged_at`` and the selected provider row carries an
admissible transfer time. Two older test modules predate that contract: their
local fixture factories still synthesize bound MERGED rows without
``merged_at`` and one uses a non-timestamp default tag as part of its timestamp.

Keep production settlement strict. This adapter changes only evidence emitted
by those legacy *test helper factories* at test runtime. Directly constructed
hostile rows remain untouched, so negative coverage still attacks the real
boundary rather than a weakened test seam.
"""

from __future__ import annotations

from types import ModuleType


# The settlement suite uses 2026-era canonical text evidence. Keep its migrated
# merge and transfer one second apart so chronology is explicit and stable.
_SETTLEMENT_MERGED_AT = "2026-09-13T00:00:00Z"
_SETTLEMENT_TRANSFER_AT = "2026-09-13T00:00:01Z"
# Transaction-identity tests deliberately exercise exact integer Unix seconds
# 1 and 2. Epoch zero is the strict merge boundary for those fixtures.
_IDENTITY_MERGED_AT = "1970-01-01T00:00:00Z"


def _patch_settlement_helpers(module: ModuleType) -> None:
    if getattr(module, "_post122_temporal_contract_migrated", False):
        return

    original_closeout = module.closeout
    original_payment = module.payment
    original_legacy_payment = module.legacy_payment

    def closeout(*args, **kwargs):
        row = original_closeout(*args, **kwargs)
        if row.get("state") == "MERGED":
            row.setdefault("merged_at", _SETTLEMENT_MERGED_AT)
        return row

    def payment(*args, **kwargs):
        row = original_payment(*args, **kwargs)
        # The historical default tag ``a`` produced ``...00aZ``. Numeric tags
        # are already valid canonical UTC and remain unchanged so pagination /
        # row-identity tests preserve their original evidence bytes.
        if row.get("timestamp") == "2026-09-13T00:00:0aZ":
            row["timestamp"] = _SETTLEMENT_TRANSFER_AT
        return row

    def legacy_payment(*args, **kwargs):
        row = original_legacy_payment(*args, **kwargs)
        if row.get("created_at") == "2026-09-13T00:00:0aZ":
            row["created_at"] = _SETTLEMENT_TRANSFER_AT
        return row

    module.closeout = closeout
    module.payment = payment
    module.legacy_payment = legacy_payment
    module._post122_temporal_contract_migrated = True


def _patch_transaction_identity_helpers(module: ModuleType) -> None:
    if getattr(module, "_post122_temporal_contract_migrated", False):
        return

    original_closeout = module.closeout

    def closeout(*args, **kwargs):
        row = original_closeout(*args, **kwargs)
        row.setdefault("merged_at", _IDENTITY_MERGED_AT)
        return row

    module.closeout = closeout
    module._post122_temporal_contract_migrated = True


def pytest_runtest_setup(item) -> None:
    """Migrate only the two pre-#122 helper modules before each test executes."""
    module = item.module
    name = module.__name__
    if name.endswith("test_revenue_settlement"):
        _patch_settlement_helpers(module)
    elif name.endswith("test_transaction_identity"):
        _patch_transaction_identity_helpers(module)
