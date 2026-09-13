# SPDX-License-Identifier: MIT
"""Hostile transport-authority regressions for payout status reads."""

from unittest.mock import MagicMock, patch

import pytest

from concierge import payout_tracker


def _response(payload=None):
    response = MagicMock()
    response.json.return_value = payload if payload is not None else {
        "ok": True,
        "miner_id": "alice",
        "transactions": [],
        "total": 0,
    }
    response.raise_for_status.return_value = None
    return response


@patch("concierge.payout_tracker.requests.get")
def test_remote_http_is_rejected_before_network_io(mock_get):
    with pytest.raises(
        payout_tracker.PayoutLookupError,
        match="^remote RustChain payout reads require HTTPS$",
    ):
        payout_tracker.check_history("alice", "http://node.example")
    mock_get.assert_not_called()


@patch("concierge.payout_tracker.requests.get")
def test_remote_https_verifies_peer_identity_by_default(mock_get):
    mock_get.return_value = _response()

    payout_tracker.check_history("alice", "https://node.example")

    assert mock_get.call_args.kwargs["verify"] is True


@patch("concierge.payout_tracker.requests.get")
def test_loopback_http_remains_available_for_hermetic_development(mock_get):
    mock_get.return_value = _response()

    payout_tracker.check_history("alice", "http://127.0.0.1:8080")

    assert mock_get.call_args.kwargs["verify"] is True


@patch("concierge.payout_tracker.requests.get")
def test_private_ca_bundle_is_forwarded_to_requests(mock_get, tmp_path, monkeypatch):
    ca_bundle = tmp_path / "rustchain-ca.pem"
    ca_bundle.write_text("test-ca", encoding="utf-8")
    monkeypatch.setattr(
        payout_tracker.config,
        "RUSTCHAIN_CA_BUNDLE",
        str(ca_bundle),
    )
    mock_get.return_value = _response()

    payout_tracker.check_history("alice", "https://node.example")

    assert mock_get.call_args.kwargs["verify"] == str(ca_bundle)


@patch("concierge.payout_tracker.requests.get")
def test_unavailable_private_ca_fails_before_network_io(mock_get, tmp_path, monkeypatch):
    missing = tmp_path / "missing-ca.pem"
    monkeypatch.setattr(payout_tracker.config, "RUSTCHAIN_CA_BUNDLE", str(missing))

    with pytest.raises(
        payout_tracker.PayoutLookupError,
        match="^RustChain CA bundle was unavailable$",
    ):
        payout_tracker.check_history("alice", "https://node.example")
    mock_get.assert_not_called()


@patch("concierge.payout_tracker.requests.get")
def test_ca_bundle_probe_error_fails_closed(mock_get, monkeypatch):
    monkeypatch.setattr(payout_tracker.config, "RUSTCHAIN_CA_BUNDLE", "/tmp/ca.pem")
    with patch("concierge.payout_tracker.Path.is_file", side_effect=OSError("unreadable")):
        with pytest.raises(
            payout_tracker.PayoutLookupError,
            match="^RustChain CA bundle was unavailable$",
        ):
            payout_tracker.check_history("alice", "https://node.example")
    mock_get.assert_not_called()


@patch("concierge.payout_tracker.requests.get")
def test_malformed_ipv6_authority_fails_closed(mock_get):
    with pytest.raises(
        payout_tracker.PayoutLookupError,
        match="^RustChain node URL was invalid$",
    ):
        payout_tracker.check_history("alice", "https://[::1")
    mock_get.assert_not_called()


@pytest.mark.parametrize(
    "node_url",
    [
        "ftp://node.example",
        "https://user:password@node.example",
        "https://node.example?alternate=1",
        "https://node.example#alternate",
        " https://node.example",
    ],
)
@patch("concierge.payout_tracker.requests.get")
def test_ambiguous_or_credential_bearing_node_url_fails_closed(mock_get, node_url):
    with pytest.raises(
        payout_tracker.PayoutLookupError,
        match="^RustChain node URL was invalid$",
    ):
        payout_tracker.check_history("alice", node_url)
    mock_get.assert_not_called()
