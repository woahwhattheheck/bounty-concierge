# SPDX-License-Identifier: MIT
"""Regression for exact high-precision realized-unit ranking."""

from concierge import realized_unit_economics as rue


LOWER = "123456789012345678901234567898"
HIGHER = "123456789012345678901234567899"


def _paid(repo: str, pr: int, amount: str, fingerprint: str) -> dict:
    return {
        "repo": repo,
        "pr": pr,
        "currency": "RTC",
        "advertised_amount": amount,
        "state": "MERGED",
        "cash_status": "verified_paid",
        "verified_amount": amount,
        "payment_evidence": [{"history_sha256": fingerprint}],
    }


def test_ranking_preserves_distinct_30_digit_cross_products() -> None:
    """Ambient precision must not collapse unequal accepted ratios into a tie."""
    receipt = rue._economics_from_reconciled(
        [
            _paid("a/lower", 1, LOWER, "1" * 64),
            _paid("z/higher", 2, HIGHER, "2" * 64),
        ],
        {
            "schema_version": 1,
            "source": "operator_active_minutes",
            "items": [
                {"repo": "a/lower", "pr": 1, "active_minutes": 1},
                {"repo": "z/higher", "pr": 2, "active_minutes": 1},
            ],
        },
        wallet="wallet-1",
        history_source="captured_wallet",
        settlement_summary={
            "currency": "RTC",
            "verified_cash_total": str(int(LOWER) + int(HIGHER)),
        },
    )

    assert [(row["repo"], row["pr"]) for row in receipt["ranking"]] == [
        ("z/higher", 2),
        ("a/lower", 1),
    ]
