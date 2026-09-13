# SPDX-License-Identifier: MIT
"""Payout CLI output must not trust node-provided terminal controls."""

from concierge import payout_tracker


def test_node_fields_escape_terminal_controls_without_creating_rows():
    output = payout_tracker.format_payout_status(
        [
            {
                "amount_rtc": "3\n999",
                "memo": "ok\x1b[2Jforged",
                "created_at": "2026-09-12\rOVERWRITE",
            }
        ],
        [
            {
                "amount_rtc": "2\x07",
                "from": "treasury\x1b]8;;https://evil.example\x07",
                "to": "alice\nFORGED",
                "timestamp": "then\u2028next",
            }
        ],
    )

    assert "3\\x0a999 RTC  memo: ok\\x1b[2Jforged  (2026-09-12\\x0dOVERWRITE)" in output
    assert (
        "2\\x07 RTC  treasury\\x1b]8;;https://evil.example\\x07 -> "
        "alice\\x0aFORGED  (then\\u2028next)"
    ) in output
    assert "\x1b" not in output
    assert "\r" not in output
    assert "\u2028" not in output
    assert len(output.splitlines()) == 5


def test_printable_unicode_and_none_optional_fields_remain_readable():
    output = payout_tracker.format_payout_status(
        [{"amount_rtc": 3.5, "memo": "café 🚀", "created_at": None}],
        [{"amount_rtc": 2, "from": "trésor", "to": "alice", "timestamp": None}],
    )

    assert "3.5 RTC  memo: café 🚀" in output
    assert "2 RTC  trésor -> alice" in output
    assert "None" not in output
