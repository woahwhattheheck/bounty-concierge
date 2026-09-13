# SPDX-License-Identifier: MIT
"""Tests for the installed CLI error boundary."""

from unittest.mock import patch

import pytest

from concierge import entrypoint
from concierge.payout_tracker import PayoutLookupError


def test_entrypoint_converts_payout_lookup_error_to_sanitized_exit(capsys):
    with patch.object(
        entrypoint,
        "_cli_main",
        side_effect=PayoutLookupError("history payout request failed"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.main()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Error: payout status unavailable: history payout request failed\n"
    )


def test_entrypoint_preserves_successful_cli_completion():
    with patch.object(entrypoint, "_cli_main") as mock_main:
        entrypoint.main()

    mock_main.assert_called_once_with()
