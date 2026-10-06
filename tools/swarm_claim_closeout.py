#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Serialize provider claim closeout through the existing atomic swarm reservation.

This tool never writes to the target issue. It only reserves one canonical GitHub
issue work key and, for the winning owner, emits the exact provider claim text.
The caller must fresh-read provider claim state after reservation and immediately
before performing the single external write.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import swarm_claim_reservation as reservation

SCHEMA = "swarm-claim-closeout/v1"
_ISSUE_RE = re.compile(
    r"^(?:github:)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#0*([0-9]+)$",
    re.IGNORECASE,
)


class CloseoutError(ValueError):
    """Stable local input error for commercial claim closeout."""


def canonical_issue(value: str) -> tuple[str, int]:
    if not isinstance(value, str):
        raise CloseoutError("issue must be a string")
    raw = value.strip()
    if (
        not raw
        or raw != value
        or len(raw) > 512
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw)
    ):
        raise CloseoutError(
            "issue must be a trimmed printable GitHub owner/repo#number key"
        )
    match = _ISSUE_RE.fullmatch(raw)
    if match is None:
        raise CloseoutError(
            "issue must be owner/repo#number or github:owner/repo#number"
        )
    repo_owner, repo_name, number_raw = match.groups()
    number = int(number_raw)
    if number < 1:
        raise CloseoutError("issue number must be at least 1")
    return f"github:{repo_owner.lower()}/{repo_name.lower()}#{number}", number


def acquire_closeout(
    github: reservation.GitHub,
    *,
    issue: str,
    owner: str,
    event_id: str,
    lease_seconds: int,
    base_branch: str,
) -> tuple[dict[str, Any], int]:
    resource, issue_number = canonical_issue(issue)
    # Share the claim lane with swarm_claim_take --lane claim. The bare
    # GitHub issue identity remains available separately in the receipt.
    work_key = f"swarm:claim:{resource}"
    claimed, reserve_code = reservation.reserve(
        github,
        work_key=work_key,
        owner=owner,
        event_id=event_id,
        lease_seconds=lease_seconds,
        artifact=None,
        base_branch=base_branch,
    )

    disposition = claimed.get("disposition")
    # An owner label identifies a seat, not an individual operation. Never
    # let another operation on the same seat reuse a live closeout lease.
    if reserve_code == 0 and disposition == "OWNED" and (
        claimed.get("owner") != owner or claimed.get("event_id") != event_id
    ):
        return (
            {
                "schema": SCHEMA,
                "status": "COLLISION",
                "post_claim": False,
                "work_key": work_key,
                "resource": resource,
                "reservation": claimed,
            },
            3,
        )

    if reserve_code == 0 and disposition in {"ACQUIRED", "OWNED"}:
        return (
            {
                "schema": SCHEMA,
                "status": "CLAIM_READY",
                "post_claim": True,
                "claim_text": f"/claim #{issue_number}",
                "work_key": work_key,
                "resource": resource,
                "fresh_provider_fence_required": True,
                "reservation": claimed,
            },
            0,
        )

    if reserve_code == 3 and disposition == "BUSY":
        return (
            {
                "schema": SCHEMA,
                "status": "COLLISION",
                "post_claim": False,
                "work_key": work_key,
                "resource": resource,
                "reservation": claimed,
            },
            3,
        )

    return (
        {
            "schema": SCHEMA,
            "status": "BLOCKED",
            "post_claim": False,
            "work_key": work_key,
            "resource": resource,
            "reservation": claimed,
        },
        reserve_code if reserve_code else 2,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically reserve a canonical GitHub issue before emitting one "
            "provider /claim command."
        )
    )
    parser.add_argument("issue", help="owner/repo#number")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=reservation.DEFAULT_LEASE_SECONDS,
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get(
            reservation.REPO_ENV,
            reservation.DEFAULT_REPO,
        ),
        help="custody repository, not the target bounty repository",
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
        result, code = acquire_closeout(
            github,
            issue=args.issue,
            owner=args.owner,
            event_id=args.event_id,
            lease_seconds=args.lease_seconds,
            base_branch=args.base,
        )
        _emit(result)
        return code
    except CloseoutError as exc:
        _emit(
            {
                "schema": SCHEMA,
                "status": "ERROR",
                "post_claim": False,
                "error": {
                    "code": "invalid_closeout_input",
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
                "post_claim": False,
                "error": error,
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
