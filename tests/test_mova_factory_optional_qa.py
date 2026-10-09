# SPDX-License-Identifier: MIT
"""Focused optional independent-QA tests; no provider I/O."""
from datetime import datetime, timedelta, timezone
import pytest
from concierge.mova_factory import MovaFactoryError, compile_mova_packet, compile_mova_batch
from test_mova_factory import candidate


def evidence(value):
    return {
        "schema": "mova-independent-qa/v1",
        "separate_qa_required": False,
        "issue_url": value["canonical_issue_url"],
        "capture_sha256": value["canonical_capture_sha256"],
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "builder_focused_check": True,
    }


def test_optional_qa_handoff_does_not_claim_any_unperformed_review():
    c = candidate()
    c["qa_handoff"] = evidence(c)
    result = compile_mova_packet(c)
    assert [r["role"] for r in result["roles"]] == ["SCOUT", "BUILD", "PUBLISH", "COLLECT"]
    assert result["roles"][2]["depends_on"] == ["BUILD_RECEIPT"]
    assert result["coordination"]["builder_focused_check_still_required"] is True
    assert result["economics"]["compensation_claim"]["required"] is True
    assert compile_mova_packet(c) == result
    assert compile_mova_batch([c])["count"] == 1


def test_original_five_roles_remain_default():
    result = compile_mova_packet(candidate())
    assert [r["role"] for r in result["roles"]] == ["SCOUT", "BUILD", "QA", "PUBLISH", "COLLECT"]
    assert result["roles"][3]["depends_on"] == ["QA_ACCEPT_RECEIPT"]
    assert "qa_handoff" not in result["evidence"]


@pytest.mark.parametrize("key,bad", [
    ("issue_url", "https://github.com/foreign/repo/issues/42"),
    ("capture_sha256", "f" * 64),
    ("separate_qa_required", True),
    ("builder_focused_check", False),
    ("checked_at", (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()),
    ("checked_at", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
])
def test_waiver_evidence_failure_fails_closed(key, bad):
    c = candidate()
    c["qa_handoff"] = evidence(c)
    c["qa_handoff"][key] = bad
    with pytest.raises(MovaFactoryError):
        compile_mova_packet(c)


def test_existing_assigned_qa_owner_is_preserved():
    c = candidate()
    c["qa_handoff"] = evidence(c)
    with pytest.raises(MovaFactoryError, match="assigned QA"):
        compile_mova_packet(c, owners={"QA": "working-peer"})


def test_original_claim_id_stable_but_packet_changes():
    c = candidate()
    before = compile_mova_packet(c)
    c["qa_handoff"] = evidence(c)
    after = compile_mova_packet(c)
    assert before["operation_id"] == after["operation_id"]
    assert before["packet_sha256"] != after["packet_sha256"]
    assert before["target"] == after["target"]
