"""Bounded floor changes reuse a retained catalog, not a new catalog observation."""
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from concierge import bountyhub_catalog as catalog


class Feed:
    """Controlled transport; actual Requests responses and catalog reducers run."""
    def __init__(self, promised=False):
        self.calls = []
        self.rows = []
        for number, amount in enumerate(("100.00", "20.00", "15.00", "5.00"), 1):
            self.rows.append({
                "id": f"00000000-0000-0000-0000-{number:012d}",
                "repositoryFullName": "example/project", "issueNumber": number,
                "htmlURL": f"https://github.com/example/project/issues/{number}",
                "title": f"Work {number}", "issueState": "open", "assignmentType": "OPEN",
                "assignee": None, "claimed": False, "retracted": False,
                "solved": False, "isFrozen": False, "deletedAt": None,
                "totalAmount": amount, "claims": [],
                "pledges": [{"amount": amount, "retracted": False, "deletedAt": None,
                             "paymentStatus": "PROMISED" if promised else "PAID", "isPaid": False}],
            })

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url == catalog.API:
            start = (params["page"] - 1) * params["limit"]
            end = start + params["limit"]
            body = {"data": self.rows[start:end], "hasNextPage": end < len(self.rows)}
        else:
            body = next(row for row in self.rows if url == f"{catalog.API}/{row['id']}")
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        response._content_consumed = True
        return response

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def retained(feed, include_promised=False):
    return catalog.fetch_catalog(session=feed, max_pages=2, page_size=2,
                                 minimum_total_usd="50.00", include_promised=include_promised)


class ResumeFloorTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(catalog, "_now", return_value="2026-10-04T12:00:00+00:00")
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_lower_floor_reuses_pages_and_complete_detail(self):
        feed = Feed()
        source = retained(feed)
        before = copy.deepcopy(source)
        feed.calls.clear()
        result = catalog.resume_catalog(source, minimum_total_usd="15", max_details=2, session=feed)
        fresh = Feed()
        expected = catalog.fetch_catalog(session=fresh, max_pages=2, page_size=2,
                                         minimum_total_usd="15.00")
        self.assertEqual(len(feed.calls), 2)
        self.assertEqual(len(fresh.calls), 5)
        self.assertTrue(all(params is None for _, params in feed.calls))
        self.assertEqual(result["shortlist"]["targets"], expected["shortlist"]["targets"])
        self.assertEqual(source, before)
        self.assertEqual(result["catalog_observed_through"], source["completed_at"])
        self.assertEqual(result["resume"]["source_minimum_total_usd"], "50.00")
        self.assertEqual(result["resume"]["minimum_total_usd"], "15.00")
        self.assertTrue(result["resume"]["floor_changed"])
        self.assertTrue(result["complete"])

    def test_budget_zero_and_default_followthrough(self):
        feed = Feed()
        source = retained(feed)
        feed.calls.clear()
        unchanged = catalog.resume_catalog(source, session=feed)
        self.assertEqual(unchanged["minimum_total_usd"], "50.00")
        zero = catalog.resume_catalog(source, minimum_total_usd="15.00", max_details=0, session=feed)
        self.assertEqual(feed.calls, [])
        self.assertFalse(zero["complete"])
        self.assertEqual(zero["completed_at"], source["completed_at"])
        partial = catalog.resume_catalog(zero, max_details=1, session=feed)
        self.assertEqual(len(feed.calls), 1)
        self.assertFalse(partial["complete"])
        complete = catalog.resume_catalog(partial, max_details=1, session=feed)
        self.assertEqual(len(feed.calls), 2)
        self.assertEqual(complete["minimum_total_usd"], "15.00")
        self.assertTrue(complete["complete"])

    def test_reward_basis_and_pre_io_guards(self):
        feed = Feed(promised=True)
        source = retained(feed, include_promised=True)
        result = catalog.resume_catalog(source, minimum_total_usd="15.00", session=feed)
        self.assertEqual(result["shortlist"]["reward_basis"], "reported_funded_plus_promised")
        self.assertEqual(len(result["shortlist"]["targets"]), 3)
        self.assertEqual(result["listings"][1]["reported_funded_usd"], "0.00")
        funded_only = copy.deepcopy(source)
        funded_only["shortlist"] = catalog.select_targets(source, "50.00")
        self.assertEqual(catalog.resume_catalog(funded_only, minimum_total_usd="15.00",
                                               session=feed)["shortlist"]["targets"], [])
        feed.calls.clear()
        with self.assertRaises(ValueError):
            catalog.resume_catalog(source, minimum_total_usd=15.0, session=feed)
        source.update(rate_limited=True, retry_after_seconds=3600)
        with self.assertRaisesRegex(ValueError, "cooldown"):
            catalog.resume_catalog(source, minimum_total_usd="15.00", session=feed)
        zero = catalog.resume_catalog(source, minimum_total_usd="15.00", max_details=0, session=feed)
        self.assertEqual(zero["retry_after_seconds"], 3600)
        self.assertTrue(zero["rate_limited"])
        self.assertEqual(feed.calls, [])

    def test_cli_alias_and_partial_exit(self):
        feed = Feed()
        source = retained(feed)
        feed.calls.clear()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(catalog.requests, "Session", return_value=feed), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = catalog.main(["resume", str(path), "--min-reward-usd", "15.00",
                                       "--max-details", "1"])
            result = json.loads(output.getvalue())
            self.assertEqual(status, 2)
            self.assertEqual(len(feed.calls), 1)
            self.assertEqual(result["minimum_total_usd"], "15.00")
            self.assertIn("PARTIAL", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
