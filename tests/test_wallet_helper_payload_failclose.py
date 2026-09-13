# SPDX-License-Identifier: MIT
"""Fail-closed tests for malformed wallet read payloads."""

import math
from unittest.mock import patch

from concierge import wallet_helper


def test_wallet_exists_requires_matching_finite_balance_payload():
    valid = {"miner_id": "alice", "balance_rtc": 0.0}
    with patch("concierge.wallet_helper._get", return_value=valid):
        assert wallet_helper.check_wallet_exists("alice") is True

    malformed = [
        {},
        [],
        None,
        {"miner_id": "bob", "balance_rtc": 1.0},
        {"miner_id": "alice"},
        {"miner_id": "alice", "balance_rtc": "1"},
        {"miner_id": "alice", "balance_rtc": True},
        {"miner_id": "alice", "balance_rtc": math.nan},
        {"miner_id": "alice", "balance_rtc": math.inf},
    ]
    for payload in malformed:
        with patch("concierge.wallet_helper._get", return_value=payload):
            assert wallet_helper.check_wallet_exists("alice") is False


def test_get_all_holders_rejects_malformed_remote_shapes():
    malformed = [
        [],
        {},
        {"balances": 7},
        {"balances": ["oops"]},
        {"balances": [{"miner_id": 7, "amount_rtc": 1.0}]},
        {"balances": [{"miner_id": "alice", "amount_rtc": "1"}]},
        {"balances": [{"miner_id": "alice", "amount_rtc": True}]},
        {"balances": [{"miner_id": "alice", "amount_rtc": math.nan}]},
        {"balances": [{"miner_id": "alice", "amount_rtc": math.inf}]},
    ]
    for payload in malformed:
        with patch("concierge.wallet_helper._get", return_value=payload):
            result = wallet_helper.get_all_holders(admin_key="key")
        assert result == {"error": "Node returned malformed holder balance data"}


def test_get_all_holders_keeps_valid_empty_and_sorted_payloads():
    with patch(
        "concierge.wallet_helper._get",
        return_value={"balances": []},
    ):
        assert wallet_helper.get_all_holders(admin_key="key") == []

    payload = {
        "balances": [
            {"miner_id": "low", "amount_rtc": 1},
            {"miner_id": None, "amount_rtc": 500},
            {"miner_id": "high", "amount_rtc": 2.5},
        ]
    }
    with patch("concierge.wallet_helper._get", return_value=payload):
        result = wallet_helper.get_all_holders(admin_key="key")
    assert [row["miner_id"] for row in result] == ["high", "low"]
    assert [row["amount_rtc"] for row in result] == [2.5, 1]


def test_holder_stats_propagates_malformed_holder_payload_error():
    with patch(
        "concierge.wallet_helper._get",
        return_value={"balances": "bad"},
    ):
        result = wallet_helper.get_holder_stats(admin_key="key")
    assert result == {"error": "Node returned malformed holder balance data"}
