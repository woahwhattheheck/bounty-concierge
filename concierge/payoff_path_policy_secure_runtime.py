# SPDX-License-Identifier: MIT
"""Closure-bound runtime authority for payoff policy v3.

Python module attributes and Python function metadata are ordinary mutable process
state. Security-critical owner-policy verification must therefore not depend on
looking a verifier up from a public module on each call, retaining caller-mutable
``__defaults__`` / ``__kwdefaults__`` authority dependencies, or consuming the
caller's mutable document after authentication.

This module is imported during trusted ``concierge`` package bootstrap, after the v3
implementation and verify-only authority module exist but before the caller receives
the package. ``install()`` seals the verifier function graph into private globals,
seals the raw v3 compile/verify functions with that verifier, then installs
closure-bound entrypoints which authenticate and semantically consume one private
exact-builtins snapshot.

Reloading the security-critical v3 implementation in-place is intentionally not a
supported code-update mechanism. A meta-path loader turns an ordinary
``importlib.reload(v3)`` into a fail-safe no-op: the already bootstrapped module and
its closure-bound entrypoints remain active. Hosts restart to load new v3 code.

Threat boundary: arbitrary replacement of the supported core dispatcher itself,
closure-cell surgery, ``sys.meta_path`` surgery, bytecode/function-code replacement,
installed-source replacement, or executing attacker code before trusted package
bootstrap is arbitrary interpreter/host takeover and is out of scope. Ordinary
caller documents, post-bootstrap environment mutation, authority/v3 module
public/private attribute rebinding, mutable public verifier function metadata, and
ordinary ``importlib.reload(v3)`` are in scope.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys
import types
from datetime import datetime
from typing import Any

from . import payoff_path_gate_core as _core
from . import payoff_path_policy_authority as _authority
from . import payoff_path_policy_v3 as _v3

_V3_MODULE_NAME = _v3.__name__
_INSTALLED = False
_RELOAD_GUARD = None
_MAX_SNAPSHOT_NODES = 200_000
_MAX_SNAPSHOT_TEXT = 4_000_000
_MAX_SNAPSHOT_DEPTH = 64


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


def _seal_function_graph(function, memo=None):
    """Clone a Python function and its Python-function dependency graph once.

    The clones receive private globals dictionaries plus detached copies of defaults
    and keyword defaults. This is a bootstrap-time capability seal: later mutation of
    the public module bindings or the original functions' ``__kwdefaults__`` cannot
    alter the retained verifier/semantic graph.

    Module objects and C/builtin callables remain shared. Mutating arbitrary imported
    runtime modules or interpreter primitives is outside this carrier's threat model.
    """

    if not isinstance(function, types.FunctionType):
        return function
    if memo is None:
        memo = {}
    identity = id(function)
    existing = memo.get(identity)
    if existing is not None:
        return existing

    private_globals = dict(function.__globals__)
    clone = types.FunctionType(
        function.__code__,
        private_globals,
        name=function.__name__,
        argdefs=function.__defaults__,
        closure=function.__closure__,
    )
    memo[identity] = clone

    def seal_value(value):
        if isinstance(value, types.FunctionType):
            return _seal_function_graph(value, memo)
        if type(value) is tuple:
            return tuple(seal_value(item) for item in value)
        if type(value) is list:
            return [seal_value(item) for item in value]
        if type(value) is dict:
            return {key: seal_value(item) for key, item in value.items()}
        return value

    # Only names actually loaded by this code object need sealed Python-function
    # bindings. Memoization safely closes recursive/cyclic call graphs.
    for name in function.__code__.co_names:
        value = private_globals.get(name)
        if isinstance(value, types.FunctionType):
            private_globals[name] = _seal_function_graph(value, memo)

    defaults = function.__defaults__
    clone.__defaults__ = (
        tuple(seal_value(value) for value in defaults) if defaults is not None else None
    )
    kwdefaults = function.__kwdefaults__
    clone.__kwdefaults__ = (
        {key: seal_value(value) for key, value in kwdefaults.items()}
        if kwdefaults is not None
        else None
    )
    clone.__annotations__ = dict(getattr(function, "__annotations__", {}))
    clone.__doc__ = function.__doc__
    clone.__module__ = function.__module__
    clone.__qualname__ = function.__qualname__
    return clone


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

    # Snapshot limits are captured at trusted bootstrap rather than looked up from
    # caller-mutable module attributes on each security decision.
    max_snapshot_nodes = _MAX_SNAPSHOT_NODES
    max_snapshot_text = _MAX_SNAPSHOT_TEXT
    max_snapshot_depth = _MAX_SNAPSHOT_DEPTH

    def snapshot_document(document: Any) -> dict[str, Any]:
        remaining_nodes = max_snapshot_nodes
        remaining_text = max_snapshot_text

        def copy_value(value: Any, depth: int):
            nonlocal remaining_nodes, remaining_text
            remaining_nodes -= 1
            if remaining_nodes < 0:
                raise error_type("v3 document snapshot exceeds node limit")
            if depth > max_snapshot_depth:
                raise error_type("v3 document snapshot exceeds nesting limit")

            value_type = type(value)
            if value is None or value_type is bool or value_type is int:
                return value
            if value_type is str:
                remaining_text -= len(value)
                if remaining_text < 0:
                    raise error_type("v3 document snapshot exceeds text limit")
                return value
            if value_type is list:
                return [copy_value(item, depth + 1) for item in value]
            if value_type is dict:
                copied: dict[str, Any] = {}
                for key, item in value.items():
                    if type(key) is not str:
                        raise error_type("v3 document snapshot object keys must be strings")
                    remaining_text -= len(key)
                    if remaining_text < 0:
                        raise error_type("v3 document snapshot exceeds text limit")
                    copied[key] = copy_value(item, depth + 1)
                return copied
            raise error_type(
                "v3 document snapshot accepts only exact JSON-compatible built-in types"
            )

        snapshot = copy_value(document, 0)
        if type(snapshot) is not dict:
            raise error_type("v3 document must be an object")
        return snapshot

    def secure_v3_compile(
        document: Any,
        trusted_as_of: datetime | str | None,
        previous_receipt: Any,
    ):
        snapshot = snapshot_document(document)
        trusted_verify(snapshot)
        return raw_v3_compile(snapshot, trusted_as_of, previous_receipt)

    def secure_v3_verify(
        document: Any,
        packet: Any,
        markdown: Any,
        receipt: Any,
        trusted_now: datetime | str | None = None,
        previous_receipt: Any = None,
    ) -> bool:
        snapshot = snapshot_document(document)
        trusted_verify(snapshot)
        return raw_v3_verify(
            snapshot,
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

    # Seal the verifier before exposing concierge to callers. This detaches the
    # security decision from public function metadata such as __kwdefaults__ and from
    # Python-function dependencies reachable through those defaults/globals.
    sealed_memo = {}
    trusted_verify = _seal_function_graph(
        _authority.verify_current_policy_authority,
        sealed_memo,
    )

    # Clone the raw v3 functions into private globals and replace their authority hook
    # with the sealed verifier. verify_v3 also recompiles through the sealed compiler.
    # Therefore the semantic read after the outer authority check cannot cross a later
    # caller-rebound v3._verify_current_policy_authority hook.
    raw_v3_compile = _seal_function_graph(_v3._compile_v3, sealed_memo)
    raw_v3_compile.__globals__["_verify_current_policy_authority"] = trusted_verify
    raw_v3_verify = _seal_function_graph(_v3.verify_v3, sealed_memo)
    raw_v3_verify.__globals__["_verify_current_policy_authority"] = trusted_verify
    raw_v3_verify.__globals__["_compile_v3"] = raw_v3_compile

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

    _v3._compile_v3 = secure_v3_compile
    _v3.verify_v3 = secure_v3_verify
    _core._compile_gate = secure_core_compile
    _core.verify_gate = secure_core_verify

    _RELOAD_GUARD = _V3ReloadGuard()
    sys.meta_path.insert(0, _RELOAD_GUARD)
    _INSTALLED = True


__all__ = ["install"]
