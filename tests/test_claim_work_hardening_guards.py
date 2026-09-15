from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from concierge import claim_work_authority as cwa
from concierge.sponsor_adjudication import authority as sa_authority
from tests.test_claim_work_authority import (
    RELATION_KEY,
    RELATION_PRINCIPAL,
    RELATION_PROVIDER,
    SPONSOR_KEY,
    SPONSOR_PRINCIPAL,
    SPONSOR_PROVIDER,
    bind_payload,
    signed_report,
)


class ClaimWorkHardeningGuardTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                sa_authority.KEY_ENV: SPONSOR_KEY,
                sa_authority.PROVIDER_ENV: SPONSOR_PROVIDER,
                sa_authority.PRINCIPAL_ENV: SPONSOR_PRINCIPAL,
                sa_authority.TEST_UNSIGNED_ENV: "0",
                cwa.KEY_ENV: RELATION_KEY,
                cwa.RECEIPT_KEY_ENV: "33" * 32,
                cwa.PROVIDER_ENV: RELATION_PROVIDER,
                cwa.PRINCIPAL_ENV: RELATION_PRINCIPAL,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.report = signed_report()

    def test_reused_relation_key_fails_before_provider_read(self):
        payload = bind_payload(self.report)
        with patch.dict(os.environ, {cwa.RECEIPT_KEY_ENV: RELATION_KEY}, clear=False):
            with patch.object(cwa.requests, "get") as get:
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "must be distinct"):
                    cwa.bind_claim_work(payload)
                get.assert_not_called()

    def test_malformed_receipt_key_fails_before_provider_read(self):
        payload = bind_payload(self.report)
        with patch.dict(os.environ, {cwa.RECEIPT_KEY_ENV: "not-hex"}, clear=False):
            with patch.object(cwa.requests, "get") as get:
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, cwa.RECEIPT_KEY_ENV):
                    cwa.bind_claim_work(payload)
                get.assert_not_called()

    def test_unsigned_sponsor_mode_fails_before_provider_read(self):
        payload = bind_payload(self.report)
        with patch.dict(os.environ, {sa_authority.TEST_UNSIGNED_ENV: "1"}, clear=False):
            with patch.object(cwa.requests, "get") as get:
                with self.assertRaisesRegex(cwa.ClaimWorkAuthorityError, "unsigned-test mode"):
                    cwa.bind_claim_work(payload)
                get.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
