from concierge.github_notification_queue import compile_notification_queue


def test_subsecond_generation_wins_before_unread_filtering():
    older = {
        "id": "7", "reason": "comment", "unread": True,
        "updated_at": "2026-10-06T18:00:00Z",
        "repository": {"full_name": "owner/repo"},
        "subject": {"type": "Issue", "title": "Example",
                    "url": "https://api.github.com/repos/owner/repo/issues/1",
                    "latest_comment_url": None},
    }
    newer = dict(older, unread=False, updated_at="2026-10-06T18:00:00.500000Z")
    for rows in ([older, newer], [newer, older]):
        result = compile_notification_queue(rows)
        assert result["queue"] == []
        assert result["exact_read_urls"] == []
        assert result["source"]["skipped_read_rows"] == 1
        retained = compile_notification_queue(rows, include_read=True)["queue"]
        assert len(retained) == 1
        assert retained[0]["updated_at"] == newer["updated_at"]
        assert retained[0]["unread"] is False

def test_equal_timestamp_uses_later_retained_read_state():
    first = {
        "id": "7", "reason": "comment", "unread": True,
        "updated_at": "2026-10-08T18:00:00Z",
        "repository": {"full_name": "owner/repo"},
        "subject": {"type": "Issue", "title": "Paid claim update",
                    "url": "https://api.github.com/repos/owner/repo/issues/7",
                    "latest_comment_url": None},
    }
    read = dict(first, unread=False)
    assert compile_notification_queue([first, read])["queue"] == []
    # The opposite order represents a subsequent genuinely unread snapshot.
    assert compile_notification_queue([read, first])["queue"][0]["unread"] is True


def test_overdue_inbox_threads_precede_fresh_review_requests():
    def notification(notification_id, reason, updated_at):
        return {
            "id": notification_id, "reason": reason, "unread": True,
            "updated_at": updated_at, "repository": {"full_name": "owner/repo"},
            "subject": {
                "type": "PullRequest", "title": notification_id,
                "url": "https://api.github.com/repos/owner/repo/pulls/" + notification_id,
                "latest_comment_url": None,
            },
        }

    old = notification("1", "comment", "2026-10-07T04:00:00Z")
    fresh = notification("2", "review_requested", "2026-10-09T04:00:00Z")
    oldest = notification("3", "subscribed", "2026-10-06T04:00:00Z")
    rows = [old, fresh, oldest]
    assert [row["notification_id"] for row in compile_notification_queue(rows)["queue"]] == ["2", "1", "3"]
    queued = compile_notification_queue(rows, as_of_utc="2026-10-09T05:00:00Z")
    assert [row["notification_id"] for row in queued["queue"]] == ["3", "1", "2"]
    assert queued["source"]["estimated_overdue_rows"] == 2
