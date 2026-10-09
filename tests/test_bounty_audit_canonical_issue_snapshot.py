# SPDX-License-Identifier: MIT
"""Place in repository tests/; focused replay of renamed/empty issue observations."""
import json
import unittest
from concierge.bounty_audit import (
    BountyAuditError, CanonicalIssueSnapshotError, audit_bounty, audit_bounties,
)


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


class Session:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(url)
        if not self.payloads:
            raise AssertionError(f"unexpected GitHub call: {url}")
        return Response(self.payloads.pop(0))


class CanonicalIssues(unittest.TestCase):
    old = "https://api.github.com/repos/old/project/issues/183"

    def test_null_response_never_triggers_search(self):
        session = Session({"state": None, "html_url": None, "body": None})
        with self.assertRaisesRegex(CanonicalIssueSnapshotError, "incomplete"):
            audit_bounty("old/project", 183, session=session)
        self.assertEqual(session.calls, [self.old])

    def test_repo_redirect_alias_never_triggers_search(self):
        session = Session({"number": 183, "state": "closed", "html_url": "https://github.com/new/project/issues/183"})
        with self.assertRaisesRegex(CanonicalIssueSnapshotError, "resolve new/project#183"):
            audit_bounty("old/project", 183, session=session)
        self.assertEqual(session.calls, [self.old])

    def test_unavailable_alias_does_not_block_independent_rows(self):
        session = Session(
            {"state": None, "html_url": None},
            {"number": 1, "state": "open", "html_url": "https://github.com/good/project/issues/1"},
            {"incomplete_results": False, "total_count": 0, "items": []},
            [],
        )
        with self.assertRaises(BountyAuditError) as ctx:
            audit_bounties([
                {"repo": "old/project", "number": 183, "advertised": 10000},
                {"repo": "good/project", "number": 1, "advertised": 30},
            ], session=session)
        report = ctx.exception.partial_report
        self.assertEqual(report["audited_count"], 1)
        self.assertEqual(report["unavailable_count"], 1)
        self.assertEqual(report["unavailable_candidates"][0]["status"], "ISSUE_UNAVAILABLE")
        self.assertEqual(report["unavailable_candidates"][0]["candidate"]["advertised"], 10000)
        self.assertEqual(report["rows"][0]["canonical_audit"]["issue_state"], "open")
        self.assertEqual(sum("/search/issues" in u for u in session.calls), 1)


if __name__ == "__main__":
    unittest.main()
