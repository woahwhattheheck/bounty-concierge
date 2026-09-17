# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReinvestmentReloadSealTests(unittest.TestCase):
    """Ordinary reload preserves the bootstrapped one-shot authority graph."""

    @staticmethod
    def _clean_env():
        env = dict(os.environ)
        for key in tuple(env):
            if key.startswith("REALIZED_REINVESTMENT_") or key == "ZTE_PROVIDER_MARKER":
                env.pop(key, None)
        return env

    def _run_reload_case(self, poison_helpers: bool) -> str:
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        script = f'''
import importlib
import sys
sys.path.insert(0, {str(ROOT)!r})
checks = []
def check(condition, label):
    checks.append(label)
    if not condition:
        raise RuntimeError(label)
api = importlib.import_module("concierge._reinvestment_allocator_api")
transport = importlib.import_module("concierge._reinvestment_allocator_transport")
ra = importlib.import_module("concierge.reinvestment_allocator")
compile_fn = ra.compile_reinvestment_review
check(not hasattr(api, "build_api"), "boot-only API factory was not retired")
check(not hasattr(api, "make_worker_invoker"), "API worker-factory alias was not retired")
poison_calls = []
def poison(*args, **kwargs):
    poison_calls.append(1)
    raise RuntimeError("poisoned worker factory executed")
if {poison_helpers!r}:
    api.make_worker_invoker = poison
    transport.make_worker_invoker = poison
api_before = api
public_before = ra
check(importlib.reload(api) is api_before, "first API reload replaced exact module")
check(importlib.reload(api) is api_before, "second API reload replaced exact module")
check(not hasattr(api, "build_api"), "ordinary API reload resurrected build_api")
check(importlib.reload(ra) is public_before, "first public reload replaced exact module")
check(importlib.reload(ra) is public_before, "second public reload replaced exact module")
check(ra.compile_reinvestment_review is compile_fn, "ordinary public reload replaced compiler")
check(not hasattr(api, "build_api"), "public reload resurrected private factory")
check(poison_calls == [], "ordinary reload reached poisoned helper factory")
check(api.__spec__ is not None and api.__spec__.origin == "reinvestment-authority-bootstrap", "API reload guard origin missing")
check(ra.__spec__ is not None and ra.__spec__.origin == "reinvestment-authority-bootstrap", "public reload guard origin missing")
check(bool(sys.flags.optimize) is {bool(sys.flags.optimize)!r}, "child optimization mode mismatch")
if len(checks) != 13:
    raise RuntimeError(f"optimized-check canary mismatch: {{len(checks)}}")
print(f"RELOAD_NOOP:{{len(checks)}}")
'''
        completed = subprocess.run(
            command + ["-c", script],
            cwd=ROOT,
            env=self._clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=(
                f"poison_helpers={poison_helpers} "
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            ),
        )
        return completed.stdout.strip()

    def _run_parent_reload_case(self, poison_helpers: bool) -> str:
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        script = f'''
import importlib
import sys
sys.path.insert(0, {str(ROOT)!r})
checks = []
def check(condition, label):
    checks.append(label)
    if not condition:
        raise RuntimeError(label)
pkg = importlib.import_module("concierge")
api = importlib.import_module("concierge._reinvestment_allocator_api")
transport = importlib.import_module("concierge._reinvestment_allocator_transport")
ra = importlib.import_module("concierge.reinvestment_allocator")
pkg_before = pkg
api_before = api
transport_before = transport
public_before = ra
compile_fn = ra.compile_reinvestment_review
check(getattr(pkg, "_REINVESTMENT_BOOTSTRAP_COMPLETE", False) is True, "package bootstrap marker missing")
check(not hasattr(api, "build_api"), "boot-only API factory was not retired before parent reload")
poison_calls = []
def poison(*args, **kwargs):
    poison_calls.append(1)
    raise RuntimeError("poisoned worker factory executed")
if {poison_helpers!r}:
    api.make_worker_invoker = poison
    transport.make_worker_invoker = poison
check(importlib.reload(pkg) is pkg_before, "parent package reload replaced package module")
check(getattr(pkg, "_REINVESTMENT_BOOTSTRAP_COMPLETE", False) is True, "parent reload lost bootstrap marker")
check(importlib.import_module("concierge._reinvestment_allocator_api") is api_before, "parent reload replaced API module")
check(importlib.import_module("concierge.reinvestment_allocator") is public_before, "parent reload replaced public module")
check(importlib.import_module("concierge._reinvestment_allocator_transport") is transport_before, "parent reload replaced transport module")
check(not hasattr(api, "build_api"), "parent reload resurrected private factory")
check(ra.compile_reinvestment_review is compile_fn, "parent reload replaced captured compiler")
check(poison_calls == [], "parent reload reached poisoned helper factory")
check(importlib.reload(api) is api_before, "post-parent API reload replaced exact module")
check(importlib.reload(ra) is public_before, "post-parent public reload replaced exact module")
check(ra.compile_reinvestment_review is compile_fn, "post-parent reload replaced captured compiler")
check(poison_calls == [], "post-parent reload reached poisoned helper factory")
check(bool(sys.flags.optimize) is {bool(sys.flags.optimize)!r}, "child optimization mode mismatch")
if len(checks) != 15:
    raise RuntimeError(f"parent optimized-check canary mismatch: {{len(checks)}}")
print(f"PARENT_RELOAD_SEALED:{{len(checks)}}")
'''
        completed = subprocess.run(
            command + ["-c", script],
            cwd=ROOT,
            env=self._clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=(
                f"poison_helpers={poison_helpers} "
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            ),
        )
        return completed.stdout.strip()

    def test_api_and_public_reload_are_noops_after_bootstrap(self):
        self.assertEqual(self._run_reload_case(False), "RELOAD_NOOP:13")

    def test_poisoned_helpers_then_reload_stays_on_captured_graph(self):
        self.assertEqual(self._run_reload_case(True), "RELOAD_NOOP:13")

    def test_parent_package_reload_preserves_exact_sealed_children(self):
        self.assertEqual(
            self._run_parent_reload_case(False),
            "PARENT_RELOAD_SEALED:15",
        )

    def test_parent_package_reload_with_poisoned_helpers_stays_sealed(self):
        self.assertEqual(
            self._run_parent_reload_case(True),
            "PARENT_RELOAD_SEALED:15",
        )

    def test_module_cli_delegates_to_bootstrapped_public_surface(self):
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        completed = subprocess.run(
            command + ["-m", "concierge.reinvestment_allocator", "--help"],
            cwd=ROOT,
            env=self._clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        self.assertIn("usage", completed.stdout.lower())


if __name__ == "__main__":
    unittest.main()
