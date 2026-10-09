# SPDX-License-Identifier: MIT
"""Focused offline regression for MOVA's source-bound optional QA handoff."""
import copy

import pytest

from concierge.mova_factory import SCHEMA
from concierge.mova_progress import (
    MovaProgressError,
    _digest,
    compile_mova_progress,
)

_TARGET = "paying/repo#42"
_SOURCE_HEAD = "c" * 40


def _packet(*, independent_qa=True):
    names = (
        ("SCOUT", "BUILD", "QA", "PUBLISH", "COLLECT")
        if independent_qa else ("SCOUT", "BUILD", "PUBLISH", "COLLECT")
    )
    roles = []
    for name in names:
        dependency = {
            "SCOUT": [],
            "BUILD": ["SCOUT_RECEIPT"],
            "QA": ["BUILD_RECEIPT"],
            "PUBLISH": ["QA_ACCEPT_RECEIPT" if independent_qa else "BUILD_RECEIPT"],
            "COLLECT": ["PUBLICATION_RECEIPT"],
        }[name]
        roles.append({
            "role": name,
            "target_key": _TARGET,
            "lease_key": f"MOVA-OPTIONALQA:{name}",
            "owner": "fleet-member",
            "account": "woahwhattheheck" if name in {"PUBLISH", "COLLECT"} else "tokenjunkielabs",
            "depends_on": dependency,
        })
    core = {
        "schema": SCHEMA,
        "operation_id": "MOVA-OPTIONALQA",
        "target": {
            "repo": "paying/repo",
            "issue_number": 42,
            "target_key": _TARGET,
            "canonical_issue_url": "https://github.com/paying/repo/issues/42",
        },
        "roles": roles,
        "economics": {
            "compensation_claim": {
                "required": True,
                "text": "I request the sponsor bounty payment for the original work.",
            },
        },
        "evidence": {"canonical_capture_sha256": "a" * 64},
        "coordination": {},
    }
    if not independent_qa:
        core["evidence"]["qa_handoff"] = {
            "schema": "mova-independent-qa/v1",
            "separate_qa_required": False,
            "issue_url": core["target"]["canonical_issue_url"],
            "capture_sha256": "a" * 64,
            "checked_at": "2026-10-09T04:30:00+00:00",
            "builder_focused_check": True,
        }
        core["coordination"] = {
            "independent_qa_handoff": "NOT_REQUIRED_WITH_SOURCE_EVIDENCE",
            "builder_focused_check_still_required": True,
        }
    return {**core, "packet_sha256": _digest(core)}


def _receipt(packet, name, *, head=_SOURCE_HEAD):
    marker = {
        "SCOUT": ("SCOUT_RECEIPT", "COMPLETE"),
        "BUILD": ("BUILD_RECEIPT", "COMPLETE"),
        "QA": ("QA_ACCEPT_RECEIPT", "ACCEPTED"),
        "PUBLISH": ("PUBLICATION_RECEIPT", "COMPLETE"),
        "COLLECT": ("COLLECT_RECEIPT", "COMPLETE"),
    }
    role = next(role for role in packet["roles"] if role["role"] == name)
    value = {
        "schema": "mova-role-receipt/v1",
        "target_key": _TARGET,
        "operation_id": packet["operation_id"],
        "packet_sha256": packet["packet_sha256"],
        "lease_key": role["lease_key"],
        "role": name,
        "owner": role["owner"],
        "receipt_type": marker[name][0],
        "status": marker[name][1],
        "receipt_id": f"optionalqa-{name}",
        "evidence_sha256": "b" * 64,
    }
    if name in {"BUILD", "QA", "PUBLISH"}:
        value["head_sha"] = head
    if name == "PUBLISH":
        value["publication_url"] = "https://github.com/paying/repo/pull/99"
    return value


def test_optional_qa_wave_reaches_collection_without_phantom_qa():
    packet = _packet(independent_qa=False)
    receipts = []
    for role, next_role in (
        ("SCOUT", "BUILD"), ("BUILD", "PUBLISH"),
        ("PUBLISH", "COLLECT"), ("COLLECT", None)
    ):
        receipts.append(_receipt(packet, role))
        state = compile_mova_progress(packet, receipts)["results"][0]
        assert state["completed_roles"][-1] == role
        assert state["next_role"] == next_role
        assert state["award_state"] == "UNVERIFIED"
        assert state["payment_state"] == "UNVERIFIED"
    assert state["status"] == "SETTLEMENT_PROVIDER_RECHECK_REQUIRED"
    assert state["settlement_followup"]["publication_head"] == _SOURCE_HEAD
    assert state["settlement_followup"]["original_collect_account"] == "woahwhattheheck"
    assert "request the sponsor bounty payment" in (
        state["settlement_followup"]["compensation_claim"]["text"]
    )


def test_original_five_stage_qa_acceptance_still_required():
    packet = _packet(independent_qa=True)
    scout_build = [_receipt(packet, role) for role in ("SCOUT", "BUILD")]
    next_state = compile_mova_progress(packet, scout_build)["results"][0]
    assert next_state["next_role"] == "QA"
    full = scout_build + [_receipt(packet, role) for role in ("QA", "PUBLISH", "COLLECT")]
    assert compile_mova_progress(packet, full)["results"][0]["status"] == (
        "SETTLEMENT_PROVIDER_RECHECK_REQUIRED"
    )


def test_optional_qa_must_be_bound_to_original_capture_and_build():
    packet = _packet(independent_qa=False)
    for alteration in ("remove_waiver", "wrong_capture", "missing_build_dependency"):
        broken = copy.deepcopy(packet)
        if alteration == "remove_waiver":
            del broken["evidence"]["qa_handoff"]
        elif alteration == "wrong_capture":
            broken["evidence"]["qa_handoff"]["capture_sha256"] = "f" * 64
        else:
            broken["roles"][2]["depends_on"] = ["QA_ACCEPT_RECEIPT"]
        broken["packet_sha256"] = _digest({
            key: value for key, value in broken.items() if key != "packet_sha256"
        })
        with pytest.raises(MovaProgressError, match="optional-QA"):
            compile_mova_progress(broken, [])


def test_omitted_qa_receipt_and_source_head_drift_fail_closed():
    packet = _packet(independent_qa=False)
    with pytest.raises(MovaProgressError, match="not present"):
        compile_mova_progress(packet, [_receipt(_packet(independent_qa=True), "QA")])
    receipts = [_receipt(packet, role) for role in ("SCOUT", "BUILD")]
    receipts.append(_receipt(packet, "PUBLISH", head="e" * 40))
    with pytest.raises(MovaProgressError, match="source head"):
        compile_mova_progress(packet, receipts)
    with pytest.raises(MovaProgressError, match="non-contiguous"):
        compile_mova_progress(packet, [_receipt(packet, "PUBLISH")])
