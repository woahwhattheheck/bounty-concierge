# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from concierge import superteam_provider as sp


class _StreamingResponse:
    def __init__(self, chunks=(), *, status=200, headers=None):
        self.status_code = status
        self.headers = {} if headers is None else dict(headers)
        self._chunks = list(chunks)
        self.iter_calls = 0
        self.closed = False

    def iter_content(self, *, chunk_size):
        assert chunk_size == sp.STREAM_CHUNK_BYTES
        self.iter_calls += 1
        yield from self._chunks

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _row(**overrides):
    value = {
        "id": "listing-1",
        "rewardAmount": 500,
        "deadline": "2026-10-01T00:00:00Z",
        "type": "bounty",
        "title": "Build a useful agent skill",
        "token": "USDC",
        "slug": "build-useful-agent-skill",
        "isWinnersAnnounced": False,
        "compensationType": "fixed",
        "minRewardAsk": None,
        "maxRewardAsk": None,
        "agentAccess": "AGENT_ALLOWED",
        "status": "OPEN",
        "_count": {"Comments": 0, "Submission": 0},
        "sponsor": {"name": "Verified Sponsor", "isVerified": True},
        "rewards": None,
    }
    value.update(overrides)
    return value


def _details(**overrides):
    value = _row(
        isPrivate=False,
        isPublished=True,
        description="Build the integration.",
        requirements="Public repository.",
        region="Global",
        isFndnPaying=False,
        skills=[],
        eligibility=[],
    )
    value.pop("_count", None)
    value.update(overrides)
    return value


def _json_response(value):
    return _StreamingResponse([json.dumps(value).encode("utf-8")])


def test_production_read_requests_streaming_and_closes_response():
    response = _json_response([])
    session = _Session(response)

    result = sp.fetch_live_opportunities(
        api_key="sk_test",
        take=1,
        as_of=datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
        session=session,
    )

    assert result["count"] == 0
    assert session.calls[0][1]["stream"] is True
    assert session.calls[0][1]["allow_redirects"] is False
    assert response.iter_calls == 1
    assert response.closed is True


def test_oversized_declared_length_rejects_before_body_iteration_and_closes():
    response = _StreamingResponse(
        [b"must-not-be-read"],
        headers={"Content-Length": str(sp.MAX_RESPONSE_BYTES + 1)},
    )

    with pytest.raises(sp.SuperteamProviderError, match="exceeds size limit"):
        sp._response_json(response, name="hostile provider")

    assert response.iter_calls == 0
    assert response.closed is True


def test_streamed_body_overflow_is_bounded_and_closes():
    response = _StreamingResponse([b"x" * sp.MAX_RESPONSE_BYTES, b"y"])

    with pytest.raises(sp.SuperteamProviderError, match="exceeds size limit"):
        sp._response_json(response, name="hostile provider")

    assert response.iter_calls == 1
    assert response.closed is True


def test_decimal_positive_exponent_cannot_expand_tiny_token_into_huge_text():
    with pytest.raises(sp.SuperteamProviderError, match="amount boundary"):
        sp.normalize_live_listing(_row(rewardAmount=Decimal("1e999999999")))


def test_decimal_negative_zero_scale_is_bounded_before_fixed_formatting():
    with pytest.raises(sp.SuperteamProviderError, match="amount boundary"):
        sp.normalize_live_listing(_row(rewardAmount=Decimal("0e-999999999")))


def test_normal_exact_decimal_semantics_are_preserved():
    result = sp.normalize_live_listing(_row(rewardAmount=Decimal("500.0100")))
    assert result["compensation"]["advertised_amount"] == "500.01"


def test_details_payload_must_match_requested_slug_identity():
    response = _json_response(_details(slug="different-listing"))
    session = _Session(response)

    with pytest.raises(sp.SuperteamProviderError, match="requested slug"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            session=session,
        )

    assert response.closed is True
