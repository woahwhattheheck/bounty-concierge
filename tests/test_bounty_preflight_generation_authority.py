# SPDX-License-Identifier: MIT

from concierge import bounty_preflight as bp


_GENERATION_TIME = "2026-09-13T00:00:00Z"
_ISSUE_URL = "https://api.github.com/repos/acme/repo/issues/18"


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def issue():
    return {
        "title": "Paid repair",
        "body": "/bounty $500",
        "labels": ["$500"],
        "assignees": [],
        "state": "open",
        "updated_at": _GENERATION_TIME,
        "comments": 1,
    }


def comment(*, body="Watching this one.", association="NONE", login="carol"):
    return {
        "id": 1803,
        "updated_at": _GENERATION_TIME,
        "body": body,
        "author_association": association,
        "user": {"login": login, "type": "User"},
    }


def actionable_audit(*args, **kwargs):
    return {
        "issue_state": "open",
        "open_pr_count": 0,
        "stale_listing_signal": False,
        "search_truncated": False,
    }


def actionable_qualification(snapshot, *, saturation_threshold):
    return {
        "disposition": "ACTIONABLE",
        "dispatch": True,
        "reason_codes": [],
        "reasons": [],
        "signals": {},
    }


class EditingSession:
    def __init__(self, initial_comment, later_comment):
        self.initial_comment = initial_comment
        self.later_comment = later_comment
        self.comment_reads = 0

    def get(self, url, *, headers, params=None, timeout=15):
        if url == _ISSUE_URL:
            return Response(issue())
        if url.endswith("/comments"):
            self.comment_reads += 1
            payload = self.initial_comment if self.comment_reads == 1 else self.later_comment
            return Response([payload] if params["page"] == 1 else [])
        raise AssertionError(f"unexpected URL: {url}")


def test_same_timestamp_claim_body_edit_holds_actionable_dispatch(monkeypatch):
    initial = comment(body="Watching this one.")
    changed = comment(body="I'm working on this bounty.")
    session = EditingSession(initial, changed)
    monkeypatch.setattr(bp, "audit_bounty", actionable_audit)
    monkeypatch.setattr(bp, "qualify_dispatch", actionable_qualification)

    result = bp.preflight_bounty("acme/repo", 18, session=session)

    assert result["qualification"]["disposition"] == "HOLD"
    assert result["qualification"]["dispatch"] is False
    assert "CANONICAL_GENERATION_CHANGED" in result["qualification"]["reason_codes"]
    assert result["qualification"]["signals"]["canonical_generation_stable"] is False
    assert session.comment_reads == 2


def test_same_timestamp_authority_association_edit_holds(monkeypatch):
    initial = comment(body="Ordinary note.", association="NONE")
    changed = comment(body="Ordinary note.", association="MEMBER")
    session = EditingSession(initial, changed)
    monkeypatch.setattr(bp, "audit_bounty", actionable_audit)
    monkeypatch.setattr(bp, "qualify_dispatch", actionable_qualification)

    result = bp.preflight_bounty("acme/repo", 18, session=session)

    assert result["qualification"]["disposition"] == "HOLD"
    assert result["qualification"]["dispatch"] is False
    assert result["qualification"]["signals"]["canonical_generation_stable"] is False


def test_generation_marker_is_content_bound_without_retaining_identity_or_body():
    first = bp._comment_generation_entry(
        comment(body="secret-ish source text", login="private-user")
    )
    second = bp._comment_generation_entry(
        comment(body="different text", login="private-user")
    )

    assert first is not None
    assert second is not None
    assert first != second
    assert "secret-ish source text" not in repr(first)
    assert "private-user" not in repr(first)
    assert len(first[2]) == 64
