# SPDX-License-Identifier: MIT
"""Only live-work-risk regressions; no provider traffic or test farm."""
import hashlib
import unittest

from concierge.test_target_admission import is_explicit_test_disclosure, screen_paid_target

REPO = "ApexOpsStudio/ai-gitops-test-target"
URL = "https://github.com/ApexOpsStudio/ai-gitops-test-target/blob/main/README.md"
TEXT = "# Task CLI - Test Target for ai-gitops\n\nThis is a **test target repository** - not a real project. It exists solely to validate our AI-assisted bounty hunting workflow."


def blob_sha(data: str) -> str:
    b = data.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest()


class TestTargetGateTest(unittest.TestCase):
    def test_verified_owner_readme_is_blocked(self):
        r = screen_paid_target(REPO, readme=TEXT, source_url=URL, blob_sha=blob_sha(TEXT))
        self.assertEqual(r["disposition"], "BLOCK_SELF_DISCLOSED_NONPAYABLE_TARGET")
        self.assertFalse(r["new_paid_work_allowed"])
        self.assertTrue(r["existing_claims_preserved"])

    def test_known_fake_blocks_without_another_github_request(self):
        r = screen_paid_target(REPO)
        self.assertEqual(r["reason_code"], "SPONSOR_SELF_DISCLOSED_TEST_ONLY")
        self.assertEqual(r["evidence"]["git_blob_sha"], "91da9a7d29915af0508a0a90e29cff05760b9856")

    def test_legit_test_documentation_is_not_proof_of_nonpayment(self):
        self.assertFalse(is_explicit_test_disclosure("Our CI tests simulate bounty payment; actual program has paid developers."))
        self.assertIsNone(screen_paid_target("python/cpython")["new_paid_work_allowed"])

    def test_spoofed_source_or_wrong_blob_cannot_block_other_repo(self):
        with self.assertRaises(ValueError):
            screen_paid_target("python/cpython", readme=TEXT, source_url=URL, blob_sha=blob_sha(TEXT))
        with self.assertRaises(ValueError):
            screen_paid_target(REPO, readme=TEXT, source_url=URL, blob_sha="0"*40)

    def test_quoted_and_fenced_example_is_not_sponsor_disclosure(self):
        self.assertFalse(is_explicit_test_disclosure("> This is a test target repository - not a real project.\n\x60\x60\x60md\nThis is a test target repository - not a real project.\n\x60\x60\x60"))


if __name__ == "__main__":
    unittest.main()
