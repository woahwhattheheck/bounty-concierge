# SPDX-License-Identifier: MIT
"""Collect the historical settlement tests under the strict temporal contract.

The original cases are preserved byte-for-byte in a non-collected module.  This
adapter modernizes only their shared fixtures: a bound merged RTC closeout now
carries merge time, and cash-bearing wallet rows carry canonical provider time.
Production code is not patched or relaxed for test compatibility.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_CASES = Path(__file__).with_name("_legacy_revenue_settlement_cases.py")
_SPEC = importlib.util.spec_from_file_location("_legacy_revenue_settlement_cases", _CASES)
assert _SPEC is not None and _SPEC.loader is not None
_LEGACY = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _LEGACY
_SPEC.loader.exec_module(_LEGACY)

_original_closeout = _LEGACY.closeout
_original_payment = _LEGACY.payment
_original_legacy_payment = _LEGACY.legacy_payment


def _closeout(*args, **kwargs):
    row = _original_closeout(*args, **kwargs)
    row["merged_at"] = "2026-09-13T00:00:00Z"
    return row


def _payment(*args, **kwargs):
    row = _original_payment(*args, **kwargs)
    row["timestamp"] = "2026-09-13T00:00:01Z"
    return row


def _legacy_payment(*args, **kwargs):
    row = _original_legacy_payment(*args, **kwargs)
    row["created_at"] = "2026-09-13T00:00:01Z"
    return row


_LEGACY.closeout = _closeout
_LEGACY.payment = _payment
_LEGACY.legacy_payment = _legacy_payment

for _name, _value in vars(_LEGACY).items():
    if _name.startswith("test_"):
        globals()[_name] = _value
