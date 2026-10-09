"""Focused Superteam agent-feed operative-window gates (offline)."""
import json
import unittest
from datetime import datetime, timezone

from concierge.superteam_provider import (
    SuperteamProviderError,
    fetch_listing_details,
    fetch_live_opportunities,
    operative_window_open,
)


NOW = datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)


def listing(listing_id, deadline, *, winners=False):
    return {
        "id": listing_id,
        "slug": listing_id,
        "type": "bounty",
        "agentAccess": "AGENT_ALLOWED",
        "status": "OPEN",
        "title": "Provider status may be stale",
        "deadline": deadline,
        "isWinnersAnnounced": winners,
        "sponsor": {"name": "Verified sponsor", "isVerified": True},
    }


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.content = json.dumps(payload).encode("utf-8")

    def close(self):
        pass


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return FakeResponse(self.payload)


class SuperteamOperativeWindowTests(unittest.TestCase):
    def test_exact_deadline_and_announced_winners_are_terminal(self):
        self.assertFalse(operative_window_open("2026-10-09T16:00:00Z", False, as_of=NOW))
        self.assertFalse(operative_window_open("2026-10-09T16:00:01Z", True, as_of=NOW))
        self.assertTrue(operative_window_open("2026-10-09T16:00:01Z", False, as_of=NOW))

    def test_timezone_aware_deadline_and_as_of_required(self):
        self.assertFalse(operative_window_open("2026-10-09T11:59:00-04:00", False, as_of=NOW))
        with self.assertRaisesRegex(SuperteamProviderError, "as_of must be timezone-aware"):
            operative_window_open("2026-10-10T16:00:00Z", False, as_of=NOW.replace(tzinfo=None))

    def test_feed_filters_concluded_rows_and_reports_full_page_truthfully(self):
        live = [
            listing("upcoming", "2026-10-11T10:00:00Z"),
            listing("expired", "2026-10-09T15:59:59Z"),
            listing("winners", "2026-10-11T10:00:00Z", winners=True),
            listing("at-deadline", "2026-10-09T16:00:00Z"),
        ]
        session = FakeSession(live)
        result = fetch_live_opportunities(
            api_key="fixture", take=4, max_batches=1, as_of=NOW, session=session
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["excluded_closed"], 3)
        self.assertEqual([item["external_id"] for item in result["opportunities"]], ["upcoming"])
        self.assertTrue(result["truncated"])
        self.assertEqual(session.calls, 1)

    def test_details_refuses_stale_provider_open_flag(self):
        for row in (
            listing("winners", "2026-10-11T10:00:00Z", winners=True),
            listing("expired", "2026-10-09T15:59:59Z"),
        ):
            with self.subTest(row=row["slug"]):
                session = FakeSession({
                    **row, "isPrivate": False, "isPublished": True,
                    "description": "Sponsor description for this listing",
                })
                with self.assertRaisesRegex(SuperteamProviderError, "no longer open"):
                    fetch_listing_details(
                        row["slug"], api_key="fixture", as_of=NOW, session=session
                    )


if __name__ == "__main__":
    unittest.main()
