import copy
import os
import unittest
from unittest.mock import patch

from concierge import adjudication_collection as ac
from concierge.sponsor_adjudication import authority as sa_authority
from tests.test_adjudication_collection import (
    TEST_KEY,
    TEST_PRINCIPAL,
    TEST_PROVIDER,
    add_offer,
    base_manifest,
    payload,
    route,
)


class ExactTypeVerifierTests(unittest.TestCase):
    def setUp(self):
        self.host_env = patch.dict(
            os.environ,
            {
                sa_authority.KEY_ENV: TEST_KEY,
                sa_authority.PROVIDER_ENV: TEST_PROVIDER,
                sa_authority.PRINCIPAL_ENV: TEST_PRINCIPAL,
                sa_authority.TEST_UNSIGNED_ENV: "0",
            },
            clear=False,
        )
        self.host_env.start()
        self.addCleanup(self.host_env.stop)

    def _valid(self):
        p = payload(add_offer(base_manifest()), payout_route=route())
        return p, ac.compile_adjudication_collection(p)

    def test_exact_packet_still_verifies(self):
        p, packet = self._valid()
        self.assertTrue(ac.verify_adjudication_collection(p, packet))

    def test_false_to_zero_alias_is_rejected(self):
        p, packet = self._valid()
        self.assertIs(packet["authority"]["external_send"], False)
        tampered = copy.deepcopy(packet)
        tampered["authority"]["external_send"] = 0
        self.assertFalse(ac.verify_adjudication_collection(p, tampered))

    def test_true_to_one_alias_is_rejected(self):
        p, packet = self._valid()
        self.assertIs(
            packet["authority"]["owner_or_separately_authorized_sender_required"],
            True,
        )
        tampered = copy.deepcopy(packet)
        tampered["authority"]["owner_or_separately_authorized_sender_required"] = 1
        self.assertFalse(ac.verify_adjudication_collection(p, tampered))


if __name__ == "__main__":
    unittest.main()
