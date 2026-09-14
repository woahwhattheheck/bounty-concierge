from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

import requests

import concierge.bounty_live_status as live
from concierge.bounty_live_status import (
    inspect_live_status,
    is_clear_for_further_qualification,
    preflight_further_qualification,
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


class PublicSession:
    """The v2 public transport injection primitive; v3 must reject this kwarg."""

    def get(self, url, **kwargs):
        del url, kwargs
        return Response(issue())


def issue(*, repo="acme/widgets", number=17, state="open", updated_at=None):
    return {
        "number": number,
        "state": state,
        "updated_at": updated_at or "2026-09-13T13:00:00Z",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "title": "Bounty",
        "body": "Reward",
    }


class LiveStatusTests(unittest.TestCase):
    def setUp(self):
        self.old_clock = live._now_utc
        self.old_token = live.GITHUB_TOKEN
        self.old_get = live._github_get
        live._now_utc = lambda: datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc)
        live.GITHUB_TOKEN = ""
        self.calls = []

    def tearDown(self):
        live._now_utc = self.old_clock
        live.GITHUB_TOKEN = self.old_token
        live._github_get = self.old_get

    def install(self, *responses, error=None):
        queue = list(responses)

        def fake(url, *, headers):
            self.calls.append((url, deepcopy(headers)))
            if error is not None:
                raise error
            if not queue:
                raise AssertionError("unexpected provider request")
            return queue.pop(0)

        live._github_get = fake

    def test_open_live_issue_advances_only_in_atomic_preflight(self):
        self.install(Response(issue()))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17",
            {
                "source_name": "Opire",
                "advertised_state": "OPEN",
                "advertised_amount": "$1500",
                "solver_count": 0,
            },
        )
        receipt = decision["receipt"]
        self.assertEqual(decision["schema"], "bounty-live-qualification-decision/v1")
        self.assertTrue(decision["clear_for_further_qualification"])
        self.assertEqual(receipt["schema"], "bounty-live-status/v3")
        self.assertEqual(receipt["live"]["classification"], "OPEN")
        self.assertNotIn("clear_for_further_qualification", receipt["live"])
        self.assertTrue(receipt["authority"]["provider_response_code_owned"])
        self.assertFalse(receipt["authority"]["retained_receipt_is_qualification_authority"])
        self.assertFalse(decision["authority"]["decision_is_dispatch_authority"])
        self.assertFalse(decision["authority"]["decision_is_payment_or_revenue_authority"])
        self.assertTrue(verify_receipt(receipt))
        self.assertFalse(is_clear_for_further_qualification(receipt))
        self.assertEqual(len(self.calls), 1)

    def test_inspection_receipt_is_audit_only_even_when_open_and_fresh(self):
        self.install(Response(issue()))
        receipt = inspect_live_status("https://github.com/acme/widgets/issues/17")
        self.assertTrue(verify_receipt(receipt))
        self.assertEqual(receipt["live"]["classification"], "OPEN")
        self.assertFalse(is_clear_for_further_qualification(receipt))

    def test_fully_fabricated_correctly_resealed_open_receipt_never_advances(self):
        forged_unsigned = {
            "schema": "bounty-live-status/v3",
            "requested_issue_url": "https://github.com/acme/widgets/issues/17",
            "requested_identity": {"repo": "acme/widgets", "number": 17},
            "canonical_issue": {
                "repo": "acme/widgets",
                "number": 17,
                "url": "https://github.com/acme/widgets/issues/17",
            },
            "live": {
                "provider": "github",
                "classification": "OPEN",
                "reason_code": "GITHUB_ISSUE_OPEN",
                "issue_state": "open",
                "issue_updated_at": "2026-09-13T13:00:00Z",
                "repository_redirected": False,
                "verified_at": "2026-09-13T14:00:00Z",
                "fresh_until": "2026-09-13T14:05:00Z",
            },
            "discovery": {},
            "authority": {
                "provider_response_code_owned": True,
                "github_live_state_is_authoritative_at_capture": True,
                "retained_receipt_is_qualification_authority": False,
                "clear_requires_fresh_code_owned_acquisition": True,
                "discovery_state_is_authoritative": False,
                "discovery_amount_is_payout_proof": False,
                "discovery_solver_count_is_claim_authority": False,
                "clear_is_dispatch_authority": False,
                "next_gate_required": True,
                "network_fetches_discovery_source_url": False,
            },
        }
        forged = live._seal(forged_unsigned)
        self.assertTrue(verify_receipt(forged))
        self.assertFalse(is_clear_for_further_qualification(forged))
        self.assertEqual(self.calls, [])

    def test_public_injected_transport_is_rejected(self):
        with self.assertRaises(TypeError):
            inspect_live_status(
                "https://github.com/acme/widgets/issues/17", session=PublicSession()
            )
        with self.assertRaises(TypeError):
            preflight_further_qualification(
                "https://github.com/acme/widgets/issues/17", session=PublicSession()
            )
        self.assertEqual(self.calls, [])

    def test_each_positive_decision_requires_a_fresh_provider_request(self):
        self.install(Response(issue()), Response(issue(state="closed")))
        first = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        second = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertTrue(first["clear_for_further_qualification"])
        self.assertFalse(second["clear_for_further_qualification"])
        self.assertEqual(second["receipt"]["live"]["classification"], "CLOSED")
        self.assertEqual(len(self.calls), 2)

    def test_closed_github_overrides_stale_aggregator_open(self):
        self.install(Response(issue(state="closed")))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17",
            {"advertised_state": "OPEN", "advertised_amount": "$100"},
        )
        receipt = decision["receipt"]
        self.assertEqual(receipt["live"]["classification"], "CLOSED")
        self.assertFalse(decision["clear_for_further_qualification"])
        self.assertEqual(receipt["discovery"]["advertised_state"], "OPEN")

    def test_404_without_token_is_unverifiable_not_deleted(self):
        self.install(Response(None, status=404))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17", token=""
        )
        receipt = decision["receipt"]
        self.assertEqual(receipt["live"]["classification"], "UNVERIFIABLE")
        self.assertEqual(
            receipt["live"]["reason_code"], "GITHUB_NOT_FOUND_OR_INACCESSIBLE"
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_404_with_supplied_token_is_still_ambiguous(self):
        self.install(Response(None, status=404))
        receipt = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            token="scoped-but-unknown-access",
        )
        self.assertEqual(receipt["live"]["classification"], "UNVERIFIABLE")
        self.assertEqual(
            receipt["live"]["reason_code"], "GITHUB_NOT_FOUND_OR_INACCESSIBLE"
        )

    def test_410_is_terminal_gone_hold(self):
        self.install(Response(None, status=410))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        receipt = decision["receipt"]
        self.assertEqual(receipt["live"]["classification"], "DELETED_OR_MOVED")
        self.assertEqual(receipt["live"]["reason_code"], "GITHUB_GONE")
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_explicit_empty_token_suppresses_ambient_token(self):
        live.GITHUB_TOKEN = "ambient-secret"
        self.install(Response(issue()))
        inspect_live_status("https://github.com/acme/widgets/issues/17", token="")
        self.assertNotIn("Authorization", self.calls[0][1])

    def test_none_token_uses_ambient_token(self):
        live.GITHUB_TOKEN = "ambient-secret"
        self.install(Response(issue()))
        inspect_live_status("https://github.com/acme/widgets/issues/17", token=None)
        self.assertEqual(self.calls[0][1]["Authorization"], "Bearer ambient-secret")

    def test_repository_redirect_binds_canonical_identity(self):
        self.install(
            Response(
                issue(repo="neworg/widgets"),
                url="https://api.github.com/repos/neworg/widgets/issues/17",
            )
        )
        decision = preflight_further_qualification(
            "https://github.com/oldorg/widgets/issues/17"
        )
        receipt = decision["receipt"]
        self.assertTrue(decision["clear_for_further_qualification"])
        self.assertTrue(receipt["live"]["repository_redirected"])
        self.assertEqual(receipt["canonical_issue"]["repo"], "neworg/widgets")

    def test_redirect_to_different_issue_number_is_unverifiable(self):
        self.install(
            Response(
                issue(repo="acme/widgets", number=18),
                url="https://api.github.com/repos/acme/widgets/issues/18",
            )
        )
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"],
            "REDIRECT_ISSUE_NUMBER_CHANGED",
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_cross_host_redirect_is_unverifiable(self):
        self.install(
            Response(issue(), url="https://evil.example/repos/acme/widgets/issues/17")
        )
        receipt = inspect_live_status("https://github.com/acme/widgets/issues/17")
        self.assertEqual(
            receipt["live"]["reason_code"], "AMBIGUOUS_OR_CROSS_HOST_REDIRECT"
        )

    def test_redirect_query_is_unverifiable(self):
        self.install(
            Response(
                issue(),
                url="https://api.github.com/repos/acme/widgets/issues/17?shadow=1",
            )
        )
        receipt = inspect_live_status("https://github.com/acme/widgets/issues/17")
        self.assertEqual(
            receipt["live"]["reason_code"], "AMBIGUOUS_OR_CROSS_HOST_REDIRECT"
        )

    def test_payload_identity_conflict_is_unverifiable(self):
        self.install(Response(issue(repo="other/widgets")))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"],
            "CANONICAL_IDENTITY_CONFLICT",
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_pull_request_payload_cannot_be_misclassified_as_issue(self):
        payload = issue()
        payload["pull_request"] = {"url": "x"}
        self.install(Response(payload))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"],
            "TARGET_BECAME_PULL_REQUEST",
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_bad_json_fails_closed(self):
        self.install(Response(json_error=True))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"], "MALFORMED_GITHUB_JSON"
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_server_error_fails_closed(self):
        self.install(Response({}, status=503))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"], "GITHUB_HTTP_503"
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_request_error_fails_closed(self):
        self.install(error=requests.RequestException("offline"))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"], "GITHUB_REQUEST_FAILED"
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_discovery_source_url_is_provenance_only_and_never_fetched(self):
        self.install(Response(issue(state="closed")))
        receipt = inspect_live_status(
            "https://github.com/acme/widgets/issues/17",
            {
                "source_url": "https://aggregator.example/bounties/abc",
                "advertised_state": "OPEN",
            },
        )
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(receipt["authority"]["network_fetches_discovery_source_url"])
        self.assertEqual(receipt["live"]["classification"], "CLOSED")

    def test_bool_solver_count_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            inspect_live_status(
                "https://github.com/acme/widgets/issues/17", {"solver_count": True}
            )
        self.assertEqual(self.calls, [])

    def test_unknown_discovery_fields_are_rejected_before_network(self):
        with self.assertRaises(ValueError):
            inspect_live_status(
                "https://github.com/acme/widgets/issues/17", {"dispatch": True}
            )
        self.assertEqual(self.calls, [])

    def test_non_github_authority_url_is_rejected_without_network(self):
        with self.assertRaises(ValueError):
            inspect_live_status("https://opire.dev/bounties/17")
        self.assertEqual(self.calls, [])

    def test_pull_request_input_is_rejected_without_network(self):
        with self.assertRaises(ValueError):
            inspect_live_status("https://github.com/acme/widgets/pull/17")
        self.assertEqual(self.calls, [])

    def test_issue_input_query_is_rejected_without_network(self):
        with self.assertRaises(ValueError):
            inspect_live_status("https://github.com/acme/widgets/issues/17?state=open")
        self.assertEqual(self.calls, [])

    def test_api_github_issue_input_is_accepted(self):
        self.install(Response(issue()))
        receipt = inspect_live_status(
            "https://api.github.com/repos/acme/widgets/issues/17"
        )
        self.assertEqual(
            receipt["requested_issue_url"],
            "https://github.com/acme/widgets/issues/17",
        )

    def test_tampering_breaks_receipt_verification_but_never_changes_authority(self):
        self.install(Response(issue()))
        receipt = inspect_live_status("https://github.com/acme/widgets/issues/17")
        self.assertTrue(verify_receipt(receipt))
        receipt["live"]["classification"] = "CLOSED"
        self.assertFalse(verify_receipt(receipt))
        self.assertFalse(is_clear_for_further_qualification(receipt))

    def test_malformed_issue_updated_at_fails_closed(self):
        self.install(Response(issue(updated_at="not-a-time")))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"],
            "MALFORMED_ISSUE_IDENTITY_OR_STATE",
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_bool_http_status_fails_closed(self):
        response = Response(issue())
        response.status_code = True
        self.install(response)
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertEqual(
            decision["receipt"]["live"]["reason_code"], "MALFORMED_HTTP_RESPONSE"
        )
        self.assertFalse(decision["clear_for_further_qualification"])

    def test_receipt_freshness_fields_are_metadata_not_replay_authority(self):
        self.install(Response(issue()))
        receipt = inspect_live_status("https://github.com/acme/widgets/issues/17")
        self.assertEqual(receipt["live"]["verified_at"], "2026-09-13T14:00:00Z")
        self.assertEqual(receipt["live"]["fresh_until"], "2026-09-13T14:05:00Z")
        live._now_utc = lambda: datetime(2026, 9, 13, 14, 1, tzinfo=timezone.utc)
        self.assertTrue(verify_receipt(receipt))
        self.assertFalse(is_clear_for_further_qualification(receipt))

    def test_decision_object_is_not_a_receipt_or_durable_authority(self):
        self.install(Response(issue()))
        decision = preflight_further_qualification(
            "https://github.com/acme/widgets/issues/17"
        )
        self.assertTrue(decision["clear_for_further_qualification"])
        self.assertFalse(verify_receipt(decision))
        self.assertFalse(decision["authority"]["decision_is_durable_or_replayable"])
        self.assertTrue(decision["authority"]["next_gate_required"])


if __name__ == "__main__":
    unittest.main()
