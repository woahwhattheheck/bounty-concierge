# SPDX-License-Identifier: MIT
import hashlib

from concierge.grantfox_queue_batch import (
    _swarm_reservation,
    compile_grantfox_queue_batch,
)


def _snapshot(
    number: int,
    *,
    assigned_to=None,
    actor_applied: bool = False,
    issue_state: str = "open",
):
    return {
        "schema": "grantfox-queue-gate/v1",
        "listing_url": (
            f"https://contribute.grantfox.xyz/org/Owner/repo/issue/{number}"
        ),
        "canonical_issue_url": f"https://github.com/Owner/repo/issues/{number}",
        "actor_login": "worker",
        "assigned_to": assigned_to,
        "actor_applied": actor_applied,
        "issue_state": issue_state,
        "application_count": 0,
        "linked_pr_urls": [],
        "labels": ["GrantFox OSS"],
        "observed_at": "2026-10-06T06:00:00Z",
        "evaluated_at": "2026-10-06T06:00:30Z",
        "max_snapshot_age_seconds": 900,
    }


def test_grantfox_reservation_metadata_is_canonical_and_activation_gated():
    mixed = {
        "identity": {
            "owner": "Thalamii",
            "repo": "StellarGrid",
            "issue_number": 8,
        }
    }
    lower = {
        "identity": {
            "owner": "thalamii",
            "repo": "stellargrid",
            "issue_number": 8,
        }
    }

    expected_key = "grantfox:thalamii/stellargrid#8"
    expected_branch = "swarm-custody/v1/" + hashlib.sha256(
        expected_key.encode("utf-8")
    ).hexdigest()

    assert _swarm_reservation(mixed) == _swarm_reservation(lower) == {
        "schema": "swarm-custody-reservation/v1",
        "work_key": expected_key,
        "branch": expected_branch,
        "tool": "tools/swarm_claim_reservation.py",
        "dispatch_authority": False,
        "activation_required": True,
        "activation_receipt_schema": "grantfox-activation-gate/v1",
    }


def test_queue_batch_does_not_mint_custody_for_wait_or_hold_rows():
    receipt = compile_grantfox_queue_batch(
        {
            "schema": "grantfox-queue-batch/v1",
            "snapshots": [
                _snapshot(1),
                _snapshot(2, actor_applied=True),
                _snapshot(3, assigned_to="worker"),
                _snapshot(4, issue_state="closed"),
            ],
        }
    )

    assert set(receipt["swarm_reservations"]) == {
        "owner/repo#1",
        "owner/repo#3",
    }
    assert receipt["counts"] == {
        "APPLY_ELIGIBLE": 1,
        "WAIT_ASSIGNMENT": 1,
        "IMPLEMENTATION_ELIGIBLE": 1,
        "HOLD": 1,
    }
