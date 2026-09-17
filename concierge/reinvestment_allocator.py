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


# Consume the private factory exactly once during trusted package bootstrap.
# Afterwards, retire both the factory and its API-level transport alias, then
# install an exact-module meta-path guard so ordinary importlib.reload() is a
# no-op rather than a second build against caller-mutable helper bindings.
_build_api = _api.build_api
(
    compile_reinvestment_review,
    verify_reinvestment_receipt_current,
    verify_receipt_integrity_only,
    commercial_evidence_scope_sha256,
) = _build_api(ReinvestmentInputError, __file__)
for _retired_name in ("build_api", "make_worker_invoker"):
    try:
        delattr(_api, _retired_name)
    except AttributeError:
        pass

from . import _reinvestment_allocator_reload_guard as _reload_guard

_reload_guard.install(
    api_module=_api,
    public_module=sys.modules[__name__],
)
del _reload_guard, _retired_name, _build_api, _api


def main(argv: Optional[List[str]] = None) -> int:
    from ._reinvestment_allocator_cli import run_cli

    return run_cli(
        compile_reinvestment_review,
        verify_reinvestment_receipt_current,
        ReinvestmentInputError,
        4 * 1024 * 1024,
        argv,
    )
