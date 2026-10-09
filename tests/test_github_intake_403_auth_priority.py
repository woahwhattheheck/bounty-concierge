# SPDX-License-Identifier: MIT
"""Focused regression for the GitHub 403 classification priority."""
from __future__ import annotations

import unittest

from concierge.github_intake_cache import classify_github_403


class AuthPriorityTest(unittest.TestCase):
    def test_auth_failure_is_not_recast_as_secondary_with_retry_after(self) -> None:
        self.assertEqual(
            classify_github_403("Bad credentials", remaining=100, retry_after_seconds=45),
            "AUTH_DENIED",
        )

    def test_explicit_auth_wins_over_exhausted_unauthenticated_quota(self) -> None:
        self.assertEqual(
            classify_github_403("Requires authentication", remaining=0),
            "AUTH_DENIED",
        )

    def test_real_primary_rate_limit_still_classified(self) -> None:
        self.assertEqual(
            classify_github_403("API rate limit exceeded", remaining=0),
            "PRIMARY_RATE_LIMITED",
        )

    def test_secondary_rate_limit_still_classified(self) -> None:
        self.assertEqual(
            classify_github_403("You have exceeded a secondary rate limit", retry_after_seconds=30),
            "SECONDARY_RATE_LIMITED",
        )

    def test_explicit_integration_scope_error_still_wins(self) -> None:
        self.assertEqual(
            classify_github_403("Resource not accessible by integration", retry_after_seconds=30),
            "SCOPE_DENIED",
        )


if __name__ == "__main__":
    unittest.main()
