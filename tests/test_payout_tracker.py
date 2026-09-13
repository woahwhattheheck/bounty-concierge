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


class TestCheckPending:
    @patch("concierge.payout_tracker.requests.get")
    def test_pending_accepts_list_payload(self, mock_get):
        mock_get.return_value = _response(payload=[{"amount_rtc": 2}])

        result = payout_tracker.check_pending("alice", node_url="https://node/")

        assert result == [{"amount_rtc": 2}]
        mock_get.assert_called_once_with(
            "https://node/wallet/pending",
            params={"miner_id": "alice"},
            timeout=15,
            verify=False,
        )

    @patch("concierge.payout_tracker.requests.get")
    def test_pending_accepts_wrapped_payload(self, mock_get):
        mock_get.return_value = _response(payload={"pending": [{"id": "p1"}]})

        assert payout_tracker.check_pending("alice", node_url="https://node") == [
            {"id": "p1"}
        ]

    @patch("concierge.payout_tracker.requests.get")
    def test_pending_404_returns_empty_list(self, mock_get):
        mock_get.return_value = _response(status_code=404, payload={"error": "missing"})

        assert payout_tracker.check_pending("alice", node_url="https://node") == []

    @patch("concierge.payout_tracker.requests.get")
    def test_pending_network_error_is_not_reported_as_empty(self, mock_get):
        mock_get.side_effect = requests.RequestException("secret upstream detail")

        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^pending payout request failed$",
        ):
            payout_tracker.check_pending("alice", node_url="https://node")

    @patch("concierge.payout_tracker.requests.get")
    def test_pending_invalid_json_is_not_reported_as_empty(self, mock_get):
        response = _response(payload=[])
        response.json.side_effect = ValueError("raw response bytes")
        mock_get.return_value = response

        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^pending payout response was not valid JSON$",
        ):
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
            verify=False,
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

    def test_missing_fields_use_question_mark_placeholders(self):
        output = payout_tracker.format_payout_status([{}], [{}])

        assert "? RTC" in output
        assert "? -> ?" in output
