# SPDX-License-Identifier: MIT
"""Single-writer payout delivery gate with anchored output publication.

The decision/compiler implementation remains in `_payout_delivery_gate_core`.
This facade replaces only the filesystem publication primitive so CLI and
package callers cannot follow symlinked output parents.
"""
from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # Preserve direct `python path/to/module.py` use.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from concierge import _payout_delivery_gate_core as _core
from concierge._payout_delivery_gate_core import *  # noqa: F401,F403
from concierge.secure_output import SecureOutputError, create_exclusive_regular


def _write_new(path: Path, payload: bytes) -> None:
    try:
        create_exclusive_regular(path, payload)
    except SecureOutputError as exc:
        raise PayoutDeliveryInputError(str(exc)) from exc


# The core CLI's `_emit()` resolves `_write_new` in the core module globals.
# Rebind it once at import so every existing caller gets the hardened writer.
_core._write_new = _write_new

# Preserve private helper access used by focused regression code and operators.
_read_json = _core._read_json
_emit = _core._emit
_reject_duplicate_keys = _core._reject_duplicate_keys


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


def __getattr__(name: str):
    return getattr(_core, name)


if __name__ == "__main__":
    raise SystemExit(main())
