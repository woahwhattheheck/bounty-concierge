# SPDX-License-Identifier: MIT
"""Compile retained GitHub Notifications into a bounded exact-read queue.

This module performs no network I/O. It consumes a JSON response previously
captured from GitHub's Notifications API and emits exact subject/comment URLs
that a caller can read without broad repository or issue search.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


_SCHEMA = "bounty-concierge.github-notification-exact-queue/v1"
_REASON_PRIORITY = {
    "review_requested": 0,
    "mention": 1,
    "comment": 2,
    "state_change": 3,
    "author": 4,
    "subscribed": 5,
}
_ACTION_CLASS = {
    "review_requested": "REVIEW_REQUESTED",
    "mention": "MENTION",
    "comment": "THREAD_COMMENT",
    "state_change": "STATE_CHANGE",
    "author": "AUTHORED_THREAD_UPDATE",
    "subscribed": "SUBSCRIBED_UPDATE",
}
_ALLOWED_TYPES = {"PullRequest", "Issue"}


class NotificationQueueError(ValueError):
    """Retained notification input cannot safely produce an exact-read queue."""


def _utc_timestamp(value: Any, *, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise NotificationQueueError(f"{field} must be an ISO-8601 timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise NotificationQueueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NotificationQueueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _api_url(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.startswith("https://api.github.com/repos/"):
        raise NotificationQueueError(f"{field} must be a GitHub repository API URL")
    return value


def _repo_name(raw: Any) -> str:
    if type(raw) is not dict:
        raise NotificationQueueError("repository must be an object")
    full_name = raw.get("full_name")
    if (
        type(full_name) is not str
        or full_name != full_name.strip()
        or "/" not in full_name
        or full_name.startswith("/")
        or full_name.endswith("/")
    ):
        raise NotificationQueueError("repository.full_name must be owner/repo")
    return full_name


def _subject(raw: Any) -> dict[str, Any] | None:
    if type(raw) is not dict:
        raise NotificationQueueError("subject must be an object")
    subject_type = raw.get("type")
    # Notifications can include releases/discussions/other thread types. They are
    # valid provider rows but not PR/issue acceptance work, so skip rather than
    # failing an otherwise useful retained batch.
    if subject_type not in _ALLOWED_TYPES:
        return None
    title = raw.get("title")
    if type(title) is not str or not title.strip():
        raise NotificationQueueError("subject.title must be nonempty text")
    subject_url = _api_url(raw.get("url"), field="subject.url")
    if subject_url is None:
        raise NotificationQueueError("subject.url is required")
    latest_comment_url = _api_url(
        raw.get("latest_comment_url"), field="subject.latest_comment_url"
    )
    return {
        "type": subject_type,
        "title": title.strip(),
        "url": subject_url,
        "latest_comment_url": latest_comment_url,
    }


def _row(raw: Any, *, index: int) -> dict[str, Any] | None:
    if type(raw) is not dict:
        raise NotificationQueueError(f"notifications[{index}] must be an object")
    notification_id = raw.get("id")
    if type(notification_id) is not str or not notification_id:
        raise NotificationQueueError(f"notifications[{index}].id must be text")
    reason = raw.get("reason")
    if type(reason) is not str or not reason:
        raise NotificationQueueError(f"notifications[{index}].reason must be text")
    unread = raw.get("unread")
    if type(unread) is not bool:
        raise NotificationQueueError(f"notifications[{index}].unread must be boolean")
    updated_at = _utc_timestamp(
        raw.get("updated_at"), field=f"notifications[{index}].updated_at"
    )
    repo = _repo_name(raw.get("repository"))
    subject = _subject(raw.get("subject"))
    if subject is None:
        return None

    # A retained notification does not identify the latest comment's author.
    # Do not infer whether an update came from a maintainer, our own account, or a bot.
    reads = [subject["url"]]
    if subject["latest_comment_url"] and subject["latest_comment_url"] not in reads:
        reads.append(subject["latest_comment_url"])

    return {
        "notification_id": notification_id,
        "repo": repo,
        "reason": reason,
        "unread": unread,
        "updated_at": updated_at,
        "subject_type": subject["type"],
        "title": subject["title"],
        "subject_url": subject["url"],
        "latest_comment_url": subject["latest_comment_url"],
        "action_class": _ACTION_CLASS.get(reason, "THREAD_UPDATE"),
        "exact_reads": reads,
        "requires_author_check": subject["latest_comment_url"] is not None,
    }


def compile_notification_queue(
    notifications: Any, *, include_read: bool = False, limit: int = 100,
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    """Return a deduplicated, priority-ordered exact-read queue."""
    if type(notifications) is not list:
        raise NotificationQueueError("notification input must be a JSON array")
    if isinstance(limit, bool) or type(limit) is not int or not 1 <= limit <= 1000:
        raise NotificationQueueError("limit must be an integer between 1 and 1000")

    # A caller-supplied clock makes overdue sorting deterministic without I/O.
    as_of = (
        datetime.fromisoformat(
            _utc_timestamp(as_of_utc, field="as_of_utc").replace("Z", "+00:00")
        )
        if as_of_utc is not None else None
    )

    retained: dict[str, dict[str, Any]] = {}
    skipped_unsupported = 0
    for index, raw in enumerate(notifications):
        row = _row(raw, index=index)
        if row is None:
            skipped_unsupported += 1
            continue
        # GitHub notification IDs are thread-stable. Keep only the newest retained
        # generation before applying unread filtering; otherwise an older unread
        # copy could survive after a newer copy has already been marked read.
        previous = retained.get(row["notification_id"])
        # Equal provider timestamps do not imply identical read/unread state.
        # Retained snapshots are ordered oldest to newest; on an exact tie
        # prefer the later input row, not the stale first row.
        # ISO text omits fractional seconds when zero, so lexical ordering
        # would rank "...00Z" above the newer "...00.500000Z".
        if previous is None or datetime.fromisoformat(
            row["updated_at"].replace("Z", "+00:00")
        ) >= datetime.fromisoformat(previous["updated_at"].replace("Z", "+00:00")):
            retained[row["notification_id"]] = row

    eligible = []
    skipped_read = 0
    for row in retained.values():
        if not include_read and not row["unread"]:
            skipped_read += 1
            continue
        eligible.append(row)

    def is_overdue(row: dict[str, Any]) -> bool:
        if as_of is None:
            return False
        updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
        return (as_of - updated).total_seconds() > 24 * 3600

    overdue_rows = sum(is_overdue(row) for row in eligible)

    def priority(row: dict[str, Any]) -> tuple[Any, ...]:
        updated_epoch = datetime.fromisoformat(
            row["updated_at"].replace("Z", "+00:00")
        ).timestamp()
        reason_priority = _REASON_PRIORITY.get(row["reason"], 99)
        kind_priority = 0 if row["subject_type"] == "PullRequest" else 1
        if is_overdue(row):
            # Older unanswered candidates go first. An old notification alone
            # is not proof the maintainer needs a response: inspect exact reads.
            return (0, updated_epoch, reason_priority, kind_priority,
                    row["repo"].casefold(), row["notification_id"])
        return (1, reason_priority, kind_priority, -updated_epoch,
                row["repo"].casefold(), row["notification_id"])

    ordered = sorted(eligible, key=priority)
    selected = ordered[:limit]
    unique_reads: list[str] = []
    seen_reads: set[str] = set()
    for row in selected:
        for url in row["exact_reads"]:
            if url not in seen_reads:
                seen_reads.add(url)
                unique_reads.append(url)

    return {
        "schema": _SCHEMA,
        "source": {
            "kind": "RETAINED_GITHUB_NOTIFICATIONS",
            "provider_calls": 0,
            "input_rows": len(notifications),
            "supported_threads": len(retained),
            "eligible_rows": len(ordered),
            "skipped_read_rows": skipped_read,
            "skipped_unsupported_rows": skipped_unsupported,
            "returned_rows": len(selected),
            "estimated_overdue_rows": overdue_rows,
            "as_of_utc": (
                as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if as_of is not None else None
            ),
            "truncated": len(ordered) > len(selected),
        },
        "queue": selected,
        "exact_read_urls": unique_reads,
        "limits": {
            "notification_payload_does_not_identify_latest_comment_author": True,
            "maintainer_or_self_update_requires_exact_comment_read": True,
            "no_merge_payment_or_acceptance_inferred": True,
        },
    }


def _load(path: str) -> Any:
    try:
        if path == "-":
            return json.load(sys.stdin)
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NotificationQueueError("unable to read notification JSON") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concierge-notifications",
        description=(
            "Compile retained GitHub Notifications JSON into a zero-network exact-read queue."
        ),
    )
    parser.add_argument(
        "--input",
        default="-",
        help="Retained Notifications API JSON file, or - for stdin (default: -)",
    )
    parser.add_argument(
        "--include-read",
        action="store_true",
        help="Include notifications already marked read",
    )
    parser.add_argument(
        "--as-of-utc",
        default=None,
        help="Optional ISO-8601 UTC time: prioritize unread threads older than 24h, oldest first",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum queue rows to emit (1-1000, default: 100)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = compile_notification_queue(
            _load(args.input), include_read=args.include_read, limit=args.limit,
            as_of_utc=args.as_of_utc,
        )
    except NotificationQueueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
