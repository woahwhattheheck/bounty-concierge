"""Collect historical transaction-identity tests with explicit merge time.

The original case module remains byte-for-byte unchanged; this adapter upgrades
only its shared closeout fixture to the strict settlement chronology contract.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_CASES = Path(__file__).with_name("_legacy_transaction_identity_cases.py")
_SPEC = importlib.util.spec_from_file_location("_legacy_transaction_identity_cases", _CASES)
assert _SPEC is not None and _SPEC.loader is not None
_LEGACY = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _LEGACY
_SPEC.loader.exec_module(_LEGACY)

_original_closeout = _LEGACY.closeout


def _closeout(*args, **kwargs):
    row = _original_closeout(*args, **kwargs)
    row["merged_at"] = "1970-01-01T00:00:00Z"
    return row


_LEGACY.closeout = _closeout

for _name, _value in vars(_LEGACY).items():
    if _name.startswith("test_"):
        globals()[_name] = _value
