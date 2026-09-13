# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal

import pytest

from concierge import realized_unit_economics as rue


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def paid(repo="acme/one", pr=1, amount="10", fingerprint=H1):
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


def partial(
    repo="acme/two",
    pr=2,
    advertised="10",
    verified="4",
    fingerprint=H2,
):
    return {
        "repo": repo,
        "pr": pr,
        "currency": "RTC",
        "advertised_amount": advertised,
        "state": "MERGED",
        "cash_status": "partially_verified",
        "verified_amount": verified,
        "payment_evidence": [{"history_sha256": fingerprint}],
    }


def unpaid(repo="acme/three", pr=3, advertised="20", state="OPEN"):
    return {
        "repo": repo,
        "pr": pr,
        "currency": "RTC",
        "advertised_amount": advertised,
        "state": state,
        "cash_status": "not_inferred",
        "verified_amount": "0",
        "payment_evidence": [],
    }


def effort(*rows):
    return {
        "schema_version": 1,
        "source": "operator_active_minutes",
        "items": list(rows),
    }


def work(repo, pr, minutes):
    return {"repo": repo, "pr": pr, "active_minutes": minutes}


def compile_rows(rows, effort_payload):
    return rue._economics_from_reconciled(
        rows,
        effort_payload,
        wallet="wallet-1",
        history_source="captured_wallet",
        settlement_summary={
            "currency": "RTC",
            "verified_cash_total": str(
                sum(Decimal(row["verified_amount"]) for row in rows)
            ),
        },
    )


def test_realized_rate_counts_zero_cash_work_in_denominator():
    receipt = compile_rows(
        [paid(), partial(), unpaid()],
        effort(
            work("acme/one", 1, 120),
            work("acme/two", 2, 60),
            work("acme/three", 3, 60),
        ),
    )
    assert receipt["summary"]["verified_cash_total"] == "14"
    assert receipt["summary"]["active_minutes_total"] == 240
    assert receipt["summary"]["realized_rtc_per_hour_estimate"] == "3.5"
    assert receipt["summary"]["fully_paid_items"] == 1
    assert receipt["summary"]["partially_paid_items"] == 1
    assert receipt["summary"]["zero_verified_cash_items"] == 1
    assert receipt["summary"]["scope_complete"] is True


def test_ranking_uses_exact_cash_per_minute_not_advertised_reward():
    receipt = compile_rows(
        [
            paid(amount="10"),
            partial(advertised="100", verified="4"),
            unpaid(advertised="1000"),
        ],
        effort(
            work("acme/one", 1, 120),  # 5/hour
            work("acme/two", 2, 30),   # 8/hour
            work("acme/three", 3, 1),  # 0/hour
        ),
    )
    assert [(r["repo"], r["pr"]) for r in receipt["ranking"]] == [
        ("acme/two", 2),
        ("acme/one", 1),
        ("acme/three", 3),
    ]


def test_equal_ratio_ranking_is_deterministic_by_identity():
    receipt = compile_rows(
        [
            paid(repo="z/x", pr=2, amount="10", fingerprint=H1),
            paid(repo="a/x", pr=3, amount="5", fingerprint=H2),
        ],
        effort(work("z/x", 2, 120), work("a/x", 3, 60)),
    )
    assert [(r["repo"], r["pr"]) for r in receipt["ranking"]] == [
        ("a/x", 3),
        ("z/x", 2),
    ]


def test_repeating_rate_is_labeled_estimate_and_deterministic():
    receipt = compile_rows(
        [paid(amount="1")],
        effort(work("acme/one", 1, 7)),
    )
    rate = receipt["summary"]["realized_rtc_per_hour_estimate"]
    assert rate.startswith("8.571428571428571428571428571428")
    assert rate == receipt["items"][0]["realized_rtc_per_hour_estimate"]


def test_missing_effort_fails_closed():
    with pytest.raises(rue.RealizedUnitEconomicsInputError, match="missing effort"):
        compile_rows(
            [paid(), unpaid()],
            effort(work("acme/one", 1, 10)),
        )


def test_extra_effort_fails_closed():
    with pytest.raises(rue.RealizedUnitEconomicsInputError, match="extra effort"):
        compile_rows(
            [paid()],
            effort(
                work("acme/one", 1, 10),
                work("acme/extra", 9, 1),
            ),
        )


@pytest.mark.parametrize("minutes", [0, -1, True, 525601, "60", 1.5])
def test_active_minutes_are_strict_bounded_integers(minutes):
    with pytest.raises(rue.RealizedUnitEconomicsInputError, match="active_minutes"):
        compile_rows(
            [paid()],
            effort(work("acme/one", 1, minutes)),
        )


def test_duplicate_effort_identity_fails_closed():
    with pytest.raises(rue.RealizedUnitEconomicsInputError, match="duplicate effort"):
        compile_rows(
            [paid()],
            effort(
                work("acme/one", 1, 10),
                work("ACME/ONE", 1, 20),
            ),
        )


def test_duplicate_reconciliation_identity_fails_closed():
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="duplicate reconciliation",
    ):
        compile_rows(
            [
                paid(),
                paid(repo="ACME/ONE", pr=1, fingerprint=H2),
            ],
            effort(work("acme/one", 1, 10)),
        )


@pytest.mark.parametrize(
    "mutation,pattern",
    [
        (lambda row: row.update(currency="USD"), "not denominated in RTC"),
        (lambda row: row.update(state="OPEN"), "verified_paid state is inconsistent"),
        (lambda row: row.update(verified_amount="9"), "verified_paid state is inconsistent"),
        (lambda row: row.update(payment_evidence=[]), "verified_paid state is inconsistent"),
        (lambda row: row.update(cash_status="mystery"), "unsupported cash_status"),
        (lambda row: row.update(verified_amount="-1"), "must not be negative"),
        (lambda row: row.update(verified_amount=1.5), "exact decimal"),
    ],
)
def test_forged_paid_result_shape_fails_closed(mutation, pattern):
    row = paid()
    mutation(row)
    with pytest.raises(rue.RealizedUnitEconomicsInputError, match=pattern):
        compile_rows(
            [row],
            effort(work("acme/one", 1, 10)),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(state="OPEN"),
        lambda row: row.update(verified_amount="0"),
        lambda row: row.update(verified_amount="10"),
        lambda row: row.update(payment_evidence=[]),
    ],
)
def test_forged_partial_result_shape_fails_closed(mutation):
    row = partial()
    mutation(row)
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="partially_verified state is inconsistent",
    ):
        compile_rows(
            [row],
            effort(work("acme/two", 2, 10)),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(verified_amount="1"),
        lambda row: row.update(payment_evidence=[{"history_sha256": H3}]),
    ],
)
def test_not_inferred_cannot_smuggle_cash_evidence(mutation):
    row = unpaid()
    mutation(row)
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="not_inferred state contains cash evidence",
    ):
        compile_rows(
            [row],
            effort(work("acme/three", 3, 10)),
        )


def test_one_payment_evidence_row_cannot_support_two_items():
    rows = [
        paid(repo="acme/one", pr=1, amount="10", fingerprint=H1),
        paid(repo="acme/two", pr=2, amount="10", fingerprint=H1),
    ]
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="cannot support multiple",
    ):
        compile_rows(
            rows,
            effort(
                work("acme/one", 1, 10),
                work("acme/two", 2, 10),
            ),
        )


@pytest.mark.parametrize("fingerprint", ["A" * 64, "0" * 63, "x" * 64, 7])
def test_payment_evidence_fingerprint_is_strict(fingerprint):
    row = paid()
    row["payment_evidence"][0]["history_sha256"] = fingerprint
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="lowercase SHA-256",
    ):
        compile_rows(
            [row],
            effort(work("acme/one", 1, 10)),
        )


def test_settlement_summary_must_match_item_total():
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="cash total disagrees",
    ):
        rue._economics_from_reconciled(
            [paid()],
            effort(work("acme/one", 1, 10)),
            wallet="wallet-1",
            history_source="captured_wallet",
            settlement_summary={
                "currency": "RTC",
                "verified_cash_total": "999",
            },
        )


def test_settlement_summary_currency_must_be_rtc():
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="summary currency",
    ):
        rue._economics_from_reconciled(
            [paid()],
            effort(work("acme/one", 1, 10)),
            wallet="wallet-1",
            history_source="captured_wallet",
            settlement_summary={
                "currency": "USD",
                "verified_cash_total": "10",
            },
        )


@pytest.mark.parametrize("wallet", ["", " wallet", "wallet 1", "wallet\n"])
def test_wallet_shape_fails_closed(wallet):
    with pytest.raises(rue.RealizedUnitEconomicsInputError, match="wallet"):
        rue._economics_from_reconciled(
            [paid()],
            effort(work("acme/one", 1, 10)),
            wallet=wallet,
            history_source="captured_wallet",
        )


def test_history_source_is_strict():
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="history_source",
    ):
        rue._economics_from_reconciled(
            [paid()],
            effort(work("acme/one", 1, 10)),
            wallet="wallet-1",
            history_source="invented",
        )


def test_receipt_self_integrity_and_non_authority_flags():
    receipt = compile_rows(
        [paid()],
        effort(work("acme/one", 1, 120)),
    )
    assert rue.verify_receipt(receipt) is True
    assert receipt["summary"]["accounting_revenue_claim"] is False
    assert receipt["summary"]["tax_claim"] is False
    assert receipt["summary"]["payout_or_transfer_authority"] is False
    assert receipt["summary"]["fx_conversion"] is False

    tampered = copy.deepcopy(receipt)
    tampered["summary"]["verified_cash_total"] = "999"
    assert rue.verify_receipt(tampered) is False


def test_self_hash_recomputation_is_not_payment_authority():
    receipt = compile_rows(
        [paid()],
        effort(work("acme/one", 1, 60)),
    )
    forged = copy.deepcopy(receipt)
    forged["summary"]["verified_cash_total"] = "999"
    forged.pop("receipt_sha256")
    forged["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            forged,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert rue.verify_receipt(forged) is True
    # Self-integrity is intentionally not called authoritative verification.
    assert forged["summary"]["accounting_revenue_claim"] is False
    assert forged["summary"]["payout_or_transfer_authority"] is False


def test_compile_calls_settlement_boundary_before_economics(monkeypatch):
    calls = {}

    def reconcile(closeout, history, bindings, **kwargs):
        calls["reconcile"] = (closeout, history, bindings, kwargs)
        return [paid()]

    def summarize(rows):
        calls["summarize"] = rows
        return {"currency": "RTC", "verified_cash_total": "10"}

    monkeypatch.setattr(rue.settlement, "reconcile_cash", reconcile)
    monkeypatch.setattr(rue.settlement, "summarize_cash", summarize)

    receipt = rue.compile_realized_unit_economics(
        [{"closeout": "opaque"}],
        [{"history": "opaque"}],
        [{"binding": "opaque"}],
        effort(work("acme/one", 1, 120)),
        wallet="wallet-1",
        history_wallet="wallet-1",
        history_source="captured_wallet",
    )
    assert "reconcile" in calls and "summarize" in calls
    assert calls["reconcile"][3] == {
        "wallet": "wallet-1",
        "history_wallet": "wallet-1",
        "history_source": "captured_wallet",
    }
    assert receipt["summary"]["realized_rtc_per_hour_estimate"] == "5"


def test_compile_propagates_settlement_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise rue.settlement.RevenueSettlementEvidenceError("bad wallet evidence")

    monkeypatch.setattr(rue.settlement, "reconcile_cash", fail)
    with pytest.raises(
        rue.settlement.RevenueSettlementEvidenceError,
        match="bad wallet evidence",
    ):
        rue.compile_realized_unit_economics(
            [],
            [],
            [],
            effort(work("acme/one", 1, 60)),
            wallet="wallet-1",
            history_wallet="wallet-1",
            history_source="captured_wallet",
        )


def test_strict_json_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "dupe.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="duplicate JSON key",
    ):
        rue._strict_json(str(path))


def test_strict_json_preserves_decimal_exactness(tmp_path):
    path = tmp_path / "decimal.json"
    path.write_text('{"value":0.1234567890123456789}', encoding="utf-8")
    parsed = rue._strict_json(str(path))
    assert parsed["value"] == Decimal("0.1234567890123456789")


def test_scope_digest_changes_when_effort_changes():
    one = compile_rows(
        [paid()],
        effort(work("acme/one", 1, 60)),
    )
    two = compile_rows(
        [paid()],
        effort(work("acme/one", 1, 61)),
    )
    assert one["scope_sha256"] != two["scope_sha256"]
    assert one["receipt_sha256"] != two["receipt_sha256"]


def test_scope_digest_changes_when_cash_evidence_changes():
    one = compile_rows(
        [paid(fingerprint=H1)],
        effort(work("acme/one", 1, 60)),
    )
    two = compile_rows(
        [paid(fingerprint=H2)],
        effort(work("acme/one", 1, 60)),
    )
    assert one["scope_sha256"] != two["scope_sha256"]


def test_output_never_exposes_advertised_amount_as_realized_cash():
    receipt = compile_rows(
        [unpaid(advertised="1000000")],
        effort(work("acme/three", 3, 60)),
    )
    assert receipt["summary"]["verified_cash_total"] == "0"
    assert receipt["summary"]["realized_rtc_per_hour_estimate"] == "0"
    assert receipt["items"][0]["verified_cash_rtc"] == "0"


def test_float_money_is_rejected_even_if_numerically_equal():
    row = paid()
    row["verified_amount"] = 10.0
    with pytest.raises(
        rue.RealizedUnitEconomicsInputError,
        match="exact decimal",
    ):
        compile_rows(
            [row],
            effort(work("acme/one", 1, 60)),
        )
