#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fail closed when a fresh bounty snapshot already has a live upstream carrier.

This tool is deliberately offline. Provider-facing workers fetch the canonical
issue and relevant pull requests, save one JSON snapshot, then run this guard
immediately before creating a new pull request.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

MAX_BYTES = 1024 * 1024
DEFAULT_MAX_AGE_SECONDS = 300
FUTURE_SKEW_SECONDS = 30


class GuardError(ValueError):
    """Invalid or incomplete pre-publish snapshot."""


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise GuardError("snapshot exceeds the 1 MiB limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise GuardError("snapshot must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise GuardError("snapshot root must be an object")
    return payload


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GuardError("captured_at is required")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GuardError("captured_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise GuardError("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _issue_key(owner: str, repo: str, number: int) -> str:
    return f"github:{owner.lower()}/{repo.lower()}#{number}"


def _parse_issue_key(value: str) -> tuple[str, str, int]:
    match = re.fullmatch(
        r"(?:github:)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([1-9][0-9]*)",
        value.strip(),
        re.I,
    )
    if not match:
        raise GuardError(f"invalid issue key: {value!r}")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def _pr_number(pr: dict[str, Any]) -> int:
    value = pr.get("number")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise GuardError("each pull request needs a positive integer number")
    return value


def _head_sha(pr: dict[str, Any]) -> str | None:
    direct = pr.get("head_sha")
    if isinstance(direct, str) and direct.strip():
        return direct.strip().lower()
    head = pr.get("head")
    if isinstance(head, dict):
        nested = head.get("sha")
        if isinstance(nested, str) and nested.strip():
            return nested.strip().lower()
    return None


def _provider_duplicate_pr_head(create_error: Any) -> str | None:
    """Return the conflicting head from GitHub's authoritative duplicate-PR 422."""
    if create_error is None:
        return None
    if not isinstance(create_error, dict):
        raise GuardError("snapshot.create_error must be an object when provided")

    payloads = [create_error]
    nested = create_error.get("data")
    if nested is not None:
        if not isinstance(nested, dict):
            raise GuardError("snapshot.create_error.data must be an object")
        payloads.append(nested)

    status: int | None = None
    messages: list[str] = []
    for payload in payloads:
        raw_status = payload.get("status")
        if isinstance(raw_status, int) and not isinstance(raw_status, bool):
            status = raw_status
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message.strip())
        errors = payload.get("errors")
        if errors is None:
            continue
        if not isinstance(errors, list):
            raise GuardError("snapshot.create_error.errors must be a list")
        for error in errors:
            if not isinstance(error, dict):
                raise GuardError("snapshot.create_error.errors entries must be objects")
            error_message = error.get("message")
            if isinstance(error_message, str) and error_message.strip():
                messages.append(error_message.strip())

    if status != 422:
        raise GuardError("snapshot.create_error must be a GitHub 422 response")

    duplicate = re.compile(
        r"^A pull request already exists for\s+(.+?)(?:\.\s*)?$",
        re.I,
    )
    for message in messages:
        match = duplicate.match(message)
        if match:
            head = match.group(1).strip()
            if not head or any(char.isspace() for char in head):
                raise GuardError("duplicate-PR response contains an invalid head")
            return head

    raise GuardError(
        "GitHub 422 response is not a recognized duplicate-pull-request collision"
    )


def _content_fingerprint(files: Any, *, field: str) -> str | None:
    """Hash an exact changed-path/postimage-blob set when one is supplied."""
    if files is None:
        return None
    if not isinstance(files, list) or not files:
        raise GuardError(f"{field} must be a non-empty list when provided")

    normalized: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for raw_file in files:
        if not isinstance(raw_file, dict):
            raise GuardError(f"{field} entries must be objects")
        path = raw_file.get("filename")
        if not isinstance(path, str) or not path.strip():
            path = raw_file.get("path")
        sha = raw_file.get("sha")
        if not isinstance(sha, str) or not sha.strip():
            sha = raw_file.get("blob_sha")
        if not isinstance(path, str) or not path.strip():
            raise GuardError(f"{field} entries need filename or path")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha.strip()):
            raise GuardError(f"{field} entries need a 40-64 character hex blob SHA")
        clean_path = path.strip()
        if clean_path in seen_paths:
            raise GuardError(f"{field} contains duplicate path {clean_path!r}")
        seen_paths.add(clean_path)
        normalized.append((clean_path, sha.strip().lower()))

    digest = hashlib.sha256()
    for path, sha in sorted(normalized):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _references_issue(
    pr: dict[str, Any],
    *,
    owner: str,
    repo: str,
    number: int,
    submitted: set[int],
) -> bool:
    if _pr_number(pr) in submitted:
        return True
    title = pr.get("title") if isinstance(pr.get("title"), str) else ""
    body = pr.get("body") if isinstance(pr.get("body"), str) else ""
    text = f"{title}\n{body}"
    issue_url = re.compile(
        rf"https?://github\.com/{re.escape(owner)}/{re.escape(repo)}/issues/{number}\b",
        re.I,
    )
    if issue_url.search(text):
        return True
    keyword_ref = re.compile(
        rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references?|for)\s*:?\s*"
        rf"(?:{re.escape(owner)}/{re.escape(repo)})?#{number}\b",
        re.I,
    )
    return bool(keyword_ref.search(text))


def evaluate(
    snapshot: dict[str, Any],
    *,
    expected_issue_key: str,
    self_head_sha: str,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one deterministic pre-publish decision from a canonical snapshot."""
    if max_age_seconds < 1:
        raise GuardError("max_age_seconds must be positive")
    captured_at = _parse_time(snapshot.get("captured_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - captured_at).total_seconds()

    owner, repo, number = _parse_issue_key(expected_issue_key)
    expected_key = _issue_key(owner, repo, number)

    issue = snapshot.get("issue")
    if not isinstance(issue, dict):
        raise GuardError("snapshot.issue must be an object")
    issue_repo = issue.get("repository")
    issue_number = issue.get("number")
    issue_state = issue.get("state")
    if not isinstance(issue_repo, str) or "/" not in issue_repo:
        raise GuardError("snapshot.issue.repository must be OWNER/REPO")
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number <= 0:
        raise GuardError("snapshot.issue.number must be a positive integer")
    actual_owner, actual_repo = issue_repo.split("/", 1)
    actual_key = _issue_key(actual_owner, actual_repo, issue_number)
    if actual_key != expected_key:
        raise GuardError(f"snapshot issue {actual_key} does not match {expected_key}")
    if not isinstance(issue_state, str):
        raise GuardError("snapshot.issue.state is required")


    if age > max_age_seconds or age < -FUTURE_SKEW_SECONDS:
        status = "STALE_SNAPSHOT"
        reason = f"snapshot age {age:.1f}s is outside the allowed freshness window"
        return _report(expected_key, status, reason, captured_at, [], [], age)

    if issue_state.lower() != "open":
        return _report(
            expected_key,
            "ISSUE_NOT_OPEN",
            f"canonical issue state is {issue_state!r}",
            captured_at,
            [],
            [],
            age,
        )

    provider_collision_head = _provider_duplicate_pr_head(snapshot.get("create_error"))
    if provider_collision_head is not None:
        return _report(
            expected_key,
            "PROVIDER_COLLISION",
            (
                "GitHub authoritatively rejected pull-request creation because "
                f"a carrier already exists for {provider_collision_head}"
            ),
            captured_at,
            [],
            [],
            age,
            provider_collision_head=provider_collision_head,
        )

    pulls = snapshot.get("pulls")
    if not isinstance(pulls, list):
        raise GuardError("snapshot.pulls must be a list")
    submitted_raw = snapshot.get("submitted_pr_numbers", [])
    if not isinstance(submitted_raw, list):
        raise GuardError("submitted_pr_numbers must be a list")
    submitted: set[int] = set()
    for value in submitted_raw:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise GuardError("submitted_pr_numbers must contain positive integers")
        submitted.add(value)

    own_sha = self_head_sha.strip().lower()
    if not own_sha:
        raise GuardError("self_head_sha must not be empty")
    candidate_fingerprint = _content_fingerprint(
        snapshot.get("candidate_files"),
        field="snapshot.candidate_files",
    )

    self_carriers: list[dict[str, Any]] = []
    competitors: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for raw_pr in pulls:
        if not isinstance(raw_pr, dict):
            raise GuardError("snapshot.pulls entries must be objects")
        pr_num = _pr_number(raw_pr)
        if pr_num in seen_numbers:
            raise GuardError(f"duplicate pull request #{pr_num} in snapshot")
        seen_numbers.add(pr_num)
        references_issue = _references_issue(
            raw_pr,
            owner=owner,
            repo=repo,
            number=number,
            submitted=submitted,
        )
        pr_fingerprint = _content_fingerprint(
            raw_pr.get("files"),
            field=f"snapshot.pulls[#{pr_num}].files",
        )
        same_content = (
            candidate_fingerprint is not None
            and pr_fingerprint == candidate_fingerprint
        )
        if not references_issue and not same_content:
            continue
        state = raw_pr.get("state")
        merged = raw_pr.get("merged") is True or raw_pr.get("merged_at") not in (None, "")
        if not merged and (not isinstance(state, str) or state.lower() not in {"open", "closed"}):
            raise GuardError(f"related pull request #{pr_num} needs state open or closed")
        live = merged or (isinstance(state, str) and state.lower() == "open")
        if not live:
            continue
        match_reasons = []
        if references_issue:
            match_reasons.append("issue_reference")
        if same_content:
            match_reasons.append("content_fingerprint")
        record = {
            "number": pr_num,
            "state": state,
            "merged": merged,
            "head_sha": _head_sha(raw_pr),
            "url": raw_pr.get("html_url") or raw_pr.get("url"),
            "title": raw_pr.get("title"),
            "content_fingerprint": pr_fingerprint,
            "match_reason": "+".join(match_reasons),
        }
        if record["head_sha"] == own_sha:
            self_carriers.append(record)
        else:
            competitors.append(record)

    if competitors:
        content_collision = any(
            "content_fingerprint" in record["match_reason"]
            for record in competitors
        )
        return _report(
            expected_key,
            "CONTENT_COLLISION" if content_collision else "COLLISION",
            (
                "another live or merged pull request has the same changed-path/blob fingerprint"
                if content_collision
                else "another live or merged pull request already carries this issue"
            ),
            captured_at,
            self_carriers,
            competitors,
            age,
            content_fingerprint=candidate_fingerprint,
        )
    if self_carriers:
        return _report(
            expected_key,
            "SELF_CARRIER_EXISTS",
            "an exact-head live carrier already exists; do not create another pull request",
            captured_at,
            self_carriers,
            [],
            age,
            content_fingerprint=candidate_fingerprint,
        )
    return _report(
        expected_key,
        "PUBLISH_ALLOWED",
        "fresh snapshot contains no live related carrier",
        captured_at,
        [],
        [],
        age,
        content_fingerprint=candidate_fingerprint,
    )


def _report(
    issue_key: str,
    status: str,
    reason: str,
    captured_at: datetime,
    self_carriers: list[dict[str, Any]],
    competitors: list[dict[str, Any]],
    age_seconds: float,
    content_fingerprint: str | None = None,
    provider_collision_head: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "upstream-pr-collision-guard/v1",
        "status": status,
        "publish_allowed": status == "PUBLISH_ALLOWED",
        "issue_key": issue_key,
        "reason": reason,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "snapshot_age_seconds": round(age_seconds, 3),
        "self_carriers": self_carriers,
        "competitors": competitors,
        "content_fingerprint": content_fingerprint,
        "provider_collision_head": provider_collision_head,
        "retry_create": status == "PUBLISH_ALLOWED",
        "instruction": (
            "create one pull request"
            if status == "PUBLISH_ALLOWED"
            else (
                "do not retry pull-request creation; resolve the canonical carrier "
                "by exact head/branch read"
                if status == "PROVIDER_COLLISION"
                else "do not create a new pull request; refresh/reconcile first"
            )
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--issue", required=True, help="OWNER/REPO#N")
    parser.add_argument("--self-head-sha", required=True)
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
        help=f"freshness limit (default {DEFAULT_MAX_AGE_SECONDS})",
    )
    args = parser.parse_args(argv)
    try:
        report = evaluate(
            _read_json(args.snapshot),
            expected_issue_key=args.issue,
            self_head_sha=args.self_head_sha,
            max_age_seconds=args.max_age_seconds,
        )
    except (GuardError, OSError) as exc:
        print(f"upstream-pr-collision-guard: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if report["publish_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
