# SPDX-License-Identifier: MIT
"""Fail closed on ambiguous payout-status wallet identifiers."""

from unittest.mock import MagicMock, patch

import pytest

from concierge import payout_tracker


class WalletIdSubclass(str):
    """A hostile string subtype must not cross the request boundary."""


@pytest.mark.parametrize(
    "checker",
    (payout_tracker.check_pending, payout_tracker.check_history),
)
@pytest.mark.parametrize(
    "wallet_id",
    (
        None,
        7,
        "",
        " ",
        " alice",
        "alice ",
        "alice bob",
        "alice\tbob",
        "alice\nbob",
        "alice\x00bob",
        WalletIdSubclass("alice"),
    ),
)
def test_invalid_wallet_id_fails_before_request(checker, wallet_id):
    with patch("concierge.payout_tracker.requests.get") as mock_get:
        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^wallet identifier was invalid$",
        ):
            checker(wallet_id, node_url="https://node")

    mock_get.assert_not_called()


@pytest.mark.parametrize(
    "wallet_id",
    (
        "alice",
        "founder_team_bounty",
        "AUTO-HASH-RTC",
        "wallet-alpha-7",
    ),
)
def test_valid_broad_wallet_id_is_forwarded_unchanged(wallet_id):
    response = MagicMock(status_code=404)
    with patch(
        "concierge.payout_tracker.requests.get",
        return_value=response,
    ) as mock_get:
        assert payout_tracker.check_pending(wallet_id, node_url="https://node/") == []

    mock_get.assert_called_once_with(
        "https://node/wallet/pending",
        params={"miner_id": wallet_id},
        timeout=15,
        verify=False,
    )
