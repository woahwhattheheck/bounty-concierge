# SPDX-License-Identifier: MIT
"""Focused live sponsor issue-carrier fence contract, with no GitHub traffic."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.github_sponsor_collision_gate import (
    SponsorCollisionPreflightError,
    publish_sponsor_issue_pr,
)

REPO = "test-owner/test-project"
ISSUE = 55
ACTOR = "woahwhattheheck"
ACTOR_ID = 293286387
HEAD = "a" * 40
API = "https://api.github.com"


class FakeResponse:
    def __init__(self, payload, status=200, headers=None, text=""):
        self.payload, self.status_code = payload, status
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, prs=(), *, archived=False, pull_status=200,
                 pull_headers=None, pull_text=""):
        self.urls = []
        self.prs, self.archived, self.pull_status = list(prs), archived, pull_status
        self.pull_headers, self.pull_text = pull_headers, pull_text

    def get(self, url, **kwargs):
        self.urls.append(url)
        assert kwargs["allow_redirects"] is False
        if url == f"{API}/user":
            return FakeResponse({"login": ACTOR, "id": ACTOR_ID})
        if url == f"{API}/repos/{REPO}":
            return FakeResponse({"full_name": REPO, "archived": self.archived})
        if url == f"{API}/repos/{REPO}/pulls?state=all&per_page=100&page=1":
            return FakeResponse(
                self.prs, status=self.pull_status,
                headers=self.pull_headers, text=self.pull_text,
            )
        raise AssertionError(f"unexpected GitHub API URL: {url}")


def sponsor_pr(num=3, *, body="Closes #55", title="fix", author="peer", head="b" * 40):
    return {
        "number": num,
        "html_url": f"https://github.com/{REPO}/pull/{num}",
        "title": title,
        "body": body,
        "user": {"login": author},
        "head": {"sha": head},
        "base": {"ref": "main"},
        "state": "open",
        "merged_at": None,
    }


class SponsorCollisionPublisherTest(unittest.TestCase):
    def publish(self, session, *, callback=None):
        calls = []

        def transport():
            calls.append("CREATE")
            return {"created": True}

        def controlled_gateway(*args, **kwargs):
            state = kwargs["provider_repository_state"]()
            if state["archived"]:
                return {"status": "PROVIDER_REPOSITORY_ARCHIVED"}
            self.assertEqual(kwargs["action"], "create-pull-request")
            self.assertTrue(callable(kwargs["provider_reconcile"]))
            kwargs["provider_reconcile"]()
            return kwargs["transport"]()

        with patch("concierge.github_sponsor_collision_gate.execute_publish_operation",
                   side_effect=controlled_gateway):
            result = publish_sponsor_issue_pr(
                "/tmp/non-mutating-test-ledger.json", "synthetic-placeholder",
                rail="original-user-token", actor=ACTOR, actor_id=ACTOR_ID,
                operation="TEST-ISSUE-55", repo=REPO, issue_number=ISSUE,
                carrier="original-source-branch", expected_head=HEAD,
                transport=transport, session=session, max_pages=2,
            )
        return result, calls

    def test_other_author_and_exact_original_head_are_collision_holds(self):
        for pr in (
            sponsor_pr(body="Fixes test-owner/test-project#55", author="peer"),
            sponsor_pr(body="", author=ACTOR, head=HEAD),
        ):
            with self.subTest(pr=pr):
                result, calls = self.publish(FakeSession([pr]))
                self.assertEqual(result["status"], "HOLD_EXISTING_ISSUE_CARRIER")
                self.assertEqual(result["matching_prs"][0]["number"], pr["number"])
                self.assertEqual(len(result["receipt_sha256"]), 64)
                self.assertFalse(result["provider_write_called"])
                self.assertEqual(calls, [])

    def test_clean_source_and_verified_archive(self):
        clean, write_calls = self.publish(FakeSession([sponsor_pr(body="Fixes #88")]))
        self.assertEqual(clean, {"created": True})
        self.assertEqual(write_calls, ["CREATE"])
        archived, archived_calls = self.publish(FakeSession(archived=True))
        self.assertEqual(archived["status"], "PROVIDER_REPOSITORY_ARCHIVED")
        self.assertEqual(archived_calls, [])

    def test_secondary_403_is_classified_without_publish(self):
        session = FakeSession(
            pull_status=403,
            pull_headers={"X-RateLimit-Remaining": "500"},
            pull_text="You have exceeded a secondary rate limit.",
        )
        with self.assertRaisesRegex(
            SponsorCollisionPreflightError, "SECONDARY_RATE_LIMITED"
        ):
            self.publish(session)
        self.assertEqual(
            session.urls[-1],
            f"{API}/repos/{REPO}/pulls?state=all&per_page=100&page=1",
        )

    def test_rate_error_fails_closed_before_transport(self):
        calls = []
        with patch(
            "concierge.github_sponsor_collision_gate.execute_publish_operation",
            side_effect=lambda *a, **k: (
                k["provider_repository_state"](),
                k["provider_reconcile"](),
                k["transport"](),
            ),
        ):
            with self.assertRaises(SponsorCollisionPreflightError):
                publish_sponsor_issue_pr(
                    "/tmp/non-mutating-test-ledger.json", "synthetic-placeholder",
                    rail="original-user-token", actor=ACTOR, actor_id=ACTOR_ID,
                    operation="TEST-ISSUE-55", repo=REPO, issue_number=ISSUE,
                    carrier="original-source-branch", expected_head=HEAD,
                    transport=lambda: calls.append("CREATE"),
                    session=FakeSession(pull_status=429),
                )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
