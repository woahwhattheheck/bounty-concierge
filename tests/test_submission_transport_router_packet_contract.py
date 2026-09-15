import hashlib
import json

import pytest

from concierge.submission_transport_router import (
    SubmissionTransportInputError,
    compile_transport_decision,
)


SOURCE = "https://github.com/example/project/issues/315"
HEX40 = "a" * 40
EVIDENCE = "b" * 64


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def ready_packet():
    packet = {
        "canonical_source_url": SOURCE,
        "advertised_reward_usd": "90",
        "disposition": "READY_FOR_HUMAN_SUBMISSION",
        "reason_codes": [],
        "evidence": {
            "pull_request_url": "https://github.com/example/project/pull/400",
            "pull_request_repo": "example/project",
            "pull_request_number": 400,
            "head_sha": HEX40,
            "changed_paths": ["src/fix.py"],
            "allowed_paths": ["src/fix.py"],
            "tests": [{"command": "pytest -q", "outcome": "PASS"}],
            "evidence_sha256": EVIDENCE,
            "acceptance_checks": [{"criterion_id": "tests", "status": "PASS"}],
        },
        "authority": {
            "submission": "human_only",
            "reward": "advertised_only",
            "acceptance": "not_inferred",
            "payout": "not_inferred",
            "cash_claim": False,
        },
    }
    packet["packet_sha256"] = digest(packet)
    return packet


def route_policy(packet):
    policy = {
        "schema": "sponsor-submission-transport-policy/v1",
        "policy_id": "packet-contract-v1",
        "canonical_source_url": SOURCE,
        "packet_sha256": packet["packet_sha256"],
        "policy_evidence_sha256": "c" * 64,
        "routes": [
            {
                "route_id": "upstream-github",
                "route_class": "github-pr",
                "authority_id": "github-app-adapter-v1",
                "route_evidence_sha256": "d" * 64,
                "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"],
            }
        ],
    }
    policy["policy_sha256"] = digest(policy)
    return policy


def test_transport_reuses_custody_path_allowlist_contract():
    packet = ready_packet()
    packet["evidence"]["changed_paths"] = ["src/forbidden.py"]
    packet["packet_sha256"] = digest({k: v for k, v in packet.items() if k != "packet_sha256"})
    with pytest.raises(
        SubmissionTransportInputError,
        match="PACKET_CHANGED_PATH_OUTSIDE_ALLOWLIST",
    ):
        compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_transport_reuses_custody_positive_reward_contract():
    packet = ready_packet()
    packet["advertised_reward_usd"] = "0"
    packet["packet_sha256"] = digest({k: v for k, v in packet.items() if k != "packet_sha256"})
    with pytest.raises(SubmissionTransportInputError, match="PACKET_REWARD_INVALID"):
        compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )
