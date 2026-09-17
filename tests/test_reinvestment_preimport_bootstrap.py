# SPDX-License-Identifier: MIT
from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReinvestmentPreimportBootstrapTests(unittest.TestCase):
    maxDiff = None

    def _run_first_private_import(self, private_name: str) -> str:
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        script = f'''\nimport importlib\nimport sys\nsys.path.insert(0, {str(ROOT)!r})\nprivate = importlib.import_module({private_name!r})\npublic_name = "concierge.reinvestment_allocator"\nif public_name not in sys.modules:\n    raise RuntimeError("public authority surface was not package-bootstrapped")\nra = sys.modules[public_name]\napi = importlib.import_module("concierge._reinvestment_allocator_api")\ntransport = importlib.import_module("concierge._reinvestment_allocator_transport")\ncompile_fn = ra.compile_reinvestment_review\ncompile_cells = dict(zip(compile_fn.__code__.co_freevars, [cell.cell_contents for cell in compile_fn.__closure__]))\ninvoke_worker = compile_cells.get("invoke_worker")\nif invoke_worker is None:\n    raise RuntimeError("public compiler did not capture its worker invoker")\nworker_cells = dict(zip(invoke_worker.__code__.co_freevars, [cell.cell_contents for cell in invoke_worker.__closure__]))\ncaptured_spawn = worker_cells.get("posix_spawn")\nif captured_spawn is None:\n    raise RuntimeError("worker invoker did not capture POSIX spawn")\n\ndef poisoned_invoker_factory(*args, **kwargs):\n    def poisoned_invoker(request):\n        return {{"protocol": "realized-reinvestment-isolated-worker/v1", "receipt": {{"schema": "realized-reinvestment-review/v4"}}}}\n    return poisoned_invoker\n\ndef poisoned_spawn(*args, **kwargs):\n    raise RuntimeError("pre-import poison reached authority launcher")\n\napi.make_worker_invoker = poisoned_invoker_factory\ntransport.make_worker_invoker = poisoned_invoker_factory\ntransport.os.posix_spawn = poisoned_spawn\ncompile_cells_after = dict(zip(compile_fn.__code__.co_freevars, [cell.cell_contents for cell in compile_fn.__closure__]))\nif compile_cells_after.get("invoke_worker") is not invoke_worker:\n    raise RuntimeError("post-import mutation replaced captured worker invoker")\nworker_cells_after = dict(zip(invoke_worker.__code__.co_freevars, [cell.cell_contents for cell in invoke_worker.__closure__]))\nif worker_cells_after.get("posix_spawn") is not captured_spawn:\n    raise RuntimeError("post-import mutation replaced captured POSIX spawn")\nif worker_cells_after.get("posix_spawn") is poisoned_spawn:\n    raise RuntimeError("poisoned POSIX spawn became authority launcher")\nprint("SEALED")\n'''
        completed = subprocess.run(
            command + ["-c", script],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"private={private_name}\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return completed.stdout.strip()

    def _run_preseeded_module_poison(self, preseeded_names: tuple[str, ...]) -> str:
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        script = f'''\nimport importlib\nimport sys\nimport types\nsys.path.insert(0, {str(ROOT)!r})\n\nfake_public = types.ModuleType("concierge.reinvestment_allocator")\nfake_public.compile_reinvestment_review = lambda *a, **k: {{"attacker": True}}\n\nfake_api = types.ModuleType("concierge._reinvestment_allocator_api")\ndef poisoned_build_api(*args, **kwargs):\n    def fake_compile(*a, **k):\n        return {{"attacker": True}}\n    return fake_compile, (lambda *a, **k: True), (lambda *a, **k: True), (lambda *a, **k: "0" * 64)\nfake_api.build_api = poisoned_build_api\n\nfake_transport = types.ModuleType("concierge._reinvestment_allocator_transport")\ndef poisoned_make_worker_invoker(*args, **kwargs):\n    raise RuntimeError("preseeded transport poison reached authority launcher")\nfake_transport.make_worker_invoker = poisoned_make_worker_invoker\n\nfakes = {{\n    "concierge.reinvestment_allocator": fake_public,\n    "concierge._reinvestment_allocator_api": fake_api,\n    "concierge._reinvestment_allocator_transport": fake_transport,\n}}\nfor name in {preseeded_names!r}:\n    sys.modules[name] = fakes[name]\n\nimport concierge\nra = importlib.import_module("concierge.reinvestment_allocator")\napi = importlib.import_module("concierge._reinvestment_allocator_api")\ntransport = importlib.import_module("concierge._reinvestment_allocator_transport")\n\nif ra is fake_public:\n    raise RuntimeError("preseeded public module survived bootstrap")\nif api is fake_api:\n    raise RuntimeError("preseeded API module survived bootstrap")\nif transport is fake_transport:\n    raise RuntimeError("preseeded transport module survived bootstrap")\nif not getattr(ra, "__file__", "").endswith("concierge/reinvestment_allocator.py"):\n    raise RuntimeError("public authority module is not repository-backed")\nif not getattr(api, "__file__", "").endswith("concierge/_reinvestment_allocator_api.py"):\n    raise RuntimeError("API authority module is not repository-backed")\nif not getattr(transport, "__file__", "").endswith("concierge/_reinvestment_allocator_transport.py"):\n    raise RuntimeError("transport authority module is not repository-backed")\ncompile_fn = ra.compile_reinvestment_review\ncompile_cells = dict(zip(compile_fn.__code__.co_freevars, [cell.cell_contents for cell in compile_fn.__closure__]))\nif compile_cells.get("invoke_worker") is None:\n    raise RuntimeError("repository compiler did not retain a real worker invoker")\nprint("EVICTED")\n'''
        completed = subprocess.run(
            command + ["-c", script],
            cwd=ROOT,
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
                f"preseeded={preseeded_names}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            ),
        )
        return completed.stdout.strip()

    def test_api_first_import_cannot_precede_public_authority_seal(self):
        self.assertEqual(
            self._run_first_private_import("concierge._reinvestment_allocator_api"),
            "SEALED",
        )

    def test_transport_first_import_cannot_precede_public_authority_seal(self):
        self.assertEqual(
            self._run_first_private_import("concierge._reinvestment_allocator_transport"),
            "SEALED",
        )

    def test_preseeded_public_module_is_evicted(self):
        self.assertEqual(
            self._run_preseeded_module_poison(("concierge.reinvestment_allocator",)),
            "EVICTED",
        )

    def test_preseeded_api_module_is_evicted(self):
        self.assertEqual(
            self._run_preseeded_module_poison(("concierge._reinvestment_allocator_api",)),
            "EVICTED",
        )

    def test_preseeded_transport_module_is_evicted(self):
        self.assertEqual(
            self._run_preseeded_module_poison(
                ("concierge._reinvestment_allocator_transport",)
            ),
            "EVICTED",
        )

    def test_preseeded_public_api_and_transport_modules_are_evicted(self):
        self.assertEqual(
            self._run_preseeded_module_poison(
                (
                    "concierge.reinvestment_allocator",
                    "concierge._reinvestment_allocator_api",
                    "concierge._reinvestment_allocator_transport",
                )
            ),
            "EVICTED",
        )


if __name__ == "__main__":
    unittest.main()
