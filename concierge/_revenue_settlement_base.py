# SPDX-License-Identifier: MIT
"""Historical import alias for the authoritative settlement core.

Completeness and one-generation snapshot semantics live at
``_revenue_settlement_core`` so import order cannot weaken them. Keep this name
only as an object-identity alias for existing public wrappers and tests.
"""

from __future__ import annotations

import sys

from concierge import _revenue_settlement_core as _core


sys.modules[__name__] = _core
