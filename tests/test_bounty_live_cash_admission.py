# SPDX-License-Identifier: MIT

import copy

import pytest

from concierge import bounty_live_cash_admission as live


class Response:
    def __init__(self, payload):
        self.payload = copy.deepcopy(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return copy.deepcopy(self.payload)


class IssueSession:
    def __init__(self, issues):
        self.issues = [copy.deepcopy(value) for value in issues]
        self.reads = 0

    def get(self, url, *, headers, params=None, timeout=15):
        if "/issues/" not in url or url.endswith("/comments"):
            raise AssertionError(f"unexpected direct read: {url}")
        index = min(self.reads, len(self.issues) - 1)
        self.reads += 1
        return Response(self.issues[index])


def issue(
    body="/bounty $50",
    *,
    title="Paid repair",
    labels=None,
    updated="2026-09-19T00:00:00Z",
):
    return {
        "state": "open",
        "title": title,
        "body": body,
        "labels": [{"name": value} for value in (labels or ["Bounty"])],
        "assignees": [],
        "comments": 0,
        "updated_at": updated,
        "author_association": "MEMBER",
    }


def preflight(
    amount="50",
    *,
    dispatch=True,
    disposition="ACTIONABLE",
    reasons=None,
    rtc=None,
):
    return {
        "repo": "acme/repo",
        "number": 7,
        "attempt_count": 0,
        "attempt_signal_count": 0,
        "comments_truncated": False,
        "canonical_audit": {
            "issue_state": "open",
            "open_pr_count": 0,
            "stale_listing_signal": False,
            "search_truncated": False,
        },
        "qualification": {
            "disposition": disposition,
            "dispatch": dispatch,
            "reason_codes": list(reasons or []),
            "reasons": [],
            "signals": {
                "advertised_reward_usd": [] if amount is None else [amount],
                "live_label_reward_usd": [],
                "advertised_reward_rtc": [] if rtc is None else [rtc],
                "live_label_reward_rtc": [],
                "already_rewarded": False,
                "attempt_count": 0,
                "open_pr_count": 0,
                "stale_listing_signal": False,
                "search_truncated": False,
                "canonical_audit_complete": True,
                "issue_state": "open",
                "canonical_generation_stable": True,
                "canonical_audit_stable": True,
            },
        },
    }


def install_preflight(monkeypatch, result):
    monkeypatch.setattr(
        live.bp,
        "preflight_bounty",
        lambda *args, **kwargs: copy.deepcopy(result),
    )


@pytest.mark.parametrize(
    ("amount", "expected", "route"),
    [
        ("50", "ACTIVE_REVIEW", "main_bounty_queue"),
        ("125.00", "ACTIVE_REVIEW", "main_bounty_queue"),
        ("49.99", "PILE_SAVE_UP", "bounty_pile_10_49"),
        ("10", "PILE_SAVE_UP", "bounty_pile_10_49"),
        ("9.99", "PRUNE_BELOW_DOLLAR_FLOOR", None),
    ],
)
def test_fixed_live_usd_routes_exact_owner_floors(
    monkeypatch, amount, expected, route
):
    install_preflight(monkeypatch, preflight(amount))
    session = IssueSession([issue("/bounty $" + amount)])

    receipt = live.evaluate_live_cash_admission(
        "acme/repo", 7, session=session
    )

    expected_amount = (
        amount.rstrip("0").rstrip(".") if "." in amount else amount
    )
    assert receipt["disposition"] == expected
    assert receipt["route"] == route
    assert receipt["economics"]["fixed_amount"] == expected_amount
    assert receipt["authority"]["claim_authority"] is False
    assert receipt["authority"]["payment_or_wallet_authority"] is False


@pytest.mark.parametrize(
    "body",
    [
        "Reward: up to $500",
        "Maximum bounty: $500",
        "Bounty range $10 - $500",
        "Between $10 and $500 reward",
        "$500 max bounty",
        "Prize pool $500 bounty",
        "Milestone reward total: $500",
    ],
)
def test_ceiling_range_pool_or_milestone_total_never_promotes(
    monkeypatch, body
):
    install_preflight(monkeypatch, preflight("500"))
    receipt = live.evaluate_live_cash_admission(
        "acme/repo", 7, session=IssueSession([issue(body)])
    )
    assert receipt["disposition"] == "HOLD_NON_FIXED_USD_REWARD"
    assert receipt["route"] is None
    assert receipt["reason_codes"] == [
        "USD_REWARD_NOT_FIXED_GUARANTEED_AMOUNT"
    ]


def test_title_ceiling_matching_label_amount_never_promotes(monkeypatch):
    install_preflight(monkeypatch, preflight("500"))
    receipt = live.evaluate_live_cash_admission(
        "acme/repo",
        7,
        session=IssueSession(
            [
                issue(
                    "Implement the requested feature.",
                    title="Up to $500 for accepted work",
                    labels=["$500"],
                )
            ]
        ),
    )
    assert receipt["disposition"] == "HOLD_NON_FIXED_USD_REWARD"
    assert receipt["route"] is None


def test_canonical_preflight_hold_precedes_large_amount(monkeypatch):
    install_preflight(
        monkeypatch,
        preflight(
            "500",
            dispatch=False,
            disposition="HOLD",
            reasons=["SATURATED_COMPETITION"],
        ),
    )
    receipt = live.evaluate_live_cash_admission(
        "acme/repo",
        7,
        session=IssueSession([issue("/bounty $500")]),
    )
    assert receipt["disposition"] == "HOLD_CANONICAL_PREFLIGHT"
    assert receipt["reason_codes"] == ["SATURATED_COMPETITION"]
    assert receipt["route"] is None


def test_canonical_preflight_reject_precedes_large_amount(monkeypatch):
    install_preflight(
        monkeypatch,
        preflight(
            "500",
            dispatch=False,
            disposition="REJECT",
            reasons=["ISSUE_NOT_OPEN"],
        ),
    )
    receipt = live.evaluate_live_cash_admission(
        "acme/repo",
        7,
        session=IssueSession([issue("/bounty $500")]),
    )
    assert receipt["disposition"] == "REJECT_CANONICAL_PREFLIGHT"
    assert receipt["route"] is None


def test_native_token_only_never_receives_usd_route(monkeypatch):
    install_preflight(monkeypatch, preflight(None, rtc="200"))
    receipt = live.evaluate_live_cash_admission(
        "acme/repo",
        7,
        session=IssueSession([issue("Reward: 200 RTC")]),
    )
    assert receipt["disposition"] == "HOLD_NO_FIXED_USD_REWARD"
    assert receipt["reason_codes"] == ["NATIVE_NON_USD_REWARD_ONLY"]


def test_mixed_usd_and_native_reward_holds(monkeypatch):
    install_preflight(monkeypatch, preflight("100", rtc="200"))
    receipt = live.evaluate_live_cash_admission(
        "acme/repo",
        7,
        session=IssueSession([issue("/bounty $100\nReward: 200 RTC")]),
    )
    assert receipt["disposition"] == "HOLD_MIXED_REWARD_CURRENCY"
    assert receipt["route"] is None


def test_issue_generation_move_holds_even_if_preflight_was_actionable(
    monkeypatch,
):
    install_preflight(monkeypatch, preflight("500"))
    session = IssueSession(
        [
            issue("/bounty $500", updated="2026-09-19T00:00:00Z"),
            issue("/bounty $500", updated="2026-09-19T00:00:01Z"),
        ]
    )
    receipt = live.evaluate_live_cash_admission(
        "acme/repo", 7, session=session
    )
    assert receipt["disposition"] == "HOLD_SOURCE_GENERATION_CHANGED"
    assert receipt["route"] is None


def test_receipt_verifier_rereads_live_source_not_historical_self_replay(
    monkeypatch,
):
    state = {"amount": "500"}

    def fake_preflight(*args, **kwargs):
        return preflight(state["amount"])

    monkeypatch.setattr(live.bp, "preflight_bounty", fake_preflight)
    fixed_issue = issue("/bounty $500")
    receipt = live.evaluate_live_cash_admission(
        "acme/repo", 7, session=IssueSession([fixed_issue])
    )
    assert live.verify_live_cash_receipt(
        receipt, session=IssueSession([fixed_issue])
    )

    state["amount"] = "20"
    changed_issue = issue(
        "/bounty $20", updated="2026-09-19T00:00:01Z"
    )
    assert not live.verify_live_cash_receipt(
        receipt, session=IssueSession([changed_issue])
    )


def test_receipt_tamper_is_rejected_without_live_replay(monkeypatch):
    install_preflight(monkeypatch, preflight("50"))
    receipt = live.evaluate_live_cash_admission(
        "acme/repo", 7, session=IssueSession([issue()])
    )
    tampered = copy.deepcopy(receipt)
    tampered["economics"]["fixed_amount"] = "5000"
    assert not live.verify_live_cash_receipt(
        tampered, session=IssueSession([issue()])
    )


def test_api_has_no_caller_reward_amount_or_authority_parameters(monkeypatch):
    install_preflight(monkeypatch, preflight("50"))
    with pytest.raises(TypeError):
        live.evaluate_live_cash_admission(
            "acme/repo",
            7,
            session=IssueSession([issue()]),
            amount="500",
            authority="FIRST_PARTY",
        )
