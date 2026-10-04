# SPDX-License-Identifier: MIT
"""Authoritative, fail-closed publication for the bounty index.

``concierge.bounty_index.fetch_bounties_report`` owns the bounded live read and
explicit source status. This module applies the stricter publication boundary:
every configured repository/page and canonical issue identity must be validated
before a new index can replace the last-known-good file.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile

from concierge import bounty_index


class BountyIndexIncompleteError(RuntimeError):
    """The configured source set could not be proven complete."""


class BountyIndexSupersededError(RuntimeError):
    """A later-started complete refresh already owns the published index."""


def _fail(message: str, cause: Exception | None = None) -> None:
    if cause is None:
        raise BountyIndexIncompleteError(message)
    raise BountyIndexIncompleteError(message) from cause


def _matches_issue_url(url: str, repo: str, number: int) -> bool:
    """Bind a GitHub issue URL while allowing owner/repository case variants."""
    prefix = "https://github.com/"
    suffix = f"/issues/{number}"
    return (
        url.isascii()
        and url.startswith(prefix)
        and url.endswith(suffix)
        and url[len(prefix):-len(suffix)].casefold() == repo.casefold()
    )


def _fetch_complete_report(repos=None, token=None):
    """Validate a publishable report without discarding its source metadata.

    The shared collector validates source names, rejects an empty source set,
    normalizes issue rows, traverses at most its configured default page limit,
    closes responses and stops subsequent requests when GitHub reports a rate
    limit. It performs no retry loop. Publication additionally binds each row
    to its canonical issue URL (owner/repo case-insensitive) and rejects
    duplicate identities.

    Transport/HTTP/schema errors, quota deferrals and page-limit exhaustion
    abort publication, retaining the previous index. A source failure is not
    an empty queue, and a complete read is not claim or payment eligibility.
    """
    try:
        report = bounty_index.fetch_bounties_report(repos=repos, token=token)
    except (TypeError, ValueError) as exc:
        _fail(f"invalid bounty source configuration: {exc}", exc)

    if not report["complete"]:
        # The collector's summary contains safe source statuses and partial row
        # counts, not raw request exception text or authenticated request data.
        _fail(str(bounty_index.BountyFetchIncompleteError(report)))

    bounties = report["bounties"]
    seen_identities = set()
    for bounty in bounties:
        repo = bounty["repo"]
        number = bounty["number"]
        identity = (repo.casefold(), number)
        if not _matches_issue_url(bounty["url"], repo, number):
            _fail(f"cross-wired bounty identity for {repo}: expected issue {number} URL")
        if identity in seen_identities:
            _fail(f"duplicate bounty identity for {repo} issue {number}")
        seen_identities.add(identity)
    return report


def fetch_complete_bounties(repos=None, token=None):
    """Return validated rows, preserving the existing list-valued API."""
    return _fetch_complete_report(repos=repos, token=token)["bounties"]


def aggregate_complete(repos=None, token=None):
    """Return a sorted index with its original read interval and coverage."""
    report = _fetch_complete_report(repos=repos, token=token)
    report["bounties"].sort(key=lambda bounty: bounty["reward_rtc"], reverse=True)
    return report


def render_index(data) -> str:
    """Serialize a canonical index for publication."""
    return json.dumps(data, indent=2, default=str) + "\n"


def _report_order(report):
    """Order complete reads by start, then end; export time is not freshness."""
    if not isinstance(report, dict) or report.get("complete") is not True:
        return None
    try:
        started = datetime.fromisoformat(report["started_at"])
        finished = datetime.fromisoformat(report["updated_at"])
        if started.tzinfo is None or finished.tzinfo is None or finished < started:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return started, finished


@contextmanager
def _publication_lock(target):
    """Lock only the final comparison/replacement, never collection or fsync.

    Keep the sidecar inode: unlinking it would split cooperating writers across
    different locks. The OS releases the lock when a publisher exits or crashes.
    """
    lock_path = target.with_name(f".{target.name}.publish.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as handle:
        if os.name == "nt":
            import msvcrt
            # Byte-range locks may extend past EOF; avoid an unlocked
            # initialization write racing another publisher's acquired lock.
            handle.seek(0)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)


def write_index_atomic(output_path, repos=None, token=None):
    """Build completely, fsync, then atomically replace the published index.

    Network and schema validation happen before a temporary file is created.
    Any failure before the final ``os.replace`` leaves the last-known-good
    target untouched and removes temporary residue. Cooperating publishers use
    a short per-output lock: the later-started complete read wins even when an
    earlier collector finishes last. Superseded callers raise explicitly rather
    than reporting their unpersisted data as published. This is local/shared-file
    coordination, not a distributed provider quota or an atomic GitHub snapshot.
    """
    data = aggregate_complete(repos=repos, token=token)
    incoming_order = _report_order(data)
    if incoming_order is None:
        _fail("collector report has no valid timezone-aware read interval")
    payload = render_index(data)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with _publication_lock(target):
            try:
                with target.open("r", encoding="utf-8") as current_file:
                    current = json.load(current_file)
            except (FileNotFoundError, ValueError, UnicodeError):
                current = None
            current_order = _report_order(current)
            # Legacy exports without a source interval can be upgraded, but
            # their export timestamp must never outrank a validated live read.
            if current_order is not None and current_order > incoming_order:
                raise BountyIndexSupersededError(
                    f"kept read started at {current_order[0].isoformat()}; "
                    f"discarded read started at {incoming_order[0].isoformat()}"
                )
            os.replace(temp_name, target)
            replaced = True
    finally:
        if not replaced:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and atomically publish a complete bounty index"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="canonical index path to replace only after a complete build",
    )
    args = parser.parse_args(argv)

    try:
        write_index_atomic(args.output)
    except BountyIndexSupersededError as exc:
        print(f"[deferred] bounty index superseded: {exc}", file=sys.stderr)
        return 3
    except BountyIndexIncompleteError as exc:
        print(f"[error] bounty index incomplete: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
