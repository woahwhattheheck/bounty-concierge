# SPDX-License-Identifier: MIT
from copy import deepcopy
from decimal import Decimal

import pytest

from concierge.mova_factory import MovaFactoryError, compile_mova_packet, compile_mova_batch


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
