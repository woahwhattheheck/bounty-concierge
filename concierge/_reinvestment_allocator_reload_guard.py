# SPDX-License-Identifier: MIT
"""Ordinary-reload guard for the bootstrapped reinvestment authority surface.

The reinvestment API captures its isolated-worker launch graph exactly once during
trusted package bootstrap. Ordinary ``importlib.reload`` of the parent package,
public module, or private API module must therefore preserve the already-captured
objects rather than re-executing source against caller-mutable helper bindings.

This guard deliberately covers ordinary reload only. Arbitrary ``sys.meta_path``
surgery, direct loader execution, source replacement, closure surgery, interpreter
replacement, or equivalent same-process/host takeover are outside this application
boundary; hosts needing resistance to those capabilities must use a controlled fresh
interpreter/process boundary.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from typing import Any

_INSTALLED = False
_RELOAD_GUARD = None


class _ReinvestmentReloadGuard(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Return a no-op loader for ordinary reload of exact bootstrapped modules."""

    def __init__(self, api_module: Any, public_module: Any, package_module: Any) -> None:
        self._targets = {
            api_module.__name__: api_module,
            public_module.__name__: public_module,
            package_module.__name__: package_module,
        }

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):
        expected = self._targets.get(fullname)
        if expected is None or target is not expected:
            return None
        is_package = hasattr(expected, "__path__")
        spec = importlib.util.spec_from_loader(
            fullname,
            self,
            origin="reinvestment-authority-bootstrap",
            is_package=is_package,
        )
        if spec is None:
            raise ImportError("could not build reinvestment reload guard spec")
        if is_package:
            # Keep the exact package search path usable after the no-op reload.
            spec.submodule_search_locations = list(expected.__path__)
        return spec

    def create_module(self, spec):
        # Initial import completed before this guard is installed.
        return None

    def exec_module(self, module) -> None:
        # Deliberate no-op on ordinary reload. Existing bootstrap-captured public
        # closures and the retired private factory remain exactly as they are.
        expected = self._targets.get(module.__name__)
        if expected is not module:
            raise ImportError("reinvestment reload target identity mismatch")


def install(*, api_module: Any, public_module: Any, package_module: Any) -> None:
    """Install the exact-module ordinary-reload guard once per interpreter."""
    global _INSTALLED, _RELOAD_GUARD
    if _INSTALLED:
        return
    _RELOAD_GUARD = _ReinvestmentReloadGuard(api_module, public_module, package_module)
    sys.meta_path.insert(0, _RELOAD_GUARD)
    _INSTALLED = True


__all__ = ["install"]
