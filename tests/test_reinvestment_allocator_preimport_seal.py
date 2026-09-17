# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "concierge"
BOOT_BEGIN = "# BEGIN_REINVESTMENT_AUTHORITY_BOOTSTRAP_SEAL"
BOOT_END = "# END_REINVESTMENT_AUTHORITY_BOOTSTRAP_SEAL"


def _bootstrap_seal_source() -> str:
    source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    begin = source.index(BOOT_BEGIN)
    end = source.index(BOOT_END, begin) + len(BOOT_END)
    return source[begin:end] + "\n"


class ReinvestmentBootstrapSealTests(unittest.TestCase):
    maxDiff = None

    def _isolated_package(self, root: Path) -> None:
        package = root / "concierge"
        package.mkdir()
        (package / "__init__.py").write_text(
            "# isolated hostile-import harness\n" + _bootstrap_seal_source(),
            encoding="utf-8",
        )
        for name in (
            "reinvestment_allocator.py",
            "_reinvestment_allocator_api.py",
            "_reinvestment_allocator_transport.py",
        ):
            shutil.copy2(PACKAGE / name, package / name)

    def _run_script(self, source: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._isolated_package(root)
            script = root / "probe.py"
            script.write_text(textwrap.dedent(source), encoding="utf-8")
            command = [sys.executable]
            if sys.flags.optimize:
                command.append("-O")
            command.extend(["-I", str(script), str(root)])
            env = dict(os.environ)
            completed = subprocess.run(
                command,
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"stderr={completed.stderr}\nstdout={completed.stdout}",
            )
            return completed

    def test_bootstrap_seal_runs_before_other_package_dispatchers(self):
        source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index(BOOT_BEGIN),
            source.index("from . import payoff_path_policy_v3"),
        )
        self.assertIn(
            '"_reinvestment_allocator_api"',
            _bootstrap_seal_source(),
        )
        self.assertIn(
            '"_reinvestment_allocator_transport"',
            _bootstrap_seal_source(),
        )

    def test_private_api_first_import_cannot_precede_public_capture(self):
        completed = self._run_script(
            '''
            import importlib
            import sys

            sys.path.insert(0, sys.argv[1])
            api = importlib.import_module("concierge._reinvestment_allocator_api")
            assert "concierge.reinvestment_allocator" in sys.modules
            public_before = sys.modules["concierge.reinvestment_allocator"]
            compile_before = public_before.compile_reinvestment_review

            def poison(**kwargs):
                raise RuntimeError("pre-import API poison reached")

            api.make_worker_invoker = poison
            from concierge import reinvestment_allocator as public_after

            assert public_after is public_before
            assert public_after.compile_reinvestment_review is compile_before
            print("SEALED_API_FIRST")
            '''
        )
        self.assertIn("SEALED_API_FIRST", completed.stdout)

    def test_preseeded_public_and_transport_modules_are_evicted_before_capture(self):
        completed = self._run_script(
            '''
            import sys
            import types

            sys.path.insert(0, sys.argv[1])

            fake_public = types.ModuleType("concierge.reinvestment_allocator")
            fake_public.compile_reinvestment_review = lambda *a, **k: {"fake": True}
            sys.modules["concierge.reinvestment_allocator"] = fake_public

            fake_transport = types.ModuleType(
                "concierge._reinvestment_allocator_transport"
            )

            def poisoned_make_worker_invoker(**kwargs):
                raise RuntimeError("preloaded transport poison reached")

            fake_transport.make_worker_invoker = poisoned_make_worker_invoker
            sys.modules[
                "concierge._reinvestment_allocator_transport"
            ] = fake_transport

            from concierge import reinvestment_allocator as public
            transport = sys.modules[
                "concierge._reinvestment_allocator_transport"
            ]

            assert public is not fake_public
            assert transport is not fake_transport
            assert getattr(public, "__file__", "").endswith(
                "concierge/reinvestment_allocator.py"
            )
            assert getattr(transport, "__file__", "").endswith(
                "concierge/_reinvestment_allocator_transport.py"
            )
            print("EVICTED_PRELOAD")
            '''
        )
        self.assertIn("EVICTED_PRELOAD", completed.stdout)


if __name__ == "__main__":
    unittest.main()
