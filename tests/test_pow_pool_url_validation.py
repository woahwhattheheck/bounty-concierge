import unittest

from concierge import pow_miners


class ExplicitPoolUrlValidationTests(unittest.TestCase):
    def test_valid_explicit_stratum_endpoints_are_preserved(self):
        urls = (
            "stratum+tcp://example.org:1234",
            "stratum+ssl://127.0.0.1:443",
            "stratum+tcp://[2001:db8::1]:3140",
            "stratum+tcp://localhost:3008/",
        )
        for url in urls:
            with self.subTest(url=url):
                result = pow_miners.resolve_pool_endpoint("lab-pool", url)
                self.assertEqual(
                    result,
                    {
                        "verified": True,
                        "pool": "lab-pool",
                        "endpoint": url,
                        "source": "explicit-url",
                    },
                )

    def test_invalid_explicit_pool_endpoints_fail_closed(self):
        cases = (
            ([], "string"),
            ("", "empty"),
            (" stratum+tcp://example.org:1234", "whitespace"),
            ("stratum+tcp://example.org:1234\n", "whitespace"),
            ("https://example.org:443", "scheme"),
            ("stratum+tcp://example.org", "explicit port"),
            ("stratum+tcp://:1234", "hostname"),
            ("stratum+tcp://bad_host:1234", "hostname"),
            ("stratum+tcp://example.org:99999", "Malformed"),
            ("stratum+tcp://user:pass@example.org:1234", "credentials"),
            ("stratum+tcp://example.org:1234/miner", "path"),
            ("stratum+tcp://example.org:1234?x=1", "query or fragment"),
            ("stratum+tcp://example.org:1234#frag", "query or fragment"),
        )
        for pool_url, error_fragment in cases:
            with self.subTest(pool_url=pool_url):
                result = pow_miners.resolve_pool_endpoint("lab-pool", pool_url)
                self.assertIs(result["verified"], False)
                self.assertIn(error_fragment, result["error"])
                self.assertNotIn("endpoint", result)

    def test_invalid_explicit_endpoint_cannot_produce_pool_proof(self):
        result = pow_miners.verify_pool_account(
            "RTCwallet",
            "lab-pool",
            "https://attacker.example:443",
        )
        self.assertIs(result["verified"], False)
        self.assertIn("scheme", result["error"])
        self.assertNotIn("proof", result)

    def test_known_pool_behavior_is_unchanged(self):
        result = pow_miners.resolve_pool_endpoint("wooly")
        self.assertEqual(
            result,
            {
                "verified": True,
                "pool": "woolypooly",
                "endpoint": pow_miners.POOL_ENDPOINTS["woolypooly"],
                "source": "known-pool",
            },
        )


if __name__ == "__main__":
    unittest.main()
