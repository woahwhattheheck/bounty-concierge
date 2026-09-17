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


class ReinvestmentPreseededModuleTests(unittest.TestCase):
    maxDiff = None

    def _run_preseed(self, target: str) -> dict:
        script = textwrap.dedent(
            f"""
            import json
            import sys
            import types

            sys.path.insert(0, {str(ROOT)!r})

            poison_hits = []
            def poison(*args, **kwargs):
                poison_hits.append({target!r})
                raise RuntimeError({('preseeded ' + target + ' poison reached')!r})

            if {target!r} in ("api", "both"):
                fake_api = types.ModuleType("concierge._reinvestment_allocator_api")
                fake_api.build_api = poison
                sys.modules["concierge._reinvestment_allocator_api"] = fake_api
            if {target!r} in ("transport", "both"):
                fake_transport = types.ModuleType("concierge._reinvestment_allocator_transport")
                fake_transport.make_worker_invoker = poison
                sys.modules["concierge._reinvestment_allocator_transport"] = fake_transport

            from concierge import reinvestment_allocator as ra

            print(json.dumps({{
                "poison_hits": poison_hits,
                "compile_callable": callable(ra.compile_reinvestment_review),
                "current_verify_callable": callable(ra.verify_reinvestment_receipt_current),
                "scope_callable": callable(ra.commercial_evidence_scope_sha256),
                "api_blob": ra._API_BLOB_SHA1,
                "transport_blob": ra._TRANSPORT_BLOB_SHA1,
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

    def _assert_source_pinned(self, result: dict) -> None:
        self.assertEqual(result["poison_hits"], [])
        self.assertTrue(result["compile_callable"])
        self.assertTrue(result["current_verify_callable"])
        self.assertTrue(result["scope_callable"])
        self.assertEqual(
            result["api_blob"],
            _git_blob_sha1(ROOT / "concierge" / "_reinvestment_allocator_api.py"),
        )
        self.assertEqual(
            result["transport_blob"],
            _git_blob_sha1(ROOT / "concierge" / "_reinvestment_allocator_transport.py"),
        )

    def test_preseeded_api_module_is_not_authority_source(self) -> None:
        self._assert_source_pinned(self._run_preseed("api"))

    def test_preseeded_transport_module_is_not_authority_source(self) -> None:
        self._assert_source_pinned(self._run_preseed("transport"))

    def test_preseeded_api_and_transport_modules_are_not_authority_source(self) -> None:
        self._assert_source_pinned(self._run_preseed("both"))


if __name__ == "__main__":
    unittest.main()
