# SPDX-License-Identifier: MIT
"""Authoritative, fail-closed publication for the bounty index.

``concierge.bounty_index.fetch_bounties_report`` owns the bounded live read and
explicit source status. This module applies the stricter publication boundary:
every configured repository/page and canonical issue identity must be validated
before a new index can replace the last-known-good file.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from concierge import bounty_index


class BountyIndexIncompleteError(RuntimeError):
    """The configured source set could not be proven complete."""


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


def write_index_atomic(output_path, repos=None, token=None):
    """Build completely, fsync, then atomically replace the published index.

    Network and schema validation happen before a temporary file is created.
    Any failure before the final ``os.replace`` leaves the last-known-good
    target untouched and removes temporary residue.
    """
    data = aggregate_complete(repos=repos, token=token)
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
    except BountyIndexIncompleteError as exc:
        print(f"[error] bounty index incomplete: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
