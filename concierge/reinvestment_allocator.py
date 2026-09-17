# SPDX-License-Identifier: MIT
"""Isolated, externally signed realized-cash reinvestment review.

The public API never imports or calls the allocator/provider graph in-process.
Every authority check, live-provider read, and allocator invocation occurs in a
fresh isolated Python worker. Commercial attribution and effort must carry a
fresh external RSA signature from a boot-pinned authority. The returned receipt
is advisory and grants no spend, contact, payment, wallet, accounting, tax, or
future-revenue authority.
"""
from __future__ import annotations

import sys
from typing import List, Optional


# ``python -m concierge.reinvestment_allocator`` initializes ``concierge``
# first, and package bootstrap already imports the canonical module. Delegate a
# subsequent runpy ``__main__`` execution to that sealed instance rather than
# trying to consume the private factory a second time.
if __name__ == "__main__":
    from . import reinvestment_allocator as _sealed_reinvestment_allocator

    raise SystemExit(_sealed_reinvestment_allocator.main())

from . import _reinvestment_allocator_api as _api


class ReinvestmentInputError(ValueError):
    """Malformed, unauthenticated, or unverifiable reinvestment input."""


# This factory is intentionally one-shot. concierge.__init__ imports this
# public surface before a caller can obtain any concierge helper submodule via
# ordinary package import. Once the exported closures are built, record the
# consumption outside the reloadable private API module and retire both the
# factory and its transport-factory alias.
_build_api = _api.build_api
(
    compile_reinvestment_review,
    verify_reinvestment_receipt_current,
    verify_receipt_integrity_only,
    commercial_evidence_scope_sha256,
) = _build_api(ReinvestmentInputError, __file__)
_package = sys.modules.get(__package__)
if _package is not None:
    setattr(_package, "_REINVESTMENT_AUTHORITY_API_CONSUMED", True)
for _retired_name in ("build_api", "make_worker_invoker"):
    try:
        delattr(_api, _retired_name)
    except AttributeError:
        pass
del _package, _retired_name, _build_api, _api


def main(argv: Optional[List[str]] = None) -> int:
    from ._reinvestment_allocator_cli import run_cli

    return run_cli(
        compile_reinvestment_review,
        verify_reinvestment_receipt_current,
        ReinvestmentInputError,
        4 * 1024 * 1024,
        argv,
    )
