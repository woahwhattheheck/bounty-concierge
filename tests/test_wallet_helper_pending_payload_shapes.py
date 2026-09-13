# SPDX-License-Identifier: MIT
"""Fail-closed coverage for pending-transfer payload shapes."""

from unittest.mock import patch

from concierge import wallet_helper


def test_pending_transfers_accepts_supported_list_shapes():
    supported = [
        ([], []),
        ([{"pending_id": 1}], [{"pending_id": 1}]),
        ({"pending": []}, []),
        ({"pending": [{"pending_id": 2}]}, [{"pending_id": 2}]),
    ]

    for payload, expected in supported:
        with patch("concierge.wallet_helper._get", return_value=payload):
            assert wallet_helper.get_pending_transfers("alice") == expected


def test_pending_transfers_rejects_malformed_remote_shapes():
    malformed = [
        None,
        7,
        "pending",
        {"error": "unavailable"},
        {"pending": None},
        {"pending": "not-a-list"},
        {"pending": {}},
        {"pending": ["not-an-object"]},
        {"pending": [{"pending_id": 1}, "not-an-object"]},
        ["not-an-object"],
        [{"pending_id": 1}, 2],
    ]

    for payload in malformed:
        with patch("concierge.wallet_helper._get", return_value=payload):
            assert wallet_helper.get_pending_transfers("alice") == []
