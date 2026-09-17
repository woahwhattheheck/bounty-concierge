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

from typing import List, Optional

from ._reinvestment_allocator_api import build_api as _build_api


class ReinvestmentInputError(ValueError):
    """Malformed, unauthenticated, or unverifiable reinvestment input."""


(
    compile_reinvestment_review,
    verify_reinvestment_receipt_current,
    verify_receipt_integrity_only,
    commercial_evidence_scope_sha256,
) = _build_api(ReinvestmentInputError, __file__)
del _build_api


def main(argv: Optional[List[str]] = None) -> int:
    from ._reinvestment_allocator_cli import run_cli

    return run_cli(
        compile_reinvestment_review,
        verify_reinvestment_receipt_current,
        ReinvestmentInputError,
        4 * 1024 * 1024,
        argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
