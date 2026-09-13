# SPDX-License-Identifier: MIT
"""Current RustChain /wallet/history envelope compatibility and fail-closed tests."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from concierge imporut_tracker


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.side_effect = (
        requests.HTTPError("bad status") if status_code >= 400 else None
    )
    return response


def test_history_accepts_current_rustchain_envelope():
    transactions = [
        {
            "type": "transfer_out",
            "amount": 1.25,
            "epoch": None,
            "timestamp": 1772849000,
            "tx_hash": "abc123",
            "to": "bobRTC",
            "status": "pending",
        },
        {
            "type": "reward",
            "amount": 0.5,
            "epoch": 201,
            "timestamp": 1772850000,
            "tx_hash": None,
        },
    ]
    payload = {
        "ok": True,
        "miner_id": "aliceRTC",
        "transactions": transactions,
        "total": 2,
    }

    with patch(
        "concierge.payout_tracker.requests.get",
        return_value=_response(payload=payload),
    ) as mock_get:
        assert payout_tracker.check_history(
            "aliceRTC", node_url="https://node/"
        ) == transactions

    mock_get.assert_called_once_with(
        "https://node/wallet/history",
        params={"miner_id": "aliceRTC"},
        timeout=15,
        verify=False,
    )


def test_history_accepts_current_empty_envelope():
    payload = {
        "ok": True,
        "miner_id": "aliceRTC",
        "transactions": [],
        "total": 0,
    }
    with patch(
        "concierge.payout_tracker.requests.get",
        return_value=_response(payload=payload),
    ):
        assert payout_tracker.check_history("aliceRTC", node_url="https://node") == []


@pytest.mark.parametrize(
    "payload",
    (
        {
            "ok": False,
            "miner_id": "aliceRTC",
            "transactions": [],
            "total": 0,
        },
        {
            "ok": True,
            "miner_id": "otherRTC",
            "transactions": [],
            "total": 0,
        },
        {
            "ok": True,
            "miner_id": "aliceRTC",
            "transactions": "not-a-list",
            "total": 0,
        },
        {
            "ok": True,
            "miner_id": "aliceRTC",
            "transactions": [{"amount": 1}, "corrupt-row"],
            "total": 2,
        },
        {
            "ok": True,
            "miner_id": "aliceRTC",
            "transactions": [{"amount": 1}],
            "total": 0,
        },
        {
            "ok": True,
            "miner_id": "aliceRTC",
            "transactions": [],
            "total": True,
        },
    ),
)
def test_history_canonical_envelope_fail_closes_on_ambiguous_metadata(payload):
    with patch(
        "concierge.payout_tracker.requests.get",
        return_value=_response(payload=payload),
    ):
        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^history payout response was malformed$",
        ):
            payout_tracker.check_history("aliceRTC", node_url="https://node")


def test_history_404_is_not_misreported_as_empty_current_history():
    with patch(
        "concierge.payout_tracker.requests.get",
        return_value=_response(status_code=404, payload={"error": "missing"}),
    ):
        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^history payout request failed$",
        ):
            payout_tracker.check_history("aliceRTC", node_url="https://node")


def test_legacy_history_shapes_remain_accepted():
    rows = [{"amount_rtc": 2, "from": "treasury", "to": "aliceRTC"}]
    with patch(
        "concierge.payout_tracker.requests.get",
        return_value=_response(payload={"history": rows}),
    ):
        assert payout_tracker.check_history("aliceRTC", node_url="https://node") == rows


def test_formatter_reads_current_history_amount_field():
    output = payout_tracker.format_payout_status(
        [],
        [
            {
                "type": "transfer_out",
                "amount": 1.25,
                "to": "bobRTC",
                "timestamp": 1772849000,
            }
        ],
    )

    assert "1.25 RTC" in output
    assert "? RTC" not in output
