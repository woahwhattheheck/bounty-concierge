# SPDX-License-Identifier: MIT
"""Inspect shared GitHub cooldown state without making a provider request."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
from time import time
from typing import Any

from concierge.github_cooldown import CooldownStateError, GitHubCooldown

_SCHEMA = "github-cooldown-status/v1"
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _deadline_status(deadline: float | None, now_epoch: float) -> dict[str, Any]:
    if deadline is None:
        return {"active": False, "until_epoch": None, "retry_after_seconds": 0}
    remaining = max(0, math.ceil(deadline - now_epoch))
    return {
        "active": deadline > now_epoch,
        "until_epoch": deadline,
        "retry_after_seconds": remaining,
    }


def status_snapshot(
    path: str | Path,
    token: str | None,
    *,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Return local cooldown/quota state for one credential scope."""
    now = time() if now_epoch is None else float(now_epoch)
    if not math.isfinite(now) or now < 0:
        raise ValueError("now_epoch must be a finite non-negative number")

    store_path = Path(path)
    if not store_path.exists():
        return {
            "schema": _SCHEMA,
            "state": "ABSENT",
            "blocked": False,
            "effective_until_epoch": None,
            "retry_after_seconds": 0,
            "provider_cooldown": _deadline_status(None, now),
            "quota_reservation": _deadline_status(None, now),
            "note": "Local shared state only; no GitHub request was made.",
        }

    cooldown, quota = GitHubCooldown(store_path, token).deadlines(read_only=True)
    provider_status = _deadline_status(cooldown, now)
    quota_status = _deadline_status(quota, now)
    active_deadlines = [
        value
        for value, status in ((cooldown, provider_status), (quota, quota_status))
        if value is not None and status["active"]
    ]
    effective = max(active_deadlines) if active_deadlines else None
    return {
        "schema": _SCHEMA,
        "state": "READY",
        "blocked": effective is not None,
        "effective_until_epoch": effective,
        "retry_after_seconds": (
            max(0, math.ceil(effective - now)) if effective is not None else 0
        ),
        "provider_cooldown": provider_status,
        "quota_reservation": quota_status,
        "note": "Local shared state only; no GitHub request was made.",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect shared GitHub cooldown/quota state without provider I/O."
    )
    parser.add_argument(
        "--cooldown-file",
        default=os.environ.get("CONCIERGE_BOUNTY_COOLDOWN"),
        help=(
            "Shared SQLite file. Defaults to CONCIERGE_BOUNTY_COOLDOWN. "
            "The path is never included in output."
        ),
    )
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help=(
            "Environment variable containing the credential used to select its "
            "existing cooldown scope. The variable name is not included in output."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.cooldown_file:
        parser.error("--cooldown-file or CONCIERGE_BOUNTY_COOLDOWN is required")
    if not _ENV_NAME_RE.fullmatch(args.token_env):
        parser.error("--token-env must be an environment-variable name")

    token = os.environ.get(args.token_env) or None
    try:
        snapshot = status_snapshot(args.cooldown_file, token)
    except CooldownStateError:
        print("shared GitHub cooldown state unavailable", file=os.sys.stderr)
        return 2
    except (OSError, ValueError):
        print("invalid shared GitHub cooldown status input", file=os.sys.stderr)
        return 2

    if args.json:
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0

    print(
        f"{snapshot['state']} blocked={str(snapshot['blocked']).lower()} "
        f"retry_after_seconds={snapshot['retry_after_seconds']}"
    )
    provider = snapshot["provider_cooldown"]
    quota = snapshot["quota_reservation"]
    print(
        "provider_cooldown "
        f"active={str(provider['active']).lower()} "
        f"retry_after_seconds={provider['retry_after_seconds']} "
        f"until_epoch={provider['until_epoch']}"
    )
    print(
        "quota_reservation "
        f"active={str(quota['active']).lower()} "
        f"retry_after_seconds={quota['retry_after_seconds']} "
        f"until_epoch={quota['until_epoch']}"
    )
    print(snapshot["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
