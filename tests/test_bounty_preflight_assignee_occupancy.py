# SPDX-License-Identifier: MIT

import pytest

from concierge import bounty_preflight as bp


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, issue, comments=None):
        self.issue = issue
        self.comments = [] if comments is None else comments

    def get(self, url, *, headers, params=None, timeout=15):
        if url.endswith("/comments"):
            return Response(self.comments if params["page"] == 1 else [])
        return Response(self.issue)


def canonical_audit(*args, **kwargs):
    return {
        "issue_state": "open",
        "open_pr_count": 0,
        "stale_listing_signal": False,
        "search_truncated": False,
    }


def paid_issue(*, assignees=None):
    return {
        "title": "Paid fix",
        "body": "/bounty $100",
        "labels": [{"name": "$100"}],
        "assignees": [] if assignees is None else assignees,
    }


def test_formal_foreign_assignment_holds_otherwise_actionable_dispatch(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(paid_issue(assignees=[{"login": "alice", "type": "User"}]))

    result = bp.preflight_bounty("acme/repo", 7, session=session)

    assert result["formal_assignee_count"] == 1
    assert result["foreign_assignee_count"] == 1
    assert result["assigned_to_operator"] is False
    assert result["qualification"]["disposition"] == "HOLD"
    assert result["qualification"]["dispatch"] is False
    assert "FORMALLY_ASSIGNED" in result["qualification"]["reason_codes"]
    assert result["qualification"]["signals"]["formal_assignee_count"] == 1
    assert "alice" not in repr(result)


def test_operator_assignment_does_not_block_own_dispatch(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(paid_issue(assignees=[{"login": "WoahWhatTheHeck"}]))

    result = bp.preflight_bounty(
        "acme/repo",
        8,
        session=session,
        operator_login="woahwhattheheck",
    )

    assert result["formal_assignee_count"] == 1
    assert result["assigned_to_operator"] is True
    assert result["foreign_assignee_count"] == 0
    assert result["qualification"]["disposition"] == "ACTIONABLE"
    assert result["qualification"]["dispatch"] is True
    assert "FORMALLY_ASSIGNED" not in result["qualification"]["reason_codes"]


def test_mixed_operator_and_foreign_assignment_still_holds(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(
        paid_issue(
            assignees=[
                {"login": "woahwhattheheck", "type": "User"},
                {"login": "alice", "type": "User"},
            ]
        )
    )

    result = bp.preflight_bounty(
        "acme/repo",
        9,
        session=session,
        operator_login="WOAHWHATTHEHECK",
    )

    assert result["formal_assignee_count"] == 2
    assert result["assigned_to_operator"] is True
    assert result["foreign_assignee_count"] == 1
    assert result["qualification"]["disposition"] == "HOLD"


def test_duplicate_and_bot_assignees_are_deduped_but_still_formal():
    issue = paid_issue(
        assignees=[
            {"login": "helper[bot]", "type": "Bot"},
            {"login": "HELPER[bot]", "type": "Bot"},
            {"login": "alice", "type": "User"},
        ]
    )

    state = bp._assignee_state(issue, None)

    assert state == {
        "formal_assignee_count": 2,
        "assigned_to_operator": False,
        "foreign_assignee_count": 2,
    }


@pytest.mark.parametrize(
    "assignees",
    [
        "alice",
        ["alice"],
        [{}],
        [{"login": ""}],
        [{"login": 7}],
    ],
)
def test_malformed_assignee_payload_fails_closed(assignees):
    session = Session(paid_issue(assignees=assignees))

    with pytest.raises(bp.BountyPreflightError):
        bp.collect_issue_context("acme/repo", 10, session=session)


def test_unassigned_control_remains_actionable(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(paid_issue())

    result = bp.preflight_bounty("acme/repo", 11, session=session)

    assert result["formal_assignee_count"] == 0
    assert result["foreign_assignee_count"] == 0
    assert result["qualification"]["disposition"] == "ACTIONABLE"
    assert result["qualification"]["dispatch"] is True


def test_invalid_operator_login_fails_closed_before_network_use():
    session = Session(paid_issue())

    with pytest.raises(ValueError):
        bp.collect_issue_context(
            "acme/repo",
            12,
            session=session,
            operator_login="   ",
        )
