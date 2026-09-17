# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReinvestmentPreImportSealTests(unittest.TestCase):
    maxDiff = None

    def _run_script(self, source: str):
        env = dict(os.environ)
        for key in tuple(env):
            if key.startswith("REALIZED_REINVESTMENT_") or key == "ZTE_PROVIDER_MARKER":
                env.pop(key, None)
        completed = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stderr={completed.stderr}\nstdout={completed.stdout}",
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, msg=f"no JSON output; stderr={completed.stderr}")
        return json.loads(lines[-1])

    def _preimport_poison_case(self, internal_module: str):
        return self._run_script(
            f"""
            import json
            import os
            import sys

            os.environ.update({{
                "REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX": "f" * 512,
                "REALIZED_REINVESTMENT_AUTHORITY_RSA_KEY_ID": "seal-test-key",
                "REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER": "seal-test-provider",
                "REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256": "1" * 64,
            }})
            sys.path.insert(0, {str(ROOT)!r})

            target = __import__({internal_module!r}, fromlist=["*"])
            public_preloaded = "concierge.reinvestment_allocator" in sys.modules

            def poisoned_factory(**kwargs):
                def poisoned_invoker(_request):
                    raise RuntimeError("PREIMPORT_POISON_REACHED")
                return poisoned_invoker

            target.make_worker_invoker = poisoned_factory
            from concierge import reinvestment_allocator as ra

            try:
                ra.compile_reinvestment_review(
                    [],
                    [],
                    {{"schema_version": 1, "items": []}},
                    {{}},
                    {{}},
                    {{}},
                    1,
                    wallet="seal-test-wallet",
                )
            except Exception as exc:
                result = {{
                    "public_preloaded": public_preloaded,
                    "error_type": type(exc).__name__,
                    "poison_reached": "PREIMPORT_POISON_REACHED" in str(exc),
                }}
            else:
                result = {{
                    "public_preloaded": public_preloaded,
                    "error_type": None,
                    "poison_reached": False,
                }}
            print(json.dumps(result, sort_keys=True))
            """
        )

    def test_api_module_poison_cannot_precede_public_seal(self):
        result = self._preimport_poison_case(
            "concierge._reinvestment_allocator_api"
        )
        self.assertTrue(result["public_preloaded"])
        self.assertEqual(result["error_type"], "ReinvestmentInputError")
        self.assertFalse(result["poison_reached"])

    def test_transport_module_poison_cannot_precede_public_seal(self):
        result = self._preimport_poison_case(
            "concierge._reinvestment_allocator_transport"
        )
        self.assertTrue(result["public_preloaded"])
        self.assertEqual(result["error_type"], "ReinvestmentInputError")
        self.assertFalse(result["poison_reached"])

    def test_preseeded_internal_modules_fail_closed(self):
        for internal_module in (
            "concierge._reinvestment_allocator_api",
            "concierge._reinvestment_allocator_transport",
        ):
            with self.subTest(internal_module=internal_module):
                result = self._run_script(
                    f"""
                    import json
                    import sys
                    import types

                    fake = types.ModuleType({internal_module!r})
                    sys.modules[{internal_module!r}] = fake
                    try:
                        import concierge  # noqa: F401
                    except Exception as exc:
                        result = {{
                            "blocked": True,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }}
                    else:
                        result = {{
                            "blocked": False,
                            "error_type": None,
                            "message": "",
                        }}
                    print(json.dumps(result, sort_keys=True))
                    """
                )
                self.assertTrue(result["blocked"])
                self.assertEqual(result["error_type"], "ImportError")
                self.assertIn("must not be preloaded", result["message"])


if __name__ == "__main__":
    unittest.main()
