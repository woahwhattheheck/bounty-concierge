#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Overlay captured GitHub issue state on a sanitized BountyHub catalog offline.

No network calls, retries, assignments, claims, or submissions are performed.
All catalog rows and funding fields remain visible; observations are age-checked.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit

MAX_BYTES = 8 * 1024 * 1024
ISSUE_PATH = re.compile(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?\Z")


class ReconcileError(ValueError):
    """An invalid capture that cannot be reconciled."""


def timestamp(value: Any) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise ReconcileError("timestamps must be ISO strings with a timezone") from exc


def issue_key(issue: dict[str, Any]) -> str:
    """Bind evidence to canonical issue URLs and its numeric issue identity."""
    keys = set()
    number = issue.get("number", issue.get("issue_number"))
    if type(number) is not int or number < 1 or "pull_request" in issue:
        raise ReconcileError("each observation must be an issue with a positive integer number")
    for field in ("html_url", "url"):
        value = issue.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ReconcileError("issue URLs must be strings")
        try:
            url = urlsplit(value)
        except ValueError as exc:
            raise ReconcileError("invalid issue URL") from exc
        path = url.path
        if url.netloc == "api.github.com":
            if not path.startswith("/repos/"):
                raise ReconcileError("GitHub API issue URLs must use /repos/owner/repo/issues/number")
            path = path[len("/repos"):]
        match = ISSUE_PATH.fullmatch(path)
        if (url.scheme != "https" or url.netloc not in {"github.com", "api.github.com"}
                or url.query or url.fragment or match is None
                or int(match[3]) != number):
            raise ReconcileError("issue URL and number must identify the same GitHub issue")
        keys.add(f"github:{match[1].lower()}/{match[2].lower()}#{number}")
    if len(keys) != 1:
        raise ReconcileError("issue observation needs consistent canonical GitHub URLs")
    return keys.pop()


def observation(issue: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(issue, dict):
        raise ReconcileError("issue observations must be objects")
    key = issue_key(issue)
    state = issue.get("state")
    state = state if isinstance(state, str) and state in {"open", "closed"} else None
    raw_assignees = issue.get("assignees")
    assignees = None
    if isinstance(raw_assignees, list) and all(
        isinstance(item, dict) and isinstance(item.get("login"), str)
        and 0 < len(item["login"]) <= 100 and item["login"].isprintable()
        for item in raw_assignees
    ):
        assignees = sorted({item["login"] for item in raw_assignees})
    # Do not retain issue bodies, account profiles, or arbitrary capture fields.
    return key, {"state": state, "assignees": assignees}


def reconcile(catalog: Any, snapshot: Any, *, as_of: datetime | None = None,
              max_age_seconds: int = 900) -> dict[str, Any]:
    if (not isinstance(catalog, dict) or not isinstance(catalog.get("schema"), str)
            or catalog["schema"] not in {"bountyhub-public-intake/v1", "bountyhub-public-intake/compact-v1"}
            or not isinstance(catalog.get("rows"), list)
            or not all(isinstance(row, dict) for row in catalog["rows"])):
        raise ReconcileError("input must be a sanitized BountyHub v1 or compact-v1 catalog")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("issues"), list):
        raise ReconcileError("GitHub snapshot must contain retrieved_at and an issues list")
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ReconcileError("max-age-seconds must be a positive integer")
    observed_at = timestamp(snapshot.get("retrieved_at"))
    evaluated_at = as_of or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        raise ReconcileError("as-of must have a timezone")
    evaluated_at = evaluated_at.astimezone(timezone.utc)
    age = (evaluated_at - observed_at).total_seconds()
    freshness = "future" if age < 0 else "stale" if age > max_age_seconds else "fresh"
    # A fresh GitHub issue cannot revive a stale or unknown provider listing:
    # the BountyHub card may have been assigned, claimed, or withdrawn since
    # its funding snapshot was captured.
    catalog_observed_at = None
    # Compact-v1 historically allowed no capture timestamp. Preserve the
    # existing offline compatibility path; full v1 catalogs must be fresh.
    catalog_freshness = (
        "legacy_compact_without_timestamp"
        if (catalog["schema"] == "bountyhub-public-intake/compact-v1"
            and "retrieved_at" not in catalog)
        else "missing_catalog_timestamp"
    )
    if catalog.get("retrieved_at") is not None:
        try:
            catalog_observed_at = timestamp(catalog["retrieved_at"])
        except ReconcileError:
            catalog_freshness = "invalid_catalog_timestamp"
        else:
            catalog_age = (evaluated_at - catalog_observed_at).total_seconds()
            catalog_freshness = (
                "future_catalog" if catalog_age < 0
                else "stale_catalog" if catalog_age > max_age_seconds
                else "fresh"
            )
    indexed: dict[str, dict[str, Any]] = {}
    conflicts = set()
    for issue in snapshot["issues"]:
        key, value = observation(issue)
        if key in indexed and indexed[key] != value:
            conflicts.add(key)
        indexed[key] = value
    result = deepcopy(catalog)
    counts: Counter[str] = Counter()
    for row in result["rows"]:
        key = row.get("work_key")
        evidence = indexed.get(key) if isinstance(key, str) else None
        if evidence is None:
            status = "not_observed"
        elif key in conflicts:
            status = "conflicting_observations"
            evidence = None
        elif freshness != "fresh":
            status = freshness
        elif catalog_freshness not in {"fresh", "legacy_compact_without_timestamp"}:
            status = catalog_freshness
        elif evidence["state"] == "closed":
            status = "closed"
        elif evidence["state"] is None:
            status = "unknown_state"
        elif evidence["assignees"] is None:
            status = "unknown_assignees"
        else:
            status = "assigned" if evidence["assignees"] else "open_unassigned"
        row["github_observation"] = {"status": status, **(evidence or {})}
        row["reconciled_candidate"] = row.get("catalog_candidate") is True and status == "open_unassigned"
        counts[status] += 1
    result["github_reconciliation"] = {
        "schema": "bountyhub-github-reconciliation/v1",
        "observed_at": observed_at.isoformat(), "evaluated_at": evaluated_at.isoformat(),
        "max_age_seconds": max_age_seconds, "freshness": freshness,
        "catalog_observed_at": (
            catalog_observed_at.isoformat() if catalog_observed_at else None
        ),
        "catalog_freshness": catalog_freshness,
        "observed_issue_count": len(indexed), "conflicting_issue_count": len(conflicts),
        "row_status_counts": dict(sorted(counts.items())),
        "candidate_row_count": sum(row["reconciled_candidate"] for row in result["rows"]),
        "scope": "Issue state and assignees only; not a claim, PR-duplication, funding or payout check.",
    }
    return result


def read_json(path: Path) -> tuple[Any, str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ReconcileError("capture exceeds the 8 MiB input limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ReconcileError("capture is not valid UTF-8 JSON") from exc
    return value, hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="sanitized intake JSON")
    parser.add_argument("--github-snapshot", type=Path, required=True, help="captured issue JSON")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of", help="evaluation time for reproducible offline runs")
    parser.add_argument("--max-age-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        catalog, catalog_hash = read_json(args.input)
        snapshot, snapshot_hash = read_json(args.github_snapshot)
        result = reconcile(catalog, snapshot, as_of=timestamp(args.as_of) if args.as_of else None,
                           max_age_seconds=args.max_age_seconds)
        result["github_reconciliation"].update(
            catalog_sha256=catalog_hash, snapshot_sha256=snapshot_hash)
        output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            if args.output.resolve() in {args.input.resolve(), args.github_snapshot.resolve()}:
                raise ReconcileError("output must not overwrite either source capture")
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (ReconcileError, OSError, ValueError) as exc:
        print(f"bountyhub-reconcile: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
