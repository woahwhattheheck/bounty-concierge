import pathlib
import sys
from urllib.parse import urlparse

import pytest
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import bounty_audit


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, issue, search_pages, pull_details, incomplete_pages=None, comment_pages=None):
        self.issue = issue
        self.search_pages = search_pages
        self.pull_details = pull_details
        self.incomplete_pages = set(incomplete_pages or [])
        self.comment_pages = comment_pages or {}
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, params))
        path = urlparse(url).path
        if path.endswith("/issues/42"):
            return FakeResponse(self.issue)
        if path.endswith("/issues/42/comments"):
            page = int((params or {}).get("page", 1))
            return FakeResponse(self.comment_pages.get(page, []))
        if path == "/search/issues":
            page = int((params or {}).get("page", 1))
            return FakeResponse({
                "items": self.search_pages.get(page, []),
                "incomplete_results": page in self.incomplete_pages,
            })
        if "/pulls/" in path:
            number = int(path.rsplit("/", 1)[-1])
            return FakeResponse(self.pull_details[number])
        raise AssertionError(f"unexpected URL {url}")


def _candidate(number, title, body):
    return {"number": number, "title": title, "body": body, "html_url": f"https://github.com/acme/widget/pull/{number}"}


def _detail(number, state="open", merged_at=None, draft=False):
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/acme/widget/pull/{number}",
        "state": state,
        "merged_at": merged_at,
        "draft": draft,
    }


def _comment(body, association="OWNER", login="maintainer", number=1):
    return {
        "body": body,
        "author_association": association,
        "html_url": f"https://github.com/acme/widget/issues/42#issuecomment-{number}",
        "created_at": "2026-09-01T00:00:00Z",
        "user": {"login": login},
    }


def test_references_issue_rejects_larger_number_false_positive():
    assert bounty_audit.references_issue(_candidate(1, "fix #42", ""), "acme/widget", 42)
    assert not bounty_audit.references_issue(_candidate(2, "fix #420", ""), "acme/widget", 42)


def test_references_issue_accepts_full_issue_url():
    pr = _candidate(1, "feature", "Closes https://github.com/acme/widget/issues/42")
    assert bounty_audit.references_issue(pr, "acme/widget", 42)


def test_audit_counts_canonical_pr_states_and_flags_open_issue_with_merged_pr():
    session = FakeSession(
        issue={"state": "open", "html_url": "https://github.com/acme/widget/issues/42"},
        search_pages={1: [
            _candidate(10, "Fix #42", ""),
            _candidate(11, "WIP", "/claim #42"),
            _candidate(12, "Old try", "Refs #42"),
            _candidate(13, "False positive #420", ""),
        ]},
        pull_details={
            10: _detail(10, state="closed", merged_at="2026-09-01T00:00:00Z"),
            11: _detail(11, state="open"),
            12: _detail(12, state="closed"),
        },
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["linked_pr_count"] == 3
    assert result["merged_pr_count"] == 1
    assert result["open_pr_count"] == 1
    assert result["closed_unmerged_pr_count"] == 1
    assert result["competition_level"] == "low"
    assert result["maintainer_expiry_signal"] is False
    assert result["stale_listing_signal"] is True
    assert [pr["number"] for pr in result["linked_prs"]] == [10, 11, 12]


def test_closed_issue_with_merged_pr_is_not_stale_listing_signal():
    session = FakeSession(
        issue={"state": "closed"},
        search_pages={1: [_candidate(10, "Fix #42", "")]},
        pull_details={10: _detail(10, state="closed", merged_at="2026-09-01T00:00:00Z")},
    )
    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)
    assert result["stale_listing_signal"] is False
    assert result["maintainer_expiry_signal"] is False
    assert not any(url.endswith("/issues/42/comments") for url, _ in session.calls)


def test_owner_expiry_comment_flags_open_listing_without_linked_pr():
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: []},
        pull_details={},
        comment_pages={1: [_comment("This bounty has been expired for quite a while.")]},
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["linked_pr_count"] == 0
    assert result["maintainer_expiry_signal"] is True
    assert result["maintainer_expiry_comment_count"] == 1
    assert result["maintainer_expiry_comments"][0]["author_association"] == "OWNER"
    assert result["stale_listing_signal"] is True


def test_member_cancellation_comment_is_accepted_as_maintainer_signal():
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: []},
        pull_details={},
        comment_pages={1: [_comment("The bounty was cancelled.", association="MEMBER")]},
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["maintainer_expiry_signal"] is True
    assert result["stale_listing_signal"] is True


def test_non_maintainer_and_negated_expiry_comments_do_not_flag_listing():
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: []},
        pull_details={},
        comment_pages={1: [
            _comment("This bounty is expired.", association="CONTRIBUTOR", number=1),
            _comment("This bounty is not expired.", association="OWNER", number=2),
            _comment("I asked whether the bounty has expired.", association="OWNER", number=3),
        ]},
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["maintainer_expiry_signal"] is False
    assert result["maintainer_expiry_comment_count"] == 0
    assert result["stale_listing_signal"] is False


def test_maintainer_comment_scan_paginates_to_find_expiry_signal():
    page_one = [_comment("This bounty is still available.", number=i) for i in range(1, 101)]
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: []},
        pull_details={},
        comment_pages={
            1: page_one,
            2: [_comment("The bounty is no longer offered.", association="COLLABORATOR", number=101)],
        },
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["maintainer_expiry_signal"] is True
    comment_pages = [
        params["page"]
        for url, params in session.calls
        if url.endswith("/issues/42/comments")
    ]
    assert comment_pages == [1, 2]


def test_comment_page_cap_marks_search_truncated():
    page = [_comment("This bounty is still available.", number=i) for i in range(1, 101)]
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: []},
        pull_details={},
        comment_pages={1: page},
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session, max_pages=1)

    assert result["maintainer_expiry_signal"] is False
    assert result["search_truncated"] is True


def test_search_paginates_and_deduplicates_exact_prs():
    page_one = [_candidate(i, f"Fix #42 part {i}", "") for i in range(1, 101)]
    page_two = [_candidate(100, "Fix #42 duplicate", ""), _candidate(101, "Fix #42 final", "")]
    details = {i: _detail(i) for i in range(1, 102)}
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: page_one, 2: page_two},
        pull_details=details,
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["linked_pr_count"] == 101
    assert result["open_pr_count"] == 101
    assert result["competition_level"] == "high"
    search_pages = [params["page"] for url, params in session.calls if url.endswith("/search/issues")]
    assert search_pages == [1, 2]


def test_incomplete_github_search_marks_partial_page_truncated():
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: [_candidate(10, "Fix #42", "")]},
        pull_details={10: _detail(10)},
        incomplete_pages={1},
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["linked_pr_count"] == 1
    assert result["open_pr_count"] == 1
    assert result["search_truncated"] is True
    assert "(search truncated)" in bounty_audit.format_summary(result)


def test_max_page_cap_marks_search_truncated():
    page = [_candidate(i, f"Fix #42 part {i}", "") for i in range(1, 101)]
    details = {i: _detail(i) for i in range(1, 101)}
    session = FakeSession(
        issue={"state": "open"},
        search_pages={1: page},
        pull_details=details,
    )
    result = bounty_audit.audit_bounty("acme/widget", 42, session=session, max_pages=1)
    assert result["search_truncated"] is True


def test_audit_bounties_preserves_original_fields():
    session = FakeSession(issue={"state": "open"}, search_pages={1: []}, pull_details={})
    rows = bounty_audit.audit_bounties(
        [{"repo": "acme/widget", "number": 42, "reward_rtc": 50.0}],
        session=session,
    )
    assert rows[0]["reward_rtc"] == 50.0
    assert rows[0]["canonical_audit"]["linked_pr_count"] == 0


def test_invalid_issue_inputs_fail_fast():
    with pytest.raises(ValueError):
        bounty_audit.audit_bounty("badrepo", 42, session=FakeSession({}, {}, {}))
    with pytest.raises(ValueError):
        bounty_audit.audit_bounty("acme/widget", 0, session=FakeSession({}, {}, {}))
    with pytest.raises(ValueError):
        bounty_audit.audit_bounty("acme/widget", 42, session=FakeSession({}, {}, {}), max_pages=0)


def test_http_failure_is_explicit():
    class BrokenSession:
        def get(self, *args, **kwargs):
            return FakeResponse({}, status=403)

    with pytest.raises(bounty_audit.BountyAuditError):
        bounty_audit.audit_bounty("acme/widget", 42, session=BrokenSession())


def test_format_summary_surfaces_stale_and_competition_signals():
    audit = {
        "repo": "acme/widget", "number": 42, "issue_state": "open",
        "linked_pr_count": 3, "open_pr_count": 2, "merged_pr_count": 1,
        "closed_unmerged_pr_count": 0, "competition_level": "medium",
        "maintainer_expiry_signal": True,
        "stale_listing_signal": True, "search_truncated": False,
    }
    text = bounty_audit.format_summary(audit)
    assert "linked_prs=3" in text
    assert "competition=medium" in text
    assert "maintainer_expiry_signal=YES" in text
    assert "stale_listing_signal=YES" in text
