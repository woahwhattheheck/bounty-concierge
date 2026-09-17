# SPDX-License-Identifier: MIT
"""Isolated, externally signed realized-cash reinvestment review.

The public API never imports the authority-bearing helper module objects.  It
loads exact checked-in API and transport source blobs into private namespaces,
then captures the resulting launch graph before exporting any callable.  Thus
pre-import mutation of ``concierge._reinvestment_allocator_api`` or
``concierge._reinvestment_allocator_transport`` cannot become authority.

Every commercial-evidence authority check, live-provider read, and allocator
invocation still occurs in a fresh isolated Python worker.  Commercial
attribution and effort must carry a fresh external RSA signature from a
boot-pinned authority.  The returned receipt is advisory and grants no spend,
contact, payment, wallet, accounting, tax, or future-revenue authority.

This source pin closes the named importable-helper predecessor; it is not a
claim of integrity against arbitrary hostile mutation of the Python runtime,
interpreter executable, filesystem, or operating system before this module is
loaded.
"""
from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
from pathlib import Path as _Path
from typing import Any, Callable, Dict, List, Optional


_API_BLOB_SHA1 = "cc8cf003e182e8ddd22d2b0c1de3a03047d3e258"
_TRANSPORT_BLOB_SHA1 = "472efce478e918d4e17f87620b301302d303e39c"
_MAX_BOOTSTRAP_SOURCE_BYTES = 256 * 1024


class ReinvestmentInputError(ValueError):
    """Malformed, unauthenticated, or unverifiable reinvestment input."""


def _load_pinned_symbol(
    filename: str,
    expected_blob_sha1: str,
    symbol: str,
    namespace_name: str,
) -> Callable[..., Any]:
    """Execute an exact sibling source blob without importing its module object."""
    source_path = _Path(__file__).with_name(filename)
    try:
        source = source_path.read_bytes()
    except OSError as exc:
        raise ImportError(f"pinned reinvestment bootstrap source {filename} unavailable") from exc
    if len(source) > _MAX_BOOTSTRAP_SOURCE_BYTES:
        raise ImportError(f"pinned reinvestment bootstrap source {filename} is too large")
    digest = _hashlib.sha1(
        b"blob " + str(len(source)).encode("ascii") + b"\x00" + source
    ).hexdigest()
    if not _hmac.compare_digest(digest, expected_blob_sha1):
        raise ImportError(f"pinned reinvestment bootstrap source {filename} digest mismatch")
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ImportError(f"pinned reinvestment bootstrap source {filename} is not UTF-8") from exc
    namespace: Dict[str, Any] = {
        "__name__": namespace_name,
        "__file__": str(source_path),
        "__package__": "concierge",
    }
    try:
        code = compile(text, str(source_path), "exec", dont_inherit=True, optimize=0)
        exec(code, namespace, namespace)
    except Exception as exc:
        raise ImportError(f"pinned reinvestment bootstrap source {filename} could not be loaded") from exc
    value = namespace.get(symbol)
    if not callable(value):
        raise ImportError(f"pinned reinvestment bootstrap source {filename} lacks {symbol}")
    return value


_make_worker_invoker = _load_pinned_symbol(
    "_reinvestment_allocator_transport.py",
    _TRANSPORT_BLOB_SHA1,
    "make_worker_invoker",
    "concierge._sealed_reinvestment_allocator_transport",
)
_build_api = _load_pinned_symbol(
    "_reinvestment_allocator_api.py",
    _API_BLOB_SHA1,
    "build_api",
    "concierge._sealed_reinvestment_allocator_api",
)

(
    compile_reinvestment_review,
    verify_reinvestment_receipt_current,
    verify_receipt_integrity_only,
    commercial_evidence_scope_sha256,
) = _build_api(ReinvestmentInputError, __file__, _make_worker_invoker)

del _build_api
del _make_worker_invoker


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
