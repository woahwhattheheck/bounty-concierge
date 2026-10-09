# SPDX-License-Identifier: MIT
"""Only the claim-text publication behavior; no provider calls."""
from concierge.bounty_claim_text import audit_bounty_claim_text
from concierge.github_publish_preflight import execute_publish_operation


def test_affirmative_bounty_request():
    assert audit_bounty_claim_text(
        "Fixes #742. /claim #742. I affirmatively request the advertised bounty payment."
    ) == ()


def test_waiver_overrides_contradictory_positive_request():
    assert audit_bounty_claim_text(
        "I am not claiming this bounty. I request the advertised bounty payment."
    ) == ("BOUNTY_COMPENSATION_WAIVER",)


def test_test-evidence_limitation_is_not_bounty_waiver():
    assert audit_bounty_claim_text(
        "I am not claiming tests passed. We request eligible compensation."
    ) == ()


def test_slash_claim_alone_not_affirmative_payment_request():
    assert audit_bounty_claim_text("/claim #17\nCloses #17") == (
        "BOUNTY_AFFIRMATIVE_REQUEST_MISSING",
    )


def test_rejected_before_any_sponsor_reads_or_provider_writes(tmp_path):
    called = []
    receipt = execute_publish_operation(
        tmp_path / "rail.sqlite",
        "test-only-token",
        rail="private-token",
        actor="original-author",
        operation="test-bounty-post",
        action="create-pull-request",
        repo="example/project",
        carrier="example/project#12",
        expected_head="a" * 40,
        transport=lambda: called.append("write"),
        provider_repository_state=lambda: called.append("read") or {},
        provider_reconcile=lambda: called.append("census") or None,
        bounty_claim_required=True,
        bounty_claim_body="I am not claiming this bounty.",
    )
    assert called == []
    assert receipt["status"] == "BOUNTY_CLAIM_TEXT_BLOCKED"
    assert receipt["provider_called"] is False
    assert set(receipt["finding_codes"]) == {
        "BOUNTY_COMPENSATION_WAIVER", "BOUNTY_AFFIRMATIVE_REQUEST_MISSING"
    }


def test_required_missing_claim_body_fails_closed(tmp_path):
    called = []
    receipt = execute_publish_operation(
        tmp_path / "rail.sqlite", "test-only-token", rail="private-token",
        actor="original-author", operation="test-no-body",
        action="update-pr-body", repo="example/project",
        carrier="example/project#12", expected_head="a" * 40,
        transport=lambda: called.append("write"), bounty_claim_required=True,
    )
    assert receipt["finding_codes"] == ["BOUNTY_CLAIM_BODY_MISSING"]
    assert called == []
