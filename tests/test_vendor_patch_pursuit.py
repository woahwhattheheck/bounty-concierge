from copy import deepcopy
import hashlib

import pytest

from concierge import vendor_patch_pursuit as vpp


NOW = "2026-09-15T00:00:00Z"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def valid_document():
    return {
        "schema": vpp.SCHEMA_VERSION,
        "opportunity_id": "protobuf-29672-google-patch-rewards",
        "operator_id": "ZCV-K8M3",
        "candidate": {
            "repository": "protocolbuffers/protobuf",
            "issue_number": 29672,
            "issue_url": "https://github.com/protocolbuffers/protobuf/issues/29672",
            "issue_state": "OPEN",
            "assignee_count": 0,
            "open_pr_count": 0,
            "pr_search_complete": True,
            "observed_at": "2026-09-14T23:50:00Z",
            "evidence_ref": "https://api.github.com/repos/protocolbuffers/protobuf/issues/29672",
            "evidence_sha256": digest("issue"),
        },
        "program": {
            "program_id": "google-patch-rewards",
            "program_name": "Google Patch Rewards",
            "source_url": "https://bughunters.google.com/open-source-security/patch-rewards",
            "status": "ACTIVE",
            "repository_in_scope": True,
            "scope_repository": "protocolbuffers/protobuf",
            "reward_state": "PROGRAM_SCHEDULE",
            "currency": "USD",
            "min_reward_minor": 50000,
            "max_reward_minor": 1500000,
            "contribution_route": "MERGED_PR_THEN_CLAIM",
            "observed_at": "2026-09-14T23:00:00Z",
            "evidence_ref": "https://bughunters.google.com/open-source-security/patch-rewards",
            "evidence_sha256": digest("program"),
        },
        "reservation": {
            "reservation_key": "vendor-patch:protocolbuffers/protobuf#29672",
            "owner_id": "ZCV-K8M3",
            "state": "ACTIVE",
            "collision_count": 0,
            "observed_at": "2026-09-14T23:55:00Z",
            "expires_at": "2026-09-15T03:55:00Z",
            "evidence_ref": "https://tokenjunkielabs.slack.com/archives/C0BTRNE6Y58/p1789430527568489",
            "evidence_sha256": digest("reservation"),
        },
    }


def compile_doc(doc=None, now=NOW):
    return vpp.compile_pursuit(valid_document() if doc is None else doc, now_utc=now)


def test_clean_program_wide_reward_candidate_is_reviewable():
    packet, markdown, receipt = compile_doc()
    assert packet["disposition"] == "PURSUE_FOR_OWNER_REVIEW"
    assert packet["reason_codes"] == []
    assert packet["payoff_path_handoff"]["mechanism"] == "BOUNTY"
    assert packet["authority_ceiling"]["authorizes_submission"] is False
    assert "program schedule only" in markdown
    assert receipt["disposition"] == "PURSUE_FOR_OWNER_REVIEW"


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("candidate", "issue_state"), "CLOSED", "CANDIDATE_ISSUE_NOT_OPEN"),
        (("candidate", "assignee_count"), 1, "CANDIDATE_ASSIGNED"),
        (("candidate", "open_pr_count"), 1, "CANDIDATE_PR_OCCUPIED"),
        (("candidate", "pr_search_complete"), False, "CANDIDATE_PR_SEARCH_INCOMPLETE"),
        (("program", "status"), "PAUSED", "PROGRAM_NOT_ACTIVE"),
        (("program", "repository_in_scope"), False, "PROGRAM_REPOSITORY_OUT_OF_SCOPE"),
        (("reservation", "state"), "RELEASED", "RESERVATION_NOT_ACTIVE"),
        (("reservation", "collision_count"), 1, "RESERVATION_COLLISION"),
    ],
)
def test_fail_closed_signals_hold(path, value, reason):
    doc = valid_document()
    doc[path[0]][path[1]] = value
    packet, _, _ = compile_doc(doc)
    assert packet["disposition"] == "HOLD"
    assert reason in packet["reason_codes"]


def test_program_without_reward_schedule_cannot_be_called_paid():
    doc = valid_document()
    doc["program"].update(
        {
            "reward_state": "UNSPECIFIED_BY_SOURCE",
            "currency": None,
            "min_reward_minor": None,
            "max_reward_minor": None,
        }
    )
    packet, _, _ = compile_doc(doc)
    assert packet["disposition"] == "HOLD"
    assert packet["reason_codes"] == ["PROGRAM_REWARD_NOT_EVIDENCED"]


def test_candidate_observation_expires_after_thirty_minutes():
    doc = valid_document()
    doc["candidate"]["observed_at"] = "2026-09-14T23:29:59Z"
    packet, _, _ = compile_doc(doc)
    assert "CANDIDATE_OBSERVATION_STALE" in packet["reason_codes"]


def test_program_evidence_expires_after_seven_days():
    doc = valid_document()
    doc["program"]["observed_at"] = "2026-09-07T23:59:59Z"
    packet, _, _ = compile_doc(doc)
    assert "PROGRAM_OBSERVATION_STALE" in packet["reason_codes"]


def test_reservation_expiry_and_owner_mismatch_hold():
    doc = valid_document()
    doc["reservation"]["owner_id"] = "OtherSeat"
    doc["reservation"]["expires_at"] = "2026-09-14T23:59:59Z"
    packet, _, _ = compile_doc(doc)
    assert "RESERVATION_OWNER_MISMATCH" in packet["reason_codes"]
    assert "RESERVATION_EXPIRED" in packet["reason_codes"]


def test_reservation_key_must_bind_exact_upstream_issue():
    doc = valid_document()
    doc["reservation"]["reservation_key"] = "vendor-patch:protocolbuffers/protobuf#999"
    packet, _, _ = compile_doc(doc)
    assert packet["reason_codes"] == ["RESERVATION_KEY_MISMATCH"]


def test_scope_repository_mismatch_is_structural_error():
    doc = valid_document()
    doc["program"]["scope_repository"] = "other/repo"
    with pytest.raises(vpp.VendorPatchPursuitInputError, match="must equal"):
        compile_doc(doc)


def test_issue_url_must_bind_exact_issue():
    doc = valid_document()
    doc["candidate"]["issue_url"] = "https://github.com/protocolbuffers/protobuf/issues/1"
    with pytest.raises(vpp.VendorPatchPursuitInputError, match="exactly identify"):
        compile_doc(doc)


def test_unspecified_reward_cannot_smuggle_amounts():
    doc = valid_document()
    doc["program"]["reward_state"] = "UNSPECIFIED_BY_SOURCE"
    with pytest.raises(vpp.VendorPatchPursuitInputError, match="must not carry"):
        compile_doc(doc)


def test_receipt_verifies_while_evidence_is_current():
    packet, markdown, receipt = compile_doc()
    current = vpp.verify_pursuit(
        valid_document(), packet, markdown, receipt, now_utc="2026-09-15T00:10:00Z"
    )
    assert current["disposition"] == "PURSUE_FOR_OWNER_REVIEW"


def test_packet_tamper_is_rejected():
    packet, markdown, receipt = compile_doc()
    packet = deepcopy(packet)
    packet["candidate"]["issue_number"] = 1
    with pytest.raises(vpp.VendorPatchPursuitVerificationError, match="packet"):
        vpp.verify_pursuit(
            valid_document(), packet, markdown, receipt, now_utc=NOW
        )


def test_previous_pursue_receipt_fails_when_candidate_freshness_expires():
    packet, markdown, receipt = compile_doc()
    with pytest.raises(
        vpp.VendorPatchPursuitVerificationError, match="no longer current"
    ):
        vpp.verify_pursuit(
            valid_document(),
            packet,
            markdown,
            receipt,
            now_utc="2026-09-15T00:21:00Z",
        )


def test_unknown_fields_fail_closed():
    doc = valid_document()
    doc["candidate"]["invented"] = True
    with pytest.raises(vpp.VendorPatchPursuitInputError, match="shape mismatch"):
        compile_doc(doc)
