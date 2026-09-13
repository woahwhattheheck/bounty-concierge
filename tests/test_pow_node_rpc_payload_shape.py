# SPDX-License-Identifier: MIT
"""Fail-closed coverage for node RPC proof response shapes."""

from unittest.mock import MagicMock, patch

from concierge import pow_miners


def _response(payload):
    response = MagicMock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = payload
    response.text = ""
    return response


def test_non_object_success_payload_does_not_verify_node_proof():
    with patch(
        "concierge.pow_miners.requests.get",
        side_effect=[
            _response({"height": 1}),
            _response([]),
            _response({"balance": 0}),
        ],
    ):
        result = pow_miners.query_node_rpc("RTCabc", base_url="http://node")

    assert result["verified"] is False
    assert result["results"]["mine_eligibility"]["data"] == []
    assert "mine_eligibility: response payload was not an object" in result["errors"]
