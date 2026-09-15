import hashlib
import hmac
import json
from pathlib import Path

import pytest

from concierge import submission_transport_router as public
from concierge import _submission_transport_router_core as core


SOURCE = "https://github.com/example/project/issues/315"
HEX40 = "a" * 40
EVIDENCE = "b" * 64
GH_AUTHORITY = "github-app-adapter-v1"
EMAIL_AUTHORITY = "smtp-adapter-v1"
GH_KEY = bytes.fromhex("11" * 32)
EMAIL_KEY = bytes.fromhex("22" * 32)
WRONG_KEY = bytes.fromhex("33" * 32)


def check(condition, message="check failed"):
    if not condition:
        raise AssertionError(message)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


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


def route_policy(packet, include_email=True):
    routes = [
        {
            "route_id": "upstream-github",
            "route_class": "github-pr",
            "authority_id": GH_AUTHORITY,
            "route_evidence_sha256": "d" * 64,
            "failover_on": ["PROVIDER_403_INTEGRATION_FORBIDDEN"],
        }
    ]
    if include_email:
        routes.append(
            {
                "route_id": "sponsor-email",
                "route_class": "email",
                "authority_id": EMAIL_AUTHORITY,
                "route_evidence_sha256": "e" * 64,
                "failover_on": ["PROVIDER_TERMINAL_REJECTED"],
            }
        )
    policy = {
        "schema": "sponsor-submission-transport-policy/v1",
        "policy_id": "host-root-v1",
        "canonical_source_url": SOURCE,
        "packet_sha256": packet["packet_sha256"],
        "policy_evidence_sha256": "c" * 64,
        "routes": routes,
    }
    policy["policy_sha256"] = digest(policy)
    return policy


def sign_receipt(packet, policy, index, *, outcome, failure=None, key=None):
    operation = core.compile_transport_operation(packet, policy, index)
    route = policy["routes"][index]
    if key is None:
        key = GH_KEY if index == 0 else EMAIL_KEY
    receipt = {
        "schema": "provider-attempt-authority/v1",
        "authority_id": route["authority_id"],
        "operation_sha256": operation["operation_sha256"],
        "attempt_id": f"try-{index + 1}",
        "route_id": route["route_id"],
        "packet_sha256": packet["packet_sha256"],
        "head_sha": HEX40,
        "artifact_evidence_sha256": EVIDENCE,
        "policy_sha256": policy["policy_sha256"],
        "outcome": outcome,
        "failure_class": failure,
        "provider_evidence_sha256": ("f" if index == 0 else "1") * 64,
    }
    receipt["auth_tag_hmac_sha256"] = hmac.new(
        key, canonical(receipt), hashlib.sha256
    ).hexdigest()
    return receipt


def private_verifier(keys=None):
    return core.make_hmac_authority_verifier(
        keys or {GH_AUTHORITY: GH_KEY, EMAIL_AUTHORITY: EMAIL_KEY}
    )


def test_public_primary_route_needs_no_provider_trust_root():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    receipt = public.compile_transport_decision(req)
    check(receipt["disposition"] == "READY_PRIMARY")
    check(receipt["next_route"]["route_id"] == "upstream-github")
    check(receipt["authority"]["caller_verifier_injection_supported"] is False)
    check(receipt["authority"]["caller_trust_root_selection_supported"] is False)


def test_public_compile_rejects_lambda_verifier_injection_at_signature():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    with pytest.raises(TypeError):
        public.compile_transport_decision(req, authority_verifier=lambda _receipt: True)


def test_public_verify_rejects_lambda_verifier_injection_at_signature():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    receipt = public.compile_transport_decision(req)
    with pytest.raises(TypeError):
        public.verify_transport_decision(
            req, receipt, authority_verifier=lambda _receipt: True
        )


def test_public_module_does_not_export_caller_keyring_builder():
    check("make_hmac_authority_verifier" not in public.__all__)
    check(not hasattr(public, "make_hmac_authority_verifier"))


def test_public_cli_has_no_authority_or_ledger_selector():
    with pytest.raises(SystemExit):
        public.main(["request.json", "--authority-keyring", "/tmp/attacker.json"])
    with pytest.raises(SystemExit):
        public.main(["request.json", "--authority-ledger", "/tmp/attacker"])


def test_public_claimed_failure_without_fixed_host_receipt_holds():
    fixed = Path("/var/lib/bounty-concierge/provider-attempt-authority")
    if fixed.exists():
        pytest.skip("host receipt ledger is provisioned in this environment")
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    receipt = public.compile_transport_decision(
        {"packet": packet, "policy": policy, "attempts": [failed]}
    )
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
    check(receipt["next_route"] is None)


def test_private_engine_can_validate_real_authority_for_isolated_engine_tests():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    req = {"packet": packet, "policy": policy, "attempts": [failed]}
    receipt = core.compile_transport_decision(
        req, authority_verifier=private_verifier()
    )
    check(receipt["disposition"] == "READY_FALLBACK")
    check(receipt["next_route"]["route_id"] == "sponsor-email")


def test_private_engine_without_verifier_holds_claimed_failure():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    receipt = core.compile_transport_decision(
        {"packet": packet, "policy": policy, "attempts": [failed]}
    )
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")
    check(receipt["next_route"] is None)
    check("outcome" not in receipt["completed_attempts"][0])


def test_private_engine_wrong_key_holds():
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    receipt = core.compile_transport_decision(
        {"packet": packet, "policy": policy, "attempts": [failed]},
        authority_verifier=private_verifier(
            {GH_AUTHORITY: WRONG_KEY, EMAIL_AUTHORITY: EMAIL_KEY}
        ),
    )
    check(receipt["disposition"] == "HOLD_PROVIDER_AUTHORITY_UNVERIFIED")


def test_private_engine_ambiguous_outcome_never_fails_over():
    packet = ready_packet()
    policy = route_policy(packet)
    ambiguous = sign_receipt(packet, policy, 0, outcome="AMBIGUOUS")
    receipt = core.compile_transport_decision(
        {"packet": packet, "policy": policy, "attempts": [ambiguous]},
        authority_verifier=private_verifier(),
    )
    check(receipt["disposition"] == "HOLD_AMBIGUOUS_PROVIDER_OUTCOME")
    check(receipt["next_route"] is None)


def test_private_engine_success_is_terminal():
    packet = ready_packet()
    policy = route_policy(packet)
    success = sign_receipt(packet, policy, 0, outcome="SUCCESS_CONFIRMED")
    receipt = core.compile_transport_decision(
        {"packet": packet, "policy": policy, "attempts": [success]},
        authority_verifier=private_verifier(),
    )
    check(receipt["disposition"] == "ALREADY_SUBMITTED")
    check(receipt["next_route"] is None)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("packet_sha256", "9" * 64),
        ("head_sha", "9" * 40),
        ("artifact_evidence_sha256", "9" * 64),
        ("policy_sha256", "9" * 64),
        ("operation_sha256", "9" * 64),
    ],
)
def test_private_engine_rejects_replayed_receipt_bindings(field, bad):
    packet = ready_packet()
    policy = route_policy(packet)
    failed = sign_receipt(
        packet,
        policy,
        0,
        outcome="FAILED_CONFIRMED",
        failure="PROVIDER_403_INTEGRATION_FORBIDDEN",
    )
    failed[field] = bad
    with pytest.raises(core.SubmissionTransportInputError):
        core.compile_transport_decision(
            {"packet": packet, "policy": policy, "attempts": [failed]},
            authority_verifier=private_verifier(),
        )


def test_public_adapter_reuses_canonical_custody_path_allowlist():
    packet = ready_packet()
    packet["evidence"]["changed_paths"] = ["src/forbidden.py"]
    packet["packet_sha256"] = digest(
        {k: v for k, v in packet.items() if k != "packet_sha256"}
    )
    with pytest.raises(
        public.SubmissionTransportInputError,
        match="PACKET_CHANGED_PATH_OUTSIDE_ALLOWLIST",
    ):
        public.compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_public_adapter_reuses_canonical_positive_reward_rule():
    packet = ready_packet()
    packet["advertised_reward_usd"] = "0"
    packet["packet_sha256"] = digest(
        {k: v for k, v in packet.items() if k != "packet_sha256"}
    )
    with pytest.raises(public.SubmissionTransportInputError, match="PACKET_REWARD_INVALID"):
        public.compile_transport_decision(
            {"packet": packet, "policy": route_policy(packet), "attempts": []}
        )


def test_public_decision_verifier_recompiles_fixed_trust_path():
    packet = ready_packet()
    req = {"packet": packet, "policy": route_policy(packet), "attempts": []}
    receipt = public.compile_transport_decision(req)
    check(public.verify_transport_decision(req, receipt))
    tampered = dict(receipt)
    tampered["disposition"] = "READY_FALLBACK"
    check(not public.verify_transport_decision(req, tampered))


def test_strict_json_rejects_duplicate_keys():
    with pytest.raises(public.SubmissionTransportInputError, match="duplicate JSON key"):
        public.strict_json_loads('{"x":1,"x":2}')
