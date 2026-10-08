# SPDX-License-Identifier: MIT
from decimal import Decimal

import pytest

from concierge.mova_factory import MovaFactoryError, compile_mova_packet


CAPTURE = "1" * 64
REFRESH = "2" * 64
GENERATION = "3" * 64


def candidate():
    return {
        "platform": "Algora",
        "platform_green": True,
        "canonical_open": True,
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
