# SPDX-License-Identifier: MIT
import hashlib

from concierge.grantfox_queue_batch import _swarm_reservation


def test_grantfox_reservation_metadata_is_canonical_and_deterministic():
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
    }
