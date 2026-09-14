from urllib.parse import urlparse

import pytest

from concierge import bounty_preflight as bp
from concierge import bounty_qualification as bq


def audit(**overrides):
    value = {
        "issue_state": "open",
        "open_pr_count": 0,
        "stale_listing_signal": False,
        "search_truncated": False,
    }
    value.update(overrides)
    return value


def qualify(**patch):
    snapshot = {
        "title": "",
        "body": "",
        "labels": [],
        "canonical_audit": audit(),
    }
    snapshot.update(patch)
    return bq.qualify_dispatch(snapshot)


def test_missing_reward_is_held_instead_of_dispatched():
    result = qualify(body="Please implement the parser.")
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["REWARD_NOT_ADVERTISED"]


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("body", "Reward: $250"),
        ("body", "Bounty amount = $250"),
        ("body", "/bounty $250"),
        ("title", "[Bounty: $250] Add parser"),
        ("title", "$250 bounty — add parser"),
    ],
)
def test_high_precision_advertised_reward_forms_are_actionable(field, text):
    result = qualify(**{field: text})
    assert result["disposition"] == "ACTIONABLE"
    assert result["signals"]["advertised_reward_usd"] == ["250"]


def test_unrelated_dollar_prose_does_not_masquerade_as_reward():
    result = qualify(
        body="The implementation may require a $500 cloud budget, but no reward is offered."
    )
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["REWARD_NOT_ADVERTISED"]


def test_live_reward_label_alone_is_sufficient_advertisement():
    result = qualify(labels=["bounty", "$125"])
    assert result["disposition"] == "ACTIONABLE"
    assert result["signals"]["live_label_reward_usd"] == ["125"]


def test_title_or_body_mismatch_with_live_label_holds():
    result = qualify(title="Bounty: $500", labels=["$250"])
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["REWARD_MISMATCH"]


def test_distinct_title_and_body_rewards_are_ambiguous():
    result = qualify(title="Bounty: $500", body="Reward: $250")
    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["AMBIGUOUS_ADVERTISED_REWARD"]


def test_title_must_be_string():
    with pytest.raises(bq.QualificationInputError, match="title must be a string"):
        qualify(title=500)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, issue):
        self.issue = issue

    def get(self, url, *, headers, params=None, timeout=15):
        path = urlparse(url).path
        if path.endswith("/comments"):
            return Response([])
        if path.endswith("/issues/42"):
            return Response(self.issue)
        raise AssertionError(url)


def test_preflight_forwards_canonical_issue_title_into_reward_gate(monkeypatch):
    issue = {
        "state": "open",
        "title": "Bounty: $175 — add parser",
        "body": "Implement the parser.",
        "labels": [],
        "comments": 0,
        "updated_at": "2026-09-13T12:00:00Z",
    }
    monkeypatch.setattr(
        bp,
        "audit_bounty",
        lambda *args, **kwargs: audit(),
    )

    result = bp.preflight_bounty("acme/widget", 42, session=Session(issue))

    assert result["qualification"]["disposition"] == "ACTIONABLE"
    assert result["qualification"]["signals"]["title_reward_usd"] == ["175"]
