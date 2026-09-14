from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from concierge.collection_dispatch_guard import (
    CollectionDispatchGuardError,
    acquire_guarded_local_lease,
    authorize_collection_dispatch,
    derive_collection_dispatch_identity,
)
from concierge.collection_request import compile_collection_request
from concierge.outbound_singlewriter import LeaseBusy


NOW = datetime(2026, 9, 14, 23, 40, 0, tzinfo=timezone.utc)
OWNER = "ZSM-U7P5"
RECIPIENT = "payables@example.com"


def _packet(*, accepted: bool = False, payout_handle: str = "bryce-main", repo: str = "Acme/Widget"):
    acceptance = {
        "kind": "AWARDED" if accepted else "NONE",
        "evidence_ref": "https://example.com/award/7" if accepted else None,
        "evidence_sha256": "b" * 64 if accepted else None,
    }
    payload = {
        "schema": "bounty-collection-request-input/v1",
        "sponsor_name": "Acme Sponsor",
        "work": {
            "repo": repo,
            "pr": 7,
            "canonical_url": f"https://github.com/{repo}/pull/7",
            "head_sha": "a" * 40,
            "state": "MERGED",
            "advertised_amount": "500",
            "currency": "USD",
        },
        "payout_route": {"type": "HOSTED_HANDLE", "value": payout_handle},
        "acceptance": acceptance,
    }
    return compile_collection_request(payload)


def _queries(identity, *, recipient=RECIPIENT, providers=("gmail", "outlook"), sent_provider=None, complete=True):
    rows = []
    for index, provider in enumerate(providers):
        observations = []
        if provider == sent_provider:
            observations.append(
                {
                    "state": "sent",
                    "message_id": f"m-{index}",
                    "recipient": recipient,
                    "offer_key": identity["offer_key"],
                    "observed_at": "2026-09-14T23:39:30Z",
                }
            )
        rows.append(
            {
                "provider": provider,
                "recipient": recipient,
                "offer_key": identity["offer_key"],
                "complete": complete,
                "completed_at": "2026-09-14T23:39:40Z",
                "observations": observations,
            }
        )
    return rows


def _claim(identity, *, owner=OWNER, event_id="claim-zsm", order="1"):
    return {
        "operation_key": identity["operation_key"],
        "kind": "CLAIM",
        "owner": owner,
        "event_id": event_id,
        "order": order,
    }


def _authorize(packet, *, recipient=RECIPIENT, queries=None, events=None, snapshot_complete=True, providers=("gmail", "outlook")):
    identity = derive_collection_dispatch_identity(packet)
    return authorize_collection_dispatch(
        packet,
        recipient=recipient,
        provider_queries=_queries(identity, recipient=recipient, providers=providers) if queries is None else queries,
        required_providers=providers,
        owner=OWNER,
        claim_event_id="claim-zsm",
        shared_events=[_claim(identity)] if events is None else events,
        snapshot_complete=snapshot_complete,
        now=NOW,
        max_query_age_seconds=300,
    )


def test_clear_requires_provider_truth_and_shared_winner():
    packet = _packet()
    result = _authorize(packet)
    assert result["disposition"] == "CLEAR"
    assert result["dispatch"] is True
    assert result["shared_claim"]["authorized"] is True
    assert result["provider_gate"]["dispatch"] is True
    assert result["singlewriter"]["local_lease_required"] is True
    assert result["authority"]["external_send_performed"] is False


def test_provider_sent_match_is_terminal_dnr_without_needing_election():
    packet = _packet()
    identity = derive_collection_dispatch_identity(packet)
    result = _authorize(
        packet,
        queries=_queries(identity, sent_provider="outlook"),
        events=[],
    )
    assert result["disposition"] == "DNR"
    assert result["dispatch"] is False
    assert result["reason_codes"] == ["PROVIDER_GATE:DNR"]
    assert result["provider_gate"]["signals"]["matching_sent_count"] == 1


def test_missing_required_provider_is_hold():
    packet = _packet()
    identity = derive_collection_dispatch_identity(packet)
    result = authorize_collection_dispatch(
        packet,
        recipient=RECIPIENT,
        provider_queries=_queries(identity, providers=("gmail",)),
        required_providers=("gmail", "outlook"),
        owner=OWNER,
        claim_event_id="claim-zsm",
        shared_events=[_claim(identity)],
        snapshot_complete=True,
        now=NOW,
    )
    assert result["disposition"] == "HOLD"
    assert "REQUIRED_PROVIDER_MISSING" in result["provider_gate"]["reason_codes"]


def test_incomplete_shared_snapshot_is_hold():
    packet = _packet()
    result = _authorize(packet, snapshot_complete=False)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["SHARED_CLAIM:BLOCKED"]


def test_losing_shared_election_is_hold():
    packet = _packet()
    identity = derive_collection_dispatch_identity(packet)
    events = [
        _claim(identity, owner="other-seat", event_id="claim-other", order="0"),
        _claim(identity, order="1"),
    ]
    result = _authorize(packet, events=events)
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["SHARED_CLAIM:LOST_ELECTION"]
    assert result["shared_claim"]["winner_owner"] == "other-seat"


def test_shared_terminal_sent_is_dnr():
    packet = _packet()
    identity = derive_collection_dispatch_identity(packet)
    events = [
        _claim(identity, owner="other-seat", event_id="claim-other", order="0"),
        {
            "operation_key": identity["operation_key"],
            "kind": "SENT",
            "owner": "other-seat",
            "event_id": "sent-other",
            "order": "0.5",
        },
        _claim(identity, order="1"),
    ]
    result = _authorize(packet, events=events)
    assert result["disposition"] == "DNR"
    assert result["reason_codes"] == ["SHARED_CLAIM:ALREADY_SENT"]


def test_packet_regeneration_cannot_mint_second_operation_key():
    first = derive_collection_dispatch_identity(_packet(payout_handle="route-a"))
    second = derive_collection_dispatch_identity(_packet(payout_handle="route-b"))
    assert first["packet_sha256"] != second["packet_sha256"]
    assert first["offer_key"] == second["offer_key"]
    assert first["operation_key"] == second["operation_key"]


def test_repo_case_cannot_mint_second_operation_key():
    first = derive_collection_dispatch_identity(_packet(repo="Acme/Widget"))
    second = derive_collection_dispatch_identity(_packet(repo="acme/widget"))
    assert first["operation_key"] == second["operation_key"]


def test_assessment_and_payment_have_distinct_operation_keys():
    assessment = derive_collection_dispatch_identity(_packet(accepted=False))
    payment = derive_collection_dispatch_identity(_packet(accepted=True))
    assert assessment["phase"] == "assessment"
    assert payment["phase"] == "payment"
    assert assessment["operation_key"] != payment["operation_key"]
    assert assessment["offer_key"] != payment["offer_key"]


def test_recipient_choice_does_not_change_shared_singlewriter_identity():
    packet = _packet()
    identity = derive_collection_dispatch_identity(packet)
    first = _authorize(packet, recipient="one@example.com")
    second = _authorize(packet, recipient="two@example.com")
    assert first["operation_key"] == identity["operation_key"]
    assert second["operation_key"] == identity["operation_key"]
    assert first["provider_gate"]["recipient_id"] != second["provider_gate"]["recipient_id"]


def test_local_lease_closes_same_owner_pre_send_race(tmp_path):
    result = _authorize(_packet())
    lease = acquire_guarded_local_lease(result, root=tmp_path, owner=OWNER, now=NOW)
    assert lease["status"] == "HELD"
    assert lease["operation_key"] == result["operation_key"]
    with pytest.raises(LeaseBusy):
        acquire_guarded_local_lease(result, root=tmp_path, owner=OWNER, now=NOW)


def test_guard_receipt_tamper_blocks_local_lease(tmp_path):
    result = _authorize(_packet())
    tampered = deepcopy(result)
    tampered["owner"] = "attacker"
    with pytest.raises(CollectionDispatchGuardError) as caught:
        acquire_guarded_local_lease(tampered, root=tmp_path, owner="attacker", now=NOW)
    assert caught.value.code == "GUARD_RECEIPT_TAMPERED"


def test_packet_tamper_is_rejected():
    packet = _packet()
    packet["source"]["work"]["advertised_amount"] = "999"
    with pytest.raises(CollectionDispatchGuardError) as caught:
        derive_collection_dispatch_identity(packet)
    assert caught.value.code == "PACKET_VERIFICATION_FAILED"
