import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from concierge import sponsor_adjudication as sa
import concierge.sponsor_adjudication.authority as authority
import concierge.sponsor_adjudication.compiler as compiler
import concierge.sponsor_adjudication.schema as schema
import concierge.sponsor_adjudication.verified as verified
from concierge.sponsor_adjudication.authority import (
    KEY_ENV,
    PRINCIPAL_ENV,
    PROVIDER_ENV,
    TEST_UNSIGNED_ENV,
    bind_report_generation,
    sign_for_test_or_host_fixture,
)
from tests.test_sponsor_adjudication_authority import base_manifest, h, utc_text


class SponsorAdjudicationRuntimeUnsignedRejectionTests(unittest.TestCase):
    def setUp(self):
        self.host_env = patch.dict(
            os.environ,
            {
                KEY_ENV: "11" * 32,
                PROVIDER_ENV: "gmail",
                PRINCIPAL_ENV: h("tokenjunkielabs@gmail.com"),
            },
            clear=False,
        )
        self.host_env.start()
        os.environ.pop(TEST_UNSIGNED_ENV, None)

    def tearDown(self):
        os.environ.pop(TEST_UNSIGNED_ENV, None)
        self.host_env.stop()

    def authorize(self, manifest):
        manifest["sponsor_authority"] = sign_for_test_or_host_fixture(
            manifest,
            manifest["program"],
            manifest["sponsor_events"],
            captured_at=utc_text(datetime.now(timezone.utc)),
        )
        return manifest

    def test_test_only_adapter_is_not_active_in_host_suite(self):
        self.assertIs(schema.verify_manifest_authority, authority.verify_manifest_authority)
        self.assertIs(compiler.bind_report_generation, authority.bind_report_generation)
        self.assertIs(verified.verify_report_authority, authority.verify_report_authority)

    def test_runtime_toggle_cannot_mint_unsigned_sponsor_state(self):
        with self.assertRaisesRegex(sa.AdjudicationError, "require host sponsor_authority"):
            sa.compile_manifest(base_manifest())

        with patch.dict(os.environ, {TEST_UNSIGNED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(sa.AdjudicationError, "is retired"):
                sa.compile_manifest(base_manifest())

    def test_runtime_toggle_cannot_disable_retained_verification(self):
        report = sa.compile_manifest(self.authorize(base_manifest()))
        self.assertEqual(sa.verify_report(report)["report_sha256"], report["report_sha256"])

        with patch.dict(os.environ, {TEST_UNSIGNED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(sa.AdjudicationError, "is retired"):
                sa.verify_report(report)

    def test_runtime_toggle_blocks_direct_report_binding(self):
        with patch.dict(os.environ, {TEST_UNSIGNED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(sa.AdjudicationError, "is retired"):
                bind_report_generation({}, {})

    def test_runtime_toggle_fails_closed_for_zero_event_generation(self):
        with patch.dict(os.environ, {TEST_UNSIGNED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(sa.AdjudicationError, "is retired"):
                sa.compile_manifest(base_manifest(events=False))

    def test_any_legacy_variable_value_is_rejected(self):
        for value in ("", "0", "false", "1"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {TEST_UNSIGNED_ENV: value}, clear=False):
                    with self.assertRaisesRegex(sa.AdjudicationError, "is retired"):
                        sa.compile_manifest(base_manifest())

    def test_runtime_toggle_blocks_host_fixture_signing(self):
        manifest = base_manifest()
        with patch.dict(os.environ, {TEST_UNSIGNED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(sa.AdjudicationError, "is retired"):
                sign_for_test_or_host_fixture(
                    manifest,
                    manifest["program"],
                    manifest["sponsor_events"],
                    captured_at=utc_text(datetime.now(timezone.utc)),
                )


if __name__ == "__main__":
    unittest.main()
