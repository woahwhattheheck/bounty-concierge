from __future__ import annotations

from datetime import datetime, timezone

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


def _with_ids(events, start):
    if events is not None and not isinstance(events, list):
        return events
    result = []
    for offset, event in enumerate(events or []):
        value = dict(event)
        value.setdefault("id", start + offset)
        result.append(value)
    return result


def routes(*, pr=None, reviews=None, comments=None, review_comments=None):
    return {
        f"{BASE}/pulls/17": pr or pr_payload(),
        (f"{BASE}/pulls/17/reviews", (("page", 1), ("per_page", 100))): _with_ids(reviews, 1000),
        (f"{BASE}/issues/17/comments", (("page", 1), ("per_page", 100))): _with_ids(comments, 2000),
        (f"{BASE}/pulls/17/comments", (("page", 1), ("per_page", 100))): _with_ids(review_comments, 3000),
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

def test_persisted_equality_cursor_deduplicates_unchanged_feedback():
    boundary = datetime(2026, 9, 13, 0, 0, 5, tzinfo=timezone.utc)
    comments = [{
        "id": 77,
        "updated_at": "2026-09-13T00:00:00Z",
        "author_association": "OWNER",
        "user": {"login": "owner"},
    }]
    first = scan_paid_pr(
        item(),
        session=FakeSession(routes(comments=comments)),
        scan_started_at=boundary,
    )
    assert first["next_action"] == "respond_to_maintainer"
    assert first["next_cursor"]["through_at"] == "2026-09-13T00:00:00Z"
    second_item = item()
    second_item.pop("last_seen_at")
    second_item["feedback_cursor"] = first["next_cursor"]
    second = scan_paid_pr(
        second_item,
        session=FakeSession(routes(comments=comments)),
        scan_started_at=boundary,
    )
    assert second["new_feedback_count"] == 0
    assert second["next_action"] == "await_acceptance"


def test_cross_endpoint_race_stays_beyond_safe_watermark_and_is_not_skipped():
    first_start = datetime(2026, 9, 13, 3, 0, 0, tzinfo=timezone.utc)
    issue_comment = [{
        "id": 88,
        "updated_at": "2026-09-13T02:59:58Z",
        "author_association": "OWNER",
        "user": {"login": "owner"},
    }]
    first = scan_paid_pr(
        item(last_seen_at="2026-09-13T02:59:00Z"),
        session=FakeSession(routes(comments=issue_comment)),
        scan_started_at=first_start,
    )
    assert first["next_cursor"]["through_at"] == "2026-09-13T02:59:55Z"
    assert first["new_feedback_count"] == 1

    raced_review = [{
        "id": 99,
        "state": "COMMENTED",
        "submitted_at": "2026-09-13T02:59:57Z",
        "author_association": "MEMBER",
        "user": {"login": "maintainer"},
    }]
    second_item = item()
    second_item.pop("last_seen_at")
    second_item["feedback_cursor"] = first["next_cursor"]
    second = scan_paid_pr(
        second_item,
        session=FakeSession(routes(reviews=raced_review, comments=issue_comment)),
        scan_started_at=datetime(2026, 9, 13, 3, 0, 10, tzinfo=timezone.utc),
    )
    assert second["next_action"] == "respond_to_maintainer"
    assert second["new_feedback_count"] == 1
    assert second["latest_feedback"]["event_identity"] == "review:99"


def test_edited_inline_review_comment_is_new_version_of_same_stable_identity():
    first_inline = [{
        "id": 123,
        "updated_at": "2026-09-13T01:00:00Z",
        "author_association": "MEMBER",
        "user": {"login": "maintainer"},
        "html_url": PR_URL + "#discussion_r123",
    }]
    first = scan_paid_pr(
        item(last_seen_at="2026-09-13T00:00:00Z"),
        session=FakeSession(routes(review_comments=first_inline)),
        scan_started_at=datetime(2026, 9, 13, 1, 0, 10, tzinfo=timezone.utc),
    )
    assert first["next_action"] == "respond_to_maintainer"
    assert first["latest_feedback"]["event_identity"] == "review_comment:123"

    edited_inline = [{
        "id": 123,
        "updated_at": "2026-09-13T01:01:00Z",
        "author_association": "MEMBER",
        "user": {"login": "maintainer"},
        "html_url": PR_URL + "#discussion_r123",
    }]
    second_item = item()
    second_item.pop("last_seen_at")
    second_item["feedback_cursor"] = first["next_cursor"]
    second = scan_paid_pr(
        second_item,
        session=FakeSession(routes(review_comments=edited_inline)),
        scan_started_at=datetime(2026, 9, 13, 1, 1, 10, tzinfo=timezone.utc),
    )
    assert second["next_action"] == "respond_to_maintainer"
    assert second["new_feedback_count"] == 1
    assert second["latest_feedback"]["event_identity"] == "review_comment:123"
