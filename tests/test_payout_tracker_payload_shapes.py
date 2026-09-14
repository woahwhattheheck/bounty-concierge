# SPDX-License-Identifier: MIT
"""Payout reads fail closed when the node returns unsupported JSON shapes."""

from unittest.mock import MagicMock, patch

import pytest

from concierge import payout_tracker


def _response(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


@patch("concierge.payout_tracker.requests.get")
def test_pending_scalar_payloads_fail_closed(mock_get):
    for payload in (None, "pending", 7, True):
        mock_get.return_value = _response(payload)
        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="history payout response was malformed",
        ):
            payout_tracker.check_pending("alice", node_url="https://node")


@patch("concierge.payout_tracker.requests.get")
def test_history_scalar_payloads_fail_closed(mock_get):
    for payload in (None, "history", 7, True):
        mock_get.return_value = _response(payload)
        with pytest.raises(payout_tracker.PayoutLookupError, match="history payout response was malformed"):
            payout_tracker.check_history("alice", node_url="https://node")


@patch("concierge.payout_tracker.requests.get")
def test_wrapped_payload_requires_list_value(mock_get):
    for reader in (payout_tracker.check_pending, payout_tracker.check_history):
        for value in (None, "oops", {"id": 1}, 7, True):
            mock_get.return_value = _response({"history": value})
            with pytest.raises(
                payout_tracker.PayoutLookupError,
                match="history payout response was malformed",
            ):
                reader("alice", node_url="https://node")


def test_payload_list_rejects_malformed_elements():
    valid = {"id": "valid"}

    with pytest.raises(payout_tracker.PayoutLookupError, match="pending payout response was malformed"):
        payout_tracker._payload_list([valid, "bad", None, 7], "pending")
    with pytest.raises(payout_tracker.PayoutLookupError, match="history payout response was malformed"):
        payout_tracker._payload_list({"history": ["bad", valid, True]}, "history")


def test_payload_list_preserves_supported_list_shapes():
    first = {"id": "first"}
    second = {"id": "second"}

    assert payout_tracker._payload_list([first, second], "pending") == [first, second]
    assert payout_tracker._payload_list({"history": [second, first]}, "history") == [second, first]
