from unittest.mock import MagicMock, call, patch

import pytest

from concierge import payout_tracker


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _history_envelope(transactions, *, total, wallet="alice"):
    return {
        "ok": True,
        "miner_id": wallet,
        "transactions": transactions,
        "total": total,
    }


def _confirmed(index):
    return {
        "type": "transfer_out",
        "amount": 1,
        "to": f"wallet-{index}",
        "timestamp": 1000 - index,
    }


@patch("concierge.payout_tracker.requests.get")
def test_pending_reads_all_canonical_pages_before_certifying_state(mock_get):
    first = [_confirmed(index) for index in range(50)]
    middle = [_confirmed(index) for index in range(50, 200)]
    pending = {
        "type": "transfer_out",
        "amount": 3,
        "to": "bob",
        "timestamp": 799,
        "status": "pending",
    }
    mock_get.side_effect = [
        _response(_history_envelope(first, total=201)),
        _response(_history_envelope(middle, total=201)),
        _response(_history_envelope([pending], total=201)),
    ]

    assert payout_tracker.check_pending("alice", node_url="https://node") == [pending]
    assert mock_get.call_args_list == [
        call(
            "https://node/wallet/history",
            params={"miner_id": "alice"},
            timeout=15,
            verify=True,
        ),
        call(
            "https://node/wallet/history",
            params={"miner_id": "alice", "limit": 150, "offset": 50},
            timeout=15,
            verify=True,
        ),
        call(
            "https://node/wallet/history",
            params={"miner_id": "alice", "limit": 1, "offset": 200},
            timeout=15,
            verify=True,
        ),
    ]


@patch("concierge.payout_tracker.requests.get")
def test_pending_fails_closed_when_history_total_changes_between_pages(mock_get):
    mock_get.side_effect = [
        _response(_history_envelope([_confirmed(0)], total=2)),
        _response(_history_envelope([_confirmed(1)], total=3)),
    ]

    with pytest.raises(
        payout_tracker.PayoutLookupError,
        match="^history payout pagination changed during read$",
    ):
        payout_tracker.check_pending("alice", node_url="https://node")


@patch("concierge.payout_tracker.requests.get")
def test_pending_fails_closed_when_canonical_page_stalls(mock_get):
    mock_get.side_effect = [
        _response(_history_envelope([_confirmed(0)], total=2)),
        _response(_history_envelope([], total=2)),
    ]

    with pytest.raises(
        payout_tracker.PayoutLookupError,
        match="^history payout pagination was incomplete$",
    ):
        payout_tracker.check_pending("alice", node_url="https://node")


@patch("concierge.payout_tracker.requests.get")
def test_pending_fails_closed_when_history_exceeds_public_offset_contract(mock_get):
    mock_get.return_value = _response(
        _history_envelope([_confirmed(0)], total=10001)
    )

    with pytest.raises(
        payout_tracker.PayoutLookupError,
        match="^history payout pagination exceeds supported range$",
    ):
        payout_tracker.check_pending("alice", node_url="https://node")
    assert mock_get.call_count == 1


@patch("concierge.payout_tracker.requests.get")
def test_pending_preserves_legacy_single_response_compatibility(mock_get):
    pending = {"amount_rtc": 2, "status": "confirming"}
    mock_get.return_value = _response({"history": [pending]})

    assert payout_tracker.check_pending("alice", node_url="https://node") == [pending]
    assert mock_get.call_count == 1
