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
import json
import os
import sys
from typing import Any

import swarm_claim_reservation as reservation

SCHEMA = "swarm-claim-take/v1"
MAX_IDENTITY_CHARS = 256
MAX_SUMMARY_CHARS = 1000


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


def _take_text(
    result: dict[str, Any],
    *,
    identity: str,
    summary: str,
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
) -> tuple[dict[str, Any], int]:
    """Reserve work and render TAKE text only for the winning owner."""
    identity = _one_line(identity, "identity", MAX_IDENTITY_CHARS)
    summary = _one_line(summary, "summary", MAX_SUMMARY_CHARS)

    claimed, reserve_code = reservation.reserve(
        github,
        work_key=work_key,
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
                "reservation": claimed,
                "slack_text": _take_text(
                    claimed,
                    identity=identity,
                    summary=summary,
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
                "reservation": claimed,
            },
            3,
        )

    return (
        {
            "schema": SCHEMA,
            "status": "BLOCKED",
            "post_take": False,
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
