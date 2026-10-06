"""Offline resume plans avoid repeating completed BountyHub detail reads."""
import copy
import json
import unittest
from unittest.mock import patch

import requests

from concierge import bountyhub_catalog as catalog
from concierge import bountyhub_refresh as refresh


class Feed:
    def __init__(self):
        self.rows = []
        for number in range(1, 4):
            amount = "20.00"
            self.rows.append({
                "id": f"00000000-0000-0000-0000-{number:012d}",
                "repositoryFullName": "example/project",
                "issueNumber": number,
                "htmlURL": f"https://github.com/example/project/issues/{number}",
                "title": f"Work {number}",
                "issueState": "open",
                "assignmentType": "OPEN",
                "assignee": None,
                "claimed": False,
                "retracted": False,
                "solved": False,
                "isFrozen": False,
                "deletedAt": None,
                "totalAmount": amount,
                "claims": [],
                "pledges": [{
                    "amount": amount,
                    "retracted": False,
                    "deletedAt": None,
                    "paymentStatus": "PAID",
                    "isPaid": False,
                }],
            })

    def get(self, url, params=None, timeout=None, allow_redirects=False):
        response = requests.Response()
        response._content_consumed = True
        if url == catalog.API:
            response.status_code = 200
            response._content = json.dumps({
                "data": self.rows,
                "hasNextPage": False,
            }).encode()
            return response

        row = next(row for row in self.rows if url == f"{catalog.API}/{row['id']}")
        if row["issueNumber"] == 2:
            response.status_code = 429
            response.headers["Retry-After"] = "60"
            response._content = b'{}'
            return response

        response.status_code = 200
        response._content = json.dumps(row).encode()
        return response

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class RefreshResumePlanTests(unittest.TestCase):
    def setUp(self):
        self.feed = Feed()
        self.clock = patch.object(catalog, "_now", return_value="2026-10-04T12:00:00+00:00")
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.snapshot = catalog.fetch_catalog(
            session=self.feed,
            max_pages=1,
            max_details=0,
            page_size=100,
            minimum_total_usd="15.00",
        )
        self.ids = [row["listing_id"] for row in self.snapshot["listings"]]
        self.previous = refresh.refresh_listings(
            self.snapshot,
            self.ids,
            max_requests=3,
            session=self.feed,
        )

    def test_partial_plan_skips_completed_rows_and_preserves_order(self):
        plan = refresh.plan_refresh_resume(
            self.snapshot,
            [self.ids[2], self.ids[0], self.ids[1], self.ids[0]],
            self.previous,
        )

        self.assertEqual(plan["requested_listing_ids"], [self.ids[2], self.ids[0], self.ids[1]])
        self.assertEqual(plan["completed_listing_ids"], [self.ids[0]])
        self.assertEqual(plan["pending_listing_ids"], [self.ids[2], self.ids[1]])
        self.assertEqual(plan["requests_avoided"], 1)
        self.assertEqual(plan["cooldown_remaining_seconds"], 60)
        self.assertTrue(plan["cooldown_known"])
        self.assertFalse(plan["ready_for_requests"])
        self.assertEqual(plan["network_requests"], 0)

    def test_elapsed_cooldown_makes_pending_plan_ready(self):
        with patch.object(catalog, "_now", return_value="2026-10-04T12:01:01+00:00"):
            plan = refresh.plan_refresh_resume(self.snapshot, self.ids, self.previous)

        self.assertEqual(plan["pending_listing_ids"], self.ids[1:])
        self.assertEqual(plan["cooldown_remaining_seconds"], 0)
        self.assertTrue(plan["ready_for_requests"])
        self.assertFalse(plan["resume_complete"])

    def test_unknown_rate_limit_fails_closed_and_source_mismatch_is_rejected(self):
        unknown = copy.deepcopy(self.previous)
        unknown["retry_after_seconds"] = None
        plan = refresh.plan_refresh_resume(self.snapshot, self.ids, unknown)
        self.assertFalse(plan["cooldown_known"])
        self.assertIsNone(plan["cooldown_remaining_seconds"])
        self.assertFalse(plan["ready_for_requests"])

        with patch.object(self.feed, "get", wraps=self.feed.get) as transport:
            with self.assertRaisesRegex(ValueError, "duration is unknown"):
                refresh.refresh_listings(
                    self.snapshot, self.ids,
                    previous_refresh=unknown, max_requests=3, session=self.feed,
                )
            transport.assert_not_called()

        wrong_source = copy.deepcopy(self.previous)
        wrong_source["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "same retained catalog"):
            refresh.plan_refresh_resume(self.snapshot, self.ids, wrong_source)

    def test_zero_remaining_403_uses_reset_window(self):
        original_get = self.feed.get

        def quota_exhausted(url, params=None, timeout=None, allow_redirects=False):
            if url == f"{catalog.API}/{self.ids[0]}":
                response = requests.Response()
                response._content_consumed = True
                response.status_code = 403
                response.headers["X-RateLimit-Remaining"] = "0"
                response.headers["X-RateLimit-Reset"] = "4102444800"
                response._content = b"{}"
                return response
            return original_get(
                url, params=params, timeout=timeout, allow_redirects=allow_redirects,
            )

        with patch.object(self.feed, "get", side_effect=quota_exhausted) as transport:
            result = refresh.refresh_listings(
                self.snapshot, self.ids, max_requests=3, session=self.feed,
            )

        self.assertTrue(result["rate_limited"])
        self.assertGreater(result["retry_after_seconds"], 0)
        self.assertEqual(result["requests_made"], 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["records"][0]["status"], "READ_FAILED")
        self.assertEqual(result["records"][1]["status"], "NOT_ATTEMPTED")

    def test_zero_remaining_403_without_reset_fails_next_refresh_closed(self):
        original_get = self.feed.get

        def quota_exhausted(url, params=None, timeout=None, allow_redirects=False):
            if url == f"{catalog.API}/{self.ids[0]}":
                response = requests.Response()
                response._content_consumed = True
                response.status_code = 403
                response.headers["X-RateLimit-Remaining"] = "0"
                response._content = b"{}"
                return response
            return original_get(
                url, params=params, timeout=timeout, allow_redirects=allow_redirects,
            )

        with patch.object(self.feed, "get", side_effect=quota_exhausted):
            previous = refresh.refresh_listings(
                self.snapshot, self.ids, max_requests=3, session=self.feed,
            )

        self.assertTrue(previous["rate_limited"])
        self.assertIsNone(previous["retry_after_seconds"])
        with patch.object(self.feed, "get", wraps=original_get) as transport:
            with self.assertRaisesRegex(ValueError, "duration is unknown"):
                refresh.refresh_listings(
                    self.snapshot,
                    self.ids,
                    previous_refresh=previous,
                    max_requests=3,
                    session=self.feed,
                )
            transport.assert_not_called()

    def test_live_refresh_rejects_previous_record_identity_drift_before_transport(self):
        drifted = copy.deepcopy(self.previous)
        # Keep the receipt internally self-consistent so the shared receipt
        # validator passes; only the retained-catalog identity should reject it.
        drifted["records"][0]["repo"] = "other/project"
        drifted["records"][0]["listing"]["repo"] = "other/project"

        with patch.object(self.feed, "get", wraps=self.feed.get) as transport:
            with self.assertRaisesRegex(
                    ValueError, "previous refresh record disagrees with the retained catalog"):
                refresh.refresh_listings(
                    self.snapshot, self.ids,
                    previous_refresh=drifted, max_requests=3, session=self.feed,
                )
            transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
