#!/usr/bin/env python3
"""Fail-closed source/device gate for the microG/GmsCore #2994 RCS bounty.

This verifier evaluates a pinned evidence snapshot only.  It intentionally does
not perform network access, mutate upstream, infer payout ownership, or treat a
mergeable PR as proof of physical RCS functionality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = ROOT / "acceptance.json"


class EvidenceError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def assess(data: dict[str, Any]) -> dict[str, Any]:
    issue = data["issue"]
    upstream = data["upstream"]
    candidate = data["integration_candidate"]
    followups = data["required_source_followups"]
    device_gates = data["device_gates"]
    routing = data["routing"]

    _require(issue["state"] == "open", "canonical issue is not open")
    _require(issue["assignee"] is None, "canonical issue has an assignee")
    _require(issue["bounty_usd"] >= 50, "bounty is below the swarm floor")
    _require(
        sum(item["amount_usd"] for item in issue["bounty_evidence"]) == issue["bounty_usd"],
        "bounty contribution receipts do not reconcile to the stated total",
    )
    _require(
        issue["bounty_evidence"][-1]["total_usd"] == issue["bounty_usd"],
        "last BountyHub total does not equal the stated bounty",
    )
    _require(
        upstream["maintainer_policy"]
        == "review_and_test_existing_prs_do_not_create_another_rcs_implementation",
        "maintainer routing policy is not pinned",
    )
    _require(
        routing["new_implementation_pr"] == "forbidden_by_maintainer_direction",
        "snapshot would permit a duplicate implementation PR",
    )
    _require(routing["payout_claim"] is False, "snapshot must not claim payout ownership")
    _require(
        candidate["base_sha"] == upstream["master_sha"],
        "integration candidate is not pinned to the recorded upstream master",
    )

    source_blockers: list[str] = []
    for followup in followups:
        if followup["state"] != "merged":
            source_blockers.append(
                f'{followup["repo"]}#{followup["pr"]}: {followup["reason"]}'
            )

    hardware_blockers = [
        gate["id"]
        for gate in device_gates
        if gate["required"] and gate["status"] != "pass"
    ]

    if source_blockers:
        status = "HOLD_SOURCE_INTEGRATION_AND_HARDWARE"
    elif hardware_blockers:
        status = "HOLD_HARDWARE"
    else:
        status = "READY_FOR_MAINTAINER_REVIEW"

    return {
        "status": status,
        "source_blockers": source_blockers,
        "hardware_blockers": hardware_blockers,
        "new_implementation_pr_allowed": False,
        "payout_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", nargs="?", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    data = json.loads(args.snapshot.read_text())
    try:
        result = assess(data)
    except EvidenceError as exc:
        if args.as_json:
            print(json.dumps({"status": "INVALID_EVIDENCE", "error": str(exc)}, sort_keys=True))
        else:
            print(f"INVALID_EVIDENCE: {exc}")
        return 2

    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["status"])
        for blocker in result["source_blockers"]:
            print(f"SOURCE BLOCKER: {blocker}")
        for blocker in result["hardware_blockers"]:
            print(f"HARDWARE BLOCKER: {blocker}")
        print("NEW IMPLEMENTATION PR: NO")
        print("PAYOUT CLAIM: NO")
    return 0 if result["status"] == "READY_FOR_MAINTAINER_REVIEW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
