#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Acquire swarm custody before rendering a Slack TAKE message.

This is a thin coordination wrapper around swarm_claim_reservation. It never
posts to Slack, claims an upstream bounty, contacts a sponsor, submits work, or
touches payment state. A caller may post the returned ``slack_text`` only when
``status`` is ``TAKE_READY``.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
import re
import sys
from typing import Any

try:
    from tools import swarm_claim_reservation as reservation
except ImportError:  # direct script execution from tools/
    import swarm_claim_reservation as reservation

SCHEMA = "swarm-claim-take/v1"
MAX_IDENTITY_CHARS = 256
MAX_SUMMARY_CHARS = 1000
LANES = frozenset({"build", "repair", "qa", "publish", "metadata", "claim"})
_MUTATION_LANES = frozenset({"build", "repair"})
_REPO_TEXT = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
_REPO_RE = re.compile(rf"^{_REPO_TEXT}$")
_ISSUE_RE = re.compile(
    rf"^(?:github:)?(?P<repo>{_REPO_TEXT})#(?P<number>0*[1-9][0-9]*)$",
    re.IGNORECASE,
)
_PULL_RE = re.compile(
    rf"^(?:github:)?(?P<repo>{_REPO_TEXT})!(?P<number>0*[1-9][0-9]*)$",
    re.IGNORECASE,
)
_GITHUB_URL_RE = re.compile(
    rf"^https://github[.]com/(?P<repo>{_REPO_TEXT})/"
    rf"(?P<kind>issues|pull)/(?P<number>0*[1-9][0-9]*)/?$",
    re.IGNORECASE,
)


class TakeError(ValueError):
    """Stable local input error for the reserve-before-TAKE wrapper."""


def _one_line(value: str, field: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    ):
        raise TakeError(
            f"{field} must be 1..{limit} printable single-line characters"
        )
    return value



def _canonical_parts(repository: Any, number: Any, kind: str) -> str | None:
    if (
        not isinstance(repository, str)
        or _REPO_RE.fullmatch(repository) is None
        or isinstance(number, bool)
        or not isinstance(number, int)
        or number < 1
    ):
        return None
    separator = "#" if kind == "issue" else "!"
    return f"github:{repository.casefold()}{separator}{number}"


def canonicalize_resource(value: Any) -> dict[str, Any]:
    """Normalize explicit GitHub issue/PR identities without provider inference."""
    if isinstance(value, Mapping):
        repository = value.get("repository")
        has_issue = "issue_number" in value
        has_pull = "pull_number" in value
        if has_issue == has_pull:
            raise TakeError(
                "structured resource must contain exactly one of issue_number or pull_number"
            )
        kind = "issue" if has_issue else "pull"
        number = value.get("issue_number" if has_issue else "pull_number")
        canonical = _canonical_parts(repository, number, kind)
        if canonical is None:
            raise TakeError("structured GitHub resource is invalid")
        return {"status": "NORMALIZED", "resource": canonical, "kind": kind}

    text = _one_line(value, "work_key", 512)
    match = _ISSUE_RE.fullmatch(text)
    if match is not None:
        canonical = _canonical_parts(
            match.group("repo"), int(match.group("number")), "issue"
        )
        assert canonical is not None
        return {"status": "NORMALIZED", "resource": canonical, "kind": "issue"}

    match = _PULL_RE.fullmatch(text)
    if match is not None:
        canonical = _canonical_parts(
            match.group("repo"), int(match.group("number")), "pull"
        )
        assert canonical is not None
        return {"status": "NORMALIZED", "resource": canonical, "kind": "pull"}

    match = _GITHUB_URL_RE.fullmatch(text)
    if match is not None:
        kind = "issue" if match.group("kind").casefold() == "issues" else "pull"
        canonical = _canonical_parts(
            match.group("repo"), int(match.group("number")), kind
        )
        assert canonical is not None
        return {"status": "NORMALIZED", "resource": canonical, "kind": kind}

    return {"status": "UNNORMALIZED_KEY", "resource": text, "kind": None}


def _reservation_identity(
    work_key: Any,
    *,
    lane: str,
    mutation_resource: Any | None,
) -> dict[str, Any]:
    lane = _one_line(lane, "lane", 32).casefold()
    if lane not in LANES:
        raise TakeError(
            "lane must be one of build, repair, qa, publish, metadata, claim"
        )

    source = canonicalize_resource(work_key)
    mutation = None
    if mutation_resource is not None:
        if lane not in _MUTATION_LANES:
            raise TakeError(
                "mutation_resource is permitted only for build or repair lanes"
            )
        if source["status"] != "NORMALIZED":
            raise TakeError(
                "mutation_resource requires a normalized primary GitHub resource"
            )
        mutation = canonicalize_resource(mutation_resource)
        if mutation["status"] != "NORMALIZED":
            raise TakeError("mutation_resource must be an explicit GitHub issue or PR")
        reservation_key = f"swarm:mutation:{mutation['resource']}"
    elif source["status"] == "NORMALIZED":
        reservation_key = f"swarm:{lane}:{source['resource']}"
    else:
        reservation_key = source["resource"]

    return {
        "status": source["status"],
        "lane": lane,
        "resource": source["resource"],
        "kind": source["kind"],
        "mutation_resource": mutation["resource"] if mutation else None,
        "reservation_key": reservation_key,
    }

def _take_text(
    result: dict[str, Any],
    *,
    identity: str,
    summary: str,
    canonicalization: dict[str, Any],
) -> str:
    lines = [
        f"TAKE · {result['event_id']} · {identity}",
        (
            f"work_key={result['work_key']} "
            f"custody_owner={result['owner']} "
            f"lease_expires_at={result['lease_expires_at']}"
        ),
        f"custody_branch={result['branch']}",
    ]
    if canonicalization["status"] == "UNNORMALIZED_KEY":
        lines.append(
            "canonicalization=UNNORMALIZED_KEY literal_work_key_preserved=true"
        )
    else:
        canonical_line = (
            f"canonicalization=NORMALIZED lane={canonicalization['lane']} "
            f"resource={canonicalization['resource']}"
        )
        if canonicalization["mutation_resource"] is not None:
            canonical_line += (
                f" mutation_resource={canonicalization['mutation_resource']}"
            )
        lines.append(canonical_line)
    commit_sha = result.get("commit_sha")
    if isinstance(commit_sha, str):
        lines.append(f"custody_commit={commit_sha}")
    lines.append(summary)
    return "\n".join(lines)


def acquire_take(
    github: reservation.GitHub,
    *,
    work_key: str,
    owner: str,
    event_id: str,
    identity: str,
    summary: str,
    lease_seconds: int,
    artifact: str | None,
    base_branch: str,
    lane: str = "build",
    mutation_resource: Any | None = None,
) -> tuple[dict[str, Any], int]:
    """Canonicalize, reserve work, and render TAKE text only for the winner."""
    identity = _one_line(identity, "identity", MAX_IDENTITY_CHARS)
    summary = _one_line(summary, "summary", MAX_SUMMARY_CHARS)
    canonicalization = _reservation_identity(
        work_key,
        lane=lane,
        mutation_resource=mutation_resource,
    )

    claimed, reserve_code = reservation.reserve(
        github,
        work_key=canonicalization["reservation_key"],
        owner=owner,
        event_id=event_id,
        lease_seconds=lease_seconds,
        artifact=artifact,
        base_branch=base_branch,
    )

    disposition = claimed.get("disposition")
    if reserve_code == 0 and disposition in {"ACQUIRED", "OWNED"}:
        return (
            {
                "schema": SCHEMA,
                "status": "TAKE_READY",
                "post_take": True,
                "canonicalization": canonicalization,
                "reservation": claimed,
                "slack_text": _take_text(
                    claimed,
                    identity=identity,
                    summary=summary,
                    canonicalization=canonicalization,
                ),
            },
            0,
        )

    if reserve_code == 3 and disposition == "BUSY":
        return (
            {
                "schema": SCHEMA,
                "status": "COLLISION",
                "post_take": False,
                "canonicalization": canonicalization,
                "reservation": claimed,
            },
            3,
        )

    return (
        {
            "schema": SCHEMA,
            "status": "BLOCKED",
            "post_take": False,
            "canonicalization": canonicalization,
            "reservation": claimed,
        },
        reserve_code if reserve_code else 2,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically reserve one swarm work key, then render a Slack TAKE "
            "message only for the winning owner."
        )
    )
    parser.add_argument("work_key")
    parser.add_argument("--lane", required=True, choices=sorted(LANES))
    parser.add_argument(
        "--mutation-resource",
        help=(
            "Explicit GitHub issue/PR mutation target. Valid only for build/repair; "
            "both lanes then share swarm:mutation:<resource>."
        ),
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=reservation.DEFAULT_LEASE_SECONDS,
    )
    parser.add_argument("--artifact")
    parser.add_argument(
        "--repo",
        default=os.environ.get(
            reservation.REPO_ENV,
            reservation.DEFAULT_REPO,
        ),
    )
    parser.add_argument(
        "--base",
        default=os.environ.get(
            reservation.BASE_ENV,
            reservation.DEFAULT_BASE,
        ),
    )
    return parser


def _emit(value: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
        file=stream,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        github = reservation.GitHub(
            args.repo,
            os.environ.get(reservation.TOKEN_ENV, ""),
        )
        result, code = acquire_take(
            github,
            work_key=args.work_key,
            owner=args.owner,
            event_id=args.event_id,
            identity=args.identity,
            summary=args.summary,
            lease_seconds=args.lease_seconds,
            artifact=args.artifact,
            base_branch=args.base,
            lane=args.lane,
            mutation_resource=args.mutation_resource,
        )
        _emit(result)
        return code
    except TakeError as exc:
        _emit(
            {
                "schema": SCHEMA,
                "status": "ERROR",
                "post_take": False,
                "error": {
                    "code": "invalid_take_input",
                    "message": str(exc),
                },
            },
            stream=sys.stderr,
        )
        return 2
    except reservation.ReservationError as exc:
        error: dict[str, Any] = {
            "code": exc.code,
            "message": exc.message,
        }
        if exc.detail:
            error["detail"] = exc.detail
        _emit(
            {
                "schema": SCHEMA,
                "status": "ERROR",
                "post_take": False,
                "error": error,
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
