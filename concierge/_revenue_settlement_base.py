# SPDX-License-Identifier: MIT
"""Historical import alias for the authoritative settlement core.

Completeness, snapshot, CLI, and library semantics live in exactly one module:
``concierge._revenue_settlement_core``.  Keeping this name as the exact same
module object preserves historical monkeypatch/test seams without making import
order an authority boundary.
"""

from __future__ import annotations

import sys

from concierge import _revenue_settlement_core as _core

sys.modules[__name__] = _core
