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
api = importlib.import_module("concierge._reinvestment_allocator_api")
transport = importlib.import_module("concierge._reinvestment_allocator_transport")
ra = importlib.import_module("concierge.reinvestment_allocator")
compile_fn = ra.compile_reinvestment_review
assert not hasattr(api, "build_api"), "boot-only API factory was not retired"
assert not hasattr(api, "make_worker_invoker"), "API worker-factory alias was not retired"
poison_calls = []
def poison(*args, **kwargs):
    poison_calls.append(1)
    raise RuntimeError("poisoned worker factory executed")
if {poison_helpers!r}:
    api.make_worker_invoker = poison
    transport.make_worker_invoker = poison
api_before = api
public_before = ra
assert importlib.reload(api) is api_before
assert importlib.reload(api) is api_before
assert not hasattr(api, "build_api"), "ordinary API reload resurrected build_api"
assert importlib.reload(ra) is public_before
assert importlib.reload(ra) is public_before
assert ra.compile_reinvestment_review is compile_fn, "ordinary public reload replaced compiler"
assert not hasattr(api, "build_api"), "public reload resurrected private factory"
assert poison_calls == [], "ordinary reload reached poisoned helper factory"
assert api.__spec__ is not None and api.__spec__.origin == "reinvestment-authority-bootstrap"
assert ra.__spec__ is not None and ra.__spec__.origin == "reinvestment-authority-bootstrap"
print("RELOAD_NOOP")
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
        self.assertEqual(self._run_reload_case(False), "RELOAD_NOOP")

    def test_poisoned_helpers_then_reload_stays_on_captured_graph(self):
        self.assertEqual(self._run_reload_case(True), "RELOAD_NOOP")

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
