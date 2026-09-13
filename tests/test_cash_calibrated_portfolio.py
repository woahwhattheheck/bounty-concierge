# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal

import pytest

from concierge import cash_calibrated_portfolio as ccp
from concierge import opportunity_ranker
from concierge import realized_unit_economics as rue


def _fingerprint(number: int) -> str:
    return f"{number:064x}"


def paid(*, repo="acme/widgets", pr=101, amount="10", fingerprint=None):
    if fingerprint is None:
        fingerprint = _fingerprint(pr)
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
    *,
    repo="acme/widgets",
    pr=101,
    advertised="10",
    verified="4",
    fingerprint=None,
):
    if fingerprint is None:
        fingerprint = _fingerprint(pr)
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


def unpaid(*, repo="acme/widgets", pr=102, state="CLOSED_UNMERGED"):
    return {
        "repo": repo,
        "pr": pr,
        "currency": "RTC",
        "advertised_amount": "10",
        "state": state,
        "cash_status": "not_inferred",
        "verified_amount": "0",
        "payment_evidence": [],
    }


def economics(*rows):
    effort = {
        "schema_version": 1,
        "source": "operator_active_minutes",
        "items": [
            {"repo": row["repo"], "pr": row["pr"], "active_minutes": 60}
            for row in rows
        ],
    }
    return rue._economics_from_reconciled(
        list(rows),
        effort,
        wallet="wallet-1",
        history_source="captured_wallet",
        settlement_summary={
            "currency": "RTC",
            "verified_cash_total": str(
                sum(Decimal(row["verified_amount"]) for row in rows)
            ),
        },
    )


def candidate(
    marker=1,
    *,
    probability="0.9",
    reward="100",
    source_repo="acme/widgets",
):
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


def allocate(evidence, value=None, *, minimum_terminal_samples=2):
    if value is None:
        value = candidate()
    return ccp.allocate_cash_calibrated_portfolio(
        [value],
        ["python"],
        "4",
        evidence,
        minimum_terminal_samples=minimum_terminal_samples,
    )


def _reseal(receipt):
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_actual_realized_receipt_caps_probability_and_delegates_to_exact_allocator():
    result = allocate(economics(paid(), unpaid()))
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
    assert result["authority"]["selection"] == "canonical_exact_portfolio_allocator"


def test_history_can_never_raise_operator_probability():
    result = allocate(
        economics(
            paid(pr=101, fingerprint=_fingerprint(101)),
            paid(pr=102, fingerprint=_fingerprint(102)),
        ),
        candidate(probability="0.3"),
    )
    audit = result["calibration"][0]
    assert audit["empirical_cash_observation_rate"] == "1"
    assert audit["calibrated_estimated_win_probability"] == "0.3"
    assert audit["disposition"] == "HISTORY_CAP_NOT_BINDING"
    assert result["dampened_candidate_count"] == 0


def test_partial_verified_cash_is_positive_terminal_history():
    result = allocate(
        economics(
            partial(pr=101, fingerprint=_fingerprint(101)),
            unpaid(pr=102),
        )
    )
    assert result["calibration"][0]["cash_observed_terminal_count"] == 1
    assert result["calibration"][0]["empirical_cash_observation_rate"] == "0.5"


def test_open_head_moved_and_merged_without_cash_are_unresolved_not_losses():
    receipt = economics(
        paid(pr=101, fingerprint=_fingerprint(101)),
        unpaid(pr=102, state="OPEN"),
        unpaid(pr=103, state="HEAD_MOVED"),
        unpaid(pr=104, state="MERGED"),
    )
    result = allocate(receipt)
    audit = result["calibration"][0]
    assert audit["terminal_sample_count"] == 1
    assert audit["unresolved_history_count"] == 3
    assert audit["disposition"] == "INSUFFICIENT_TERMINAL_HISTORY"
    assert audit["calibrated_estimated_win_probability"] == "0.9"


def test_zero_cash_terminal_history_can_drive_probability_to_zero():
    result = allocate(economics(unpaid(pr=101), unpaid(pr=102)))
    audit = result["calibration"][0]
    assert audit["empirical_cash_observation_rate"] == "0"
    assert audit["calibrated_estimated_win_probability"] == "0"
    assert result["portfolio"]["selected_count"] == 0


def test_insufficient_history_does_not_change_probability():
    result = allocate(
        economics(paid(pr=101, fingerprint=_fingerprint(101))),
        minimum_terminal_samples=2,
    )
    assert result["calibration"][0]["disposition"] == "INSUFFICIENT_TERMINAL_HISTORY"
    assert result["calibration"][0]["calibrated_estimated_win_probability"] == "0.9"


def test_receipt_tamper_fails_before_calibration():
    receipt = economics(paid(), unpaid())
    receipt["items"][0]["active_minutes"] = 61
    with pytest.raises(ccp.CashCalibrationInputError, match="integrity"):
        allocate(receipt)


def test_self_resealed_duplicate_history_identity_fails_closed():
    receipt = economics(paid(), unpaid())
    receipt["items"].append(copy.deepcopy(receipt["items"][0]))
    _reseal(receipt)
    with pytest.raises(ccp.CashCalibrationInputError, match="duplicate realized economics"):
        allocate(receipt)


def test_self_resealed_authority_relaxation_fails_closed():
    receipt = economics(paid(), unpaid())
    receipt["summary"]["fx_conversion"] = True
    _reseal(receipt)
    with pytest.raises(ccp.CashCalibrationInputError, match="authority summary"):
        allocate(receipt)


def test_repo_mapping_comes_only_from_canonical_github_issue_source():
    value = candidate()
    value["repo"] = "attacker/forged"
    result = allocate(economics(paid(), unpaid()), value)
    audit = result["calibration"][0]
    assert audit["repo"] == "acme/widgets"
    assert audit["disposition"] == "PROBABILITY_DAMPENED"


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


def test_rtc_magnitude_never_enters_usd_probability_math():
    low = economics(
        paid(pr=101, amount="1", fingerprint=_fingerprint(101)),
        unpaid(pr=102),
    )
    high = economics(
        paid(pr=101, amount="999999", fingerprint=_fingerprint(101)),
        unpaid(pr=102),
    )
    low_result = allocate(low)
    high_result = allocate(high)
    assert low_result["calibration"] == high_result["calibration"]
    assert low_result["portfolio"] == high_result["portfolio"]
    assert low_result["authority"]["currency_conversion"] is False
    assert low_result["authority"]["rtc_amount_used_in_usd_math"] is False


def test_probability_cap_is_floored_to_six_places():
    receipt = economics(
        paid(pr=101, fingerprint=_fingerprint(101)),
        unpaid(pr=102),
        unpaid(pr=103),
    )
    result = allocate(receipt)
    audit = result["calibration"][0]
    assert audit["empirical_cash_observation_rate"] == "0.333333"
    assert audit["calibrated_estimated_win_probability"] == "0.333333"


def test_same_input_is_fully_deterministic():
    receipt = economics(paid(), unpaid())
    first = allocate(receipt)
    second = allocate(copy.deepcopy(receipt))
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


def test_authority_ceiling_is_explicit_and_no_cash_amount_leaks_into_calibration():
    result = allocate(economics(paid(), unpaid()))
    assert result["authority"] == {
        "eligibility": "canonical_opportunity_ranker",
        "cash_history": "realized_unit_economics_schema_and_self_integrity_only",
        "cash_evidence_authority": "inherited_not_reacquired",
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
