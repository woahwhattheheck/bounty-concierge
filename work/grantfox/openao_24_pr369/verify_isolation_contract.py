#!/usr/bin/env python3
"""Host oracle for OpenAO #24 / PR #369 isolation and quota boundaries.

This intentionally does not pretend to execute the TypeScript service. It
models the exact acceptance decisions visible in the reviewed PR and compares
them with the issue-level invariants. A differentiating vector is useful only
when the candidate model accepts something the required contract rejects.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict

USER_MAP_START = 100_000
USER_MAP_END = 999_999


def candidate_storage_accepts(max_storage_bytes: int, existing_active_bytes: int, incoming_map_bytes: int) -> bool:
    """PR #369 compares only the incoming/current map size with the quota."""
    del existing_active_bytes
    return incoming_map_bytes <= max_storage_bytes


def required_storage_accepts(max_storage_bytes: int, existing_active_bytes: int, incoming_map_bytes: int) -> bool:
    """Issue contract: account-wide active storage must remain inside quota."""
    return existing_active_bytes + incoming_map_bytes <= max_storage_bytes


def candidate_npc_exp_accepts(exp: int) -> bool:
    """PR #369's economy validator has no npc.exp rejection."""
    del exp
    return True


def required_npc_exp_accepts(exp: int) -> bool:
    """V1 issue policy: user-map NPCs must not grant positive XP."""
    return exp <= 0


def candidate_exit_accepts(target: int) -> bool:
    """Exact range shape in PR #369 world-isolation predicate."""
    return not (1 <= target < USER_MAP_START)


def required_exit_accepts(target: int) -> bool:
    """Structural isolation: allowed targets stay in the reserved user range."""
    return USER_MAP_START <= target <= USER_MAP_END


@dataclass(frozen=True)
class Finding:
    id: str
    candidate_accepts: bool
    required_accepts: bool

    @property
    def differentiates(self) -> bool:
        return self.candidate_accepts and not self.required_accepts


def analyze() -> dict[str, object]:
    findings = [
        Finding(
            "account-storage-quota-per-map-bypass",
            candidate_storage_accepts(5 * 1024 * 1024, 3 * 1024 * 1024, 3 * 1024 * 1024),
            required_storage_accepts(5 * 1024 * 1024, 3 * 1024 * 1024, 3 * 1024 * 1024),
        ),
        Finding(
            "npc-exp-economy-bypass",
            candidate_npc_exp_accepts(1),
            required_npc_exp_accepts(1),
        ),
        Finding(
            "exit-above-user-range-bypass",
            candidate_exit_accepts(1_000_000),
            required_exit_accepts(1_000_000),
        ),
    ]
    return {
        "schema": "openao-pr369-isolation-oracle-v1",
        "all_hostiles_differentiate": all(item.differentiates for item in findings),
        "findings": [
            {**asdict(item), "differentiates": item.differentiates}
            for item in findings
        ],
        "exit_boundary": [
            {
                "target": target,
                "candidate_accepts": candidate_exit_accepts(target),
                "required_accepts": required_exit_accepts(target),
            }
            for target in (99_999, 100_000, 999_999, 1_000_000)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = analyze()
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if report["all_hostiles_differentiate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
