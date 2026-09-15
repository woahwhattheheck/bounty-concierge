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
)


class AdjudicationCollectionRouteAuthorityTests(unittest.TestCase):
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

    def test_attacker_payment_link_never_becomes_trusted_collection_route(self):
        attacker_route = {
            "type": "PAYMENT_LINK",
            "value": "https://attacker.example/collect",
        }
        packet = ac.compile_adjudication_collection(
            payload(add_offer(base_manifest()), payout_route=attacker_route)
        )

        self.assertEqual(packet["disposition"], "OWNER_REVIEW_REQUIRED")
        self.assertFalse(packet["authority"]["payout_route_independently_verified"])
        self.assertFalse(packet["authority"]["external_send"])
        self.assertFalse(packet["authority"]["payout_initiated"])
        self.assertIn("payout route are unverified", packet["reason"])
        self.assertIn(
            "Candidate payout route (caller-supplied; unverified): PAYMENT_LINK — https://attacker.example/collect",
            packet["draft"]["body"],
        )
        self.assertIn(
            "independently verify that the candidate payout route is owned or authorized for Bryce",
            packet["draft"]["body"],
        )
        self.assertIn("Only after both checks pass", packet["draft"]["body"])
        self.assertNotIn("\nPayout route:", packet["draft"]["body"])

    def test_non_link_route_is_equally_truth_labeled(self):
        packet = ac.compile_adjudication_collection(
            payload(
                add_offer(base_manifest()),
                payout_route={"type": "WALLET", "value": "attacker-wallet-candidate"},
            )
        )
        self.assertEqual(packet["disposition"], "OWNER_REVIEW_REQUIRED")
        self.assertFalse(packet["authority"]["payout_route_independently_verified"])
        self.assertIn(
            "Candidate payout route (caller-supplied; unverified): WALLET — attacker-wallet-candidate",
            packet["draft"]["body"],
        )


if __name__ == "__main__":
    unittest.main()
