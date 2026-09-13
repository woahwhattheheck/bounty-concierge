import json
from urllib.parse import urlparse

from concierge import bounty_audit


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, comments, *, issue_state="open"):
        self.comments = comments
        self.issue_state = issue_state
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, params))
        path = urlparse(url).path
        if path.endswith("/issues/42"):
            return FakeResponse(
                {"state": self.issue_state, "html_url": "https://github.com/acme/widget/issues/42"}
            )
        if path.endswith("/issues/42/comments"):
            page = int((params or {}).get("page", 1))
            return FakeResponse(self.comments.get(page, []))
        if path == "/search/issues":
            return FakeResponse({"items": [], "incomplete_results": False})
        raise AssertionError(f"unexpected URL {url}")


def comment(body, login, *, association="NONE", number=1):
    return {
        "body": body,
        "author_association": association,
        "html_url": f"https://github.com/acme/widget/issues/42#issuecomment-{number}",
        "created_at": "2026-09-13T00:00:00Z",
        "user": {"login": login},
    }


def test_comment_commands_count_unique_claimants_without_quote_or_code_false_positives():
    session = FakeSession(
        {
            1: [
                comment("/attempt", "alice", number=1),
                comment("/opire try\nWorking now.", "alice", number=2),
                comment("/opire try\nImplementation incoming.", "bob", number=3),
                comment("/claim", "carol", number=4),
                comment("/attempt", "maintainer", association="OWNER", number=5),
                comment("> /attempt\nquoted instructions", "dave", number=6),
                comment("```\n/attempt\n```", "erin", number=7),
                comment("I might use /attempt later.", "frank", number=8),
            ]
        }
    )

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["attempt_count"] == 3
    assert result["attempt_comment_count"] == 4
    assert result["attempt_claimants"] == ["alice", "bob", "carol"]
    assert result["competition_level"] == "medium"
    assert all("body" not in item for item in result["attempt_comments"])
    assert "Implementation incoming." not in json.dumps(result)


def test_comment_scan_paginates_and_finds_later_claimant():
    first = [comment("still evaluating", f"user{i}", number=i) for i in range(1, 101)]
    session = FakeSession({1: first, 2: [comment("/attempt", "late-user", number=101)]})

    result = bounty_audit.audit_bounty("acme/widget", 42, session=session)

    assert result["attempt_count"] == 1
    assert result["attempt_claimants"] == ["late-user"]
    assert result["search_truncated"] is False
    pages = [
        params["page"]
        for url, params in session.calls
        if url.endswith("/issues/42/comments")
    ]
    assert pages == [1, 2]


def test_comment_page_cap_marks_audit_incomplete():
    first = [
        comment("/attempt" if i == 1 else "still evaluating", f"user{i}", number=i)
        for i in range(1, 101)
    ]
    session = FakeSession({1: first})

    result = bounty_audit.audit_bounty(
        "acme/widget", 42, session=session, max_pages=1
    )

    assert result["attempt_count"] == 1
    assert result["search_truncated"] is True


def test_audit_bounties_populates_attempt_count_but_preserves_explicit_source_count():
    comments = {
        1: [
            comment("/attempt", "alice", number=1),
            comment("/claim", "bob", number=2),
        ]
    }

    derived = bounty_audit.audit_bounties(
        [{"repo": "acme/widget", "number": 42}],
        session=FakeSession(comments),
    )
    explicit = bounty_audit.audit_bounties(
        [{"repo": "acme/widget", "number": 42, "attempt_count": 7}],
        session=FakeSession(comments),
    )

    assert derived[0]["attempt_count"] == 2
    assert derived[0]["canonical_audit"]["attempt_count"] == 2
    assert explicit[0]["attempt_count"] == 7
    assert explicit[0]["canonical_audit"]["attempt_count"] == 2


def test_summary_surfaces_comment_attempt_pressure():
    audit = {
        "repo": "acme/widget",
        "number": 42,
        "issue_state": "open",
        "linked_pr_count": 0,
        "open_pr_count": 0,
        "attempt_count": 5,
        "merged_pr_count": 0,
        "closed_unmerged_pr_count": 0,
        "competition_level": "high",
        "maintainer_expiry_signal": False,
        "stale_listing_signal": False,
        "search_truncated": False,
    }

    summary = bounty_audit.format_summary(audit)

    assert "attempts=5" in summary
    assert "competition=high" in summary
