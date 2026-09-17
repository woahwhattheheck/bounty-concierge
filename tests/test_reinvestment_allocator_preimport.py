# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\x00" + data
    ).hexdigest()


class ReinvestmentPreImportTrustRootTests(unittest.TestCase):
    maxDiff = None

    def _run_preimport_poison(self, target: str) -> dict:
        script = textwrap.dedent(
            f"""
            import json
            import sys
            sys.path.insert(0, {str(ROOT)!r})

            poison_hits = []
            def poison(*args, **kwargs):
                poison_hits.append({target!r})
                raise RuntimeError({('pre-import ' + target + ' poison reached')!r})

            if {target!r} in ("api", "both"):
                import concierge._reinvestment_allocator_api as api
                api.build_api = poison
            if {target!r} in ("transport", "both"):
                import concierge._reinvestment_allocator_transport as transport
                transport.make_worker_invoker = poison

            from concierge import reinvestment_allocator as ra

            print(json.dumps({{
                "poison_hits": poison_hits,
                "compile_callable": callable(ra.compile_reinvestment_review),
                "current_verify_callable": callable(ra.verify_reinvestment_receipt_current),
                "scope_callable": callable(ra.commercial_evidence_scope_sha256),
                "api_blob": ra._API_BLOB_SHA1,
                "transport_blob": ra._TRANSPORT_BLOB_SHA1,
                "boundary_truth": "not a claim of integrity against arbitrary hostile mutation" in (ra.__doc__ or ""),
            }}, sort_keys=True))
            """
        )
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        command.extend(["-c", script])
        completed = subprocess.run(
            command,
            cwd=ROOT,
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

    def _assert_real_bootstrap(self, result: dict) -> None:
        self.assertEqual(result["poison_hits"], [])
        self.assertTrue(result["compile_callable"])
        self.assertTrue(result["current_verify_callable"])
        self.assertTrue(result["scope_callable"])
        self.assertTrue(result["boundary_truth"])
        self.assertEqual(
            result["api_blob"],
            _git_blob_sha1(ROOT / "concierge" / "_reinvestment_allocator_api.py"),
        )
        self.assertEqual(
            result["transport_blob"],
            _git_blob_sha1(ROOT / "concierge" / "_reinvestment_allocator_transport.py"),
        )

    def test_preimport_api_module_poison_is_not_authority_source(self) -> None:
        self._assert_real_bootstrap(self._run_preimport_poison("api"))

    def test_preimport_transport_module_poison_is_not_authority_source(self) -> None:
        self._assert_real_bootstrap(self._run_preimport_poison("transport"))

    def test_preimport_api_and_transport_poison_is_not_authority_source(self) -> None:
        self._assert_real_bootstrap(self._run_preimport_poison("both"))


if __name__ == "__main__":
    unittest.main()
