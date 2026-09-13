# SPDX-License-Identifier: MIT
"""Resource-custody regressions for the managed PoW miner log pump."""

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge.pow_miners import _pump_logs


def test_log_pump_closes_pipe_and_owned_log_file_on_eof():
    pipe = MagicMock()
    pipe.readline.side_effect = ["first line\n", ""]
    log_file = MagicMock()

    _pump_logs(pipe, log_file)

    log_file.write.assert_called_once_with("first line\n")
    log_file.flush.assert_called_once_with()
    pipe.close.assert_called_once_with()
    log_file.close.assert_called_once_with()


def test_log_pump_closes_both_resources_when_log_write_fails():
    pipe = MagicMock()
    pipe.readline.return_value = "line\n"
    log_file = MagicMock()
    log_file.write.side_effect = OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        _pump_logs(pipe, log_file)

    pipe.close.assert_called_once_with()
    log_file.close.assert_called_once_with()
