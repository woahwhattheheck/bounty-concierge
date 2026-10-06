"""Focused issue-body submission-method policy checks."""
import json
import unittest

from concierge.bounty_qualification import qualify_dispatch
from concierge.repository_contribution_policy import (
    AUTOMATED_UPSTREAM_SUBMISSION,
    issue_body_prohibits_automated_submission,
)


def snapshot(body: str, method: str = AUTOMATED_UPSTREAM_SUBMISSION):
    return {
        "title": "$25 bounty",
        "body": body,
        "labels": [],
        "attempt_count": 0,
        "submission_method": method,
        "canonical_audit": {
            "issue_state": "open",
            "open_pr_count": 0,
            "stale_listing_signal": False,
            "search_truncated": False,
        },
    }


class IssueSubmissionPolicyTest(unittest.TestCase):
    def test_explicit_agent_prohibition_holds_automated_submission(self):
        body = "AI coding agents are forbidden to engage in this bounty."
        result = qualify_dispatch(snapshot(body))
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn("ISSUE_AUTOMATED_SUBMISSION_PROHIBITED", result["reason_codes"])
        self.assertTrue(result["signals"]["issue_automated_submission_prohibited"])
        self.assertNotIn(body, json.dumps(result))

    def test_fully_generated_pr_prohibition_is_recognized(self):
        self.assertTrue(
            issue_body_prohibits_automated_submission(
                "Fully AI-generated PRs are not accepted."
            )
        )

    def test_quoted_examples_and_code_are_not_normative(self):
        for body in (
            "> Fully AI-generated PRs are not accepted.",
            "Example: Fully AI-generated PRs are not accepted.",
            "Quoted text: AI coding agents are forbidden to engage.",
            "~~~text\nFully AI-generated PRs are not accepted.\n~~~",
        ):
            with self.subTest(body=body):
                self.assertFalse(issue_body_prohibits_automated_submission(body))

    def test_conditional_allowance_does_not_become_prohibition(self):
        self.assertFalse(
            issue_body_prohibits_automated_submission(
                "Fully AI-generated PRs are not accepted unless a human independently reviews them."
            )
        )

    def test_other_submission_methods_do_not_gain_issue_gate(self):
        result = qualify_dispatch(
            snapshot(
                "Fully AI-generated PRs are not accepted.",
                method="owner_fork_construction",
            )
        )
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertNotIn("ISSUE_AUTOMATED_SUBMISSION_PROHIBITED", result["reason_codes"])
        self.assertNotIn("issue_automated_submission_prohibited", result["signals"])


if __name__ == "__main__":
    unittest.main()
