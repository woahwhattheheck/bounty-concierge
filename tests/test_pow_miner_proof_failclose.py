# SPDX-License-Identifier: MIT
"""Fail-closed regressions for PoW proof and bonus signals."""

from unittest.mock import patch

from concierge import pow_miners


def _detection_runner(ps_output="", active_service=None):
    def _runner(command, timeout=5):
        if command[:2] == ["ps", "-eo"]:
            return 0, ps_output, ""
        if command[:2] == ["systemctl", "is-active"]:
            service = command[2]
            if service == active_service:
                return 0, "active\n", ""
            return 3, "inactive\n", ""
        if command[:2] == ["screen", "-ls"]:
            return 127, "", "screen unavailable"
        raise AssertionError(f"unexpected command: {command}")

    return _runner


def test_wart_node_process_is_detected_but_not_external_miner():
    with patch(
        "concierge.pow_miners._run_command",
        side_effect=_detection_runner("wart-node-linux --rpc\n"),
    ):
        result = pow_miners.detect_pow_processes()

    assert result["detected"] is True
    assert result["external_miner_detected"] is False
    assert [p["type"] for p in result["processes"]] == ["wart-node"]


def test_wart_node_service_is_detected_but_not_external_miner():
    with patch(
        "concierge.pow_miners._run_command",
        side_effect=_detection_runner(active_service="wart-node"),
    ):
        result = pow_miners.detect_pow_processes()

    assert result["detected"] is True
    assert result["external_miner_detected"] is False


def test_rustchain_miner_service_is_external_miner_evidence():
    with patch(
        "concierge.pow_miners._run_command",
        side_effect=_detection_runner(active_service="rustchain-miner"),
    ):
        result = pow_miners.detect_pow_processes()

    assert result["detected"] is True
    assert result["external_miner_detected"] is True


def test_bonus_multiplier_requires_literal_true_proofs():
    result = pow_miners.calculate_bonus_multiplier(
        managed_subprocess_running="false",
        external_miner_detected=1,
        pool_account_verified={"verified": True},
        node_rpc_verified=[True],
    )

    assert result == {"base": 1.0, "total_multiplier": 1.0, "factors": []}
