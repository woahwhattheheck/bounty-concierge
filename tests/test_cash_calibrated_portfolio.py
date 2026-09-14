# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import inspect
import json
import os

import pytest

from concierge import cash_calibrated_portfolio as ccp
from concierge import opportunity_ranker
from concierge import realized_unit_economics as rue
from concierge import revenue_settlement as settlement


WALLET = "alice"
REPO = "acme/widgets"


def closeout_row(*, pr=101, state="MERGED", amount="10", repo=REPO):
    return {
        "repo": repo,
        "pr": pr,
        "state": state,
        "currency": "RTC",
        "advertised_amount": amount,
        "cash_status": "not_inferred",
    }


def payment(*, pr=101, amount=10):
    return {
        "type": "transfer_in",
        "amount": amount,
        "from": "treasury",
        "timestamp": f"2026-09-13T00:{pr % 60:02d}:00Z",
        "tx_hash": f"tx-{pr}",
    }


def binding(row, transfer):
    return {
        "repo": row["repo"],
        "pr": row["pr"],
        "history_sha256s": [settlement.history_row_sha256(transfer, wallet=WALLET)],
    }


def effort(*rows):
    return {
        "schema_version": 1,
        "source": "operator_active_minutes",
        "items": [
            {"repo": row["repo"], "pr": row["pr"], "active_minutes": 60}
            for row in rows
        ],
    }


def candidate(marker=1, *, probability="0.9", reward="100", source_repo=REPO):
    return {
        "snapshot": {
            "marker": marker,
            "_reward": reward,
            "_source_repo": source_repo,
            "_skill": 0.75,
        },
        "estimated_effort_hours": "1",
        "estimated_win_probability": probability,
        "hours_until_deadline": "8",
    }


def _intake(source: str, reward: str):
    return {
        "disposition": "ACTIONABLE",
        "dispatch": True,
        "canonical_source_url": source,
        "reason_codes": [],
        "qualification": {
            "disposition": "ACTIONABLE",
            "dispatch": True,
            "signals": {
                "advertised_reward_usd": [reward],
                "live_label_reward_usd": [reward],
            },
        },
        "provenance": {
            "disposition": "ACTIONABLE",
            "dispatch": True,
            "signals": {},
        },
    }


@pytest.fixture(autouse=True)
def canonical_ranker(monkeypatch):
    def qualify(snapshot, **_):
        return _intake(
            f"https://github.com/{snapshot['_source_repo']}/issues/{snapshot['marker']}",
            snapshot["_reward"],
        )

    monkeypatch.setattr(opportunity_ranker, "qualify_revenue_intake", qualify)
    monkeypatch.setattr(
        opportunity_ranker,
        "match_skills",
        lambda snapshot, _skills: snapshot["_skill"],
    )


def run_live(
    monkeypatch,
    rows,
    history,
    bindings=None,
    *,
    value=None,
    minimum_terminal_samples=2,
):
    rows = list(rows)
    history = list(history)
    bindings = [] if bindings is None else list(bindings)
    observed = {"closeout": 0, "wallet": 0}

    def live_closeout(manifest, token=None, *, session=None, max_pages=10):
        observed["closeout"] += 1
        assert manifest == [{"scope": "operator-owned"}]
        assert max_pages == 10
        return rows

    def live_wallet(wallet):
        observed["wallet"] += 1
        assert wallet == WALLET
        return history, WALLET

    monkeypatch.setattr(ccp.closeout, "build_closeout_queue", live_closeout)
    monkeypatch.setattr(ccp.settlement, "_query_canonical_history", live_wallet)

    result = ccp.allocate_cash_calibrated_portfolio(
        [candidate() if value is None else value],
        ["python"],
        "4",
        [{"scope": "operator-owned"}],
        bindings,
        effort(*rows),
        wallet=WALLET,
        minimum_terminal_samples=minimum_terminal_samples,
    )
    assert observed == {"closeout": 1, "wallet": 1}
    return result


def test_live_github_and_live_wallet_reacquired_before_probability_moves(monkeypatch):
    paid = closeout_row(pr=101)
    lost = closeout_row(pr=102, state="CLOSED_UNMERGED")
    transfer = payment(pr=101)
    result = run_live(monkeypatch, [paid, lost], [transfer], [binding(paid, transfer)])
    audit = result["calibration"][0]
    assert audit["terminal_sample_count"] == 2
    assert audit["cash_observed_terminal_count"] == 1
    assert audit["closed_unmerged_zero_cash_terminal_count"] == 1
    assert audit["empirical_cash_observation_rate"] == "0.5"
    assert audit["original_estimated_win_probability"] == "0.9"
    assert audit["calibrated_estimated_win_probability"] == "0.5"
    assert audit["disposition"] == "PROBABILITY_DAMPENED"
    assert result["portfolio"]["selected_count"] == 1
    assert result["portfolio"]["selected"][0]["estimated_win_probability"] == "0.5"
    assert result["portfolio"]["optimizer"]["mode"] == "exact"


def test_public_api_has_no_standalone_receipt_history_or_precomputed_closeout_input():
    parameters = inspect.signature(ccp.allocate_cash_calibrated_portfolio).parameters
    assert "economics_receipt" not in parameters
    assert "history" not in parameters
    assert "history_source" not in parameters
    assert "history_wallet" not in parameters
    assert "closeout_results" not in parameters


def test_self_consistent_forged_economics_packet_cannot_enter_public_path(monkeypatch):
    forged_rows = [
        {
            "repo": REPO,
            "pr": 101,
            "state": "CLOSED_UNMERGED",
            "currency": "RTC",
            "advertised_amount": "10",
            "cash_status": "not_inferred",
            "verified_amount": "0",
            "payment_evidence": [],
        },
        {
            "repo": REPO,
            "pr": 102,
            "state": "CLOSED_UNMERGED",
            "currency": "RTC",
            "advertised_amount": "10",
            "cash_status": "not_inferred",
            "verified_amount": "0",
            "payment_evidence": [],
        },
    ]
    forged = rue._economics_from_reconciled(
        forged_rows,
        effort(*[closeout_row(pr=101), closeout_row(pr=102)]),
        wallet=WALLET,
        history_source="captured_wallet",
        settlement_summary={"currency": "RTC", "verified_cash_total": "0"},
    )
    assert rue.verify_receipt(forged) is True

    paid = closeout_row(pr=101)
    transfer = payment(pr=101)
    monkeypatch.setattr(
        ccp.closeout,
        "build_closeout_queue",
        lambda *_args, **_kwargs: [paid],
    )
    monkeypatch.setattr(
        ccp.settlement,
        "_query_canonical_history",
        lambda _wallet: ([transfer], WALLET),
    )

    with pytest.raises(TypeError, match="economics_receipt"):
        ccp.allocate_cash_calibrated_portfolio(
            [candidate()],
            ["python"],
            "4",
            [{"scope": "operator-owned"}],
            [binding(paid, transfer)],
            effort(paid),
            wallet=WALLET,
            economics_receipt=forged,
        )


def test_provider_closeout_failure_blocks_calibration(monkeypatch):
    monkeypatch.setattr(
        ccp.closeout,
        "build_closeout_queue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ccp.closeout.RevenueCloseoutError("GitHub unavailable")
        ),
    )
    monkeypatch.setattr(
        ccp.settlement,
        "_query_canonical_history",
        lambda _wallet: pytest.fail("wallet must not be queried after closeout failure"),
    )
    with pytest.raises(ccp.closeout.RevenueCloseoutError, match="GitHub unavailable"):
        ccp.allocate_cash_calibrated_portfolio(
            [candidate()],
            ["python"],
            "4",
            [{"scope": "operator-owned"}],
            [],
            effort(closeout_row()),
            wallet=WALLET,
        )


def test_provider_wallet_failure_blocks_calibration(monkeypatch):
    row = closeout_row()
    monkeypatch.setattr(ccp.closeout, "build_closeout_queue", lambda *_a, **_k: [row])
    monkeypatch.setattr(
        ccp.settlement,
        "_query_canonical_history",
        lambda _wallet: (_ for _ in ()).throw(
            ccp.settlement.PayoutLookupError("wallet unavailable")
        ),
    )
    with pytest.raises(ccp.settlement.PayoutLookupError, match="wallet unavailable"):
        ccp.allocate_cash_calibrated_portfolio(
            [candidate()],
            ["python"],
            "4",
            [{"scope": "operator-owned"}],
            [],
            effort(row),
            wallet=WALLET,
        )


def test_history_can_never_raise_operator_probability(monkeypatch):
    first = closeout_row(pr=101)
    second = closeout_row(pr=102)
    p1, p2 = payment(pr=101), payment(pr=102)
    result = run_live(
        monkeypatch,
        [first, second],
        [p1, p2],
        [binding(first, p1), binding(second, p2)],
        value=candidate(probability="0.3"),
    )
    audit = result["calibration"][0]
    assert audit["empirical_cash_observation_rate"] == "1"
    assert audit["calibrated_estimated_win_probability"] == "0.3"
    assert audit["disposition"] == "HISTORY_CAP_NOT_BINDING"


def test_partial_verified_cash_is_positive_terminal_history(monkeypatch):
    first = closeout_row(pr=101, amount="10")
    second = closeout_row(pr=102, state="CLOSED_UNMERGED")
    p1 = payment(pr=101, amount=4)
    result = run_live(monkeypatch, [first, second], [p1], [binding(first, p1)])
    assert result["calibration"][0]["cash_observed_terminal_count"] == 1
    assert result["calibration"][0]["empirical_cash_observation_rate"] == "0.5"


def test_open_head_moved_and_merged_without_cash_are_unresolved(monkeypatch):
    rows = [
        closeout_row(pr=101, state="MERGED"),
        closeout_row(pr=102, state="OPEN"),
        closeout_row(pr=103, state="HEAD_MOVED"),
        closeout_row(pr=104, state="MERGED"),
    ]
    p1 = payment(pr=101)
    result = run_live(monkeypatch, rows, [p1], [binding(rows[0], p1)])
    audit = result["calibration"][0]
    assert audit["terminal_sample_count"] == 1
    assert audit["unresolved_history_count"] == 3
    assert audit["disposition"] == "INSUFFICIENT_TERMINAL_HISTORY"
    assert audit["calibrated_estimated_win_probability"] == "0.9"


def test_zero_cash_terminal_history_can_drive_probability_to_zero(monkeypatch):
    rows = [
        closeout_row(pr=101, state="CLOSED_UNMERGED"),
        closeout_row(pr=102, state="CLOSED_UNMERGED"),
    ]
    result = run_live(monkeypatch, rows, [])
    audit = result["calibration"][0]
    assert audit["empirical_cash_observation_rate"] == "0"
    assert audit["calibrated_estimated_win_probability"] == "0"
    assert result["portfolio"]["selected_count"] == 0


def test_insufficient_history_does_not_change_probability(monkeypatch):
    row = closeout_row(pr=101)
    p1 = payment(pr=101)
    result = run_live(monkeypatch, [row], [p1], [binding(row, p1)])
    assert result["calibration"][0]["disposition"] == "INSUFFICIENT_TERMINAL_HISTORY"
    assert result["calibration"][0]["calibrated_estimated_win_probability"] == "0.9"


def test_repo_mapping_comes_only_from_canonical_github_issue_source(monkeypatch):
    value = candidate()
    value["repo"] = "attacker/forged"
    paid = closeout_row(pr=101)
    lost = closeout_row(pr=102, state="CLOSED_UNMERGED")
    transfer = payment(pr=101)
    result = run_live(
        monkeypatch,
        [paid, lost],
        [transfer],
        [binding(paid, transfer)],
        value=value,
    )
    assert result["calibration"][0]["repo"] == REPO
    assert result["calibration"][0]["disposition"] == "PROBABILITY_DAMPENED"


@pytest.mark.parametrize(
    "source",
    [
        "http://github.com/acme/widgets/issues/1",
        "https://github.com/acme/widgets/pull/1",
        "https://github.com/acme/widgets/issues/1?repo=evil",
        "https://github.com/acme/widgets/issues/1#frag",
        "https://github.com/acme/widgets/issues/01",
        "https://user@github.com/acme/widgets/issues/1",
        "https://evil.example/acme/widgets/issues/1",
    ],
)
def test_noncanonical_source_cannot_create_repo_mapping(source):
    assert ccp._github_issue_repo(source) is None


def test_rtc_magnitude_never_enters_usd_probability_math(monkeypatch):
    low = closeout_row(pr=101, amount="1")
    lost = closeout_row(pr=102, state="CLOSED_UNMERGED")
    p1 = payment(pr=101, amount=1)
    low_result = run_live(monkeypatch, [low, lost], [p1], [binding(low, p1)])

    high = closeout_row(pr=101, amount="999999")
    p2 = payment(pr=101, amount=999999)
    high_result = run_live(monkeypatch, [high, lost], [p2], [binding(high, p2)])
    assert low_result["calibration"] == high_result["calibration"]
    assert low_result["portfolio"] == high_result["portfolio"]
    assert low_result["authority"]["currency_conversion"] is False
    assert low_result["authority"]["rtc_amount_used_in_usd_math"] is False


def test_probability_cap_is_floored_to_six_places(monkeypatch):
    rows = [
        closeout_row(pr=101),
        closeout_row(pr=102, state="CLOSED_UNMERGED"),
        closeout_row(pr=103, state="CLOSED_UNMERGED"),
    ]
    p1 = payment(pr=101)
    result = run_live(monkeypatch, rows, [p1], [binding(rows[0], p1)])
    audit = result["calibration"][0]
    assert audit["empirical_cash_observation_rate"] == "0.333333"
    assert audit["calibrated_estimated_win_probability"] == "0.333333"


def test_same_live_input_is_deterministic(monkeypatch):
    paid = closeout_row(pr=101)
    lost = closeout_row(pr=102, state="CLOSED_UNMERGED")
    transfer = payment(pr=101)
    first = run_live(monkeypatch, [paid, lost], [transfer], [binding(paid, transfer)])
    second = run_live(monkeypatch, [paid, lost], [transfer], [binding(paid, transfer)])
    assert first == second
    unsigned = {
        key: value
        for key, value in first.items()
        if key != "calibration_receipt_sha256"
    }
    expected = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert first["calibration_receipt_sha256"] == expected


def test_authority_ceiling_explicit_and_cash_amount_not_leaked(monkeypatch):
    paid = closeout_row(pr=101)
    lost = closeout_row(pr=102, state="CLOSED_UNMERGED")
    transfer = payment(pr=101)
    result = run_live(monkeypatch, [paid, lost], [transfer], [binding(paid, transfer)])
    assert result["authority"] == {
        "eligibility": "canonical_opportunity_ranker",
        "closeout_state": "live_github_reacquired_in_process",
        "wallet_history": "canonical_provider_reacquired_in_process",
        "economics": "compiled_in_process_from_live_provider_observations",
        "standalone_economics_receipt_accepted": False,
        "captured_wallet_history_accepted": False,
        "cash_evidence_authority": "reacquired_not_inherited",
        "calibration": "same_repo_terminal_cash_observation_probability_cap_only",
        "currency_conversion": False,
        "rtc_amount_used_in_usd_math": False,
        "probability_can_only_decrease": True,
        "selection": "canonical_exact_portfolio_allocator",
        "claim_or_submission_authority": False,
        "cash_or_settlement_claim": False,
        "accounting_or_tax_claim": False,
    }
    assert "verified_cash_rtc" not in repr(result["calibration"])
    assert "realized_rtc_per_hour" not in repr(result["calibration"])


def test_bounded_reader_rejects_fifo_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO not supported")
    fifo = tmp_path / "input.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ccp.CashCalibrationInputError, match="regular file"):
        ccp._read_bounded(str(fifo))
