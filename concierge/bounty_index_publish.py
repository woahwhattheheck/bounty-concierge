# SPDX-License-Identifier: MIT
"""Authoritative, fail-closed publication for the bounty index.

``concierge.bounty_index.fetch_bounties`` intentionally remains a best-effort
interactive API.  This module is the publication boundary: every configured
repository/page must be fetched and decoded successfully before a new canonical
index can replace the last-known-good file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

import requests

from concierge import bounty_index
from concierge.config import GITHUB_TOKEN, REPOS


class BountyIndexIncompleteError(RuntimeError):
    """The configured source set could not be proven complete."""


def _fail(message: str, cause: Exception | None = None) -> None:
    if cause is None:
        raise BountyIndexIncompleteError(message)
    raise BountyIndexIncompleteError(message) from cause


def _expected_issue_url(repo: str, number: int) -> str:
    """Return the only GitHub issue URL authorized for one canonical identity."""
    return f"https://github.com/{repo}/issues/{number}"


def fetch_complete_bounties(repos=None, token=None):
    """Fetch every configured bounty source, failing closed on incompleteness.

    Pull-request rows from GitHub's issues endpoint are expected and skipped.
    Every other row must match the parser's supported issue shape and bind
    exactly to its configured repository/issue identity. A source 404,
    transport error, invalid JSON, unsupported top-level shape, malformed row,
    duplicate issue identity, cross-wired URL, or later-page failure aborts the
    authoritative build.
    """
    if repos is None:
        repos = REPOS
    token = token or GITHUB_TOKEN

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    bounties = []
    seen_identities = set()
    for repo in repos:
        api_url = f"https://api.github.com/repos/{repo}/issues"
        params = {"labels": "bounty", "state": "open", "per_page": 100, "page": 1}

        while True:
            page = params["page"]
            try:
                response = requests.get(
                    api_url,
                    headers=headers,
                    params=params,
                    timeout=15,
                )
                if response.status_code == 404:
                    _fail(f"configured bounty source {repo} page {page} returned 404")
                response.raise_for_status()
            except BountyIndexIncompleteError:
                raise
            except requests.RequestException as exc:
                _fail(f"failed to fetch {repo} page {page}: {exc}", exc)

            try:
                issues = response.json()
            except ValueError as exc:
                _fail(f"failed to decode {repo} page {page}: {exc}", exc)
            if not isinstance(issues, list):
                _fail(f"unsupported payload for {repo} page {page}: expected list")

            for item_index, issue in enumerate(issues):
                if isinstance(issue, dict) and "pull_request" in issue:
                    continue

                normalized = bounty_index._normalize_issue_row(issue)
                if normalized is None:
                    _fail(
                        f"unsupported bounty row for {repo} page {page} "
                        f"item {item_index}"
                    )

                number = normalized["number"]
                identity = (repo, number)
                expected_url = _expected_issue_url(repo, number)
                if normalized["url"] != expected_url:
                    _fail(
                        f"cross-wired bounty identity for {repo} page {page} "
                        f"item {item_index}: expected issue {number} URL"
                    )
                if identity in seen_identities:
                    _fail(
                        f"duplicate bounty identity for {repo} issue {number} "
                        f"at page {page} item {item_index}"
                    )
                seen_identities.add(identity)

                title = normalized["title"]
                body = normalized["body"]
                labels = normalized["labels"]
                reward = bounty_index.parse_reward(title, body)
                bounties.append(
                    {
                        "repo": repo,
                        "number": number,
                        "title": title,
                        "body": body,
                        "url": normalized["url"],
                        "labels": labels,
                        "created_at": normalized["created_at"],
                        "reward_rtc": reward,
                        "difficulty": bounty_index.estimate_difficulty(
                            title, labels, reward
                        ),
                        "skills": bounty_index.tag_skills(title, body),
                    }
                )

            if not getattr(response, "links", {}).get("next"):
                break
            params["page"] += 1

    return bounties


def aggregate_complete(repos=None, token=None):
    """Return one authoritative index only after complete source traversal."""
    bounties = fetch_complete_bounties(repos=repos, token=token)
    bounties.sort(key=lambda bounty: bounty["reward_rtc"], reverse=True)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(bounties),
        "bounties": bounties,
    }


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
