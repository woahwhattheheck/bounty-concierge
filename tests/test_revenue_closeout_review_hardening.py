from __future__ import annotations

import requests

from concierge.revenue_closeout import RevenueCloseoutInputError, scan_paid_pr


SHA = "a" * 40
PR_URL = "https://github.com/acme/widgets/pull/17"
BASE = "https://api.github.com/repos/acme/widgets"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, *, headers=None, params=None, timeout=None):
        self.calls.append((url, params))
        key = (url, None if params is None else tuple(sorted(params.items())))
        if key in self.routes:
            payload = self.routes[key]
        elif url in self.routes:
            payload = self.routes[url]
        else:
            raise AssertionError(f"unexpected GET {url} params={params}")
        return FakeResponse(payload)


def item(**overrides):
    value = {
        "repo": "acme/widgets",
        "pr": 17,
        "operator_login": "builder",
        "advertised_amount": "250.00",
        "currency": "USD",
        "last_seen_at": "2026-09-13T00:00:00Z",
    }
    value.update(overrides)
    return value


def pr_payload(**overrides):
    value = {
        "html_url": PR_URL,
        "state": "open",
        "merged_at": None,
        "user": {"login": "builder"},
        "head": {"sha": SHA},
    }
    value.update(overrides)
    return value


def routes(*, pr=None, reviews=None, comments=None):
    return {
        f"{BASE}/pulls/17": pr or pr_payload(),
        (f"{BASE}/pulls/17/reviews", (("page", 1), ("per_page", 100))): reviews or [],
        (f"{BASE}/issues/17/comments", (("page", 1), ("per_page", 100))): comments or [],
    }


def test_merged_current_change_request_outranks_settlement():
    reviews = [{
        "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-09-12T23:00:00Z",
        "author_association": "MEMBER",
        "user": {"login": "maintainer"},
    }]
    result = scan_paid_pr(
        item(),
        session=FakeSession(routes(
            pr=pr_payload(state="closed", merged_at="2026-09-13T03:00:00Z"),
            reviews=reviews,
        )),
    )
    assert result["state"] == "MERGED"
    assert result["next_action"] == "repair_requested"
    assert result["current_change_request_count"] == 1


def test_merged_new_maintainer_comment_outranks_settlement():
    comments = [{
        "updated_at": "2026-09-13T02:00:00Z",
        "author_association": "OWNER",
        "user": {"login": "owner"},
    }]
    result = scan_paid_pr(
        item(),
        session=FakeSession(routes(
            pr=pr_payload(state="closed", merged_at="2026-09-13T03:00:00Z"),
            comments=comments,
        )),
    )
    assert result["state"] == "MERGED"
    assert result["next_action"] == "respond_to_maintainer"


def test_closed_unmerged_new_comment_outranks_investigation():
    comments = [{
        "updated_at": "2026-09-13T02:00:00Z",
        "author_association": "OWNER",
        "user": {"login": "owner"},
    }]
    result = scan_paid_pr(
        item(),
        session=FakeSession(routes(pr=pr_payload(state="closed"), comments=comments)),
    )
    assert result["state"] == "CLOSED_UNMERGED"
    assert result["next_action"] == "respond_to_maintainer"


def test_same_timestamp_feedback_is_replayed_conservatively():
    comments = [
        {
            "updated_at": "2026-09-13T00:00:00Z",
            "author_association": "OWNER",
            "user": {"login": "owner-a"},
            "html_url": PR_URL + "#issuecomment-1",
        },
        {
            "updated_at": "2026-09-13T00:00:00Z",
            "author_association": "MEMBER",
            "user": {"login": "owner-b"},
            "html_url": PR_URL + "#issuecomment-2",
        },
    ]
    result = scan_paid_pr(item(), session=FakeSession(routes(comments=comments)))
    assert result["next_action"] == "respond_to_maintainer"
    assert result["new_feedback_count"] == 2
    assert result["latest_feedback"]["url"] == PR_URL + "#issuecomment-2"


def test_pathological_amount_rejected_before_network():
    session = FakeSession({})
    try:
        scan_paid_pr(item(advertised_amount="1e999999999"), session=session)
    except RevenueCloseoutInputError as exc:
        assert "representation is too large" in str(exc)
    else:
        raise AssertionError("pathological amount must reject")
    assert session.calls == []


def test_ordinary_exact_amount_preserves_fixed_text():
    result = scan_paid_pr(
        item(advertised_amount="250.00"),
        session=FakeSession(routes()),
    )
    assert result["advertised_amount"] == "250.00"
