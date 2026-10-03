# SPDX-License-Identifier: MIT
"""Import captured GrantFox PR/comment pairs into the existing reward ledger.

This adapter reads local GitHub API response bytes. It extracts merge and
noncash recognition facts; it neither fetches nor changes provider state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Iterable

if __package__:
    from .reward_settlement_ledger import (
        INPUT_SCHEMA, LedgerInputError, _positive_int, _repo, _sha256,
        _timestamp, compile_document, load_json_bytes,
    )
else:
    from reward_settlement_ledger import (
        INPUT_SCHEMA, LedgerInputError, _positive_int, _repo, _sha256,
        _timestamp, compile_document, load_json_bytes,
    )


_MERGED = re.compile(
    r"^@([A-Za-z0-9-]+)'s PR #([1-9][0-9]*) was approved and merged by "
    r"@[A-Za-z0-9-]+\.$", re.MULTILINE,
)
_AWARD = re.compile(
    r"^🏆 \*\*@([A-Za-z0-9-]+)\*\*: You earned \*\*([1-9][0-9]*) "
    r"FoxPoints\*\* for this contribution!([^\n]*)$", re.MULTILINE,
)
_TOTAL = re.compile(r"\(([0-9]+) total points\)")


def _source(record: dict, raw: bytes, observed_at: str, kind: str, authority: str) -> dict:
    record_id = _positive_int(record.get("id"), field=f"{kind}.id")
    return {
        "source_id": f"github-{kind}:{record_id}",
        "source_ref": record["url"],
        "source_sha256": _sha256(raw),
        "observed_at": observed_at,
        "authority": authority,
    }


def _case(pr: dict, raw: bytes, observed_at: str) -> dict:
    base = pr.get("base")
    repository = base.get("repo") if isinstance(base, dict) else None
    repo = _repo(repository.get("full_name") if isinstance(repository, dict) else None)
    number = _positive_int(pr.get("number"), field="pr.number")
    if pr.get("url") != f"https://api.github.com/repos/{repo}/pulls/{number}":
        raise LedgerInputError("PR URL does not match its repository and number")
    if pr.get("merged") is not True:
        raise LedgerInputError(f"{repo}#{number} is not a captured merged PR")
    return {
        "case_id": f"github:{repo.casefold()}/pull/{number}",
        "work": {
            "repo": repo,
            "pr": number,
            "merge_commit_sha": pr.get("merge_commit_sha"),
            "merged_at": pr.get("merged_at"),
            "source": _source(pr, raw, observed_at, "pr", "REPOSITORY"),
        },
        "events": [],
    }


def _recognition(comment: dict, raw: bytes, pr: dict, case: dict, observed_at: str) -> dict:
    user = comment.get("user")
    if not isinstance(user, dict) or user.get("login") != "grantfox-oss[bot]":
        raise LedgerInputError("comment is not a captured GrantFox provider record")
    comment_id = _positive_int(comment.get("id"), field="comment.id")
    repo = case["work"]["repo"]
    expected_url = f"https://api.github.com/repos/{repo}/issues/comments/{comment_id}"
    if comment.get("url") != expected_url:
        raise LedgerInputError("comment URL does not match the PR repository and comment ID")
    body = comment.get("body")
    if not isinstance(body, str):
        raise LedgerInputError("GrantFox comment body must be text")
    merged = _MERGED.findall(body)
    awards = _AWARD.findall(body)
    if len(merged) != 1 or len(awards) != 1:
        raise LedgerInputError("comment does not contain one supported GrantFox merge and points award")
    claimant, points, tail = awards[0]
    pr_user = pr.get("user")
    author = pr_user.get("login") if isinstance(pr_user, dict) else None
    if (
        int(merged[0][1]) != case["work"]["pr"]
        or merged[0][0].casefold() != claimant.casefold()
        or not isinstance(author, str)
        or author.casefold() != claimant.casefold()
    ):
        raise LedgerInputError("GrantFox award does not match the captured PR and claimant")
    _stamp, observed_dt = _timestamp(observed_at, field="observed_at")
    for field in ("created_at", "updated_at"):
        _stamp, comment_dt = _timestamp(comment.get(field), field=f"comment.{field}")
        if comment_dt > observed_dt:
            raise LedgerInputError(f"comment.{field} is after the supplied observation time")
    totals = _TOTAL.findall(tail)
    if len(totals) > 1 or ("total points" in tail and not totals):
        raise LedgerInputError("GrantFox reported account total is ambiguous or unsupported")
    event = {
        "event_id": f"grantfox-comment-{comment_id}",
        "kind": "PROVIDER_RECOGNITION",
        "provider": "grantfox",
        "claimant": claimant.casefold(),
        "award_id": f"github-comment:{comment_id}",
        "unit": "FoxPoints",
        "quantity": int(points),
        "source": _source(comment, raw, observed_at, "comment", "PROVIDER"),
    }
    if totals:
        event["reported_account_total"] = int(totals[0])
    return event


def import_pairs(pairs: Iterable[tuple[Path, Path]], observed_at: str) -> dict:
    """Build ledger input from exact local PR/comment JSON response pairs.

    GitHub object identities keep IDs stable across filenames and input order.
    Repeated identical captures count once. Different captures of one object
    in this single-observation batch are reported instead of silently chosen.
    """
    _timestamp(observed_at, field="observed_at")
    cases: dict[tuple[str, int], dict] = {}
    events: dict[str, dict] = {}
    for pr_path, comment_path in pairs:
        pr_raw = Path(pr_path).read_bytes()
        comment_raw = Path(comment_path).read_bytes()
        pr = load_json_bytes(pr_raw)
        comment = load_json_bytes(comment_raw)
        case = _case(pr, pr_raw, observed_at)
        event = _recognition(comment, comment_raw, pr, case, observed_at)
        key = (case["work"]["repo"].casefold(), case["work"]["pr"])
        previous = cases.get(key)
        if previous is not None:
            if previous["work"] != case["work"]:
                raise LedgerInputError(f"different PR captures supplied for {case['case_id']}")
            case = previous
        else:
            cases[key] = case
        previous_event = events.get(event["event_id"])
        if previous_event is not None:
            if previous_event != event:
                raise LedgerInputError(f"different comment captures supplied for {event['event_id']}")
            continue
        events[event["event_id"]] = event
        case["events"].append(event)
    if not cases:
        raise LedgerInputError("at least one PR/comment pair is required")
    ordered = [cases[key] for key in sorted(cases)]
    for case in ordered:
        case["events"].sort(key=lambda event: event["event_id"])
    document = {"schema": INPUT_SCHEMA, "generated_at": observed_at, "cases": ordered}
    # Reuse the established compiler's input contract and lifecycle semantics.
    compile_document(document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair", nargs=2, action="append", required=True, metavar=("PR_JSON", "COMMENT_JSON"),
        help="Captured GitHub PR and GrantFox award-comment response files; repeat for a batch",
    )
    parser.add_argument(
        "--observed-at", required=True,
        help="Capture observation time in UTC seconds, for example 2026-10-03T06:53:24Z",
    )
    parser.add_argument("--output", type=Path, help="Create this input file exclusively; default: JSON to stdout")
    args = parser.parse_args(argv)
    try:
        document = import_pairs(args.pair, args.observed_at)
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if args.output is None:
            sys.stdout.buffer.write(payload)
        else:
            with args.output.open("xb") as stream:
                stream.write(payload)
    except (OSError, LedgerInputError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
