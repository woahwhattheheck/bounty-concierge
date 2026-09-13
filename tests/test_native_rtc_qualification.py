# SPDX-License-Identifier: MIT
"""Native RTC reward advertisement regressions for paid-work qualification."""

from urllib.parse import urlparse

from concierge import bounty_preflight as bp
from concierge import bounty_qualification as bq


def _audit(**overrides):
    value = {
        "issue_state": "open",
        "open_pr_count": 0,
        "stale_listing_signal": False,
        "search_truncated": False,
    }
    value.update(overrides)
    return value


def _qualify(**patch):
    snapshot = {
        "title": "",
        "body": "",
        "labels": ["bounty"],
        "canonical_audit": _audit(),
    }
    snapshot.update(patch)
    return bq.qualify_dispatch(snapshot)


def test_title_native_rtc_reward_satisfies_advertisement_gate():
    result = _qualify(title="[BOUNTY: 10 RTC] Write the parser")

    assert result["disposition"] == "ACTIONABLE"
    assert result["dispatch"] is True
    assert result["reason_codes"] == []
    assert result["signals"]["title_reward_rtc"] == ["10"]
    assert result["signals"]["advertised_reward_rtc"] == ["10"]
    assert result["signals"]["rtc_reward_source"] == "title"
    assert result["signals"]["advertised_reward_usd"] == []


def test_body_native_rtc_reward_is_fallback_when_title_has_no_amount():
    result = _qualify(
        title="[BOUNTY] Write the parser",
        body="## Bounty\n**Reward: 100 RTC**\nImplement the parser.",
    )

    assert result["disposition"] == "ACTIONABLE"
    assert result["signals"]["title_reward_rtc"] == []
    assert result["signals"]["body_reward_rtc"] == ["100"]
    assert result["signals"]["advertised_reward_rtc"] == ["100"]
    assert result["signals"]["rtc_reward_source"] == "body"


def test_title_rtc_is_authoritative_over_stale_body_amount():
    # Mirrors live Elyan Labs policy: older bodies can retain pre-adjustment
    # amounts while the title is the sponsor-authoritative reward figure.
    result = _qualify(
        title="[BOUNTY] Human Funnel Asset Pack — 30 RTC",
        body="## Bounty\n**Reward: 45 RTC**\nBuild the asset pack.",
    )

    assert result["disposition"] == "ACTIONABLE"
    assert result["reason_codes"] == []
    assert result["signals"]["title_reward_rtc"] == ["30"]
    assert result["signals"]["body_reward_rtc"] == ["45"]
    assert result["signals"]["advertised_reward_rtc"] == ["30"]
    assert result["signals"]["rtc_reward_source"] == "title"


def test_native_rtc_is_not_converted_or_confused_with_reference_rate_usd():
    result = _qualify(
        body="**Reward: 100 RTC** (~$10 at reference rate)",
    )

    assert result["disposition"] == "ACTIONABLE"
    assert result["signals"]["advertised_reward_rtc"] == ["100"]
    assert result["signals"]["advertised_reward_usd"] == []


def test_unrelated_rtc_budget_prose_does_not_masquerade_as_reward():
    result = _qualify(
        body="The treasury contains 500 RTC for future programs, but no reward is offered."
    )

    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["REWARD_NOT_ADVERTISED"]
    assert result["signals"]["advertised_reward_rtc"] == []


def test_machine_readable_reward_rtc_is_high_confidence_body_fallback():
    result = _qualify(
        body="""Implementation task.
```bounty-spec
paid: true
reward_rtc: 0.1
per: person
```
"""
    )

    assert result["disposition"] == "ACTIONABLE"
    assert result["signals"]["advertised_reward_rtc"] == ["0.1"]
    assert result["signals"]["rtc_reward_source"] == "body"


def test_grouped_native_rtc_title_amount_is_canonicalized():
    result = _qualify(title="[BOUNTY: 1,000 RTC] Major implementation")

    assert result["disposition"] == "ACTIONABLE"
    assert result["signals"]["advertised_reward_rtc"] == ["1000"]


def test_rtc_range_fails_closed_as_ambiguous_instead_of_guessing_maximum():
    result = _qualify(title="[BOUNTY] Distribution package — 15-60 RTC")

    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["AMBIGUOUS_ADVERTISED_REWARD"]
    assert result["signals"]["advertised_reward_rtc"] == ["15", "60"]


def test_live_rtc_label_can_advertise_reward_when_issue_text_does_not():
    result = _qualify(labels=["bounty", "20 RTC"])

    assert result["disposition"] == "ACTIONABLE"
    assert result["signals"]["advertised_reward_rtc"] == []
    assert result["signals"]["live_label_reward_rtc"] == ["20"]
    assert result["signals"]["rtc_reward_source"] == "label"


def test_live_rtc_label_mismatch_with_authoritative_title_holds():
    result = _qualify(
        title="[BOUNTY: 20 RTC] Implement feature",
        labels=["bounty", "30 RTC"],
    )

    assert result["disposition"] == "HOLD"
    assert result["reason_codes"] == ["REWARD_MISMATCH"]


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, issue):
        self.issue = issue

    def get(self, url, *, headers, params=None, timeout=15):
        path = urlparse(url).path
        if path.endswith("/comments"):
            return _Response([])
        if path.endswith("/issues/42"):
            return _Response(self.issue)
        raise AssertionError(url)


def test_preflight_recognizes_canonical_rtc_title_without_usd(monkeypatch):
    issue = {
        "title": "[BOUNTY: 10 RTC] Add parser",
        "body": "Implement the parser.",
        "labels": [{"name": "bounty"}],
    }
    monkeypatch.setattr(bp, "audit_bounty", lambda *args, **kwargs: _audit())

    result = bp.preflight_bounty("acme/widget", 42, session=_Session(issue))

    assert result["qualification"]["disposition"] == "ACTIONABLE"
    signals = result["qualification"]["signals"]
    assert signals["title_reward_rtc"] == ["10"]
    assert signals["advertised_reward_usd"] == []
