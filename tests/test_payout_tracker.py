# SPDX-License-Identifier: MIT
"""Unit tests for payout_tracker network handling and formatting."""

import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import payout_tracker


def _response(status_code=200, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload if payload is not None else []
    resp.raise_for_status.side_effect = (
        requests.HTTPError("bad status") if status_code >= 400 else None
    )
    return resp


def _history_envelope(transactions, wallet="alice", total=None):
    return {
        "ok": True,
        "miner_id": wallet,
        "transactions": transactions,
        "total": len(transactions) if total is None else total,
    }


class TestCheckPending:
    @patch("concierge.payout_tracker.requests.get")
    def test_pending_derives_from_canonical_history(self, mock_get):
        pending = {
            "type": "transfer_out",
            "amount": 2.0,
            "to": "bob",
            "timestamp": 123,
            "status": "pending",
        }
        confirmed = {
            "type": "transfer_out",
            "amount": 1.0,
            "to": "carol",
            "timestamp": 122,
        }
        reward = {"type": "reward", "amount": 0.5, "timestamp": 121}
        mock_get.return_value = _response(
            payload=_history_envelope([pending, confirmed, reward])
        )

        result = payout_tracker.check_pending("alice", node_url="https://node/")

        assert result == [pending]
        mock_get.assert_called_once_with(
            "https://node/wallet/history",
            params={"miner_id": "alice"},
            timeout=15,
            verify=True,
        )

    @patch("concierge.payout_tracker.requests.get")
    def test_pending_accepts_legacy_history_wrapper(self, mock_get):
        pending = {"amount_rtc": 2, "status": "pending"}
        failed = {"amount_rtc": 1, "status": "failed"}
        mock_get.return_value = _response(payload={"history": [pending, failed]})

        assert payout_tracker.check_pending("alice", node_url="https://node") == [pending]

    @patch("concierge.payout_tracker.requests.get")
    def test_confirming_is_still_in_flight(self, mock_get):
        confirming = {
            "type": "transfer_out",
            "amount": 2,
            "to": "bob",
            "status": "confirming",
        }
        mock_get.return_value = _response(payload=_history_envelope([confirming]))

        assert payout_tracker.check_pending("alice", node_url="https://node") == [
            confirming
        ]

    @patch("concierge.payout_tracker.requests.get")
    def test_missing_history_endpoint_is_not_reported_as_no_pending(self, mock_get):
        mock_get.return_value = _response(status_code=404, payload={"error": "missing"})

        with pytest.raises(payout_tracker.PayoutLookupError):
            payout_tracker.check_pending("alice", node_url="https://node")

    @patch("concierge.payout_tracker.requests.get")
    def test_unknown_in_flight_status_fails_closed(self, mock_get):
        mock_get.return_value = _response(
            payload=_history_envelope(
                [
                    {
                        "type": "transfer_out",
                        "amount": 2,
                        "to": "bob",
                        "status": "future-state",
                    }
                ]
            )
        )

        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^pending payout state was malformed$",
        ):
            payout_tracker.check_pending("alice", node_url="https://node")

    @patch("concierge.payout_tracker.requests.get")
    def test_status_on_non_transfer_row_fails_closed(self, mock_get):
        mock_get.return_value = _response(
            payload=_history_envelope(
                [{"type": "reward", "amount": 2, "status": "pending"}]
            )
        )

        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^pending payout state was malformed$",
        ):
            payout_tracker.check_pending("alice", node_url="https://node")

    @patch("concierge.payout_tracker.requests.get")
    def test_pending_network_error_is_not_reported_as_empty(self, mock_get):
        mock_get.side_effect = requests.RequestException("secret upstream detail")

        with pytest.raises(payout_tracker.PayoutLookupError):
            payout_tracker.check_pending("alice", node_url="https://node")

    @patch("concierge.payout_tracker.requests.get")
    def test_pending_invalid_json_is_not_reported_as_empty(self, mock_get):
        response = _response(payload=[])
        response.json.side_effect = ValueError("raw response bytes")
        mock_get.return_value = response

        with pytest.raises(payout_tracker.PayoutLookupError):
            payout_tracker.check_pending("alice", node_url="https://node")


class TestCheckHistory:
    @patch("concierge.payout_tracker.requests.get")
    def test_history_accepts_wrapped_payload(self, mock_get):
        mock_get.return_value = _response(payload={"history": [{"tx": "abc"}]})

        result = payout_tracker.check_history("alice", node_url="https://node")

        assert result == [{"tx": "abc"}]
        mock_get.assert_called_once_with(
            "https://node/wallet/history",
            params={"miner_id": "alice"},
            timeout=15,
            verify=True,
        )

    @patch("concierge.payout_tracker.requests.get")
    def test_history_unexpected_dict_is_not_reported_as_empty(self, mock_get):
        mock_get.return_value = _response(payload={"status": "ok"})

        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^history payout response was malformed$",
        ):
            payout_tracker.check_history("alice", node_url="https://node")

    @patch("concierge.payout_tracker.requests.get")
    def test_history_mixed_rows_are_not_silently_filtered(self, mock_get):
        mock_get.return_value = _response(
            payload={"history": [{"tx": "abc"}, "corrupt-row"]}
        )

        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^history payout response was malformed$",
        ):
            payout_tracker.check_history("alice", node_url="https://node")

    @patch("concierge.payout_tracker.requests.get")
    def test_history_http_error_is_not_reported_as_empty(self, mock_get):
        mock_get.return_value = _response(status_code=500, payload={"error": "boom"})

        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^history payout request failed$",
        ):
            payout_tracker.check_history("alice", node_url="https://node")


class TestFormatPayoutStatus:
    def test_empty_pending_and_history_show_none_markers(self):
        output = payout_tracker.format_payout_status([], [])

        assert "-- Pending Transfers --" in output
        assert "-- Recent History --" in output
        assert output.count("(none)") == 2

    def test_formats_pending_and_history_items_with_optional_timestamps(self):
        output = payout_tracker.format_payout_status(
            [{"amount_rtc": 3.5, "memo": "bounty", "created_at": "2026-05-12"}],
            [
                {
                    "amount_rtc": 2,
                    "from": "treasury",
                    "to": "alice",
                    "timestamp": "2026-05-13",
                }
            ],
        )

        assert "3.5 RTC  memo: bounty  (2026-05-12)" in output
        assert "2 RTC  treasury -> alice  (2026-05-13)" in output

    def test_formats_canonical_pending_row(self):
        output = payout_tracker.format_payout_status(
            [
                {
                    "type": "transfer_out",
                    "amount": 3.5,
                    "to": "alice",
                    "timestamp": 123,
                    "status": "pending",
                }
            ],
            [],
        )

        assert "3.5 RTC  -> alice  [pending]  (123)" in output

    def test_missing_fields_use_question_mark_placeholders(self):
        output = payout_tracker.format_payout_status([{}], [{}])

        assert "? RTC" in output
        assert "? -> ?" in output
