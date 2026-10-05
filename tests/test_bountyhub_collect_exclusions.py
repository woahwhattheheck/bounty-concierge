"""Bounded collection checks: explicit exclusions save reads, not evidence."""

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from contextlib import redirect_stdout, redirect_stderr

import requests
from requests.adapters import BaseAdapter

from concierge import bountyhub_catalog as catalog


EXCLUSIONS = {"EXAMPLE/PROJECT#1": {
    "reason": "existing team submission",
    "source_url": "https://github.com/example/project/pull/10",
    "observed_at": "2026-10-04T12:00:00Z",
}}


def listing(index, issue):
    return {
        "id": f"00000000-0000-0000-0000-{index:012d}",
        "repositoryFullName": "example/project", "issueNumber": issue,
        "htmlURL": f"https://github.com/example/project/issues/{issue}",
        "title": f"Example {issue}", "issueState": "open",
        "assignmentType": "open", "assignee": None,
        "claimed": False, "retracted": False, "solved": False,
        "isFrozen": False, "deletedAt": None, "totalAmount": "50.00",
        "pledges": [{"amount": "50.00", "paymentStatus": "PAID",
                     "retracted": False, "deletedAt": None, "isPaid": False}],
        "claims": [],
    }


class FixtureAdapter(BaseAdapter):
    """Exercise Requests preparation and collector accounting without a provider."""
    def __init__(self, rows, failure_id=None):
        self.rows = rows
        self.failure_id = failure_id
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append(request.url)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        path = urlsplit(request.url).path
        if path == "/api/bounties":
            payload = {"data": self.rows, "hasNextPage": False}
        else:
            identity = path.rsplit("/", 1)[-1]
            payload = next(row for row in self.rows if row["id"] == identity)
            if identity == self.failure_id:
                response.status_code = 429
                response.headers["Retry-After"] = "60"
                payload = {"error": "rate limited"}
        response._content = json.dumps(payload).encode()
        return response

    def close(self):
        pass


def fixture_session(rows, failure_id=None):
    session = requests.Session()
    adapter = FixtureAdapter(rows, failure_id)
    session.mount("https://api.bountyhub.dev/", adapter)
    return session, adapter


class CollectExclusionsTests(unittest.TestCase):
    def setUp(self):
        # Two independently funded cards share an already-covered issue.
        self.rows = [listing(1, 1), listing(2, 1), listing(3, 2), listing(4, 3)]

    def test_saved_calls_preserve_rows_and_reach_later_targets_with_same_budget(self):
        evidence = deepcopy(EXCLUSIONS)
        with fixture_session(self.rows)[0] as full_session:
            full = catalog.fetch_catalog(max_pages=1, max_details=4, session=full_session)
        expected = catalog.select_targets(full, excluded_issues=evidence)
        with fixture_session(self.rows)[0] as limited_session:
            limited = catalog.fetch_catalog(max_pages=1, max_details=2, session=limited_session)
        self.assertEqual(catalog.select_targets(limited, excluded_issues=evidence)["targets"], [])
        session, adapter = fixture_session(self.rows)
        with session:
            report = catalog.fetch_catalog(max_pages=1, max_details=2,
                                           excluded_issues=evidence, session=session)
        self.assertEqual((full["requests_made"], report["requests_made"]), (5, 3))
        self.assertEqual(len(adapter.calls), report["requests_made"])
        self.assertEqual(report["details_fetched"], 2)
        self.assertEqual(report["shortlist"]["targets"], expected["targets"])
        self.assertEqual(len(report["shortlist"]["targets"]), 2)
        self.assertTrue(report["shortlist"]["source_complete"])
        self.assertEqual(report["listing_count"], 4)
        self.assertEqual(report["detail_scope"], "exclude_supplied_issues")
        self.assertEqual(len(report["detail_exclusions"]), 2)
        self.assertEqual(report["shortlist"]["excluded_targets"][0]["listing_ids"],
                         [row["id"] for row in self.rows[:2]])
        for saved, skipped in zip(report["listings"], report["detail_exclusions"]):
            self.assertEqual(saved["funding_status"], "NOT_REQUESTED")
            self.assertIsNone(saved["reported_funded_usd"])
            self.assertEqual(skipped["observed_at"], evidence["EXAMPLE/PROJECT#1"]["observed_at"])
        self.assertEqual(evidence, EXCLUSIONS)
        self.assertNotIn("detail_scope", full)

    def test_unfiltered_selection_and_resume_do_not_inherit_a_hold(self):
        with fixture_session(self.rows)[0] as session:
            report = catalog.fetch_catalog(max_pages=1, max_details=2,
                                           excluded_issues=EXCLUSIONS, session=session)
        original = deepcopy(report)
        unfiltered = catalog.select_targets(report)
        self.assertFalse(unfiltered["source_complete"])
        self.assertEqual(unfiltered["unresolved_funding_count"], 2)
        session, adapter = fixture_session(self.rows)
        with session:
            resumed = catalog.resume_catalog(report, max_details=2, session=session)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(adapter.calls, [f"{catalog.API}/{row['id']}" for row in self.rows[:2]])
        self.assertTrue(resumed["shortlist"]["source_complete"])
        self.assertEqual(len(resumed["shortlist"]["targets"]), 3)
        self.assertEqual(resumed["catalog_observed_through"], report["completed_at"])
        self.assertEqual(resumed["listings"][2:], report["listings"][2:])
        self.assertNotIn("detail_scope", resumed)
        self.assertEqual(report, original)

    def test_rate_limit_and_zero_budget_keep_remaining_coverage_honest(self):
        session, adapter = fixture_session(self.rows, self.rows[2]["id"])
        with session:
            report = catalog.fetch_catalog(max_pages=1, max_details=2,
                                           excluded_issues=EXCLUSIONS, session=session)
        self.assertEqual(len(adapter.calls), 2)
        self.assertTrue(report["rate_limited"])
        self.assertEqual(report["retry_after_seconds"], 60)
        self.assertFalse(report["shortlist"]["source_complete"])
        self.assertEqual(report["listings"][3]["funding_status"], "NOT_ATTEMPTED")
        with fixture_session(self.rows)[0] as session:
            zero = catalog.fetch_catalog(max_pages=1, max_details=0,
                                         excluded_issues=EXCLUSIONS, session=session)
        self.assertEqual(zero["requests_made"], 1)
        self.assertFalse(zero["shortlist"]["source_complete"])
        with fixture_session(self.rows[:2])[0] as session:
            excluded_only = catalog.fetch_catalog(max_pages=1, max_details=0,
                                                  excluded_issues=EXCLUSIONS, session=session)
        self.assertTrue(excluded_only["shortlist"]["source_complete"])
        self.assertEqual(excluded_only["requests_made"], 1)
        self.assertEqual(len(excluded_only["detail_exclusions"]), 2)

    def test_cli_reuses_exclusion_validation_before_opening_session(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "exclude.json"
            output = Path(directory) / "catalog.json"
            evidence.write_text(json.dumps(EXCLUSIONS))
            session, adapter = fixture_session(self.rows)
            with patch.object(catalog.requests, "Session", return_value=session), redirect_stderr(io.StringIO()):
                code = catalog.main(["collect", "--max-pages", "1", "--max-details", "2",
                                     "--exclude-issues", str(evidence), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(len(adapter.calls), 3)
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = catalog.main(["targets", str(output), "--exclude-issues", str(evidence)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue()), {"candidates": [
                {"repo": "example/project", "number": 2},
                {"repo": "example/project", "number": 3},
            ]})
            # Repeated JSON keys must not silently replace retained evidence.
            raw = json.dumps(EXCLUSIONS)[1:-1]
            for invalid in ('{' + raw + ',' + raw + '}', '{"bad": {}}', 'null'):
                evidence.write_text(invalid)
                with patch.object(catalog.requests, "Session") as constructor, redirect_stderr(io.StringIO()):
                    self.assertEqual(catalog.main(["collect", "--exclude-issues", str(evidence)]), 2)
                    constructor.assert_not_called()
            with patch.object(catalog.requests, "Session") as constructor:
                with self.assertRaises(ValueError):
                    catalog.fetch_catalog(excluded_issues={"bad": {}})
                constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
