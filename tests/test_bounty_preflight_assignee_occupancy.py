# SPDX-License-Identifier: MIT

import pytest

from concierge import bounty_preflight as bp


_GENERATION_TIME = "2026-09-13T00:00:00Z"


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, issue, comments=None, authenticated_user=None):
        self.issue = issue
        self.comments = [] if comments is None else comments
        self.authenticated_user = authenticated_user
        self.urls = []

    def get(self, url, *, headers, params=None, timeout=15):
        self.urls.append(url)
        if url == bp._AUTHENTICATED_USER_URL:
            return Response(self.authenticated_user)
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
        "state": "open",
        "updated_at": _GENERATION_TIME,
        "comments": 0,
    }


def test_formal_foreign_assignment_holds_otherwise_actionable_dispatch(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    monkeypatch.setattr(bp, "GITHUB_TOKEN", None)
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
    assert bp._AUTHENTICATED_USER_URL not in session.urls


def test_authenticated_operator_assignment_does_not_block_own_dispatch(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(
        paid_issue(assignees=[{"login": "WoahWhatTheHeck"}]),
        authenticated_user={"login": "woahwhattheheck"},
    )

    result = bp.preflight_bounty(
        "acme/repo",
        8,
        token="test-token",
        session=session,
        operator_login="WOAHWHATTHEHECK",
    )

    assert result["formal_assignee_count"] == 1
    assert result["assigned_to_operator"] is True
    assert result["foreign_assignee_count"] == 0
    assert result["qualification"]["disposition"] == "ACTIONABLE"
    assert result["qualification"]["dispatch"] is True
    assert "FORMALLY_ASSIGNED" not in result["qualification"]["reason_codes"]
    assert result["qualification"]["signals"]["canonical_generation_stable"] is True
    assert session.urls.count(bp._AUTHENTICATED_USER_URL) == 1
    assert "woahwhattheheck" not in repr(result).casefold()


def test_authenticated_identity_is_derived_without_caller_assertion(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(
        paid_issue(assignees=[{"login": "alice"}]),
        authenticated_user={"login": "ALICE"},
    )

    result = bp.preflight_bounty(
        "acme/repo",
        81,
        token="test-token",
        session=session,
    )

    assert result["assigned_to_operator"] is True
    assert result["foreign_assignee_count"] == 0
    assert result["qualification"]["disposition"] == "ACTIONABLE"
    assert result["qualification"]["dispatch"] is True
    assert result["qualification"]["signals"]["canonical_generation_stable"] is True
    assert "alice" not in repr(result).casefold()


def test_caller_cannot_impersonate_assignee_with_operator_login(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(
        paid_issue(assignees=[{"login": "alice"}]),
        authenticated_user={"login": "bob"},
    )

    with pytest.raises(
        bp.BountyPreflightError,
        match="operator_login did not match authenticated GitHub identity",
    ):
        bp.preflight_bounty(
            "acme/repo",
            82,
            token="test-token",
            session=session,
            operator_login="alice",
        )


def test_operator_assertion_without_token_cannot_authorize_assignment(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    monkeypatch.setattr(bp, "GITHUB_TOKEN", None)
    session = Session(paid_issue(assignees=[{"login": "alice"}]))

    with pytest.raises(
        bp.BountyPreflightError,
        match="operator_login requires an authenticated GitHub token",
    ):
        bp.preflight_bounty(
            "acme/repo",
            83,
            session=session,
            operator_login="alice",
        )

    assert bp._AUTHENTICATED_USER_URL not in session.urls


def test_mixed_authenticated_operator_and_foreign_assignment_still_holds(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(
        paid_issue(
            assignees=[
                {"login": "woahwhattheheck", "type": "User"},
                {"login": "alice", "type": "User"},
            ]
        ),
        authenticated_user={"login": "woahwhattheheck"},
    )

    result = bp.preflight_bounty(
        "acme/repo",
        9,
        token="test-token",
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


def test_unassigned_control_remains_actionable_without_identity_read(monkeypatch):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(
        paid_issue(),
        authenticated_user={"login": "woahwhattheheck"},
    )

    result = bp.preflight_bounty(
        "acme/repo",
        11,
        token="test-token",
        session=session,
    )

    assert result["formal_assignee_count"] == 0
    assert result["foreign_assignee_count"] == 0
    assert result["qualification"]["disposition"] == "ACTIONABLE"
    assert result["qualification"]["dispatch"] is True
    assert result["qualification"]["signals"]["canonical_generation_stable"] is True
    assert bp._AUTHENTICATED_USER_URL not in session.urls


def test_invalid_operator_login_fails_closed_before_network_use():
    session = Session(paid_issue())

    with pytest.raises(ValueError):
        bp.collect_issue_context(
            "acme/repo",
            12,
            session=session,
            operator_login="   ",
        )

    assert session.urls == []


@pytest.mark.parametrize(
    "authenticated_user",
    [
        None,
        [],
        {},
        {"login": ""},
        {"login": 7},
    ],
)
def test_malformed_authenticated_user_fails_closed(monkeypatch, authenticated_user):
    monkeypatch.setattr(bp, "audit_bounty", canonical_audit)
    session = Session(
        paid_issue(assignees=[{"login": "alice"}]),
        authenticated_user=authenticated_user,
    )

    with pytest.raises(bp.BountyPreflightError):
        bp.preflight_bounty(
            "acme/repo",
            84,
            token="test-token",
            session=session,
        )


def test_assignee_gate_uses_same_captured_issue_generation(monkeypatch):
    monkeypatch.setattr(bp, "GITHUB_TOKEN", None)
    issue_url = "https://api.github.com/repos/acme/repo/issues/13"
    first_issue = {
        "title": "Paid fix",
        "body": "/bounty $100",
        "labels": [{"name": "$100"}],
        "assignees": [{"login": "alice"}],
        "state": "open",
        "html_url": "https://github.com/acme/repo/issues/13",
    }
    second_issue = {
        **first_issue,
        "assignees": [],
    }

    class MutatingSession:
        def __init__(self):
            self.issue_reads = 0

        def get(self, url, *, headers, params=None, timeout=15):
            if url == issue_url:
                self.issue_reads += 1
                return Response(first_issue if self.issue_reads == 1 else second_issue)
            if url.endswith("/comments"):
                return Response([])
            raise AssertionError(f"unexpected URL: {url}")

    session = MutatingSession()

    def replaying_audit(repo, number, token=None, *, session, max_pages):
        issue = session.get(issue_url, headers={}, timeout=15).json()
        return {
            "issue_state": issue["state"],
            "open_pr_count": 0,
            "stale_listing_signal": False,
            "search_truncated": False,
        }

    monkeypatch.setattr(bp, "audit_bounty", replaying_audit)

    result = bp.preflight_bounty("acme/repo", 13, session=session)

    assert session.issue_reads == 1
    assert result["formal_assignee_count"] == 1
    assert result["foreign_assignee_count"] == 1
    assert result["qualification"]["disposition"] == "HOLD"
    assert "FORMALLY_ASSIGNED" in result["qualification"]["reason_codes"]
