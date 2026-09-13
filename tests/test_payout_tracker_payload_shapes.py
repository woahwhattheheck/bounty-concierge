# SPDX-License-Identifier: MIT
"""Payout reads fail closed when the node returns unsupported JSON shapes."""

from unittest.mock import MagicMock, patch

from concierge import payout_tracker


def _response(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


@patch("concierge.payout_tracker.requests.get")
def test_pending_scalar_payloads_return_empty_list(mock_get):
    for payload in (None, "pending", 7, True):
        mock_get.return_value = _response(payload)
        assert payout_tracker.check_pending("alice", node_url="https://node") == []


@patch("concierge.payout_tracker.requests.get")
def test_history_scalar_payloads_return_empty_list(mock_get):
    for payload in (None, "history", 7, True):
        mock_get.return_value = _response(payload)
        assert payout_tracker.check_history("alice", node_url="https://node") == []


@patch("concierge.payout_tracker.requests.get")
def test_wrapped_payload_requires_list_value(mock_get):
    for key, reader in (
        ("pending", payout_tracker.check_pending),
        ("history", payout_tracker.check_history),
    ):
        for value in (None, "oops", {"id": 1}, 7, True):
            mock_get.return_value = _response({key: value})
            assert reader("alice", node_url="https://node") == []


def test_payload_list_preserves_dict_records_and_drops_malformed_elements():
    valid = {"id": "valid"}

    assert payout_tracker._payload_list([valid, "bad", None, 7], "pending") == [valid]
    assert payout_tracker._payload_list(
        {"history": ["bad", valid, True]}, "history"
    ) == [valid]
