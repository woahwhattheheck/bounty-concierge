from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

import requests

import concierge.bounty_live_status as live
from concierge.bounty_live_status import (
    inspect_live_status,
    is_clear_for_further_qualification,
    verify_receipt,
)


class Response:
    def __init__(
        self,
        payload=None,
        *,
        status=200,
        url="https://api.github.com/repos/acme/widgets/issues/17",
        json_error=False,
    ):
        self._payload = deepcopy(payload)
        self.status_code = status
        self.url = url
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("bad json")
        return deepcopy(self._payload)


class Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, deepcopy(kwargs)))
        if self.error is not None:
            raise self.error
        return self.response


def issue(*, repo="acme/widgets", number=17, state="open"):
    return {
        "number": number,
        "state": state,
        "updated_at": "2026-09-13T13:00:00Z",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "title": "Bounty",
        "body": "Reward",
    }


class LiveStatusTests(unittest.TestCase):
    def setUp(self):
        self.old_clock = live._now_utc
        self.old_token = live.GITHUB_TOKEN
        live._now_utc = lambda: datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc)
        live.GITHUB_TOKEN = ""

    def tearDown(self):
        live._now_utc = self.old_clock
        live.GITHUB_TOKEN = self.old_token

    def test_open_live_issue_advances_only_while_current(self):
        session = Session(Response(issue()))
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            {
                "source_name": "Opire",
                "advertised_state": "OPEN",
                "advertised_amount": "$1500",
                "solver_count": 0,
            },
            session=session,
        )
        self.assertEqual(result["schema"], "bounty-live-status/v2")
        self.assertEqual(result["live"]["classification"], "OPEN")
        self.assertTrue(result["live"]["snapshot_clear_for_further_qualification"])
        self.assertNotIn("clear_for_further_qualification", result["live"])
        self.assertFalse(result["authority"]["clear_is_dispatch_authority"])
        self.assertTrue(result["authority"]["clear_requires_currentness_check"])
        self.assertTrue(verify_receipt(result))
        self.assertTrue(is_clear_for_further_qualification(result))
        self.assertEqual(len(session.calls), 1)

    def test_open_snapshot_expires_without_refetch(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(issue())),
        )
        self.assertTrue(is_clear_for_further_qualification(result))
        live._now_utc = lambda: datetime(2026, 9, 13, 14, 5, 1, tzinfo=timezone.utc)
        self.assertTrue(verify_receipt(result))
        self.assertFalse(is_clear_for_further_qualification(result))

    def test_fresh_refetch_observes_later_close(self):
        opened = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(issue())),
        )
        self.assertTrue(is_clear_for_further_qualification(opened))
        live._now_utc = lambda: datetime(2026, 9, 13, 14, 1, tzinfo=timezone.utc)
        closed = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(issue(state="closed"))),
        )
        self.assertEqual(closed["live"]["classification"], "CLOSED")
        self.assertFalse(is_clear_for_further_qualification(closed))

    def test_future_verified_at_cannot_be_current_even_if_resealed(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(issue())),
        )
        unsigned = deepcopy(result)
        unsigned.pop("receipt_sha256")
        unsigned["live"]["verified_at"] = "2026-09-13T14:01:00Z"
        unsigned["live"]["fresh_until"] = "2026-09-13T14:06:00Z"
        forged = live._seal(unsigned)
        self.assertTrue(verify_receipt(forged))
        self.assertFalse(is_clear_for_further_qualification(forged))

    def test_extended_freshness_window_cannot_be_current_even_if_resealed(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(issue())),
        )
        unsigned = deepcopy(result)
        unsigned.pop("receipt_sha256")
        unsigned["live"]["fresh_until"] = "2026-09-13T16:00:00Z"
        forged = live._seal(unsigned)
        self.assertTrue(verify_receipt(forged))
        self.assertFalse(is_clear_for_further_qualification(forged))

    def test_closed_github_overrides_stale_aggregator_open(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            {"advertised_state": "OPEN", "advertised_amount": "$100"},
            session=Session(Response(issue(state="closed"))),
        )
        self.assertEqual(result["live"]["classification"], "CLOSED")
        self.assertFalse(result["live"]["snapshot_clear_for_further_qualification"])
        self.assertFalse(is_clear_for_further_qualification(result))
        self.assertEqual(result["discovery"]["advertised_state"], "OPEN")

    def test_404_without_token_is_unverifiable_not_deleted(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            token="",
            session=Session(Response(None, status=404)),
        )
        self.assertEqual(result["live"]["classification"], "UNVERIFIABLE")
        self.assertEqual(result["live"]["reason_code"], "GITHUB_NOT_FOUND_OR_INACCESSIBLE")
        self.assertFalse(is_clear_for_further_qualification(result))

    def test_404_with_supplied_token_is_still_ambiguous(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            token="scoped-but-unknown-access",
            session=Session(Response(None, status=404)),
        )
        self.assertEqual(result["live"]["classification"], "UNVERIFIABLE")
        self.assertEqual(result["live"]["reason_code"], "GITHUB_NOT_FOUND_OR_INACCESSIBLE")

    def test_410_is_terminal_gone_hold(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(None, status=410)),
        )
        self.assertEqual(result["live"]["classification"], "DELETED_OR_MOVED")
        self.assertEqual(result["live"]["reason_code"], "GITHUB_GONE")
        self.assertFalse(is_clear_for_further_qualification(result))

    def test_explicit_empty_token_suppresses_ambient_token(self):
        live.GITHUB_TOKEN = "ambient-secret"
        session = Session(Response(issue()))
        inspect_live_status(
            "https://github.com/acme/widgets/issues/17", token="", session=session
        )
        headers = session.calls[0][1]["headers"]
        self.assertNotIn("Authorization", headers)

    def test_none_token_uses_ambient_token(self):
        live.GITHUB_TOKEN = "ambient-secret"
        session = Session(Response(issue()))
        inspect_live_status(
            "https://github.com/acme/widgets/issues/17", token=None, session=session
        )
        headers = session.calls[0][1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer ambient-secret")

    def test_repository_redirect_binds_canonical_identity(self):
        response = Response(
            issue(repo="neworg/widgets"),
            url="https://api.github.com/repos/neworg/widgets/issues/17",
        )
        result = inspect_live_status(
            "https://github.com/oldorg/widgets/issues/17", session=Session(response)
        )
        self.assertEqual(result["live"]["classification"], "OPEN")
        self.assertTrue(result["live"]["repository_redirected"])
        self.assertEqual(result["canonical_issue"]["repo"], "neworg/widgets")
        self.assertTrue(is_clear_for_further_qualification(result))

    def test_redirect_to_different_issue_number_is_unverifiable(self):
        response = Response(
            issue(repo="acme/widgets", number=18),
            url="https://api.github.com/repos/acme/widgets/issues/18",
        )
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17", session=Session(response)
        )
        self.assertEqual(result["live"]["reason_code"], "REDIRECT_ISSUE_NUMBER_CHANGED")

    def test_cross_host_redirect_is_unverifiable(self):
        response = Response(issue(), url="https://evil.example/repos/acme/widgets/issues/17")
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17", session=Session(response)
        )
        self.assertEqual(result["live"]["reason_code"], "AMBIGUOUS_OR_CROSS_HOST_REDIRECT")

    def test_payload_identity_conflict_is_unverifiable(self):
        response = Response(issue(repo="other/widgets"))
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17", session=Session(response)
        )
        self.assertEqual(result["live"]["reason_code"], "CANONICAL_IDENTITY_CONFLICT")

    def test_pull_request_payload_cannot_be_misclassified_as_issue(self):
        payload = issue()
        payload["pull_request"] = {"url": "x"}
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17", session=Session(Response(payload))
        )
        self.assertEqual(result["live"]["reason_code"], "TARGET_BECAME_PULL_REQUEST")

    def test_bad_json_fails_closed(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(json_error=True)),
        )
        self.assertEqual(result["live"]["classification"], "UNVERIFIABLE")
        self.assertEqual(result["live"]["reason_code"], "MALFORMED_GITHUB_JSON")

    def test_server_error_fails_closed(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response({}, status=503)),
        )
        self.assertEqual(result["live"]["reason_code"], "GITHUB_HTTP_503")

    def test_request_error_fails_closed(self):
        session = Session(error=requests.RequestException("offline"))
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17", session=session
        )
        self.assertEqual(result["live"]["reason_code"], "GITHUB_REQUEST_FAILED")

    def test_discovery_source_url_is_provenance_only_and_never_fetched(self):
        session = Session(Response(issue(state="closed")))
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            {"source_url": "https://aggregator.example/bounties/abc", "advertised_state": "OPEN"},
            session=session,
        )
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(result["authority"]["network_fetches_discovery_source_url"])
        self.assertEqual(result["live"]["classification"], "CLOSED")

    def test_bool_solver_count_is_rejected(self):
        with self.assertRaises(ValueError):
            inspect_live_status(
                "https://github.com/acme/widgets/issues/17",
                {"solver_count": True},
                session=Session(Response(issue())),
            )

    def test_unknown_discovery_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            inspect_live_status(
                "https://github.com/acme/widgets/issues/17",
                {"dispatch": True},
                session=Session(Response(issue())),
            )

    def test_non_github_authority_url_is_rejected_without_network(self):
        session = Session(Response(issue()))
        with self.assertRaises(ValueError):
            inspect_live_status("https://opire.dev/bounties/17", session=session)
        self.assertEqual(session.calls, [])

    def test_pull_request_input_is_rejected_without_network(self):
        session = Session(Response(issue()))
        with self.assertRaises(ValueError):
            inspect_live_status("https://github.com/acme/widgets/pull/17", session=session)
        self.assertEqual(session.calls, [])

    def test_api_github_issue_input_is_accepted(self):
        result = inspect_live_status(
            "https://api.github.com/repos/acme/widgets/issues/17",
            session=Session(Response(issue())),
        )
        self.assertEqual(result["requested_issue_url"], "https://github.com/acme/widgets/issues/17")

    def test_tampering_breaks_receipt_verification(self):
        result = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            session=Session(Response(issue())),
        )
        self.assertTrue(verify_receipt(result))
        result["live"]["classification"] = "CLOSED"
        self.assertFalse(verify_receipt(result))
        self.assertFalse(is_clear_for_further_qualification(result))


if __name__ == "__main__":
    unittest.main()
