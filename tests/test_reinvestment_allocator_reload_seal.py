# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReinvestmentReloadSealTests(unittest.TestCase):
    """Ordinary reload may not resurrect a consumed authority surface."""

    @staticmethod
    def _clean_env():
        env = dict(os.environ)
        for key in tuple(env):
            if key.startswith("REALIZED_REINVESTMENT_") or key == "ZTE_PROVIDER_MARKER":
                env.pop(key, None)
        return env

    def _run_reload_case(self, poison_transport: bool) -> str:
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        script = f'''
import importlib
import sys
sys.path.insert(0, {str(ROOT)!r})
api = importlib.import_module("concierge._reinvestment_allocator_api")
transport = importlib.import_module("concierge._reinvestment_allocator_transport")
ra = importlib.import_module("concierge.reinvestment_allocator")
package = importlib.import_module("concierge")
compile_fn = ra.compile_reinvestment_review
assert getattr(package, "_REINVESTMENT_AUTHORITY_API_CONSUMED", False) is True
assert not hasattr(api, "build_api"), "boot-only API factory was not retired"
assert not hasattr(api, "make_worker_invoker"), "API worker-factory alias was not retired"
poison_calls = []
def poison(*args, **kwargs):
    poison_calls.append(1)
    raise RuntimeError("poisoned worker factory executed")
if {poison_transport!r}:
    transport.make_worker_invoker = poison
api_blocked = False
try:
    importlib.reload(api)
except ImportError as exc:
    api_blocked = "already consumed" in str(exc)
assert api_blocked, "ordinary importlib.reload(api) resurrected boot-only source"
assert not hasattr(api, "build_api"), "API reload failure left build_api reachable"
assert not hasattr(api, "make_worker_invoker"), "API reload failure left worker factory reachable"
public_blocked = False
try:
    importlib.reload(ra)
except ImportError as exc:
    public_blocked = "already initialized" in str(exc)
assert public_blocked, "ordinary importlib.reload(public) attempted a second authority build"
assert ra.compile_reinvestment_review is compile_fn, "reload attempt replaced public compiler"
assert poison_calls == [], "reload attempt reached poisoned transport factory"
print("RELOAD_BLOCKED")
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
                f"poison_transport={poison_transport} "
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            ),
        )
        return completed.stdout.strip()

    def test_api_and_public_reload_fail_closed_after_bootstrap(self):
        self.assertEqual(self._run_reload_case(False), "RELOAD_BLOCKED")

    def test_transport_poison_then_reload_fails_closed(self):
        self.assertEqual(self._run_reload_case(True), "RELOAD_BLOCKED")

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
