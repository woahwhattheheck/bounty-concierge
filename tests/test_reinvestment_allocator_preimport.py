# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from reinvestment_allocator_test_support import (
    ATTACKER_D,
    ATTACKER_N,
    ReinvestmentCaseBase,
    TEST_D,
    TEST_N,
)


_WRAPPER = r'''
from __future__ import annotations
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import runpy
import sys

TARGET = sys.argv[1]
ROOT = Path(sys.argv[2])
DRIVER = sys.argv[3]
N_HEX = sys.argv[4]
D_HEX = sys.argv[5]
ATTACKER_N_HEX = sys.argv[6]
ATTACKER_D_HEX = sys.argv[7]
MARKER = ROOT / "provider.marker"

os.environ.update({
    "REALIZED_REINVESTMENT_AUTHORITY_RSA_MODULUS_HEX": N_HEX,
    "REALIZED_REINVESTMENT_AUTHORITY_RSA_KEY_ID": "owner-rsa-2026-09",
    "REALIZED_REINVESTMENT_AUTHORIZED_PROVIDER": "owner-review-host",
    "REALIZED_REINVESTMENT_AUTHORIZED_PRINCIPAL_SHA256": "22" * 32,
    "ZTE_PROVIDER_MARKER": str(MARKER),
})
sys.path.insert(0, str(ROOT))

if TARGET == "api":
    first = importlib.import_module("concierge._reinvestment_allocator_api")
elif TARGET == "transport":
    first = importlib.import_module("concierge._reinvestment_allocator_transport")
else:
    raise RuntimeError("unknown target")

bootstrap_loaded = "concierge.reinvestment_allocator" in sys.modules
ra = importlib.import_module("concierge.reinvestment_allocator")
api = importlib.import_module("concierge._reinvestment_allocator_api")
transport = importlib.import_module("concierge._reinvestment_allocator_transport")
api_factory_retired = not hasattr(api, "build_api")
api_transport_alias_retired = not hasattr(api, "make_worker_invoker")
poison_calls = []

def poison(*args, **kwargs):
    poison_calls.append(TARGET)
    raise RuntimeError("pre-import poison reached authority bootstrap")

# These writes occur only after the requested helper import has returned.  The
# package bootstrap must already have created the public authority closures.
api.make_worker_invoker = poison
transport.make_worker_invoker = poison
public_compile = ra.compile_reinvestment_review

sys.argv = [
    DRIVER,
    "valid",
    str(ROOT),
    N_HEX,
    D_HEX,
    ATTACKER_N_HEX,
    ATTACKER_D_HEX,
]
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    runpy.run_path(DRIVER, run_name="__main__")
lines = [line for line in captured.getvalue().splitlines() if line.strip()]
if not lines:
    raise RuntimeError("existing valid-case driver emitted no JSON")
result = json.loads(lines[-1])
result.update({
    "target": TARGET,
    "bootstrap_loaded": bootstrap_loaded,
    "api_factory_retired": api_factory_retired,
    "api_transport_alias_retired": api_transport_alias_retired,
    "public_compile_stable": ra.compile_reinvestment_review is public_compile,
    "poison_calls": len(poison_calls),
})
print(json.dumps(result, sort_keys=True))
'''


class PreImportTrustRootTests(ReinvestmentCaseBase):
    """Close ordinary helper-first import poisoning before public bootstrap."""

    def setUp(self):
        super().setUp()
        package = self.root / "concierge"
        # Mirror the production package's relevant first action without pulling
        # unrelated package installers into the isolated authority fixture.
        (package / "__init__.py").write_text(
            "from . import reinvestment_allocator as _reinvestment_allocator_bootstrap\n"
            "del _reinvestment_allocator_bootstrap\n",
            encoding="utf-8",
        )

    def _run_preimport_case(self, target: str):
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        command.extend([
            "-c",
            _WRAPPER,
            target,
            str(self.root),
            str(self.driver),
            TEST_N,
            TEST_D,
            ATTACKER_N,
            ATTACKER_D,
        ])
        env = dict(os.environ)
        for key in tuple(env):
            if key.startswith("REALIZED_REINVESTMENT_") or key == "ZTE_PROVIDER_MARKER":
                env.pop(key, None)
        completed = subprocess.run(
            command,
            cwd=self.root,
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
            msg=f"target={target} stderr={completed.stderr}\nstdout={completed.stdout}",
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, msg=f"target={target} emitted no JSON")
        result = json.loads(lines[-1])
        self.assertNotIn("driver_error", result, msg=result)
        return result

    def test_helper_first_imports_bootstrap_before_caller_poisoning(self):
        for target in ("api", "transport"):
            with self.subTest(target=target):
                result = self._run_preimport_case(target)
                self.assertTrue(result["bootstrap_loaded"])
                self.assertTrue(result["api_factory_retired"])
                self.assertTrue(result["api_transport_alias_retired"])
                self.assertTrue(result["public_compile_stable"])
                self.assertEqual(result["poison_calls"], 0)
                self.assertEqual(result["schema"], "realized-reinvestment-review/v4")
                self.assertEqual(result["state"], "SCALE_REVIEW_ELIGIBLE")
                self.assertTrue(result["isolated"])
                self.assertTrue(result["integrity"])
                self.assertTrue(result["current"])
                self.assertTrue(result["marker"])
