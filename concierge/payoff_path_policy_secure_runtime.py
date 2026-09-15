# SPDX-License-Identifier: MIT
"""Closure-bound runtime authority for payoff policy v3.

Python module attributes are ordinary mutable process state. Security-critical owner
policy verification must therefore not depend on looking a verifier up from a module
object on each call, nor on a ModuleType ``__setattr__`` shim.

This module is imported during trusted ``concierge`` package bootstrap, after the v3
implementation and verify-only authority module exist but before the caller receives
the package. ``install()`` captures the real verifier and the v3 semantic functions
in closure cells, installs closure-bound public/direct v3 entrypoints, and installs
closure-bound core dispatchers. Rebinding either module's attributes afterwards does
not change those captured capabilities.

Reloading the security-critical v3 implementation in-place is intentionally not a
supported code-update mechanism. A meta-path loader turns an ordinary
``importlib.reload(v3)`` into a fail-safe no-op: the already bootstrapped module and
its closure-bound entrypoints remain active. Hosts restart to load new v3 code. This
preserves backward-compatible callers that invoke reload while refusing to recreate
an authority graph from caller-mutable live module attributes.

Threat boundary: arbitrary replacement of the supported core dispatcher itself,
closure-cell surgery, ``sys.meta_path`` surgery, bytecode/function-code replacement,
or installed-source replacement is arbitrary interpreter/host takeover and is out of
scope. Ordinary caller documents, environment mutation, authority-module public or
private attribute rebinding (including ``types.ModuleType.__setattr__`` and direct
``module.__dict__`` writes), and ordinary ``importlib.reload(v3)`` are in scope.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from datetime import datetime
from typing import Any

from . import payoff_path_gate_core as _core
from . import payoff_path_policy_authority as _authority
from . import payoff_path_policy_v3 as _v3

_V3_MODULE_NAME = _v3.__name__
_INSTALLED = False
_RELOAD_GUARD = None


class _V3ReloadGuard(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Return a no-op loader for ordinary reload of the secured v3 module."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):
        if fullname == _V3_MODULE_NAME and target is _v3:
            return importlib.util.spec_from_loader(
                fullname,
                self,
                origin="payoff-policy-secure-runtime",
            )
        return None

    def create_module(self, spec):
        # Initial import has already completed before this guard is installed.
        return None

    def exec_module(self, module) -> None:
        # Deliberate no-op on reload. Existing closure-bound functions and state stay
        # in the retained module dictionary. A trusted host restart loads new source.
        return None


def _build_runtime(
    *,
    trusted_verify,
    raw_v3_compile,
    raw_v3_verify,
    raw_core_compile,
    raw_core_verify,
    work_schema_v3: str,
    legacy_work_schema: str,
    error_type,
):
    """Build dispatch functions whose authority graph is closure-bound."""

    def secure_v3_compile(
        document: Any,
        trusted_as_of: datetime | str | None,
        previous_receipt: Any,
    ):
        trusted_verify(document)
        return raw_v3_compile(document, trusted_as_of, previous_receipt)

    def secure_v3_verify(
        document: Any,
        packet: Any,
        markdown: Any,
        receipt: Any,
        trusted_now: datetime | str | None = None,
        previous_receipt: Any = None,
    ) -> bool:
        trusted_verify(document)
        return raw_v3_verify(
            document,
            packet,
            markdown,
            receipt,
            trusted_now=trusted_now,
            previous_receipt=previous_receipt,
        )

    def secure_core_compile(
        document: Any,
        trusted_as_of: datetime | str | None,
        previous_receipt: Any,
        *,
        legacy_replay: bool,
    ):
        schema = document.get("schema") if type(document) is dict else None
        if schema == work_schema_v3:
            return secure_v3_compile(document, trusted_as_of, previous_receipt)
        if schema == legacy_work_schema:
            return raw_core_compile(
                document,
                trusted_as_of,
                previous_receipt,
                legacy_replay=False,
            )
        return raw_core_compile(
            document,
            trusted_as_of,
            previous_receipt,
            legacy_replay=legacy_replay,
        )

    def secure_core_verify(
        document: Any,
        packet: Any,
        markdown: Any,
        receipt: Any,
        trusted_now: datetime | str | None = None,
        previous_receipt: Any = None,
    ) -> bool:
        schema = document.get("schema") if type(document) is dict else None
        if schema == work_schema_v3:
            return secure_v3_verify(
                document,
                packet,
                markdown,
                receipt,
                trusted_now=trusted_now,
                previous_receipt=previous_receipt,
            )
        mode = None
        if type(packet) is dict and type(packet.get("continuity")) is dict:
            mode = packet["continuity"].get("mode")
        if schema == legacy_work_schema and mode == "LEGACY_REPLAY_ONLY":
            raise error_type(
                "legacy READY replay is migration-only; use verify_legacy_migration_gate"
            )
        return raw_core_verify(
            document,
            packet,
            markdown,
            receipt,
            trusted_now=trusted_now,
            previous_receipt=previous_receipt,
        )

    return secure_v3_compile, secure_v3_verify, secure_core_compile, secure_core_verify


def install() -> None:
    """Install closure-bound authority exactly once during trusted package bootstrap."""

    global _INSTALLED, _RELOAD_GUARD
    if _INSTALLED:
        return

    trusted_verify = _authority.verify_current_policy_authority
    raw_v3_compile = _v3._compile_v3
    raw_v3_verify = _v3.verify_v3
    raw_core_compile = _v3._ORIGINAL_INTERNAL_COMPILE
    raw_core_verify = _v3._ORIGINAL_VERIFY

    (
        secure_v3_compile,
        secure_v3_verify,
        secure_core_compile,
        secure_core_verify,
    ) = _build_runtime(
        trusted_verify=trusted_verify,
        raw_v3_compile=raw_v3_compile,
        raw_v3_verify=raw_v3_verify,
        raw_core_compile=raw_core_compile,
        raw_core_verify=raw_core_verify,
        work_schema_v3=_v3.WORK_SCHEMA,
        legacy_work_schema=_core.LEGACY_WORK_SCHEMA,
        error_type=_core.PayoffPathError,
    )

    # Direct v3 calls and supported public gate dispatch both enter closure-bound
    # authority first. The raw semantic functions remain reachable only through the
    # captured closure, not through a caller-mutable verifier lookup.
    _v3._compile_v3 = secure_v3_compile
    _v3.verify_v3 = secure_v3_verify
    _core._compile_gate = secure_core_compile
    _core.verify_gate = secure_core_verify

    _RELOAD_GUARD = _V3ReloadGuard()
    sys.meta_path.insert(0, _RELOAD_GUARD)
    _INSTALLED = True


__all__ = ["install"]
