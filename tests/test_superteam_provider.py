# SPDX-License-Identifier: MIT
"""Hostile regressions for the read-only Superteam opportunity provider."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from concierge import superteam_provider as sp


class _Response:
    def __init__(self, payload=None, *, status=200, raw=None, headers=None):
        self.status_code = status
        self.headers = {} if headers is None else headers
        if raw is not None:
            self.content = raw
        else:
            self.content = json.dumps(payload).encode("utf-8")


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def _row(**overrides):
    row = {
        "id": "listing-1",
        "rewardAmount": 500,
        "deadline": "2026-10-01T00:00:00.000Z",
        "type": "bounty",
        "title": "Build a useful agent skill",
        "token": "USDC",
        "winnersAnnouncedAt": None,
        "slug": "build-useful-agent-skill",
        "isWinnersAnnounced": False,
        "isFeatured": False,
        "compensationType": "fixed",
        "minRewardAsk": None,
        "maxRewardAsk": None,
        "agentAccess": "AGENT_ALLOWED",
        "status": "OPEN",
        "isPro": False,
        "_count": {"Comments": 3, "Submission": 10},
        "sponsor": {
            "name": "Verified Sponsor",
            "slug": "verified-sponsor",
            "isVerified": True,
        },
        "rewards": {"1": 250, "2": 100},
    }
    row.update(overrides)
    return row


def _details(**overrides):
    value = _row(
        isPrivate=False,
        isPublished=True,
        description="Build the integration and document the result.",
        requirements="Public repository and demo.",
        region="Global",
        isFndnPaying=False,
        skills=[{"skills": "Backend", "subskills": ["Python"]}],
        eligibility=[
            {
                "order": 1,
                "question": "Project Title",
                "type": "text",
                "optional": False,
                "isLink": False,
            }
        ],
    )
    value.pop("_count", None)
    value.update(overrides)
    return value


def _now():
    return datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)


def test_normalize_live_listing_preserves_native_compensation_without_cash_inference():
    result = sp.normalize_live_listing(_row())

    assert result["provider"] == "superteam"
    assert result["agent_access"] == "AGENT_ALLOWED"
    assert result["sponsor"]["verified"] is True
    assert result["compensation"] == {
        "type": "fixed",
        "token": "USDC",
        "advertised_amount": "500",
        "min_ask": None,
        "max_ask": None,
        "prize_breakdown": {"1": "250", "2": "100"},
        "competitive": True,
        "guaranteed": False,
        "usd_equivalent_inferred": False,
    }
    assert result["cash_claim"] is False
    assert result["dispatch_authorized"] is False
    assert result["submission_authorized"] is False
    assert result["payout_authorized"] is False


def test_decimal_reward_is_not_float_rounded_by_http_decoder():
    row = _row(rewardAmount=0.1)
    session = _Session([_Response([row])])

    result = sp.fetch_live_opportunities(
        api_key="sk_test",
        take=2,
        as_of=_now(),
        session=session,
    )

    assert result["opportunities"][0]["compensation"]["advertised_amount"] == "0.1"


def test_direct_float_input_fails_closed_instead_of_guessing_exact_money():
    with pytest.raises(sp.SuperteamProviderError, match="exact JSON numeric"):
        sp.normalize_live_listing(_row(rewardAmount=0.1))


def test_human_only_listing_is_rejected_even_if_other_fields_are_valid():
    with pytest.raises(sp.SuperteamProviderError, match="not agent eligible"):
        sp.normalize_live_listing(_row(agentAccess="HUMAN_ONLY"))


def test_unverified_sponsor_is_rejected():
    with pytest.raises(sp.SuperteamProviderError, match="sponsor must be verified"):
        sp.normalize_live_listing(
            _row(
                sponsor={
                    "name": "Unverified",
                    "slug": "unverified",
                    "isVerified": False,
                }
            )
        )


def test_closed_listing_is_rejected():
    with pytest.raises(sp.SuperteamProviderError, match="status must be OPEN"):
        sp.normalize_live_listing(_row(status="CLOSED"))


def test_quote_range_must_be_ordered():
    with pytest.raises(sp.SuperteamProviderError, match="minRewardAsk exceeds"):
        sp.normalize_live_listing(
            _row(compensationType="range", minRewardAsk=900, maxRewardAsk=100)
        )


def test_live_fetch_uses_fixed_host_bearer_header_and_disables_redirects():
    session = _Session([_Response([_row()])])

    sp.fetch_live_opportunities(
        api_key="sk_secret_never_print",
        take=2,
        as_of=_now(),
        session=session,
    )

    url, kwargs = session.calls[0]
    assert url == sp.LIVE_LISTINGS_URL
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == 15
    assert kwargs["headers"]["Authorization"] == "Bearer sk_secret_never_print"


def test_redirect_is_not_followed_with_bearer_token():
    session = _Session([_Response(None, status=302, headers={"Location": "https://evil.test"})])

    with pytest.raises(sp.SuperteamProviderError, match="HTTP 302"):
        sp.fetch_live_opportunities(
            api_key="sk_secret",
            take=1,
            as_of=_now(),
            session=session,
        )

    assert session.calls[0][1]["allow_redirects"] is False


def test_auth_failure_is_generic_and_never_echoes_api_key():
    secret = "sk_do_not_echo_123"
    session = _Session([_Response({}, status=401)])

    with pytest.raises(sp.SuperteamProviderError) as exc_info:
        sp.fetch_live_opportunities(
            api_key=secret,
            take=1,
            as_of=_now(),
            session=session,
        )

    assert "authentication failed" in str(exc_info.value)
    assert secret not in str(exc_info.value)


def test_rate_limit_is_reported_without_response_body_echo():
    session = _Session([_Response(raw=b'{"error":"secret-ish upstream text"}', status=429)])

    with pytest.raises(sp.SuperteamProviderError) as exc_info:
        sp.fetch_live_opportunities(
            api_key="sk_test",
            take=1,
            as_of=_now(),
            session=session,
        )

    assert "rate limit" in str(exc_info.value)
    assert "secret-ish" not in str(exc_info.value)


def test_oversized_content_length_fails_before_json_parsing():
    session = _Session(
        [
            _Response(
                [],
                headers={"Content-Length": str(sp.MAX_RESPONSE_BYTES + 1)},
            )
        ]
    )

    with pytest.raises(sp.SuperteamProviderError, match="exceeds size limit"):
        sp.fetch_live_opportunities(
            api_key="sk_test",
            take=1,
            as_of=_now(),
            session=session,
        )


def test_payload_must_be_a_list():
    session = _Session([_Response({"listings": []})])

    with pytest.raises(sp.SuperteamProviderError, match="payload must be a list"):
        sp.fetch_live_opportunities(
            api_key="sk_test",
            take=1,
            as_of=_now(),
            session=session,
        )


def test_page_cannot_exceed_requested_take():
    session = _Session([_Response([_row(id="a"), _row(id="b")])])

    with pytest.raises(sp.SuperteamProviderError, match="exceeded requested page size"):
        sp.fetch_live_opportunities(
            api_key="sk_test",
            take=1,
            as_of=_now(),
            session=session,
        )


def test_bounded_exclusion_pagination_collects_unique_rows():
    first = _row(id="a", slug="a-work", deadline="2026-09-20T00:00:00Z")
    second = _row(id="b", slug="b-work", deadline="2026-09-21T00:00:00Z")
    third = _row(id="c", slug="c-work", deadline="2026-09-22T00:00:00Z")
    session = _Session([_Response([first, second]), _Response([third])])

    result = sp.fetch_live_opportunities(
        api_key="sk_test",
        take=2,
        max_batches=2,
        as_of=_now(),
        session=session,
    )

    assert result["count"] == 3
    assert result["truncated"] is False
    assert [item["external_id"] for item in result["opportunities"]] == ["a", "b", "c"]
    second_params = session.calls[1][1]["params"]
    assert ("excludeIds[]", "a") in second_params
    assert ("excludeIds[]", "b") in second_params


def test_repeated_excluded_listing_fails_closed():
    first = _row(id="a", slug="a-work")
    session = _Session([_Response([first]), _Response([first])])

    with pytest.raises(sp.SuperteamProviderError, match="repeated an excluded listing"):
        sp.fetch_live_opportunities(
            api_key="sk_test",
            take=1,
            max_batches=2,
            as_of=_now(),
            session=session,
        )


def test_full_final_page_is_marked_truncated_not_complete():
    session = _Session([_Response([_row(id="a", slug="a-work")])])

    result = sp.fetch_live_opportunities(
        api_key="sk_test",
        take=1,
        max_batches=1,
        as_of=_now(),
        session=session,
    )

    assert result["count"] == 1
    assert result["truncated"] is True


def test_default_deadline_floor_is_current_utc_date():
    session = _Session([_Response([])])

    result = sp.fetch_live_opportunities(
        api_key="sk_test",
        take=1,
        as_of=datetime(2026, 9, 13, 23, 30, tzinfo=timezone.utc),
        session=session,
    )

    assert result["deadline_floor"] == "2026-09-13"
    assert ("deadline", "2026-09-13") in session.calls[0][1]["params"]


def test_listing_type_is_validated_and_forwarded():
    session = _Session([_Response([])])
    sp.fetch_live_opportunities(
        api_key="sk_test",
        listing_type="project",
        take=1,
        as_of=_now(),
        session=session,
    )
    assert ("type", "project") in session.calls[0][1]["params"]

    with pytest.raises(sp.SuperteamProviderError, match="listing_type"):
        sp.fetch_live_opportunities(
            api_key="sk_test",
            listing_type="grant",
            take=1,
            as_of=_now(),
            session=_Session([]),
        )


def test_take_and_total_feed_are_bounded():
    with pytest.raises(sp.SuperteamProviderError, match="take"):
        sp.fetch_live_opportunities(
            api_key="sk_test",
            take=51,
            as_of=_now(),
            session=_Session([]),
        )
    with pytest.raises(sp.SuperteamProviderError, match="max_batches"):
        sp.fetch_live_opportunities(
            api_key="sk_test",
            take=50,
            max_batches=6,
            as_of=_now(),
            session=_Session([]),
        )


def test_api_key_can_come_from_environment_without_being_in_result(monkeypatch):
    secret = "sk_environment_secret"
    monkeypatch.setenv(sp.API_KEY_ENV, secret)
    session = _Session([_Response([])])

    result = sp.fetch_live_opportunities(
        take=1,
        as_of=_now(),
        session=session,
    )

    encoded = json.dumps(result, sort_keys=True)
    assert secret not in encoded
    assert result["authority"]["cash_claim"] is False


def test_missing_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv(sp.API_KEY_ENV, raising=False)
    with pytest.raises(sp.SuperteamProviderError, match=sp.API_KEY_ENV):
        sp.fetch_live_opportunities(
            take=1,
            as_of=_now(),
            session=_Session([]),
        )


def test_details_revalidate_public_published_boundary_and_mark_scope_untrusted():
    session = _Session([_Response(_details())])

    result = sp.fetch_listing_details(
        "build-useful-agent-skill",
        api_key="sk_test",
        session=session,
    )

    assert result["listing"]["agent_access"] == "AGENT_ALLOWED"
    assert result["public_contract"]["is_private"] is False
    assert result["public_contract"]["is_published"] is True
    assert result["public_contract"]["skills"] == [
        {"skill": "Backend", "subskills": ["Python"]}
    ]
    assert result["public_contract"]["eligibility"][0]["question"] == "Project Title"
    assert result["public_contract"]["untrusted_scope"]["description"].startswith("Build")
    assert result["authority"]["scope_text"] == "sponsor_supplied_untrusted_data"
    assert result["authority"]["dispatch"] is False


def test_details_reject_private_or_unpublished_rows():
    for patch in ({"isPrivate": True}, {"isPublished": False}):
        session = _Session([_Response(_details(**patch))])
        with pytest.raises(sp.SuperteamProviderError, match="not public and published"):
            sp.fetch_listing_details(
                "build-useful-agent-skill",
                api_key="sk_test",
                session=session,
            )


def test_details_reject_human_only_even_if_route_returns_it():
    session = _Session([_Response(_details(agentAccess="HUMAN_ONLY"))])
    with pytest.raises(sp.SuperteamProviderError, match="not agent eligible"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            session=session,
        )


def test_details_reject_malformed_skill_contract():
    session = _Session(
        [_Response(_details(skills=[{"skills": "Backend", "subskills": "Python"}]))]
    )
    with pytest.raises(sp.SuperteamProviderError, match="subskills must be a bounded list"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            session=session,
        )


def test_details_reject_malformed_eligibility_contract():
    session = _Session(
        [
            _Response(
                _details(
                    eligibility=[
                        {
                            "order": 1,
                            "question": "Upload",
                            "type": "file",
                            "optional": False,
                        }
                    ]
                )
            )
        ]
    )
    with pytest.raises(sp.SuperteamProviderError, match="unsupported eligibility"):
        sp.fetch_listing_details(
            "build-useful-agent-skill",
            api_key="sk_test",
            session=session,
        )


def test_slug_is_canonicalized_by_validation_not_url_joining():
    for slug in ("../secret", "UPPERCASE", "has space", ""):
        with pytest.raises(sp.SuperteamProviderError):
            sp.fetch_listing_details(
                slug,
                api_key="sk_test",
                session=_Session([]),
            )


def test_summary_is_safe_and_does_not_echo_sponsor_scope_or_credentials():
    result = {
        "schema": "superteam-agent-opportunity-feed/v1",
        "count": 7,
        "truncated": False,
        "opportunities": [{"title": "untrusted scope"}],
    }
    summary = sp.format_summary(result)
    assert summary == (
        "provider=superteam count=7 truncated=false "
        "dispatch=false submission=false payout=false cash_claim=false"
    )
    assert "untrusted scope" not in summary


def test_feed_authority_never_upgrades_discovery_into_dispatch_or_revenue():
    session = _Session([_Response([_row()])])
    result = sp.fetch_live_opportunities(
        api_key="sk_test",
        take=2,
        as_of=_now(),
        session=session,
    )

    assert result["authority"] == {
        "discovery": "first_party_agent_feed",
        "agent_eligibility": "provider_enforced_and_revalidated",
        "sponsor_verification": "provider_enforced_and_revalidated",
        "dispatch": False,
        "submission": False,
        "payout": False,
        "cash_claim": False,
        "currency_conversion": False,
    }
