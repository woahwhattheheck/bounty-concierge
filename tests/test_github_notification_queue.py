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
