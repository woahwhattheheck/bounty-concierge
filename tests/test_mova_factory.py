# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from concierge.mova_factory import (
    MovaFactoryError,
    compile_mova_packet as _compile_mova_packet,
    compile_mova_batch as _compile_mova_batch,
)

# Synthetic policy fixtures, NOT claims that the named sponsors have paid.
NOW = datetime.now(timezone.utc)
POLICY = {
    "schema_version": 1,
    "policy_id": "REPO-ELIGIBILITY-20261009-BRYCE-01",
    "default_status": "HOLD_UNVERIFIED",
    "activity_max_age_days": 90,
    "observed_at": NOW.isoformat(),
    "repositories": {},
}
for _repo in ("owner/repo", "another/repo"):
    POLICY["repositories"][_repo] = {
        "status": "QUALIFIED_ACTIVE_PAID",
        "maintainer_activity": {
            "kind": "merge", "actor": "fixture-maintainer",
            "event_at": (NOW - timedelta(hours=1)).isoformat(),
            "evidence_url": f"https://github.com/{_repo}/pull/7",
        },
        "paid_merge_history": [{
            "payer": _repo.split("/")[0],
            "recipient": "fixture-recipient",
            "currency": "USD", "amount": "25",
            "merged_pr_url": f"https://github.com/{_repo}/pull/6",
            "payment_evidence_url": "https://algora.io/claims/synthetic-example",
            "paid_at": (NOW - timedelta(days=1)).isoformat(),
        }],
    }


def compile_mova_packet(*args, **kwargs):
    return _compile_mova_packet(*args, repo_eligibility_policy=POLICY, **kwargs)


def compile_mova_batch(*args, **kwargs):
    return _compile_mova_batch(*args, repo_eligibility_policy=POLICY, **kwargs)



CAPTURE = "1" * 64
REFRESH = "2" * 64
GENERATION = "3" * 64


def candidate():
    return {
        "platform": "Algora",
        "platform_green": True,
        "canonical_open": True,
        "repository_archived": False,
        "repo": "owner/repo",
        "issue_number": 42,
        "canonical_issue_url": "https://github.com/owner/repo/issues/42",
        "reward_usd": "50",
        "canonical_capture_sha256": CAPTURE,
        "source_generation_sha256": GENERATION,
        "expected_head": "a" * 40,
        "work_account": "tokenjunkielabs",
        "submission_account": "woahwhattheheck",
        "compensation_claim": {
            "required": True,
            "text": "@algora-pbc /claim #42 — compensation requested for accepted work.",
        },
        "constraints": ["focused validation only", "preserve existing contributors"],
        "lease_receipt": {
            "schema": "bounty-work-order-lease/v1",
            "status": "READY",
            "dispatch": True,
            "repo": "owner/repo",
            "number": 42,
            "original": {
                "capture_receipt_sha256": CAPTURE,
                "source_generation_sha256": GENERATION,
            },
            "refreshed": {
                "capture_receipt_sha256": REFRESH,
                "source_generation_sha256": GENERATION,
            },
        },
    }


def test_compiles_deterministic_pipeline_with_account_routing():
    first = compile_mova_packet(candidate(), minimum_reward_usd=Decimal("15"))
    second = compile_mova_packet(candidate(), minimum_reward_usd=Decimal("15"))

    assert first == second
    assert first["operation_id"].startswith("MOVA-")
    assert len(first["packet_sha256"]) == 64
    assert [role["role"] for role in first["roles"]] == [
        "SCOUT", "BUILD", "QA", "PUBLISH", "COLLECT"
    ]
    assert first["roles"][1]["account"] == "tokenjunkielabs"
    assert first["roles"][3]["account"] == "woahwhattheheck"
    assert first["roles"][4]["depends_on"] == ["PUBLICATION_RECEIPT"]
    assert first["economics"]["compensation_claim"]["required"] is True


def test_rejects_archived_or_unverified_sponsor_repositories():
    for value in (True, "false", None):
        blocked = candidate()
        if value is None:
            del blocked["repository_archived"]
        else:
            blocked["repository_archived"] = value
        with pytest.raises(MovaFactoryError, match="provider-verified as unarchived"):
            compile_mova_packet(blocked)


def test_rejects_waiver_language_in_required_compensation_claim():
    value = candidate()
    value["compensation_claim"]["text"] = "This is not a claim for compensation."
    with pytest.raises(MovaFactoryError, match="waiver"):
        compile_mova_packet(value)


@pytest.mark.parametrize("unclaimed_text", [
    "Patch uploaded; please review.",
    "Bounty issue addressed; changes ready for maintainer review.",
    "I do not claim the $50 bounty.",
    "I am not requesting compensation for this work.",
    "I claim no payment for this patch.",
])
def test_paid_packets_cannot_silently_omit_or_waive_claims(unclaimed_text):
    value = candidate()
    value["compensation_claim"]["text"] = unclaimed_text
    with pytest.raises(MovaFactoryError, match="affirmative|waiver"):
        compile_mova_packet(value)


@pytest.mark.parametrize("claim_text", [
    "@algora-pbc /claim #42",
    "I claim the $50 bounty and request payout upon acceptance.",
    "Payment requested for this accepted contribution.",
])
def test_valid_affirmative_reward_requests_survive(claim_text):
    value = candidate()
    value["compensation_claim"]["text"] = claim_text
    result = compile_mova_packet(value)
    assert result["economics"]["compensation_claim"]["text"] == claim_text


def test_rejects_stale_or_mismatched_lease_before_dispatch():
    value = candidate()
    value["lease_receipt"]["status"] = "STALE"
    value["lease_receipt"]["dispatch"] = False
    with pytest.raises(MovaFactoryError, match="READY"):
        compile_mova_packet(value)


def test_ready_lease_binds_capture_and_source_generation():
    stale = candidate()
    stale["canonical_capture_sha256"] = "f" * 64
    with pytest.raises(MovaFactoryError, match="canonical capture"):
        compile_mova_packet(stale)

    drifted = candidate()
    drifted["lease_receipt"]["original"]["source_generation_sha256"] = "f" * 64
    with pytest.raises(MovaFactoryError, match="source generation drift"):
        compile_mova_packet(drifted)


def test_paid_work_cannot_omit_compensation_claim():
    value = candidate()
    value["compensation_claim"] = {"required": False, "text": None}
    with pytest.raises(MovaFactoryError, match="affirmative"):
        compile_mova_packet(value)


def test_batch_deduplicates_exact_repeats_and_sorts_targets():
    first = candidate()
    second = deepcopy(first)
    second["repo"] = "another/repo"
    second["issue_number"] = 24
    second["canonical_issue_url"] = "https://github.com/another/repo/issues/24"
    second["lease_receipt"]["repo"] = "another/repo"
    second["lease_receipt"]["number"] = 24
    second["compensation_claim"]["text"] = "@algora-pbc /claim #24 — payment requested."
    wave = compile_mova_batch([first, second, deepcopy(first)])
    reverse = compile_mova_batch([second, first, deepcopy(first)])
    assert wave == reverse
    assert wave["count"] == 2
    assert wave["deduplicated_count"] == 1
    assert [target["target_key"] for target in wave["targets"]] == [
        "another/repo#24", "owner/repo#42"
    ]
    assert all(packet["economics"]["compensation_claim"]["required"]
               for packet in wave["packets"])


def test_batch_rejects_conflicting_source_snapshot_same_issue():
    first = candidate()
    drifted = deepcopy(first)
    drifted["canonical_capture_sha256"] = "f" * 64
    drifted["lease_receipt"]["original"]["capture_receipt_sha256"] = "f" * 64
    with pytest.raises(MovaFactoryError, match="conflicting READY"):
        compile_mova_batch([first, drifted])


@pytest.mark.parametrize("override", [
    Decimal("0"), Decimal("-1"), Decimal("14.99"),
    Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), "0",
])
def test_configured_reward_floor_cannot_bypass_15_usd_policy(override):
    """Packet and batch entrypoints reject zero, subfloor and malformed floors."""
    with pytest.raises(MovaFactoryError, match="minimum_reward_usd"):
        compile_mova_packet(candidate(), minimum_reward_usd=override)
    with pytest.raises(MovaFactoryError, match="minimum_reward_usd"):
        compile_mova_batch([candidate()], minimum_reward_usd=override)


def test_higher_reward_floor_still_restricts_claims():
    value = candidate()
    value["reward_usd"] = "24.99"
    with pytest.raises(MovaFactoryError, match="active-work floor"):
        compile_mova_packet(value, minimum_reward_usd=Decimal("25"))
    value["reward_usd"] = "25"
    accepted = compile_mova_packet(value, minimum_reward_usd=Decimal("25"))
    assert accepted["economics"]["minimum_reward_usd"] == "25"


@pytest.mark.parametrize("oversized", [
    "1e100000",
    "1e1000000",
    "1e13",
    "15." + "0" * 100,
])
def test_extreme_decimal_reward_expansion_is_rejected(oversized):
    """Finite scientific-notation values must not expand to giant work packets."""
    value = candidate()
    value["reward_usd"] = oversized
    with pytest.raises(MovaFactoryError, match="bounded"):
        compile_mova_packet(value)
    with pytest.raises(MovaFactoryError, match="bounded"):
        compile_mova_batch([value])


def test_extreme_caller_floor_is_rejected_before_output_formatting():
    # The floor itself is formatted as fixed-point in the result, so fence it.
    huge = Decimal("1e100000")
    with pytest.raises(MovaFactoryError, match="bounded"):
        compile_mova_packet(candidate(), minimum_reward_usd=huge)
    with pytest.raises(MovaFactoryError, match="bounded"):
        compile_mova_batch([candidate()], minimum_reward_usd=huge)


def test_bounded_reward_keeps_normal_currency_and_raised_floor():
    value = candidate()
    value["reward_usd"] = "2500.50"
    result = compile_mova_packet(value, minimum_reward_usd=Decimal("25"))
    assert result["economics"]["reward_usd"] == "2500.5"
    assert result["economics"]["minimum_reward_usd"] == "25"


def test_direct_factory_rejects_missing_or_unqualified_sponsor_policy():
    value = candidate()
    with pytest.raises(MovaFactoryError, match="eligibility policy is required"):
        _compile_mova_packet(value)
    with pytest.raises(MovaFactoryError, match="eligibility policy is required"):
        _compile_mova_batch([value])
    inactive = deepcopy(POLICY)
    inactive["repositories"]["owner/repo"]["status"] = "HOLD_UNVERIFIED"
    with pytest.raises(MovaFactoryError, match="not QUALIFIED_ACTIVE_PAID"):
        _compile_mova_packet(value, repo_eligibility_policy=inactive)
    stale = deepcopy(POLICY)
    stale["observed_at"] = (NOW - timedelta(days=3)).isoformat()
    with pytest.raises(MovaFactoryError, match="stale"):
        _compile_mova_packet(value, repo_eligibility_policy=stale)
    missing_receipt = deepcopy(POLICY)
    missing_receipt["repositories"]["owner/repo"]["paid_merge_history"] = []
    with pytest.raises(MovaFactoryError, match="historical completed"):
        _compile_mova_packet(value, repo_eligibility_policy=missing_receipt)


def test_direct_factory_qualified_policy_is_auditable_and_batch_bound():
    single = compile_mova_packet(candidate())
    batch = compile_mova_batch([candidate(), candidate()])
    assert batch["packets"][0] == single
    receipt = single["evidence"]["repo_paid_eligibility"]
    assert receipt["status"] == "QUALIFIED_ACTIVE_PAID"
    assert len(receipt["policy_snapshot_sha256"]) == 64
    assert receipt["paid_merge_receipt_count"] == 1
