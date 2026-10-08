# SPDX-License-Identifier: MIT
"""Read one GitHub rail/account availability snapshot without provider I/O."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
from time import time
from typing import Any

from concierge.github_cooldown import CooldownStateError, GitHubCooldown
from concierge.github_breaker_ledger import BreakerStateError, GitHubBreakerLedger

_SCHEMA = "github-rail-availability/v1"


def _label(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 128
        or "\0" in value
    ):
        raise ValueError(f"{name} must be a non-empty label up to 128 bytes")
    return value


def _deadline_status(deadline: float | None, now_epoch: float) -> dict[str, Any]:
    if deadline is None:
        return {"active": False, "until_epoch": None, "retry_after_seconds": 0}
    remaining = max(0, math.ceil(deadline - now_epoch))
    return {
        "active": deadline > now_epoch,
        "until_epoch": deadline,
        "retry_after_seconds": remaining,
    }


def availability_snapshot(
    path: str | Path,
    token: str | None,
    *,
    rail: str,
    actor: str,
    cooldown_scope: str | None = None,
    now_epoch: float | None = None,
    breaker_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return local rail/account readiness without claiming recovery or calling GitHub."""
    rail = _label(rail, "rail")
    actor = _label(actor, "actor")
    now = time() if now_epoch is None else float(now_epoch)
    if not math.isfinite(now) or now < 0:
        raise ValueError("now_epoch must be a finite non-negative number")

    store_path = Path(path)
    empty = {
        "schema": _SCHEMA,
        "rail": rail,
        "actor": actor,
        "availability": "AVAILABLE",
        "blocked": False,
        "retry_after_seconds": 0,
        "provider_cooldown": _deadline_status(None, now),
        "quota_reservation": _deadline_status(None, now),
        "recovery_lease": _deadline_status(None, now),
        "note": "Local shared state only; no GitHub request or recovery claim was made.",
    }
    if breaker_path is not None:
        empty["operation_breaker"] = GitHubBreakerLedger(
            breaker_path, provider_route=rail, credential=token,
        ).status_snapshot(now_epoch=now)
    if not store_path.exists():
        return {**empty, "state": "ABSENT"}

    cooldown = GitHubCooldown(store_path, token, cooldown_scope=cooldown_scope)
    connection = None
    try:
        connection = sqlite3.connect(
            store_path.absolute().as_uri() + "?mode=ro",
            timeout=1.0,
            uri=True,
        )
        rows = connection.execute(
            "SELECT 'cooldown', until_epoch "
            "FROM github_cooldown_v1 WHERE scope = ? "
            "UNION ALL SELECT 'quota reservation', until_epoch "
            "FROM github_quota_reserve_v1 WHERE scope = ? "
            "UNION ALL SELECT 'recovery lease', lease_until_epoch "
            "FROM github_recovery_lease_v1 WHERE scope = ?",
            (
                cooldown.cooldown_scope,
                cooldown.scope,
                cooldown.scope,
                cooldown.cooldown_scope,
            ),
        ).fetchall()
    except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
        raise CooldownStateError("shared GitHub cooldown state unavailable") from exc
    finally:
        if connection is not None:
            connection.close()

    values: dict[str, float] = {}
    for label, value in rows:
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise CooldownStateError(f"shared GitHub {label} deadline invalid")
        values[label] = float(value)
    provider = values.get("cooldown")
    quota = values.get("quota reservation")
    recovery_lease = values.get("recovery lease")
    provider_status = _deadline_status(provider, now)
    quota_status = _deadline_status(quota, now)
    lease_status = _deadline_status(recovery_lease, now)

    active_deadlines = [
        value
        for value, status in (
            (provider, provider_status),
            (quota, quota_status),
        )
        if value is not None and status["active"]
    ]
    if active_deadlines:
        effective = max(active_deadlines)
        availability = "HOT"
        blocked = True
        retry_after_seconds = max(0, math.ceil(effective - now))
    elif lease_status["active"]:
        availability = "RECOVERY_PROBE_IN_FLIGHT"
        blocked = True
        retry_after_seconds = lease_status["retry_after_seconds"]
    elif (
        provider is not None
        and provider <= now
    ):
        availability = "RECOVERY_READY"
        blocked = False
        retry_after_seconds = 0
    else:
        availability = "AVAILABLE"
        blocked = False
        retry_after_seconds = 0

    return {
        **empty,
        "state": "READY",
        "availability": availability,
        "blocked": blocked,
        "retry_after_seconds": retry_after_seconds,
        "provider_cooldown": provider_status,
        "quota_reservation": quota_status,
        "recovery_lease": lease_status,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect one GitHub rail/account cooldown state without provider I/O."
    )
    parser.add_argument(
        "--cooldown-file",
        default=os.environ.get("CONCIERGE_BOUNTY_COOLDOWN"),
        help="Shared SQLite file. Path is never emitted.",
    )
    parser.add_argument("--rail", required=True, help="Public rail label, e.g. managed-app.")
    parser.add_argument("--actor", required=True, help="Public account/actor label.")
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing the credential used to select stored state.",
    )
    parser.add_argument("--cooldown-scope", default=None)
    parser.add_argument(
        "--breaker-file", default=None,
        help="Optional existing breaker SQLite file; --rail selects its provider route.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.cooldown_file:
        parser.error("--cooldown-file or CONCIERGE_BOUNTY_COOLDOWN is required")
    token = os.environ.get(args.token_env) or None
    try:
        snapshot = availability_snapshot(
            args.cooldown_file,
            token,
            rail=args.rail,
            actor=args.actor,
            cooldown_scope=args.cooldown_scope,
            breaker_path=args.breaker_file,
        )
    except (CooldownStateError, BreakerStateError):
        print("shared GitHub rail state unavailable", file=os.sys.stderr)
        return 2
    except (OSError, ValueError):
        print("invalid GitHub rail availability input", file=os.sys.stderr)
        return 2
    if args.json:
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False))
    else:
        print(
            f"{snapshot['rail']} actor={snapshot['actor']} "
            f"availability={snapshot['availability']} "
            f"blocked={str(snapshot['blocked']).lower()} "
            f"retry_after_seconds={snapshot['retry_after_seconds']}"
        )
        if "operation_breaker" in snapshot:
            breaker = snapshot["operation_breaker"]
            print(f"operation_breaker state={breaker['state']} request_budget=UNKNOWN")
            for operation, status in sorted(breaker["operations"].items()):
                print(
                    f"{operation} state={status['state']} last_error={status['last_error']} "
                    f"observed_epoch={status['observed_epoch']} "
                    f"blocked={str(status['blocked']).lower()} "
                    f"retry_after_seconds={status['retry_after_seconds']}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
