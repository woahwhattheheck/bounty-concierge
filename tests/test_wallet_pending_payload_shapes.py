# SPDX-License-Identifier: MIT
"""Regression coverage for pending-transfer payload shapes."""

from unittest.mock import patch

from concierge import wallet_helper


def _pending(payload):
    with patch("concierge.wallet_helper._get", return_value=payload):
        return wallet_helper.get_pending_transfers("alice")


def test_pending_transfers_accepts_supported_list_shapes():
    direct = [{"pending_id": "p1"}]
    wrapped = [{"pending_id": "p2"}]

    assert _pending(direct) == direct
    assert _pending({"pending": wrapped}) == wrapped
    assert _pending({}) == []
    assert _pending({"error": "unavailable"}) == []


def test_pending_transfers_rejects_malformed_wrapped_shapes():
    for value in (None, "oops", 7, True, {"pending_id": "p1"}):
        result = _pending({"pending": value})
        assert result == []
        assert isinstance(result, list)


def test_pending_transfers_rejects_non_object_list_entries():
    malformed = [
        ["not-an-object"],
        [{"pending_id": "p1"}, 7],
        {"pending": ["not-an-object"]},
        {"pending": [{"pending_id": "p2"}, None]},
    ]

    for payload in malformed:
        assert _pending(payload) == []
