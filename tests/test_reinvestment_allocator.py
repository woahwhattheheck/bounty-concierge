# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

from reinvestment_allocator_test_support import ReinvestmentCaseBase, ROOT


class IsolatedReinvestmentAuthorityTests(ReinvestmentCaseBase):
    def test_checked_in_core_blob_and_import_surface(self):
        if os.environ.get("ZTE_TEST_CORE_OVERRIDE") == "1":
            self.skipTest("local fixture core intentionally differs from production")
        core_path = ROOT / "concierge" / "_reinvestment_allocator_core.source"
        source = core_path.read_bytes()
        blob_sha = __import__("hashlib").sha1(
            b"blob " + str(len(source)).encode("ascii") + b"\x00" + source
        ).hexdigest()
        self.assertEqual(
            blob_sha, "5731fd0652bc94b20d1f3b2de48628f633f550a9"
        )
        namespace = {
            "__name__": "concierge._core_ci_import_probe",
            "__file__": str(core_path),
            "__package__": "concierge",
        }
        exec(
            compile(
                source.decode("utf-8", errors="strict"),
                str(core_path),
                "exec",
                dont_inherit=True,
                optimize=0,
            ),
            namespace,
            namespace,
        )
        for name in (
            "compile_reinvestment_review",
            "verify_receipt_integrity_only",
            "ReinvestmentInputError",
        ):
            self.assertIn(name, namespace)

    def test_worker_deadline_covers_stalled_stdin_upload(self):
        from concierge._reinvestment_allocator_transport import make_worker_invoker

        with tempfile.TemporaryDirectory() as temp:
            worker = Path(temp) / "stall_worker.py"
            worker.write_text(
                "import time\ntime.sleep(5)\n",
                encoding="utf-8",
            )
            worker.chmod(worker.stat().st_mode | stat.S_IXUSR)
            invoker = make_worker_invoker(
                error_type=ValueError,
                canonical_json=lambda value: json.dumps(value).encode("utf-8"),
                parse_json=lambda raw, label: json.loads(raw.decode("utf-8")),
                sealed_environment_items=tuple(sorted(os.environ.items())),
                executable=sys.executable,
                worker_path=str(worker),
                max_json_bytes=2 * 1024 * 1024,
                max_worker_stderr_bytes=4096,
                max_worker_seconds=0.2,
            )
            started = time.monotonic()
            with self.assertRaisesRegex(
                ValueError, "exceeded its execution deadline"
            ):
                invoker({"payload": "x" * (1024 * 1024)})
            self.assertLess(time.monotonic() - started, 2.0)

    def test_valid_external_signature_composes_scale_only_in_isolated_worker(self):
        result = self.run_case("valid")
        self.assertEqual(result["schema"], "realized-reinvestment-review/v4")
        self.assertEqual(result["state"], "SCALE_REVIEW_ELIGIBLE")
        self.assertEqual(
            result["authority"],
            "fresh_external_rsa_signature_verified_in_isolated_worker",
        )
        self.assertTrue(result["isolated"])
        self.assertFalse(result["symmetric_secret"])
        self.assertTrue(result["integrity"])
        self.assertTrue(result["current"])
        self.assertTrue(result["marker"])

    def test_boot_trust_environment_is_not_reachable_as_a_mutable_closure_dict(self):
        result = self.run_case("closure-container-poison")
        self.assertEqual(result["trust_dict_count"], 0)
        self.assertEqual(result["schema"], "realized-reinvestment-review/v4")
        self.assertTrue(result["marker"])

    def test_post_import_module_and_posix_dependency_rebinding_does_not_reach_worker(self):
        result = self.run_case("post-import-rebind")
        self.assertEqual(result["schema"], "realized-reinvestment-review/v4")
        self.assertTrue(result["isolated"])
        self.assertTrue(result["marker"])

    def test_forged_signature_fails_before_provider_import(self):
        result = self.run_case("forged-signature")
        self.assertTrue(result["blocked"])
        self.assertFalse(result["marker"])
        self.assertIn("signature", result["error"])

    def test_scope_change_fails_before_provider_import(self):
        result = self.run_case("scope-change")
        self.assertTrue(result["blocked"])
        self.assertFalse(result["marker"])
        self.assertIn("scope mismatch", result["error"])

    def test_stale_and_future_authorities_fail_before_provider_import(self):
        stale = self.run_case("stale")
        self.assertTrue(stale["blocked"])
        self.assertFalse(stale["marker"])
        self.assertIn("stale", stale["error"])
        future = self.run_case("future")
        self.assertTrue(future["blocked"])
        self.assertFalse(future["marker"])
        self.assertIn("future", future["error"])

    def test_post_import_environment_key_switch_does_not_change_boot_trust_root(self):
        result = self.run_case("post-import-key-switch")
        self.assertTrue(result["blocked"])
        self.assertFalse(result["marker"])
        self.assertIn("key_id", result["error"])

    def test_late_host_configuration_requires_a_fresh_parent_import(self):
        result = self.run_case("late-config")
        self.assertTrue(result["blocked"])
        self.assertFalse(result["marker"])
        self.assertIn("configuration", result["error"])

    def test_pinned_core_resource_tamper_fails_before_provider_import(self):
        result = self.run_case("core-tamper")
        self.assertTrue(result["blocked"])
        self.assertFalse(result["marker"])
        self.assertIn("digest mismatch", result["error"])

    def test_parent_sys_modules_provider_poison_does_not_cross_process_boundary(self):
        result = self.run_case("parent-provider-poison")
        self.assertEqual(result["schema"], "realized-reinvestment-review/v4")
        self.assertTrue(result["marker"])

    def test_tampered_receipt_fails_integrity_and_current_reacquisition(self):
        result = self.run_case("tampered-receipt")
        self.assertFalse(result["integrity"])
        self.assertFalse(result["current"])

    def test_planning_inputs_remain_explicitly_caller_supplied(self):
        result = self.run_case("planning-inputs")
        self.assertEqual(result["family"], "alternate")
        self.assertFalse(result["authenticated"])

    def test_worker_is_executable_only_and_old_core_has_no_importable_module(self):
        worker = self.run_case("worker-import")
        self.assertTrue(worker["blocked"])
        self.assertEqual(worker["type"], "ImportError")
        core = self.run_case("core-import")
        self.assertTrue(core["blocked"])
        self.assertIn(core["type"], {"ModuleNotFoundError", "ImportError"})
        helpers = self.run_case("retired-helper-import")
        self.assertTrue(helpers["blocked"])
        self.assertNotIn("IMPORTED", helpers["types"])

    def test_workflow_and_docs_pin_the_isolated_surface(self):
        workflow = (ROOT / ".github" / "workflows" / "reinvestment-authority.yml").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "REALIZED_REINVESTMENT_REVIEW.md").read_text(encoding="utf-8")
        self.assertIn("_reinvestment_allocator_worker.py", workflow)
        self.assertIn("_reinvestment_allocator_core.source", workflow)
        self.assertIn("python -O", workflow)
        self.assertIn("RSA", docs)
        self.assertIn("executable-only", docs)
        self.assertIn("not an authorization to spend", docs)

class ReinvestmentAllocatorPortabilityTests(ReinvestmentCaseBase):
    def test_missing_posix_spawn_fails_closed_not_at_import(self):
        """Hosts without posix_spawn/SIGKILL must fail closed at invoke time."""
        from unittest import mock
        from concierge.reinvestment_allocator import ReinvestmentInputError
        from concierge._reinvestment_allocator_transport import make_worker_invoker

        with mock.patch.object(os, "posix_spawn", None, create=True):
            invoke_worker = make_worker_invoker(
                error_type=ReinvestmentInputError,
                canonical_json=lambda value: json.dumps(value).encode("utf-8"),
                parse_json=lambda raw, label: json.loads(raw.decode("utf-8")),
                sealed_environment_items=tuple(sorted(os.environ.items())),
                executable=sys.executable,
                worker_path=__file__,
                max_json_bytes=1024,
                max_worker_stderr_bytes=1024,
                max_worker_seconds=1.0,
            )
            with self.assertRaisesRegex(
                ReinvestmentInputError, "POSIX spawn support"
            ):
                invoke_worker({"request": "probe"})

