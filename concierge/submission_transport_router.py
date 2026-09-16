# SPDX-License-Identifier: MIT
"""Public submission-transport API.

The core itself owns the canonical packet verifier and fixed host receipt ledger,
so direct-core import, direct-core CLI execution, and this public wrapper have
one identical production trust boundary.
"""

from __future__ import annotations

from concierge._submission_transport_router_core import (
    SubmissionTransportInputError,
    compile_transport_decision,
    compile_transport_operation,
    format_summary,
    main,
    strict_json_loads,
    verify_transport_decision,
)

__all__ = [
    "SubmissionTransportInputError",
    "compile_transport_operation",
    "compile_transport_decision",
    "verify_transport_decision",
    "strict_json_loads",
    "format_summary",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
