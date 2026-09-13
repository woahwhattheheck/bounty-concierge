# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from concierge import superteam_provider as sp


class _Response:
    def __init__(self, payload=None, *, raw: bytes | None = None):
        self.status_code = 200
        self.headers = {}
        self.content = raw if raw is not None else json.dumps(payload).encode("utf-8")


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def _now():
    return datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)


def _row(*, listing_id="listing-1", slug="build-useful-agent-skill", **overrides):
    row = {
        "id": listing_id,
        "rewardAmount": 500,
        "deadline": "2026-10-01T00:00:00Z",
        "type": "bounty",
        "title": "Build a useful agent skill",
        "token": "USDC",
        "slug": slug,
        "isWinnersAnnounced": False,
        "compensationType": "fixed",
        "minRewardAsk": None,
        "maxRewardAsk": None,
        "agentAccess": "AGENT_ALLOWED",
        "status": "OPEN",
        "_count": {"Comments": 0, "Submission": 0},
        "sponsor": {
            "name": "Verified Sponsor",
            "slug": "verified-sponsor",
            "isVerified": True,
        },
        "rewards": None,
    }
    row.update(overrides)
    return row


def _details(**overrides):
    row = _row(
        isPrivate=False,
        isPublished=True,
        description="Build the integration.",
        requirements="Public repository.",
        region="Global",
        isFndnPaying=False,
        skills=[],
        eligibility=[],
    )
    row.pop("_count")
    row.update(overrides)
    return row


def test_duplicate_top_level_json_key_is_rejected():
    response = _Response(raw=b'{"status":"OPEN","status":"CLOSED"}')
    with pytest.raises(sp.SuperteamProviderError, match="duplicate JSON object key 'status'"):
        sp._response_json(response, name="hostile provider")


def test_duplicate_nested_json_key_is_rejected():
    response = _Response(raw=b'{"sponsor":{"isVerified":true,"isVerified":false}}')
    with pytest.raises(sp.SuperteamProviderError, match="duplicate JSON object key 'isVerified'"):
        sp._response_json(response, name="hostile provider")


def test_archived_details_are_rejected_before_scope_or_live_feed_use():
    session = _Session([_Response(_details(isArchived=True))])
    with pytest.raises(sp.SuperteamProviderError, match="archived"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            as_of=_now(),
            session=session,
        )
    assert len(session.calls) == 1


def test_details_absent_from_complete_live_feed_are_rejected():
    session = _Session([_Response(_details()), _Response([])])
    with pytest.raises(sp.SuperteamProviderError, match="not present in the current live"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            as_of=_now(),
            session=session,
        )


def test_live_external_id_cannot_map_to_different_slug():
    session = _Session(
        [
            _Response(_details()),
            _Response([_row(listing_id="listing-1", slug="different-live-slug")]),
        ]
    )
    with pytest.raises(sp.SuperteamProviderError, match="external id maps to a different slug"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            as_of=_now(),
            session=session,
        )


def test_live_slug_cannot_map_to_different_external_id():
    session = _Session(
        [
            _Response(_details()),
            _Response([_row(listing_id="different-id")]),
        ]
    )
    with pytest.raises(sp.SuperteamProviderError, match="slug maps to a different external id"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            as_of=_now(),
            session=session,
        )


def test_truncated_bounded_live_scan_holds_instead_of_inferring_absence():
    responses = [_Response(_details())]
    for page in range(sp.MAX_BATCHES):
        rows = [
            _row(
                listing_id=f"other-{page}-{index}",
                slug=f"other-{page}-{index}",
            )
            for index in range(50)
        ]
        responses.append(_Response(rows))
    session = _Session(responses)

    with pytest.raises(sp.SuperteamProviderError, match="truncated before listing identity could be proven"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            as_of=_now(),
            session=session,
        )
    assert len(session.calls) == 1 + sp.MAX_BATCHES


def test_exact_live_identity_is_recorded_but_never_upgrades_action_authority():
    session = _Session([_Response(_details()), _Response([_row()])])
    result = sp.fetch_listing_details(
        "build-useful-agent-skill",
        api_key="sk_test",
        as_of=_now(),
        session=session,
    )

    assert result["authority"]["live_identity"] == {
        "source": sp.LIVE_LISTINGS_URL,
        "external_id": "listing-1",
        "slug": "build-useful-agent-skill",
    }
    assert result["authority"]["dispatch"] is False
    assert result["authority"]["submission"] is False
    assert result["authority"]["payout"] is False
    assert result["authority"]["cash_claim"] is False
